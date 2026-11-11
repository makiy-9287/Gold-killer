import datetime as dt, json, os, subprocess, pandas as pd
from src import main as mn, agent
from src.agent import AgentResult


def test_full_lifecycle(md, cfg, now, monkeypatch, capsys):
    P = md.last_price
    cfg.force = True
    monkeypatch.setattr(mn, "load_market", lambda c, n: md)
    monkeypatch.setattr(mn, "utcnow", lambda: now)
    queue, seen = [], []

    def fake(cfg_, system, parts, sources, tools, csv="", max_tokens=None):
        seen.append([t.get("name", t["type"]) for t in tools])
        return AgentResult("antigravity", "requires_action", "NO_TRADE: x", queue.pop(0)())
    monkeypatch.setattr(mn, "run_ai", fake)

    sub = {"name": "submit_signal", "args": dict(
        direction="LONG", entry_price=round(P - 3, 2), poi_low=round(P - 4, 2), poi_high=round(P - 2, 2),
        poi_type="M15 FVG+OB", stop_loss=round(P - 7, 2), tp1=round(P + 5, 2), tp2=round(P + 12, 2),
        tp1_reason="PDH liquidity", confidence_10=8, expiry_minutes=90, wave_summary="W2 done",
        ict_summary="SSL sweep + MSS", news_check="none", cancel_conditions="M15 close below SL",
        rationale_si="සුපිරි setup එකක්.")}
    queue.append(lambda: [sub])

    # ---- run 1: scan -> signal
    assert mn.run(cfg) == 0
    out = capsys.readouterr().out
    assert "A+ BUY LIMIT" in out and "TRADE SIZE NOT SUITABLE" in out and "[TG ALBUM]" in out
    assert "submit_signal" in seen[0]
    st = json.load(open(cfg.state_path)); sig = st["signals"][0]
    assert sig["status"] == "PENDING" and sig["notified"] and st["ai_calls"]["count"] == 1

    # ---- run 2: price dips to entry; first confirm = WAIT
    def confirm(verdict, why):
        return lambda: [{"name": "confirm_entry", "args": {"signal_id": sig["id"], "verdict": verdict,
                                                           "reason_en": why, "reason_si": "ok"}}]
    queue.append(confirm("WAIT", "still probing"))
    df5, e = md.frames["M5"], sig["entry"]
    idx = pd.date_range(df5.index[-1] + pd.Timedelta(minutes=5), periods=4, freq="5min", tz="UTC")
    add = pd.DataFrame({"open": [e + 1.5] * 4, "high": [e + 2, e + 2, e + 1.5, e + 1.5],
                        "low": [e + 1, e - 0.2, e + 0.3, e + 0.5], "close": [e + 1.2, e + 0.8, e + 1.0, e + 1.0]}, index=idx)
    md.frames["M5"] = pd.concat([df5, add]); md.last_price = float(add.close.iloc[-1]); md.last_candle_open = idx[-1]
    monkeypatch.setattr(mn, "utcnow", lambda: (idx[-1] + pd.Timedelta(minutes=7)).to_pydatetime())
    assert mn.run(cfg) == 0
    out = capsys.readouterr().out
    assert "Entry zone touched" in out
    st = json.load(open(cfg.state_path))
    assert st["signals"][0]["status"] == "TRIGGERED" and st["signals"][0]["confirm_checks"] == 1
    assert seen[-1] == ["code_execution", "confirm_entry", "cancel_signal"]

    # ---- run 3: confirmed
    queue.append(confirm("CONFIRMED", "rejection wick"))
    monkeypatch.setattr(mn, "utcnow", lambda: (idx[-1] + pd.Timedelta(minutes=22)).to_pydatetime())
    assert mn.run(cfg) == 0
    out = capsys.readouterr().out
    assert "ENTRY CONFIRMED" in out
    assert json.load(open(cfg.state_path))["signals"][0]["status"] == "ACTIVE"

    # ---- run 4: TP1 + TP2 hit mechanically; trade finished so a fresh scan runs -> NO_TRADE (silent)
    queue.append(lambda: [])
    hi = pd.date_range(idx[-1] + pd.Timedelta(minutes=5), periods=2, freq="5min", tz="UTC")
    add2 = pd.DataFrame({"open": [e + 1] * 2, "high": [sig["tp1"] + 0.2, sig["tp2"] + 0.2],
                         "low": [e + 0.5, sig["tp1"]], "close": [sig["tp1"], sig["tp2"]]}, index=hi)
    md.frames["M5"] = pd.concat([md.frames["M5"], add2]); md.last_candle_open = hi[-1]; md.last_price = float(add2.close.iloc[-1])
    monkeypatch.setattr(mn, "utcnow", lambda: (hi[-1] + pd.Timedelta(minutes=7)).to_pydatetime())
    assert mn.run(cfg) == 0
    out = capsys.readouterr().out
    assert "TP1 HIT" in out and "TP2 HIT" in out
    assert json.load(open(cfg.state_path))["signals"][0]["status"] == "TP2_HIT"


