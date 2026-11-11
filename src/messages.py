"""Telegram HTML messages. Dynamic text සියල්ල html.escape කරනවා."""
from __future__ import annotations

from html import escape as e

from .config import Config
from .risk import Sizing

DIR_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}


def _p(x) -> str:
    return f"{float(x):.2f}"


def fmt_signal(sig: dict, sz: Sizing, last_price: float, cfg: Config, created_lk: str, expires_lk: str) -> str:
    long = sig["direction"] == "LONG"
    order = "BUY LIMIT" if long else "SELL LIMIT"
    sl_d = abs(sig["entry"] - sig["sl"])
    t1_d = abs(sig["tp1"] - sig["entry"])
    lines = [
        f"{DIR_EMOJI[sig['direction']]} <b>XAUUSD · A+ {order}</b>   <code>#{e(sig['id'])}</code>",
        "",
        f"📍 <b>Entry</b>  <code>{_p(sig['entry'])}</code>   <i>(POI {_p(sig['poi_low'])} – {_p(sig['poi_high'])})</i>",
        f"🛑 <b>SL</b>      <code>{_p(sig['sl'])}</code>   <i>({sl_d:.2f} pts)</i>",
        f"🎯 <b>TP1</b>    <code>{_p(sig['tp1'])}</code>   <i>(+{t1_d:.2f} · net 1:{sz.net_rr_tp1})</i>",
    ]
    if sig.get("tp2"):
        lines.append(
            f"🎯 <b>TP2</b>    <code>{_p(sig['tp2'])}</code>   <i>(+{abs(sig['tp2'] - sig['entry']):.2f} · net 1:{sz.net_rr_final})</i>"
        )
    lines += [
        "",
        f"💰 <b>Lot</b> <code>{sz.lot:.2f}</code> · risk <b>${sz.loss_at_sl}</b> ({sz.risk_pct_actual}% of ${cfg.equity:g})",
        f"   TP1 ≈ +${sz.profit_tp1}" + (f" · TP2 ≈ +${sz.profit_tp2}" if sz.profit_tp2 is not None else "")
        + f" · margin ≈ ${sz.margin}",
    ]
    if not sz.within_limit:
        lines.append(
            f"⚠️ <b>TRADE SIZE NOT SUITABLE</b>: min lot {cfg.min_lot} risks {sz.risk_pct_actual}% "
            f"&gt; your {cfg.risk_pct:g}% limit. තීරණය ඔයාගේ."
        )
    lines += [
        "",
        f"🧩 <b>POI</b>: {e(str(sig.get('poi_type', '')))}",
        f"🌊 <b>Wave</b>: {e(str(sig.get('wave_summary', '')))}",
        f"🏦 <b>ICT/SMC</b>: {e(str(sig.get('ict_summary', '')))}",
    ]
    if sig.get("tp1_reason"):
        lines.append(f"🎯 <b>TP logic</b>: {e(str(sig['tp1_reason']))}")
    if sig.get("news_check"):
        lines.append(f"📰 <b>News</b>: {e(str(sig['news_check']))}")
    lines += [
        f"⭐ Confidence {e(str(sig.get('confidence_10', '?')))}/10",
        "",
        f"📝 <i>{e(str(sig.get('rationale_si', '')))}</i>",
        "",
        f"❌ <b>Cancel before entry if</b>: {e(str(sig.get('cancel_conditions', '')))}",
        f"⏳ Expiry: <b>{expires_lk}</b> (Colombo) — set this in MT5",
        f"🕒 {created_lk} · feed price {_p(last_price)} · data feed ≠ Exness (verify price in MT5)",
    ]
    return "\n".join(lines)


def fmt_approach(sig: dict, last: float) -> str:
    d = abs(last - sig["entry"])
    return (f"👀 <b>Price approaching entry</b> <code>#{e(sig['id'])}</code>\n"
            f"{sig['direction']} limit <code>{_p(sig['entry'])}</code> · price {_p(last)} ({d:.2f} away). Be ready.")


def fmt_touched(sig: dict, ev: dict, cfg_tz_time: str) -> str:
    return (f"⚡ <b>Entry zone touched</b> <code>#{e(sig['id'])}</code>\n"
            f"{sig['direction']} limit <code>{_p(sig['entry'])}</code> was reached (M5 candle {cfg_tz_time}).\n"
            f"Limit order should be filled. Checking confirmation…")


