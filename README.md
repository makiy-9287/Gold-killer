# XAUUSD Sniper Bot (GitHub Actions + Antigravity + Telegram)

සදුදා–සිකුරාදා, **10:00–20:00 (Asia/Colombo)**, විනාඩි 15කට සැරයක්:

1. Twelve Data වලින් XAUUSD OHLCV (M5, M15, H1) → H4 / D1 resample (UTC anchor = Exness)
2. Candlestick charts 5ක් (D1, H4, H1, M15, M5) matplotlib වලින් render
3. Charts + raw CSV (150 candles) + exact key levels → **Antigravity agent (gemini-3.8-flash)**
4. Agent: code_execution (exact swings/FVG/OB/ATR) + google_search (news) → A+ setup තිබුණොත් `submit_signal` **tool call** → script එක guard rails + lot calc කරලා Telegram යවනවා
5. NO SETUP නම් **මැසේජ් එකක් නැහැ**
6. Signal එක track කරනවා (AI නැතුව): approaching → entry touched → **AI confirmation (“ENTRY CONFIRMED / INVALIDATED”)** → TP1 / TP2 / SL / expired / missed
7. පැය 3කට සැරයක් (config) chart 5 Telegram එකට

## Setup (විනාඩි 10)

1. **Telegram bot**: @BotFather → `/newbot` → token. Bot එකට `/start` යවන්න. Chat id: @userinfobot හෝ `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. **Twelve Data** free key: https://twelvedata.com (free: 8 req/min, 800/day — run එකකට 3 credits).
3. **Gemini API key**: Google AI Studio → Get API key (Antigravity access තියෙන project එකම).
4. GitHub repo (private) හදලා මේ files upload කරන්න (`.github` folder එකත් ඇතුළුව).
5. Repo → Settings → Secrets and variables → Actions → **Secrets**:
   `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TWELVEDATA_API_KEY`
6. Actions tab → “XAUUSD Sniper Bot” → **Run workflow**:
   - `selftest` → data + charts + Telegram digest (AI නැහැ)
   - `ai-ping` → Antigravity 1 request: access + function-call test. **මේක පළවෙනියෙන් කරන්න.**
   - ඊට පස්සේ cron එකෙන් auto run.

## Variables (Settings → Variables, optional)

| Name | Default | |
|---|---|---|
| ACCOUNT_EQUITY / RISK_PERCENT | 100 / 1 | lot calc |
| ENFORCE_RISK_LIMIT | false | true = risk limit ඉක්මවන signal යවන්නේ නැහැ |
| DIGEST_EVERY_HOURS | 3 | 4 කරන්නත් පුළුවන් |
| BROKER_OFFSET | 0 | Exness price − feed price (USD) |
| LEVERAGE / SPREAD_USD | 200 / 0.25 | Exness app එකෙන් verify |
| AI_DAILY_LIMIT | 95 | Pacific-day එකක AI requests cap |
| AGENT_MAX_TOKENS | 80000 | TPM 100K ට යටින් |
| MIN_NET_RR / MAX_SL_USD | 2.0 / 12 | guard rails |

## වැදගත් දේවල්

- **$100 + 1% risk**: XAUUSD 0.01 lot = 1 oz → $1 move = $1. SL $5ක් නම් loss ≈ $5 (5%). 1% (= $1) හරියන්න SL ≤ ~$0.75 ඕනේ — ඒක noise. ඒ නිසා signals වලට “TRADE SIZE NOT SUITABLE” warning එක එනවා (default: signal එක තවම යවනවා). Risk % වැඩි කරන්න, equity වැඩි කරන්න, නැත්නම් `ENFORCE_RISK_LIMIT=true` (එතකොට signals පාහේ එන්නේ නැහැ).
- **Data ≠ Exness**: feed එක spot-ish, Exness එකෙන් පොඩි offset එකක් තියෙනවා. MT5 එකේ price බලලා order දාන්න; offset එක `BROKER_OFFSET` එකට දාන්න.
- **TradingView screenshot** බැහැ (Actions වල login/bot-block/ToS) → data වලින් chart හදනවා.
- **Actions minutes**: run 41/day × ~20 days = ~820 runs. Private repo free = 2000 min → run එකකට සාමාන්‍යය ≤ 2.4 min වෙන්න ඕනේ (job එක minute එකට round up). පළවෙනි දවස Actions → Usage බලන්න. ඉක්මවනවා නම්: repo public කරන්න (standard runners free) — එතකොට system prompt එක පෙනෙනවා, ඒක බලාගන්න.
- **Antigravity quota**: AI Studio Rate-limit page එකේ Antigravity RPD/TPM බලන්න (පළවෙනි දවසේ). Agent loop එකක internal model calls වෙන වෙනම ගණන් වෙනවාද කියලා මම verify කරලා නැහැ. 429 එනවා නම් `MIN_SCAN_GAP_MIN=30` වගේ දාන්න.
- Antigravity model picker එකේ “Paid” කියලා පෙනුනා. `ai-ping` fail වුනොත් bot එක auto plain `gemini-3.8-flash` (+Google Search) fallback එකට යනවා (`AI_FALLBACK=false` කරලා off කරන්න).
- GitHub cron delay වෙනවා (විනාඩි 5–20) — normal. Public repo එකක 60 days activity නැත්නම් schedule disable වෙනවා.
- State: `bot-state` branch එකේ (single commit) save වෙනවා.
- Prompt edit කරන්න: `prompts/system_prompt.md`. Automation rules: `prompts/runtime_addendum.md`.

## Local test
```
pip install -r requirements.txt pytest
python -m pytest -q
python -m src.main --selftest --dry-run   # keys ඕනේ
```
**Signals financial advice නෙවෙයි. Demo account එකේ සති කිහිපයක් test කරන්න.**