def test_parse_interaction_variants():
    j = {"status": "requires_action", "steps": [
        {"type": "thought", "summary": "x"},
        {"type": "code_execution_call", "arguments": {"code": "1"}},
        {"type": "function_call", "id": "c1", "name": "submit_signal", "arguments": '{"direction":"LONG"}'},
        {"type": "function_call", "id": "c2", "name": "write_file", "arguments": {}},
        {"type": "model_output", "content": [{"type": "text", "text": "NO_TRADE: nope"}]}]}
    r = agent.parse_interaction(j)
    assert [c["name"] for c in r.calls] == ["submit_signal"] and r.calls[0]["args"]["direction"] == "LONG"
    assert r.text == "NO_TRADE: nope"


def test_request_body_and_headers(md, cfg, now, monkeypatch, tmp_path):
    from src import prompts, charts
    cfg.gemini_api_key = "KEY"
    paths = charts.render_all(md.frames, str(tmp_path), cfg.tz, 120)
    parts = prompts.build_scan_input(cfg, md, now, paths, [])
    assert [p["type"] for p in parts].count("image") == 5
    src = prompts.build_sources(md, now, cfg)
    assert {s["target"] for s in src} >= {"/workspace/data/M5.csv", "/workspace/data/levels.json"}
    assert all(len(s["content"]) < 1_000_000 for s in src)
    captured = {}
    class R:
        status_code = 200; text = "{}"
        def json(self): return {"status": "completed", "steps": []}
    def fake_post(url, headers=None, data=None, timeout=None):
        captured.update(url=url, headers=headers, body=json.loads(data)); return R()
    monkeypatch.setattr(agent.requests, "post", fake_post)
    agent.run_ai(cfg, prompts.scan_system_instruction(), parts, src, agent.SCAN_TOOLS)
    b = captured["body"]
    assert captured["url"].endswith("/v1beta/interactions") and captured["headers"]["x-goog-api-key"] == "KEY"
    assert b["agent"] == "antigravity-preview-09-2026" and b["agent_config"]["model"] == "gemini-3.8-flash"
    assert "ELLIOTT WAVE" in b["system_instruction"] and "AUTOMATION RUNTIME ADDENDUM" in b["system_instruction"]
    print("request size KB:", len(captured["body"].__repr__()) // 1024, "system chars:", len(b["system_instruction"]))


def test_fallback_on_error(md, cfg, now, monkeypatch):
    cfg.gemini_api_key = "KEY"
    calls = []
    class R:
        def __init__(s, code, js): s.status_code, s._j, s.text = code, js, "err"
        def json(s): return s._j
    def fake_post(url, headers=None, data=None, timeout=None):
        calls.append(url)
        if "interactions" in url: return R(404, {})
        return R(200, {"candidates": [{"content": {"parts": [{"text": 'x {"calls":[{"name":"cancel_signal","args":{"signal_id":"A","reason":"r"}}],"note":"n"}'}]}}]})
    monkeypatch.setattr(agent.requests, "post", fake_post)
    r = agent.run_ai(cfg, "sys", [{"type": "text", "text": "hi"}], [], agent.SCAN_TOOLS)
    assert r.backend == "gemini-fallback" and r.calls[0]["name"] == "cancel_signal"
