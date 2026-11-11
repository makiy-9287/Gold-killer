from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

from .config import ROOT, Config
from .market_data import MarketData, TF_ORDER, to_csv
from .util import fmt_local, to_local, window_end_utc

PROMPT_DIR = ROOT / "prompts"


def _read(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def scan_system_instruction() -> str:
    return _read("system_prompt.md") + _read("runtime_addendum.md")


def confirm_system_instruction() -> str:
    return _read("confirm_system.md")


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _img(path: str) -> dict:
    return {"type": "image", "mime_type": "image/png", "data": _b64(path)}


def build_sources(md: MarketData, now: dt.datetime, cfg: Config, tfs: list[str] | None = None,
                  n: int | None = None, extra: dict[str, str] | None = None) -> list[dict]:
    """Agent sandbox එකට mount කරන CSV files (inline, 1MB/file)."""
    n = n or cfg.csv_candles
    src = []
    for tf in tfs or TF_ORDER:
        src.append({"type": "inline", "target": f"/workspace/data/{tf}.csv",
                    "content": to_csv(md.frames[tf], tf, now, cfg.tz, n)})
    src.append({"type": "inline", "target": "/workspace/data/levels.json",
                "content": json.dumps(md.levels, indent=2)})
    for name, content in (extra or {}).items():
        src.append({"type": "inline", "target": f"/workspace/data/{name}", "content": content})
    return src


def _signals_json(signals: list[dict]) -> str:
    keep = ("id", "direction", "status", "entry", "poi_low", "poi_high", "sl", "tp1", "tp2",
            "created_lk", "expires_lk", "touched_ts", "wave_summary")
    return json.dumps([{k: s.get(k) for k in keep} for s in signals], indent=1) if signals else "none"


def build_scan_input(cfg: Config, md: MarketData, now: dt.datetime, charts: dict[str, str],
                     active: list[dict]) -> list[dict]:
    lk = to_local(now, cfg)
    end_lk = to_local(window_end_utc(now, cfg), cfg)
    mins_left = max(0, int((window_end_utc(now, cfg) - now).total_seconds() // 60))
    text = f"""SCAN REQUEST
Now: {lk:%A %Y-%m-%d %H:%M} Asia/Colombo  ({now:%H:%M} UTC)
Instrument: XAUUSD | Feed: {md.source} | broker offset applied: {cfg.broker_offset:+.2f}
CURRENT PRICE (last M5 close): {md.last_price:.2f}   (M5 candle open {fmt_local(md.last_candle_open, cfg, '%H:%M')} Colombo)
Session ends {end_lk:%H:%M} Colombo -> {mins_left} minutes left. expiry_minutes must fit before that.
Assumed spread: {cfg.spread:.2f} USD. Account/risk is handled by the system.

KEY LEVELS (exact): {json.dumps(md.levels)}

ACTIVE SIGNALS (do not duplicate; cancel_signal only if PENDING and invalid):
{_signals_json(active)}

Attached charts in order: D1, H4, H1, M15, M5 (candles only). Raw CSVs: /workspace/data/*.csv.
Perform the full analysis now and follow the AUTOMATION RUNTIME ADDENDUM."""
    parts: list[dict] = [{"type": "text", "text": text}]
    for tf in TF_ORDER:
        parts.append({"type": "text", "text": f"Chart {tf}:"})
        parts.append(_img(charts[tf]))
    return parts


def build_confirm_input(cfg: Config, md: MarketData, now: dt.datetime, sig: dict, charts: dict[str, str]) -> list[dict]:
    lk = to_local(now, cfg)
    text = f"""ENTRY CONFIRMATION REQUEST
Now: {lk:%A %H:%M} Asia/Colombo | current price {md.last_price:.2f} | check #{int(sig.get('confirm_checks', 0)) + 1}
Signal: {json.dumps({k: sig.get(k) for k in ('id','direction','entry','poi_low','poi_high','sl','tp1','tp2','touched_ts','poi_type','wave_summary','ict_summary','cancel_conditions')})}
The entry was touched at M5 candle {sig.get('touched_ts')} (UTC). Judge the reaction AFTER the touch. Call confirm_entry once."""
    parts = [{"type": "text", "text": text}]
    for tf in ("M15", "M5"):
        parts.append({"type": "text", "text": f"Chart {tf}:"})
        parts.append(_img(charts[tf]))
    return parts


def csv_for_fallback(md: MarketData, now: dt.datetime, cfg: Config, rows: int = 60) -> str:
    out = []
    for tf in ("H4", "H1", "M15", "M5"):
        out.append(f"## {tf} (last {rows})\n" + to_csv(md.frames[tf], tf, now, cfg.tz, rows))
    out.append("## levels\n" + json.dumps(md.levels))
    return "\n".join(out)
