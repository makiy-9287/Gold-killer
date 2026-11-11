"""Bot state (state/state.json). GitHub Actions run අතර signals, AI quota, digest වෙලාවන් මතක තියාගන්න.
Workflow එක මේක `bot-state` branch එකේ (single commit) save කරනවා."""
from __future__ import annotations

import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

OPEN_STATUSES = ("PENDING", "TRIGGERED", "ACTIVE")
MAX_HISTORY = 30


def default_state() -> dict:
    return {
        "version": 1,
        "signals": [],
        "last_digest_ts": None,
        "last_scan_ts": None,
        "last_error_notice_ts": None,
        "ai_calls": {"pacific_date": None, "count": 0},
    }


def load_state(path: str) -> dict:
    st = default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            st.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return st


def save_state(path: str, state: dict) -> None:
    # closed signals වලින් අලුත්ම MAX_HISTORY විතරක් තියාගන්න
    open_sigs = [s for s in state["signals"] if s["status"] in OPEN_STATUSES]
    closed = [s for s in state["signals"] if s["status"] not in OPEN_STATUSES][-MAX_HISTORY:]
    state["signals"] = closed + open_sigs
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---- AI quota: Google RPD resets at midnight Pacific time ----
def pacific_date(now: dt.datetime) -> str:
    return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def ai_count(state: dict, now: dt.datetime) -> int:
    rec = state["ai_calls"]
    if rec.get("pacific_date") != pacific_date(now):
        return 0
    return int(rec.get("count", 0))


def ai_inc(state: dict, now: dt.datetime) -> int:
    rec = state["ai_calls"]
    today = pacific_date(now)
    if rec.get("pacific_date") != today:
        rec["pacific_date"], rec["count"] = today, 0
    rec["count"] = int(rec["count"]) + 1
    return rec["count"]


def open_signals(state: dict) -> list[dict]:
    return [s for s in state["signals"] if s["status"] in OPEN_STATUSES]


def find_signal(state: dict, sid: str) -> dict | None:
    for s in state["signals"]:
        if s["id"] == sid:
            return s
    return None
