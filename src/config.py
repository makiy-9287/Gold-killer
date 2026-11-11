"""සියලුම settings මෙතනින්. GitHub Secrets / Variables වලින් env vars ලෙස එනවා.
Defaults ඔයාගේ prompt එකට ගැලපෙන්න දාලා තියෙන්නේ."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if v is not None and v.strip() != "" else default


def _f(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _i(name: str, default: int) -> int:
    return int(float(_env(name, str(default))))


def _b(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # ---- Secrets ----
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    twelvedata_api_key: str

    # ---- Market data ----
    symbol: str = "XAU/USD"            # Twelve Data symbol
    yf_symbol: str = "XAUUSD=X"        # yfinance fallback
    broker_offset: float = 0.0         # Exness price - feed price (USD). Calibrate කරන්න.
    stale_minutes: int = 25            # M5 last candle මේකට වඩා පරණ නම් market closed/stale
    chart_candles: int = 120
    csv_candles: int = 150

    # ---- Schedule ----
    tz: str = "Asia/Colombo"
    window_start: str = "10:00"
    window_end: str = "20:00"
    window_grace_min: int = 10         # GitHub cron delay වලට
    digest_every_hours: float = 3.0    # chart 5 Telegram එකට යවන ඉඟිය (3 හෝ 4)
    min_scan_gap_min: int = 12         # cron jitter නිසා double scan වළක්වන්න

    # ---- AI ----
    agent_id: str = "antigravity-preview-09-2026"
    agent_model: str = "gemini-3.8-flash"
    agent_max_tokens: int = 80000      # TPM 100K ට යටින්
    agent_timeout: int = 480
    api_revision: str = ""             # අවශ්‍ය නම් පමණි (e.g. 2026-05-20)
    fallback_enabled: bool = True      # Antigravity fail වුනොත් plain gemini-3.8-flash
    fallback_model: str = "gemini-3.8-flash"
    ai_daily_limit: int = 95           # RPD 100 ට යටින් (Pacific date එකෙන් ගණන් කරනවා)
    ai_confirm_reserve: int = 5        # confirm calls සඳහා ඉතිරි
    max_confirm_checks: int = 3

    # ---- Account / Exness (Exness app එකෙන් verify කරන්න!) ----
    equity: float = 100.0
    risk_pct: float = 1.0
    contract_size: float = 100.0       # XAUUSD: 1 lot = 100 oz  -> 0.01 lot = 1 oz
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 1.0
    leverage: float = 200.0
    spread: float = 0.25               # USD (price units)
    commission_per_lot: float = 0.0
    enforce_risk_limit: bool = False   # True නම් risk limit එක ඉක්මවන signal යවන්නේ නැහැ

    # ---- Signal guard rails ----
    min_net_rr: float = 2.0            # final TP එකට
    min_sl: float = 1.0
    max_sl: float = 12.0
    min_entry_gap: float = 0.5         # limit entry, current price එකෙන් අඩුම දුර
    max_entry_dist: float = 40.0       # hallucinated price guard
    max_expiry_min: int = 120          # = 8 x M15
    max_open_signals: int = 1
    approach_usd: float = 4.0

    # ---- Paths / flags ----
    state_path: str = "state/state.json"
    out_dir: str = "out"
    dry_run: bool = False
    force: bool = False


def load_config() -> Config:
    return Config(
        gemini_api_key=_env("GEMINI_API_KEY"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        twelvedata_api_key=_env("TWELVEDATA_API_KEY"),
        symbol=_env("SYMBOL", "XAU/USD"),
        yf_symbol=_env("YF_SYMBOL", "XAUUSD=X"),
        broker_offset=_f("BROKER_OFFSET", 0.0),
        stale_minutes=_i("STALE_MINUTES", 25),
        chart_candles=_i("CHART_CANDLES", 120),
        csv_candles=_i("CSV_CANDLES", 150),
        tz=_env("BOT_TZ", "Asia/Colombo"),
        window_start=_env("WINDOW_START", "10:00"),
        window_end=_env("WINDOW_END", "20:00"),
        window_grace_min=_i("WINDOW_GRACE_MIN", 10),
        digest_every_hours=_f("DIGEST_EVERY_HOURS", 3.0),
        min_scan_gap_min=_i("MIN_SCAN_GAP_MIN", 12),
        agent_id=_env("AGENT_ID", "antigravity-preview-09-2026"),
        agent_model=_env("AGENT_MODEL", "gemini-3.8-flash"),
        agent_max_tokens=_i("AGENT_MAX_TOKENS", 80000),
        agent_timeout=_i("AGENT_TIMEOUT", 480),
        api_revision=_env("GEMINI_API_REVISION", ""),
        fallback_enabled=_b("AI_FALLBACK", True),
        fallback_model=_env("FALLBACK_MODEL", "gemini-3.8-flash"),
        ai_daily_limit=_i("AI_DAILY_LIMIT", 95),
        ai_confirm_reserve=_i("AI_CONFIRM_RESERVE", 5),
        max_confirm_checks=_i("MAX_CONFIRM_CHECKS", 3),
        equity=_f("ACCOUNT_EQUITY", 100.0),
        risk_pct=_f("RISK_PERCENT", 1.0),
        contract_size=_f("CONTRACT_SIZE", 100.0),
        min_lot=_f("MIN_LOT", 0.01),
        lot_step=_f("LOT_STEP", 0.01),
        max_lot=_f("MAX_LOT", 1.0),
        leverage=_f("LEVERAGE", 200.0),
        spread=_f("SPREAD_USD", 0.25),
        commission_per_lot=_f("COMMISSION_PER_LOT", 0.0),
        enforce_risk_limit=_b("ENFORCE_RISK_LIMIT", False),
        min_net_rr=_f("MIN_NET_RR", 2.0),
        min_sl=_f("MIN_SL_USD", 1.0),
        max_sl=_f("MAX_SL_USD", 12.0),
        min_entry_gap=_f("MIN_ENTRY_GAP", 0.5),
        max_entry_dist=_f("MAX_ENTRY_DIST", 40.0),
        max_expiry_min=_i("MAX_EXPIRY_MIN", 120),
        max_open_signals=_i("MAX_OPEN_SIGNALS", 1),
        approach_usd=_f("APPROACH_USD", 4.0),
        state_path=_env("STATE_PATH", "state/state.json"),
        out_dir=_env("OUT_DIR", "out"),
        dry_run=_b("DRY_RUN", False),
        force=_b("FORCE_RUN", False),
    )
