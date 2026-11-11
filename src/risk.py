"""Guard rails (AI hallucinate කරන price/side වැරදි mechanical විදිහට අහුලනවා) + lot sizing.

Lot sizing AI එකට නොදී code එකෙන් කරනවා — prompt එකේ "never fabricate monetary calculations" නිසා."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from .config import Config
from .util import window_end_utc


@dataclass
class Sizing:
    lot: float
    sl_dist: float
    risk_limit_usd: float
    loss_at_sl: float
    risk_pct_actual: float
    profit_tp1: float
    profit_tp2: float | None
    margin: float
    notional: float
    within_limit: bool
    net_rr_tp1: float
    net_rr_final: float
    gross_rr_tp1: float
    gross_rr_final: float


def _num(v) -> float:
    return float(str(v).replace(",", "").strip())


def validate_signal(cfg: Config, a: dict, last_price: float) -> tuple[bool, str, dict]:
    """(ok, reason, cleaned). cleaned: normalized numeric fields."""
    try:
        direction = str(a["direction"]).upper().strip()
        entry, sl, tp1 = _num(a["entry_price"]), _num(a["stop_loss"]), _num(a["tp1"])
        tp2 = _num(a["tp2"]) if a.get("tp2") not in (None, "", 0, "0") else None
        poi_low, poi_high = sorted((_num(a["poi_low"]), _num(a["poi_high"])))
    except (KeyError, ValueError, TypeError) as e:
        return False, f"missing/invalid numeric field: {e}", {}

    if direction not in ("LONG", "SHORT"):
        return False, f"bad direction {direction}", {}
    long = direction == "LONG"

    # --- hallucination guard
    if abs(entry - last_price) > cfg.max_entry_dist:
        return False, f"entry {entry} too far from price {last_price}", {}
    # --- limit order side
    if long and entry > last_price - cfg.min_entry_gap:
        return False, f"BUY LIMIT {entry} must be below price {last_price} (gap>={cfg.min_entry_gap})", {}
    if not long and entry < last_price + cfg.min_entry_gap:
        return False, f"SELL LIMIT {entry} must be above price {last_price} (gap>={cfg.min_entry_gap})", {}
    # --- SL / TP sides
    if long and not (sl < entry < tp1):
        return False, "LONG needs SL < entry < TP1", {}
    if not long and not (tp1 < entry < sl):
        return False, "SHORT needs TP1 < entry < SL", {}
    if tp2 is not None:
        if long and not tp2 > tp1:
            return False, "LONG needs TP2 > TP1", {}
        if not long and not tp2 < tp1:
            return False, "SHORT needs TP2 < TP1", {}
    sl_dist = abs(entry - sl)
    if not (cfg.min_sl <= sl_dist <= cfg.max_sl):
        return False, f"SL distance {sl_dist:.2f} outside [{cfg.min_sl}, {cfg.max_sl}]", {}
    if not (poi_low - 0.01 <= entry <= poi_high + 0.01):
        return False, f"entry {entry} outside POI zone {poi_low}-{poi_high}", {}

    cleaned = {
        "direction": direction, "entry": round(entry, 2), "sl": round(sl, 2), "tp1": round(tp1, 2),
        "tp2": round(tp2, 2) if tp2 is not None else None,
        "poi_low": round(poi_low, 2), "poi_high": round(poi_high, 2),
    }
    sz = size_position(cfg, cleaned)
    if sz.net_rr_final < cfg.min_net_rr:
        return False, f"net R:R {sz.net_rr_final:.2f} < {cfg.min_net_rr}", {}
    if cfg.enforce_risk_limit and not sz.within_limit:
        return False, f"min lot risk {sz.risk_pct_actual:.1f}% exceeds limit {cfg.risk_pct}%", {}
    return True, "ok", cleaned


def size_position(cfg: Config, s: dict) -> Sizing:
    entry, sl, tp1, tp2 = s["entry"], s["sl"], s["tp1"], s.get("tp2")
    sl_dist = abs(entry - sl)
    loss_per_lot = (sl_dist + cfg.spread) * cfg.contract_size + cfg.commission_per_lot
    risk_limit = cfg.equity * cfg.risk_pct / 100.0
    raw = risk_limit / loss_per_lot
    lot = math.floor(raw / cfg.lot_step + 1e-9) * cfg.lot_step
    within = True
    if lot < cfg.min_lot:
        lot, within = cfg.min_lot, False   # min lot එකෙන් වුනත් risk limit එක ඉක්මවනවා
    lot = round(min(lot, cfg.max_lot), 2)

    def profit(level: float) -> float:
        return lot * ((abs(level - entry) - cfg.spread) * cfg.contract_size) - lot * cfg.commission_per_lot

    loss = lot * loss_per_lot
    final = tp2 if tp2 else tp1

    def rr(level: float, net: bool) -> float:
        reward = abs(level - entry) - (cfg.spread if net else 0.0)
        risk = sl_dist + (cfg.spread if net else 0.0)
        return reward / risk if risk else 0.0

    return Sizing(
        lot=lot, sl_dist=round(sl_dist, 2), risk_limit_usd=round(risk_limit, 2),
        loss_at_sl=round(loss, 2), risk_pct_actual=round(loss / cfg.equity * 100, 2),
        profit_tp1=round(profit(tp1), 2), profit_tp2=round(profit(tp2), 2) if tp2 else None,
        margin=round(lot * cfg.contract_size * entry / cfg.leverage, 2),
        notional=round(lot * cfg.contract_size * entry, 2), within_limit=within,
        net_rr_tp1=round(rr(tp1, True), 2), net_rr_final=round(rr(final, True), 2),
        gross_rr_tp1=round(rr(tp1, False), 2), gross_rr_final=round(rr(final, False), 2),
    )


def compute_expiry(now: dt.datetime, minutes: int | float | None, cfg: Config) -> dt.datetime:
    m = int(minutes) if minutes else cfg.max_expiry_min
    m = max(15, min(m, cfg.max_expiry_min))
    exp = now + dt.timedelta(minutes=m)
    # bot එක නතර වෙන වෙලාවෙන් පස්සේ order track කරන්න බැහැ
    end = window_end_utc(now, cfg)
    return min(exp, max(end, now + dt.timedelta(minutes=5)))
