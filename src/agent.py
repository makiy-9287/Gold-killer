"""Antigravity agent (Interactions API) wrapper.

Docs: https://ai.google.dev/gemini-api/docs/antigravity-agent
  POST /v1beta/interactions
  agent="antigravity-preview-09-2026", environment="remote"|{...sources...},
  input=[text,image(base64)...], system_instruction, tools=[code_execution, google_search, url_context, function...]
  agent_config={"type":"antigravity","model":"gemini-3.8-flash","max_total_tokens":N}

"Tool use -> Telegram": agent එක custom function (submit_signal / confirm_entry / cancel_signal) call කරනවා.
ඒ call එක අපේ script එක execute කරලා Telegram යවනවා — bot token එක Google sandbox එකට යන්නේ නැහැ.
Function result එක ආපහු යවන්නේ නැහැ (extra request/RPD වැය වෙන නිසා) — interaction එක `requires_action` එකේම තියෙනවා.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import requests

from .config import Config
from .util import log

BASE = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_TOOLS = [{"type": "code_execution"}, {"type": "google_search"}, {"type": "url_context"}]

SUBMIT_SIGNAL = {
    "type": "function",
    "name": "submit_signal",
    "description": (
        "Call ONLY for a genuine A+ BUY LIMIT / SELL LIMIT setup that passed every rule. This immediately sends the "
        "signal to the trader's Telegram. Do not call for NO TRADE / WATCHLIST / B setups. At most once per scan."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
            "entry_price": {"type": "number", "description": "Exact limit price (from CSV data, 2 decimals)"},
            "poi_low": {"type": "number"},
            "poi_high": {"type": "number"},
            "poi_type": {"type": "string", "description": "e.g. 'M15 bullish FVG + OB (H1 demand)'"},
            "stop_loss": {"type": "number", "description": "Tight but structurally valid; derived from M5/M15 structure"},
            "tp1": {"type": "number", "description": "High-probability reaction/liquidity level price will very likely reach"},
            "tp2": {"type": "number", "description": "Main opposing-liquidity target (optional)"},
            "tp1_reason": {"type": "string"},
            "tp2_reason": {"type": "string"},
            "confidence_10": {"type": "integer"},
            "expiry_minutes": {"type": "integer", "description": "<= 120 and before session end"},
            "wave_summary": {"type": "string", "description": "Preferred count, current wave, alt count, invalidation (<=220 chars)"},
            "ict_summary": {"type": "string", "description": "Liquidity sweep, displacement, BOS/CHoCH/MSS, FVG/OB (<=220 chars)"},
            "news_check": {"type": "string", "description": "High-impact USD/gold news within next 4h, Colombo time (or 'none found')"},
            "cancel_conditions": {"type": "string"},
            "rationale_si": {"type": "string", "description": "ONE concise Sinhala sentence"},
        },
        "required": ["direction", "entry_price", "poi_low", "poi_high", "poi_type", "stop_loss", "tp1",
                     "tp1_reason", "confidence_10", "expiry_minutes", "wave_summary", "ict_summary",
                     "cancel_conditions", "rationale_si"],
    },
}

CANCEL_SIGNAL = {
    "type": "function",
    "name": "cancel_signal",
    "description": "Cancel an ALREADY SENT pending signal (status PENDING) because its thesis/POI/wave count is now invalid.",
    "parameters": {
        "type": "object",
        "properties": {"signal_id": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["signal_id", "reason"],
    },
}

CONFIRM_ENTRY = {
    "type": "function",
    "name": "confirm_entry",
    "description": "Report the post-touch confirmation verdict for a signal whose entry price was just reached.",
    "parameters": {
        "type": "object",
        "properties": {
            "signal_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["CONFIRMED", "WAIT", "INVALIDATED"]},
            "reason_en": {"type": "string"},
            "reason_si": {"type": "string", "description": "ONE short Sinhala sentence"},
        },
        "required": ["signal_id", "verdict", "reason_en", "reason_si"],
    },
}

SCAN_TOOLS = DEFAULT_TOOLS + [SUBMIT_SIGNAL, CANCEL_SIGNAL]
CONFIRM_TOOLS = [{"type": "code_execution"}, CONFIRM_ENTRY, CANCEL_SIGNAL]
CUSTOM_NAMES = {"submit_signal", "cancel_signal", "confirm_entry"}


class AgentError(Exception):
    def __init__(self, msg: str, quota: bool = False):
        super().__init__(msg)
        self.quota = quota


@dataclass
class AgentResult:
    backend: str
    status: str
    text: str
    calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


# ------------------------------------------------------------------ http
def _post(cfg: Config, url: str, body: dict, headers: dict) -> dict:
    last = ""
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, data=json.dumps(body), timeout=cfg.agent_timeout)
        except requests.RequestException as e:
            raise AgentError(f"request failed: {type(e).__name__}") from None
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise AgentError("non-JSON 200 response") from None
        last = f"HTTP {r.status_code}: {r.text[:500]}"
        if r.status_code == 429:
            quota = "per day" in r.text.lower() or "daily" in r.text.lower()
            if quota or attempt == 1:
                raise AgentError(last, quota=quota)
            log("429 from Gemini API; waiting 40s then retrying once")
            time.sleep(40)
            continue
        if r.status_code in (500, 502, 503, 504) and attempt < 1:
            time.sleep(20)
            continue
        break
    raise AgentError(last)


# ------------------------------------------------------------------ response parsing
_SKIP = ("call", "result", "thought", "tool")


def _step_text(step: dict) -> str:
    stype = str(step.get("type", "")).lower()
    if any(k in stype for k in _SKIP):
        return ""
    out: list[str] = []
    content = step.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type", "text") == "text" and part.get("text"):
                out.append(str(part["text"]))
    elif isinstance(content, str):
        out.append(content)
    if not out and isinstance(step.get("text"), str):
        out.append(step["text"])
    return "\n".join(out).strip()


def parse_interaction(j: dict) -> AgentResult:
    steps = j.get("steps") or j.get("outputs") or []
    calls = []
    for s in steps:
        if isinstance(s, dict) and s.get("type") == "function_call" and s.get("name") in CUSTOM_NAMES:
            args = s.get("arguments", s.get("args", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append({"name": s["name"], "args": args or {}, "id": s.get("id")})
    text = j.get("output_text") or ""
    if not text:
        for s in reversed(steps):
            if isinstance(s, dict):
                t = _step_text(s)
                if t:
                    text = t
                    break
    return AgentResult("antigravity", str(j.get("status", "")), text, calls, j.get("usage") or {}, j)


def step_summary(j: dict) -> str:
    """Debug: step types ගණන (schema වෙනස් වුනොත් බලාගන්න)."""
    steps = j.get("steps") or j.get("outputs") or []
    counts: dict[str, int] = {}
    for s in steps:
        if isinstance(s, dict):
            counts[str(s.get("type"))] = counts.get(str(s.get("type")), 0) + 1
    return ", ".join(f"{k}x{v}" for k, v in counts.items()) or "no steps"


# ------------------------------------------------------------------ backends
def call_antigravity(cfg: Config, system_instruction: str, input_parts: list[dict],
                     sources: list[dict], tools: list[dict], max_tokens: int | None = None) -> AgentResult:
    body = {
        "agent": cfg.agent_id,
        "input": input_parts,
        "system_instruction": system_instruction,
        "environment": {"type": "remote", "sources": sources},
        "tools": tools,
        "agent_config": {"type": "antigravity", "model": cfg.agent_model,
                         "max_total_tokens": max_tokens or cfg.agent_max_tokens},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": cfg.gemini_api_key}
    if cfg.api_revision:
        headers["Api-Revision"] = cfg.api_revision
    j = _post(cfg, f"{BASE}/interactions", body, headers)
    res = parse_interaction(j)
    log(f"Antigravity status={res.status} steps=[{step_summary(j)}] calls={[c['name'] for c in res.calls]} usage={res.usage}")
    return res


def _json_from_text(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


def call_plain_gemini(cfg: Config, system_instruction: str, input_parts: list[dict], tools: list[dict],
                      csv_text: str = "") -> AgentResult:
    """Fallback: sandbox/function-calling නැතුව generateContent + Google Search. JSON text එකෙන් calls ගන්නවා."""
    schemas = [{"name": t["name"], "parameters": t["parameters"]} for t in tools if t.get("type") == "function"]
    protocol = (
        "\n\nOUTPUT PROTOCOL (no function calling available in this mode): respond with ONLY one JSON object: "
        '{"calls":[{"name":"<function name>","args":{...}}], "note":"<one line>"} . '
        'Use {"calls":[], "note":"NO_TRADE: reason"} when nothing qualifies. Allowed functions and argument schemas: '
        + json.dumps(schemas)
    )
    parts = []
    for p in input_parts:
        if p.get("type") == "text":
            parts.append({"text": p["text"]})
        elif p.get("type") == "image":
            parts.append({"inlineData": {"mimeType": p.get("mime_type", "image/png"), "data": p["data"]}})
    if csv_text:
        parts.append({"text": "RAW OHLCV (authoritative prices):\n" + csv_text})
    parts.append({"text": protocol})
    body = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": parts}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.2},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": cfg.gemini_api_key}
    j = _post(cfg, f"{BASE}/models/{cfg.fallback_model}:generateContent", body, headers)
    try:
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise AgentError("fallback: empty response") from None
    data = _json_from_text(text)
    calls = [
        {"name": c.get("name"), "args": c.get("args") or {}, "id": None}
        for c in data.get("calls", []) if isinstance(c, dict) and c.get("name") in CUSTOM_NAMES
    ]
    return AgentResult("gemini-fallback", "completed", str(data.get("note", "")), calls, j.get("usageMetadata") or {}, j)


def run_ai(cfg: Config, system_instruction: str, input_parts: list[dict], sources: list[dict],
           tools: list[dict], csv_for_fallback: str = "", max_tokens: int | None = None) -> AgentResult:
    try:
        return call_antigravity(cfg, system_instruction, input_parts, sources, tools, max_tokens)
    except AgentError as e:
        if e.quota or not cfg.fallback_enabled:
            raise
        log(f"Antigravity failed ({str(e)[:200]}); using plain {cfg.fallback_model} fallback")
        return call_plain_gemini(cfg, system_instruction, input_parts, tools, csv_for_fallback)
