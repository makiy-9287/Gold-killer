"""Entry point.  python -m src.main [--selftest | --ai-ping] [--force] [--dry-run]

Flow (විනාඩි 15කට සැරයක්):
  1. window check (Mon-Fri 10:00-20:00 Colombo) + data fetch + stale check
  2. charts render (D1,H4,H1,M15,M5)
  3. open signals mechanical tracking (approach / touched / SL / TP / expiry / missed)  -> Telegram
  4. touched signals -> AI confirmation -> "ENTRY CONFIRMED" message
  5. 3h digest: charts 5 -> Telegram
  6. AI scan (Antigravity) -> submit_signal tool call -> validate -> lot calc -> Telegram
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import asdict

import pandas as pd

from . import messages as M
from .agent import (CANCEL_SIGNAL, CONFIRM_TOOLS, DEFAULT_TOOLS, SCAN_TOOLS, AgentError, call_antigravity, run_ai)
from .charts import render_all, render_chart
from .config import Config, load_config
from .market_data import DataError, MarketData, is_stale, load_market
from .prompts import (build_confirm_input, build_scan_input, build_sources, confirm_system_instruction,
                      csv_for_fallback, scan_system_instruction)
from .risk import Sizing, compute_expiry, size_position, validate_signal
from .state import (OPEN_STATUSES, ai_count, ai_inc, find_signal, load_state, open_signals, save_state)
from .telegram_bot import Telegram, TelegramError
from .tracker import advance, closed_candles_after
from .util import fmt_local, in_trading_window, log, to_local, utcnow


# ------------------------------------------------------------------ helpers
def safe_send(tg: Telegram, text: str, silent: bool = False) -> bool:
    try:
        tg.send(text, silent=silent)
        return True
    except TelegramError as e:
        log(f"Telegram send failed: {e}")
        return False


def notify_error(cfg: Config, tg: Telegram, state: dict, msg: str, now: dt.datetime) -> None:
    """Error messages spam වෙන්නේ නැති වෙන්න පැය 2කට වරක් විතරයි."""
    last = state.get("last_error_notice_ts")
    if last and (now - dt.datetime.fromisoformat(last)) < dt.timedelta(hours=2):
        log(f"(error notice suppressed) {msg}")
        return
    state["last_error_notice_ts"] = now.isoformat()
    safe_send(tg, M.fmt_error(msg))


def digest_due(state: dict, now: dt.datetime, cfg: Config) -> bool:
    last = state.get("last_digest_ts")
    if not last:
        return True
    last_dt = dt.datetime.fromisoformat(last)
    if to_local(last_dt, cfg).date() != to_local(now, cfg).date():
        return True  # අද දවසේ පළමු run එක
    return (now - last_dt) >= dt.timedelta(hours=cfg.digest_every_hours) - dt.timedelta(minutes=8)


def send_digest(cfg: Config, tg: Telegram, state: dict, md: MarketData, charts: dict[str, str], now: dt.datetime) -> None:
    cap0 = M.fmt_digest_caption(md.last_price, fmt_local(now, cfg, "%a %H:%M"), md.source, md.levels, open_signals(state))
    order = ["D1", "H4", "H1", "M15", "M5"]
    captions = [cap0] + [f"<b>{tf}</b>" for tf in order[1:]]
    try:
        tg.send_album([charts[tf] for tf in order], captions, silent=True)
        state["last_digest_ts"] = now.isoformat()
    except TelegramError as e:
        log(f"digest failed: {e}")


def _sig_charts(cfg: Config, md: MarketData, sig: dict) -> list[str]:
    lv = [(sig["entry"], "ENTRY", "#2962ff"), (sig["sl"], "SL", "#f23645"), (sig["tp1"], "TP1", "#089981")]
    if sig.get("tp2"):
        lv.append((sig["tp2"], "TP2", "#00695c"))
    paths = []
    for tf in ("M15", "M5"):
        p = os.path.join(cfg.out_dir, f"signal_{sig['id']}_{tf}.png")
        render_chart(md.frames[tf], tf, p, cfg.tz, cfg.chart_candles, levels=lv,
                     zone=(sig["poi_low"], sig["poi_high"]), note=f"{sig['direction']} limit")
        paths.append(p)
    return paths


# ------------------------------------------------------------------ signal handling
def send_signal(cfg: Config, tg: Telegram, md: MarketData, sig: dict, resend: bool = False) -> bool:
    sz = Sizing(**sig["sizing"])
    txt = M.fmt_signal(sig, sz, md.last_price, cfg, sig["created_lk"], sig["expires_lk"])
    if resend:
        txt = "🔁 <i>(re-sent: earlier delivery failed)</i>\n" + txt
    ok = safe_send(tg, txt)
    if ok:
        try:
            paths = _sig_charts(cfg, md, sig)
            tg.send_album(paths, [f"<b>M15</b> · {sig['direction']} limit {sig['entry']:.2f}", "<b>M5</b>"])
        except Exception as e:  # noqa: BLE001
            log(f"signal chart failed: {type(e).__name__}: {e}")
    return ok


def handle_submit(cfg: Config, tg: Telegram, state: dict, md: MarketData, now: dt.datetime, args: dict) -> None:
    if len(open_signals(state)) >= cfg.max_open_signals:
        log("submit_signal ignored: an open signal already exists")
        return
    ok, reason, c = validate_signal(cfg, args, md.last_price)
    if not ok:
        log(f"submit_signal REJECTED by guard rails: {reason}")
        return
    for s in open_signals(state):
        if s["direction"] == c["direction"] and abs(s["entry"] - c["entry"]) < 3:
            log("submit_signal ignored: duplicate")
            return
    sz = size_position(cfg, c)
    exp = compute_expiry(now, args.get("expiry_minutes"), cfg)
    sid = f"XAU-{to_local(now, cfg):%m%d-%H%M}"
    sig = {
        "id": sid, **c, "status": "PENDING",
        "created_ts": now.isoformat(), "created_lk": fmt_local(now, cfg, "%a %H:%M"),
        "expires_ts": exp.isoformat(), "expires_lk": fmt_local(exp, cfg, "%H:%M"),
        "last_ts": md.last_candle_open.isoformat(), "touched_ts": None,
        "approach_alerted": False, "tp1_hit": False, "confirm_checks": 0, "confirm_errors": 0,
        "notified": False, "sizing": asdict(sz),
        **{k: args.get(k) for k in ("poi_type", "wave_summary", "ict_summary", "tp1_reason", "tp2_reason",
                                     "news_check", "confidence_10", "cancel_conditions", "rationale_si")},
    }
    state["signals"].append(sig)
    sig["notified"] = send_signal(cfg, tg, md, sig)
    log(f"SIGNAL {sid} {c['direction']} entry={c['entry']} sl={c['sl']} tp1={c['tp1']} tp2={c.get('tp2')} notified={sig['notified']}")


def handle_cancel(cfg: Config, tg: Telegram, state: dict, md: MarketData, args: dict) -> None:
    sig = find_signal(state, str(args.get("signal_id", "")))
    if not sig or sig["status"] != "PENDING":
        log(f"cancel_signal ignored (not a PENDING signal): {args.get('signal_id')}")
        return
    sig["status"] = "CANCELLED"
    ev = {"type": "CANCELLED", "price": None, "note": ""}
    safe_send(tg, M.fmt_event(sig, ev, md.last_price, extra=str(args.get("reason", ""))))


# ------------------------------------------------------------------ steps
def step_tracking(cfg: Config, tg: Telegram, state: dict, md: MarketData, now: dt.datetime) -> None:
    df5 = md.frames["M5"]
    for sig in open_signals(state):
        for ev in advance(sig, df5, now, cfg.approach_usd):
            t = ev["type"]
            log(f"event {sig['id']}: {t} {ev.get('note','')}")
            if t == "APPROACH":
                safe_send(tg, M.fmt_approach(sig, md.last_price))
            elif t == "TOUCHED":
                safe_send(tg, M.fmt_touched(sig, ev, fmt_local(pd.Timestamp(ev["time"]), cfg, "%H:%M")))
            elif t in M.EVENT_TEXT:
                safe_send(tg, M.fmt_event(sig, ev, md.last_price))


def step_confirmations(cfg: Config, tg: Telegram, state: dict, md: MarketData, charts: dict, now: dt.datetime) -> None:
    df5 = md.frames["M5"]
    for sig in [s for s in state["signals"] if s["status"] == "TRIGGERED"]:
        if closed_candles_after(sig, df5, now) < 1:
            log(f"{sig['id']}: touched but no closed M5 candle after touch yet - confirm next run")
            continue
        if ai_count(state, now) >= cfg.ai_daily_limit:
            sig["status"] = "ACTIVE"
            safe_send(tg, M.fmt_confirm(sig, "SKIPPED", "AI daily quota reached.", "", md.last_price))
            continue
        ai_inc(state, now)
        extra = {"signal.json": json.dumps({k: sig.get(k) for k in
                 ("id", "direction", "entry", "poi_low", "poi_high", "sl", "tp1", "tp2", "touched_ts")})}
        try:
            res = run_ai(cfg, confirm_system_instruction(), build_confirm_input(cfg, md, now, sig, charts),
                         build_sources(md, now, cfg, tfs=["M15", "M5"], n=80, extra=extra), CONFIRM_TOOLS,
                         csv_for_fallback(md, now, cfg, 40), max_tokens=45000)
        except AgentError as e:
            sig["confirm_errors"] = int(sig.get("confirm_errors", 0)) + 1
            log(f"confirm AI error ({sig['confirm_errors']}): {str(e)[:200]}")
            if sig["confirm_errors"] >= 3:
                sig["status"] = "ACTIVE"
                safe_send(tg, M.fmt_confirm(sig, "SKIPPED", "AI error.", "", md.last_price))
            continue
        call = next((c for c in res.calls if c["name"] == "confirm_entry"), None)
        if not call:
            sig["confirm_checks"] = int(sig.get("confirm_checks", 0)) + 1
            log(f"{sig['id']}: no confirm_entry call (text: {res.text[:120]})")
        else:
            a = call["args"]
            verdict = str(a.get("verdict", "WAIT")).upper()
            r_en, r_si = str(a.get("reason_en", "")), str(a.get("reason_si", ""))
            if verdict == "CONFIRMED":
                sig["status"], sig["confirmed"] = "ACTIVE", True
                safe_send(tg, M.fmt_confirm(sig, "CONFIRMED", r_en, r_si, md.last_price))
            elif verdict == "INVALIDATED":
                sig["status"] = "INVALIDATED"
                safe_send(tg, M.fmt_confirm(sig, "INVALIDATED", r_en, r_si, md.last_price))
            else:
                sig["confirm_checks"] = int(sig.get("confirm_checks", 0)) + 1
                log(f"{sig['id']}: WAIT ({r_en[:100]})")
        if sig["status"] == "TRIGGERED" and int(sig.get("confirm_checks", 0)) >= cfg.max_confirm_checks:
            sig["status"] = "ACTIVE"
            safe_send(tg, M.fmt_confirm(sig, "TIMEOUT", "No decisive reaction after several checks.", "", md.last_price))


def step_scan(cfg: Config, tg: Telegram, state: dict, md: MarketData, charts: dict, now: dt.datetime) -> None:
    opens = open_signals(state)
    if any(s["status"] in ("TRIGGERED", "ACTIVE") for s in opens):
        log("scan skipped: a trade is live (TRIGGERED/ACTIVE)")
        return
    last = state.get("last_scan_ts")
    if last and not cfg.force and (now - dt.datetime.fromisoformat(last)) < dt.timedelta(minutes=cfg.min_scan_gap_min):
        log("scan skipped: min gap not reached")
        return
    used, cap = ai_count(state, now), cfg.ai_daily_limit - cfg.ai_confirm_reserve
    if used >= cap:
        log(f"scan skipped: AI budget used {used}/{cap} (Pacific day)")
        return
    ai_inc(state, now)
    state["last_scan_ts"] = now.isoformat()
    try:
        res = run_ai(cfg, scan_system_instruction(), build_scan_input(cfg, md, now, charts, opens),
                     build_sources(md, now, cfg), SCAN_TOOLS, csv_for_fallback(md, now, cfg))
    except AgentError as e:
        notify_error(cfg, tg, state, f"AI scan failed: {str(e)[:300]}", now)
        return
    log(f"AI done [{res.backend}] status={res.status} | text: {res.text[:200]!r}")
    os.makedirs(cfg.out_dir, exist_ok=True)
    with open(os.path.join(cfg.out_dir, "last_ai_response.json"), "w", encoding="utf-8") as f:
        json.dump({"text": res.text, "calls": res.calls, "usage": res.usage, "status": res.status}, f, indent=1)
    submitted = False
    for call in res.calls:
        if call["name"] == "submit_signal" and not submitted:
            submitted = True
            handle_submit(cfg, tg, state, md, now, call["args"])
        elif call["name"] == "cancel_signal":
            handle_cancel(cfg, tg, state, md, call["args"])
    if not res.calls:
        log("NO SETUP -> no Telegram message (by design)")


def ai_ping(cfg: Config, tg: Telegram, now: dt.datetime) -> int:
    """Antigravity access + function-call parsing test (1 request)."""
    prompt = [{"type": "text", "text": "Connectivity test. Call the function cancel_signal with signal_id='PING' and "
                                       "reason='connectivity test'. Do nothing else."}]
    try:
        res = call_antigravity(cfg, "You are a connectivity test agent.", prompt, [], [CANCEL_SIGNAL], 8000)
        ok = any(c["name"] == "cancel_signal" for c in res.calls)
        msg = (f"🧪 AI ping: backend={res.backend} status={res.status} function_call_parsed={ok} "
               f"usage={json.dumps(res.usage)[:200]}")
    except AgentError as e:
        msg = f"🧪 AI ping FAILED: {str(e)[:400]}"
        ok = False
    log(msg)
    safe_send(tg, M.e(msg))
    return 0 if ok else 1


# ------------------------------------------------------------------ run
def run(cfg: Config, mode: str = "scan") -> int:
    now = utcnow()
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.dry_run)
    state = load_state(cfg.state_path)

    if mode == "ai-ping":
        return ai_ping(cfg, tg, now)
    if mode == "scan" and not cfg.force and not in_trading_window(now, cfg):
        log(f"outside trading window ({fmt_local(now, cfg, '%a %H:%M')} Colombo) - exit")
        return 0

    try:
        md = load_market(cfg, now)
    except DataError as e:
        log(f"DATA ERROR: {e}")
        notify_error(cfg, tg, state, f"Market data error: {e}", now)
        save_state(cfg.state_path, state)
        return 0
    log(f"data ok [{md.source}] price={md.last_price:.2f} last M5={md.last_candle_open} levels={md.levels}")
    if mode == "scan" and not cfg.force and is_stale(md, now, cfg):
        log("market closed / stale data - exit")
        save_state(cfg.state_path, state)
        return 0

    os.makedirs(cfg.out_dir, exist_ok=True)
    charts = render_all(md.frames, cfg.out_dir, cfg.tz, cfg.chart_candles)

    if mode == "selftest":
        safe_send(tg, "✅ <b>XAUUSD bot self-test</b>: data + charts + Telegram OK. Sending chart digest…")
        send_digest(cfg, tg, state, md, charts, now)
        return 0

    # unsent signals retry
    for sig in open_signals(state):
        if sig["status"] == "PENDING" and not sig.get("notified"):
            sig["notified"] = send_signal(cfg, tg, md, sig, resend=True)

    step_tracking(cfg, tg, state, md, now)
    step_confirmations(cfg, tg, state, md, charts, now)

    if digest_due(state, now, cfg):
        send_digest(cfg, tg, state, md, charts, now)

    step_scan(cfg, tg, state, md, charts, now)
    save_state(cfg.state_path, state)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="data+charts+Telegram test (no AI)")
    ap.add_argument("--ai-ping", action="store_true", help="1 Antigravity request: access + function-call test")
    ap.add_argument("--force", action="store_true", help="ignore trading window / stale checks")
    ap.add_argument("--dry-run", action="store_true", help="print Telegram messages instead of sending")
    a = ap.parse_args(argv)
    cfg = load_config()
    cfg.force = cfg.force or a.force
    cfg.dry_run = cfg.dry_run or a.dry_run
    mode = "selftest" if a.selftest else "ai-ping" if a.ai_ping else "scan"
    return run(cfg, mode)


if __name__ == "__main__":
    sys.exit(main())
