"""Signal lifecycle — AI එක නැතුව M5 candles වලින් mechanical විදිහට.

PENDING   : limit order තාම fill වෙලා නැහැ
TRIGGERED : price entry එකට ඇවිත් (fill වෙලා) — AI confirmation බලාපොරොත්තුවෙන්
ACTIVE    : confirmed (හෝ confirmation skip) — SL/TP track කරනවා
Final     : EXPIRED, CANCELLED, MISSED, SL_HIT, TP1_HIT, TP2_HIT, INVALIDATED

Conservative rules (candle එක ඇතුළේ order එක දන්නේ නැති නිසා):
  * entry + SL එකම candle එකේ -> SL hit කියලා ගන්නවා
  * SL + TP එකම candle එකේ    -> SL hit කියලා ගන්නවා
  * entry candle එකේම TP -> ගණන් ගන්නේ නැහැ (ඊළඟ candle වල සිට)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .state import OPEN_STATUSES

M5 = pd.Timedelta(minutes=5)


def _ev(kind: str, ts, price: float | None = None, note: str = "") -> dict:
    return {"type": kind, "time": pd.Timestamp(ts).isoformat() if ts is not None else None, "price": price, "note": note}


def advance(sig: dict, df5: pd.DataFrame, now: dt.datetime, approach_usd: float = 4.0) -> list[dict]:
    """sig එක mutate කරලා, සිදුවුණු events list එකක් return කරනවා."""
    events: list[dict] = []
    if sig["status"] not in OPEN_STATUSES:
        return events

    long = sig["direction"] == "LONG"
    entry, sl, tp1 = float(sig["entry"]), float(sig["sl"]), float(sig["tp1"])
    tp2 = float(sig["tp2"]) if sig.get("tp2") else None
    exp = pd.Timestamp(sig["expires_ts"])
    last_ts = pd.Timestamp(sig["last_ts"])
    touched = pd.Timestamp(sig["touched_ts"]) if sig.get("touched_ts") else None

    def hit_sl(c) -> bool:
        return c["low"] <= sl if long else c["high"] >= sl

    def hit_tp(c, level) -> bool:
        return c["high"] >= level if long else c["low"] <= level

    for ts, c in df5[df5.index >= last_ts].iterrows():
        if sig["status"] == "PENDING":
            if ts >= exp:
                sig["status"] = "EXPIRED"
                events.append(_ev("EXPIRED", ts, note="expiry reached before entry"))
                break
            touched_now = c["low"] <= entry if long else c["high"] >= entry
            if touched_now:
                sig["status"] = "TRIGGERED"
                touched = ts
                sig["touched_ts"] = ts.isoformat()
                events.append(_ev("TOUCHED", ts, entry))
                if hit_sl(c):
                    sig["status"] = "SL_HIT"
                    events.append(_ev("SL_HIT", ts, sl, "entry and SL in the same M5 candle"))
                    break
                continue
            if hit_tp(c, tp1):
                sig["status"] = "MISSED"
                events.append(_ev("MISSED", ts, tp1, "TP1 reached before entry - order should be cancelled"))
                break
        else:  # TRIGGERED / ACTIVE
            if touched is not None and ts <= touched:
                continue
            if hit_sl(c):
                sig["status"] = "SL_HIT"
                events.append(_ev("SL_HIT", ts, sl))
                break
            if tp2 is not None and hit_tp(c, tp2):
                if not sig.get("tp1_hit"):
                    sig["tp1_hit"] = True
                    events.append(_ev("TP1_HIT", ts, tp1))
                sig["status"] = "TP2_HIT"
                events.append(_ev("TP2_HIT", ts, tp2))
                break
            if not sig.get("tp1_hit") and hit_tp(c, tp1):
                sig["tp1_hit"] = True
                events.append(_ev("TP1_HIT", ts, tp1))
                if tp2 is None:
                    sig["status"] = "TP1_HIT"
                    break

    if len(df5):
        sig["last_ts"] = df5.index[-1].isoformat()

    # candle නැතිවුනත් expiry check
    if sig["status"] == "PENDING" and pd.Timestamp(now) >= exp:
        sig["status"] = "EXPIRED"
        events.append(_ev("EXPIRED", now, note="expiry reached before entry"))

    # approaching alert (once)
    if sig["status"] == "PENDING" and not sig.get("approach_alerted") and len(df5):
        last = float(df5["close"].iloc[-1])
        dist = (last - entry) if long else (entry - last)
        if 0 <= dist <= approach_usd:
            sig["approach_alerted"] = True
            events.append(_ev("APPROACH", df5.index[-1], last, f"{dist:.2f} away"))
    return events


def closed_candles_after(sig: dict, df5: pd.DataFrame, now: dt.datetime) -> int:
    """touch candle එකෙන් පස්සේ close වුණු M5 candles ගණන."""
    if not sig.get("touched_ts"):
        return 0
    t = pd.Timestamp(sig["touched_ts"])
    after = df5[(df5.index > t) & (df5.index + M5 <= pd.Timestamp(now))]
    return len(after)
