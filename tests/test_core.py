import datetime as dt, json, os, subprocess, pandas as pd, pytest
from src import util, risk, tracker, charts
from src.market_data import to_csv, build_htf
from src.state import ai_inc, ai_count, load_state, save_state


def test_window(cfg):
    U = dt.timezone.utc
    assert util.in_trading_window(dt.datetime(2026, 11, 11, 4, 30, tzinfo=U), cfg)       # Wed 10:00 LK
    assert not util.in_trading_window(dt.datetime(2026, 11, 11, 4, 20, tzinfo=U), cfg)   # 09:50
    assert util.in_trading_window(dt.datetime(2026, 11, 11, 14, 30, tzinfo=U), cfg)      # 20:00
    assert not util.in_trading_window(dt.datetime(2026, 11, 11, 15, 0, tzinfo=U), cfg)   # 20:30
    assert not util.in_trading_window(dt.datetime(2026, 11, 14, 8, 0, tzinfo=U), cfg)    # Saturday


def test_htf_and_csv(md, now, cfg):
    assert len(md.frames["D1"]) >= 100 and len(md.frames["H4"]) >= 120
    assert "prev_day_high" in md.levels and "prev_week_high" in md.levels and md.levels["atr14"]["M5"] > 0
    csv = to_csv(md.frames["M5"], "M5", now, cfg.tz, 150)
    assert csv.count("\n") == 151 and csv.strip().endswith(("False", "True"))


def test_charts(md, cfg, tmp_path):
    paths = charts.render_all(md.frames, str(tmp_path), cfg.tz, 120)
    assert all(os.path.getsize(p) > 20_000 for p in paths.values())
    p = charts.render_chart(md.frames["M5"], "M5", str(tmp_path / "s.png"), cfg.tz, 120,
                            levels=[(md.last_price - 3, "ENTRY", "#2962ff")], zone=(md.last_price - 4, md.last_price - 2))
    assert os.path.exists(p)


def sig_args(price, **kw):
    a = dict(direction="LONG", entry_price=price - 3, poi_low=price - 4, poi_high=price - 2, stop_loss=price - 7,
             tp1=price + 5, tp2=price + 12)
    a.update(kw); return a


def test_validate_and_sizing(cfg):
    P = 4000.0
    ok, why, c = risk.validate_signal(cfg, sig_args(P), P)
    assert ok, why
    sz = risk.size_position(cfg, c)
    # $100, 1%: SL 4 + spread .25 => per 1 lot 425; 0.01 min lot => $4.25 = 4.25% > 1%
    assert sz.lot == 0.01 and not sz.within_limit and sz.risk_pct_actual == 4.25
    bad = [sig_args(P, entry_price=P + 1, poi_low=P, poi_high=P + 2, stop_loss=P - 3, tp1=P + 8, tp2=None),  # buy limit above price
           sig_args(P, stop_loss=P + 1),                       # SL wrong side
           sig_args(P, tp1=P - 4, tp2=None),                   # TP wrong side
           sig_args(P, entry_price=P - 300, poi_low=P - 301, poi_high=P - 299, stop_loss=P - 305),  # hallucinated
           sig_args(P, tp1=P - 1, tp2=P - 0.5),                # low RR
           sig_args(P, stop_loss=P - 3.2)]                     # SL .2 too tight
    for b in bad:
        assert not risk.validate_signal(cfg, b, P)[0], b
    cfg.enforce_risk_limit = True
    assert not risk.validate_signal(cfg, sig_args(P), P)[0]
    cfg.equity = 1000
    ok, _, c = risk.validate_signal(cfg, sig_args(P), P); assert ok
    assert risk.size_position(cfg, c).within_limit
    # SHORT
    cfg.enforce_risk_limit = False
    ok, why, _ = risk.validate_signal(cfg, dict(direction="SHORT", entry_price=P + 3, poi_low=P + 2, poi_high=P + 4,
                                               stop_loss=P + 7, tp1=P - 5, tp2=P - 12), P)
    assert ok, why


