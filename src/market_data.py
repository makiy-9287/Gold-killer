"""XAUUSD OHLCV data.

Primary : Twelve Data (free: 8 req/min, 800/day)  -> XAU/USD spot
Fallback: yfinance (XAUUSD=X)

M5, M15, H1 විතරයි download කරන්නේ (credits 3යි). H4 සහ D1 හදන්නේ H1 වලින් resample කරලා
(UTC 00:00 anchor — Exness server time GMT+0 නිසා broker chart එකට ගැලපෙනවා).
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

from .config import Config
from .util import log

TF_ORDER = ["D1", "H4", "H1", "M15", "M5"]
TF_DELTA = {
    "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15),
    "H1": pd.Timedelta(hours=1),
    "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1),
}

TD_URL = "https://api.twelvedata.com/time_series"
# tf -> (twelvedata interval, outputsize, yfinance interval, yfinance period)
FETCH_PLAN = {
    "M5": ("5min", 400, "5m", "5d"),
    "M15": ("15min", 300, "15m", "10d"),
    "H1": ("1h", 5000, "1h", "365d"),   # D1 candles ~200ක් හදන්න (Twelve Data max 5000)
}


class DataError(Exception):
    pass


@dataclass
class MarketData:
    frames: dict[str, pd.DataFrame]
    source: str
    last_price: float
    last_candle_open: pd.Timestamp
    levels: dict = field(default_factory=dict)


# ------------------------------------------------------------------ fetchers
def _td_fetch(cfg: Config, interval: str, size: int) -> pd.DataFrame:
    params = {
        "symbol": cfg.symbol,
        "interval": interval,
        "outputsize": size,
        "timezone": "UTC",
        "order": "ASC",
        "apikey": cfg.twelvedata_api_key,
    }
    data = None
    for attempt in range(3):
        try:
            r = requests.get(TD_URL, params=params, timeout=30)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            # URL (api key සහිත) print නොකරන්න
            raise DataError(f"TwelveData request failed: {type(e).__name__}") from None
        if data.get("status") == "error":
            if data.get("code") == 429 and attempt < 2:
                log("TwelveData 429 - waiting 20s")
                time.sleep(20)
                continue
            raise DataError(f"TwelveData {data.get('code')}: {data.get('message')}")
        break
    rows = (data or {}).get("values")
    if not rows:
        raise DataError("TwelveData returned no values")
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    return df[["open", "high", "low", "close"]].astype(float)


def _yf_fetch(interval: str, period: str, symbol: str) -> pd.DataFrame:
    import yfinance as yf  # lazy import

    df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise DataError("yfinance returned no data")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    idx = df.index
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df.astype(float)


# ------------------------------------------------------------------ resample
def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = (
        df.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    return out


def build_htf(h1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """H1 -> H4, D1. පළමු (partial) bucket එක අයින් කරනවා. Sunday stub candles (<6 H1 bars) drop."""
    h4 = resample_ohlc(h1, "4h").iloc[1:]
    d1 = resample_ohlc(h1, "1D")
    counts = h1["close"].resample("1D", label="left", closed="left").count().reindex(d1.index)
    keep = (counts >= 6) | (d1.index == d1.index[-1])
    d1 = d1[keep].iloc[1:]
    return h4, d1


# ------------------------------------------------------------------ indicators / levels
def atr(df: pd.DataFrame, n: int = 14) -> float:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)
    v = tr.rolling(n).mean().iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def _hl(df: pd.DataFrame) -> tuple[float, float] | tuple[None, None]:
    if df is None or df.empty:
        return None, None
    return float(df["high"].max()), float(df["low"].min())


def compute_levels(frames: dict[str, pd.DataFrame], now: dt.datetime) -> dict:
    d1, m15 = frames["D1"], frames["M15"]
    lv: dict = {}
    if len(d1) >= 2:
        lv["prev_day_high"], lv["prev_day_low"] = float(d1["high"].iloc[-2]), float(d1["low"].iloc[-2])
        lv["prev_day_close"] = float(d1["close"].iloc[-2])
    lv["today_high"], lv["today_low"] = float(d1["high"].iloc[-1]), float(d1["low"].iloc[-1])
    lv["today_open"] = float(d1["open"].iloc[-1])

    wk = d1.groupby(d1.index.tz_localize(None).to_period("W-SUN"))
    groups = [g for _, g in wk]
    if len(groups) >= 2:
        pw = groups[-2]
        lv["prev_week_high"], lv["prev_week_low"] = float(pw["high"].max()), float(pw["low"].min())
    cw = groups[-1]
    lv["week_high"], lv["week_low"] = float(cw["high"].max()), float(cw["low"].min())

    day_start = pd.Timestamp(now).tz_convert("UTC").normalize()
    today15 = m15[m15.index >= day_start]
    for name, (a, b) in {"asia": (0, 7), "london": (7, 12)}.items():
        seg = today15[(today15.index.hour >= a) & (today15.index.hour < b)]
        h, l = _hl(seg)
        if h is not None:
            lv[f"{name}_high"], lv[f"{name}_low"] = h, l
    lv["atr14"] = {tf: round(atr(frames[tf]), 2) for tf in ["M5", "M15", "H1", "H4"] if len(frames[tf]) > 15}
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in lv.items()}


# ------------------------------------------------------------------ main loader
def load_market(cfg: Config, now: dt.datetime) -> MarketData:
    raw: dict[str, pd.DataFrame] = {}
    source = "TwelveData"
    try:
        if not cfg.twelvedata_api_key:
            raise DataError("TWELVEDATA_API_KEY not set")
        for tf, (td_i, size, _, _) in FETCH_PLAN.items():
            raw[tf] = _td_fetch(cfg, td_i, size)
    except DataError as e:
        log(f"Primary data failed ({e}); trying yfinance fallback")
        source = "yfinance(XAUUSD=X)"
        raw = {}
        try:
            for tf, (_, _, yi, yp) in FETCH_PLAN.items():
                raw[tf] = _yf_fetch(yi, yp, cfg.yf_symbol)
        except Exception as e2:  # noqa: BLE001
            raise DataError(f"all data sources failed: {e} | {type(e2).__name__}") from None

    if cfg.broker_offset:
        for tf in raw:
            raw[tf] = raw[tf] + cfg.broker_offset

    h4, d1 = build_htf(raw["H1"])
    frames = {"D1": d1, "H4": h4, "H1": raw["H1"], "M15": raw["M15"], "M5": raw["M5"]}
    m5 = frames["M5"]
    md = MarketData(
        frames=frames,
        source=source,
        last_price=float(m5["close"].iloc[-1]),
        last_candle_open=m5.index[-1],
    )
    md.levels = compute_levels(frames, now)
    return md


def is_stale(md: MarketData, now: dt.datetime, cfg: Config) -> bool:
    age = pd.Timestamp(now) - md.last_candle_open
    return age > pd.Timedelta(minutes=cfg.stale_minutes)


def to_csv(df: pd.DataFrame, tf: str, now: dt.datetime, tz: str, n: int) -> str:
    """Agent sandbox එකට දාන CSV (exact prices)."""
    d = df.tail(n).copy()
    closed = (d.index + TF_DELTA[tf]) <= pd.Timestamp(now)
    out = pd.DataFrame(
        {
            "time_utc": d.index.strftime("%Y-%m-%d %H:%M"),
            "time_lk": d.index.tz_convert(tz).strftime("%Y-%m-%d %H:%M"),
            "open": d["open"].round(2),
            "high": d["high"].round(2),
            "low": d["low"].round(2),
            "close": d["close"].round(2),
            "closed": closed,
        }
    )
    return out.to_csv(index=False)