def fmt_confirm(sig: dict, verdict: str, reason_en: str, reason_si: str, last: float) -> str:
    head = {
        "CONFIRMED": "✅ <b>ENTRY CONFIRMED</b>",
        "INVALIDATED": "🚫 <b>ENTRY INVALIDATED</b>",
        "TIMEOUT": "ℹ️ <b>Entry live — no decisive reaction yet</b>",
        "SKIPPED": "ℹ️ <b>Entry touched — AI confirmation unavailable</b>",
    }[verdict]
    body = [
        f"{head} <code>#{e(sig['id'])}</code>",
        f"{sig['direction']} @ <code>{_p(sig['entry'])}</code> · SL <code>{_p(sig['sl'])}</code> · "
        f"TP1 <code>{_p(sig['tp1'])}</code>" + (f" · TP2 <code>{_p(sig['tp2'])}</code>" if sig.get("tp2") else ""),
        f"Price now {_p(last)}",
    ]
    if reason_en:
        body.append(f"🔎 {e(reason_en)}")
    if reason_si:
        body.append(f"📝 <i>{e(reason_si)}</i>")
    if verdict == "INVALIDATED":
        body.append("⚠️ Structure failed after fill. SL/TP fixed — manual close ඔයාගේ තීරණය. Tracking නතර කළා.")
    if verdict in ("TIMEOUT", "SKIPPED"):
        body.append("SL/TP fixed. Tracking continues.")
    return "\n".join(body)


EVENT_TEXT = {
    "TP1_HIT": ("🎯 <b>TP1 HIT</b>", "Partial/secure profit ඔයාගේ plan එක අනුව. SL එක fixed (widen කරන්න එපා)."),
    "TP2_HIT": ("🏆 <b>TP2 HIT — trade closed</b>", ""),
    "SL_HIT": ("🛑 <b>SL HIT — trade closed</b>", "Loss controlled. Next A+ setup එකට රැඳී ඉන්න."),
    "EXPIRED": ("⌛ <b>Order EXPIRED</b>", "MT5 එකේ pending order එක cancel කරන්න (expiry set කරලා නැත්නම්)."),
    "MISSED": ("🚷 <b>Setup missed — cancel order</b>", "TP1 entry එකට කලින් reach වුණා. Pending order එක cancel කරන්න."),
    "CANCELLED": ("❌ <b>CANCEL ORDER</b>", ""),
}


def fmt_event(sig: dict, ev: dict, last: float, extra: str = "") -> str:
    head, tail = EVENT_TEXT[ev["type"]]
    lines = [f"{head} <code>#{e(sig['id'])}</code>",
             f"{sig['direction']} · entry <code>{_p(sig['entry'])}</code> · SL <code>{_p(sig['sl'])}</code> · "
             f"TP1 <code>{_p(sig['tp1'])}</code>" + (f" · TP2 <code>{_p(sig['tp2'])}</code>" if sig.get("tp2") else "")]
    if ev.get("price"):
        lines.append(f"Level {_p(ev['price'])} · price now {_p(last)}")
    if ev.get("note"):
        lines.append(f"({e(ev['note'])})")
    if extra:
        lines.append(f"🔎 {e(extra)}")
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def fmt_digest_caption(last: float, now_lk: str, source: str, levels: dict, open_sigs: list[dict]) -> str:
    parts = [f"📊 <b>XAUUSD chart digest</b> · {now_lk} (Colombo)", f"Price <code>{_p(last)}</code> · feed: {e(source)}"]
    if "prev_day_high" in levels:
        parts.append(f"PDH {levels['prev_day_high']} · PDL {levels['prev_day_low']}")
    if open_sigs:
        s = open_sigs[0]
        parts.append(f"Open signal: {s['direction']} #{e(s['id'])} [{s['status']}] entry {_p(s['entry'])}")
    else:
        parts.append("No open signal — watching.")
    return "\n".join(parts)


def fmt_error(msg: str) -> str:
    return f"⚠️ <b>XAUUSD bot</b>: {e(msg)[:600]}"