def mk_sig(direction="LONG", t0="2026-11-11T05:00:00+00:00"):
    L = direction == "LONG"
    return dict(id="T1", direction=direction, entry=100.0 if L else 100.0, sl=97.0 if L else 103.0,
                tp1=105.0 if L else 95.0, tp2=110.0 if L else 90.0, status="PENDING",
                expires_ts="2026-11-11T07:00:00+00:00", last_ts=t0, touched_ts=None, approach_alerted=False, tp1_hit=False)


def cdl(rows, start="2026-11-11 05:00"):
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]).astype(float)


NOW = dt.datetime(2026, 11, 11, 5, 30, tzinfo=dt.timezone.utc)


def types(ev): return [e["type"] for e in ev]


def test_tracker_long_flow():
    s = mk_sig()
    # price above entry, approaching
    ev = tracker.advance(s, cdl([[103, 104, 102, 102.5]]), NOW, 4)
    assert types(ev) == ["APPROACH"] and s["status"] == "PENDING"
    # touch + same-candle TP ignored, then TP1, then TP2
    df = cdl([[103, 104, 102, 102.5], [102, 106, 99.5, 101], [101, 106, 100.5, 105.5], [105, 111, 104, 110]])
    ev = tracker.advance(s, df, NOW, 4)
    assert types(ev) == ["TOUCHED", "TP1_HIT", "TP2_HIT"] and s["status"] == "TP2_HIT"


def test_tracker_sl_cases():
    s = mk_sig(); ev = tracker.advance(s, cdl([[102, 103, 96, 98]]), NOW)
    assert types(ev) == ["TOUCHED", "SL_HIT"] and s["status"] == "SL_HIT"
    s = mk_sig("SHORT"); ev = tracker.advance(s, cdl([[98, 100.5, 97.5, 99], [99, 106, 98, 105]]), NOW)
    assert types(ev) == ["TOUCHED", "SL_HIT"]
    s = mk_sig(); tracker.advance(s, cdl([[102, 103, 99.5, 101]]), NOW)       # touched
    ev = tracker.advance(s, cdl([[102, 103, 99.5, 101], [101, 106, 96, 100]]), NOW)  # SL+TP same candle -> SL
    assert types(ev) == ["SL_HIT"]


def test_tracker_missed_expired_and_reprocess():
    s = mk_sig(); ev = tracker.advance(s, cdl([[103, 106, 102.5, 105.5]]), NOW)
    assert types(ev) == ["MISSED"]
    s = mk_sig(); ev = tracker.advance(s, cdl([[103, 104, 102.5, 103]]), dt.datetime(2026, 11, 11, 7, 1, tzinfo=dt.timezone.utc))
    assert types(ev) == ["EXPIRED"]
    # forming candle re-processed later: idempotent, touch only once
    s = mk_sig(); df = cdl([[103, 104, 102.5, 103], [103, 104, 102.5, 103]])
    tracker.advance(s, df, NOW)
    df2 = df.copy(); df2.iloc[-1] = [103, 104, 99.8, 100.5]
    assert types(tracker.advance(s, df2, NOW)) == ["TOUCHED"]
    assert tracker.advance(s, df2, NOW) == []
    assert tracker.closed_candles_after(s, cdl([[1, 1, 1, 1]] * 4), dt.datetime(2026, 11, 11, 5, 40, tzinfo=dt.timezone.utc)) in (0, 1, 2)


def test_ai_budget_pacific(tmp_path):
    st = {"ai_calls": {"pacific_date": None, "count": 0}}
    a = dt.datetime(2026, 11, 11, 6, 0, tzinfo=dt.timezone.utc)   # Nov 10 22:00 PST
    b = dt.datetime(2026, 11, 11, 9, 0, tzinfo=dt.timezone.utc)   # Nov 11 01:00 PST (new quota day)
    for _ in range(3): ai_inc(st, a)
    assert ai_count(st, a) == 3 and ai_count(st, b) == 0


def test_state_roundtrip(tmp_path):
    p = str(tmp_path / "s.json"); st = load_state(p); st["signals"].append({"id": "x", "status": "SL_HIT"})
    save_state(p, st); assert load_state(p)["signals"][0]["id"] == "x"
