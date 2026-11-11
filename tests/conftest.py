import datetime as dt
import numpy as np
import pandas as pd
import pytest

from src.config import load_config


def make_ohlc(n, freq, end, start_price=4000.0, seed=1, vol=0.6):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    close = start_price + np.cumsum(rng.normal(0, vol, n))
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.1, vol, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, vol, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c.state_path = str(tmp_path / "state.json")
    c.out_dir = str(tmp_path / "out")
    c.dry_run = True
    c.equity, c.risk_pct = 100.0, 1.0
    return c


@pytest.fixture
def now():
    # Wednesday 2026-11-11 05:03 UTC = 10:33 Colombo
    return dt.datetime(2026, 11, 11, 5, 3, tzinfo=dt.timezone.utc)


@pytest.fixture
def md(now):
    from src.market_data import MarketData, build_htf, compute_levels
    end = pd.Timestamp(now).floor("5min")
    m5 = make_ohlc(400, "5min", end, seed=1)
    m15 = make_ohlc(300, "15min", end.floor("15min"), seed=2)
    h1 = make_ohlc(3000, "1h", end.floor("1h"), seed=3, vol=2.0)
    h4, d1 = build_htf(h1)
    frames = {"D1": d1, "H4": h4, "H1": h1, "M15": m15, "M5": m5}
    m = MarketData(frames, "synthetic", float(m5.close.iloc[-1]), m5.index[-1])
    m.levels = compute_levels(frames, now)
    return m
