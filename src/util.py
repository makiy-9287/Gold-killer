from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .config import Config


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def log(*args) -> None:
    print(utcnow().strftime("%H:%M:%S"), *args, flush=True)


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def to_local(ts: dt.datetime, cfg: Config) -> dt.datetime:
    return ts.astimezone(ZoneInfo(cfg.tz))


def fmt_local(ts, cfg: Config, fmt: str = "%H:%M") -> str:
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return to_local(ts, cfg).strftime(fmt)


def window_end_utc(now: dt.datetime, cfg: Config) -> dt.datetime:
    """අද දවසේ trading window එක අවසන් වෙන වෙලාව (UTC)."""
    lk = to_local(now, cfg)
    end = dt.datetime.combine(lk.date(), _hm(cfg.window_end), tzinfo=ZoneInfo(cfg.tz))
    return end.astimezone(dt.timezone.utc)


def in_trading_window(now: dt.datetime, cfg: Config) -> bool:
    """සදුදා–සිකුරාදා, 10:00–20:00 (Asia/Colombo)."""
    lk = to_local(now, cfg)
    if lk.weekday() >= 5:
        return False
    start = dt.datetime.combine(lk.date(), _hm(cfg.window_start), tzinfo=ZoneInfo(cfg.tz))
    end = dt.datetime.combine(lk.date(), _hm(cfg.window_end), tzinfo=ZoneInfo(cfg.tz))
    end += dt.timedelta(minutes=cfg.window_grace_min)
    return start <= lk <= end
