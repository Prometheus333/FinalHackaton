"""
AI Trading Terminal — single-file Flask app for the hackathon.

Run:
    pip install -r requirements.txt
    export ALPACA_KEY_ID="your-alpaca-key-id"
    export ALPACA_SECRET_KEY="your-alpaca-secret-key"
    export ANTHROPIC_API_KEY="sk-ant-your-claude-key"
    python app.py
Then open http://localhost:5000

CONNECTING REAL APIS:
- Alpaca: set ALPACA_KEY_ID / ALPACA_SECRET_KEY (from https://app.alpaca.markets,
  Paper Trading keys work fine for market data — the free IEX feed is used).
- Claude: set ANTHROPIC_API_KEY (from https://console.anthropic.com/settings/keys).
  Uses the official `anthropic` Python SDK, model configurable via CLAUDE_MODEL.
Hit GET /api/status once the server is running to check both connections —
it makes one real lightweight call to each API and reports pass/fail with
the actual error message, so you can debug key/network issues quickly.
If either API is unreachable, the app automatically falls back to synthetic
data / a rule-based mock assistant so the demo never breaks — every bars/quote
response includes a "source" field ("alpaca" or "synthetic") and every chat
reply includes one too ("claude" or "fallback") so the UI can show you which
one is actually live.
"""

import os
import re
import json
import time
import threading
from datetime import datetime, timedelta, timezone

import requests
import feedparser
from flask import Flask, request, jsonify, Response

# --------------------------------------------------------------------------
# Configuration — set these via environment variables, no secrets hardcoded.
# --------------------------------------------------------------------------

ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "SPY", "TSLA", "NVDA"]

app = Flask(__name__)

if not ALPACA_KEY_ID or not ALPACA_SECRET_KEY:
    app.logger.warning("ALPACA_KEY_ID / ALPACA_SECRET_KEY not set — market data will use synthetic fallback data.")
if not ANTHROPIC_API_KEY:
    app.logger.warning("ANTHROPIC_API_KEY not set — chat will use the rule-based fallback assistant.")

# Simple in-memory cache so we don't hammer Alpaca / RSS on every poll.
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 20
STATUS_CACHE_TTL_SECONDS = 45


def cache_get(key, ttl=CACHE_TTL_SECONDS):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["t"]) < ttl:
            return entry["v"]
    return None


def cache_set(key, value):
    with _cache_lock:
        _cache[key] = {"t": time.time(), "v": value}


# --------------------------------------------------------------------------
# Alpaca market data helpers
# --------------------------------------------------------------------------

def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def get_bars(symbol, timeframe="1Day", limit=120):
    """Fetch OHLC bars for a symbol from Alpaca's free IEX feed.
    Returns (bars, source) where source is 'alpaca' or 'synthetic'."""
    cache_key = f"bars:{symbol}:{timeframe}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if not ALPACA_KEY_ID or not ALPACA_SECRET_KEY:
        result = (_synthetic_bars(symbol, limit), "synthetic")
        cache_set(cache_key, result)
        return result

    url = f"{ALPACA_DATA_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "feed": "iex",  # free-tier data feed
        "adjustment": "raw",
    }
    try:
        r = requests.get(url, headers=alpaca_headers(), params=params, timeout=10)
        r.raise_for_status()
        raw_bars = r.json().get("bars", [])
        if not raw_bars:
            raise ValueError("Alpaca returned no bars (symbol may be invalid or market data plan restricted)")
        bars = [
            {
                "time": b["t"][:10] if timeframe == "1Day" else b["t"],
                "open": b["o"],
                "high": b["h"],
                "low": b["l"],
                "close": b["c"],
                "volume": b["v"],
            }
            for b in raw_bars
        ]
        result = (bars, "alpaca")
        cache_set(cache_key, result)
        return result
    except Exception as exc:
        # Fall back to a deterministic synthetic series so the demo never
        # breaks on stage because of API-key / network issues.
        result = (_synthetic_bars(symbol, limit), "synthetic")
        cache_set(cache_key, result)
        app.logger.warning(f"Alpaca bars fetch failed for {symbol}: {exc}. Using synthetic data.")
        return result


def get_latest_quote(symbol):
    """Returns {"symbol", "price", "time", "source"}."""
    cache_key = f"quote:{symbol}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if ALPACA_KEY_ID and ALPACA_SECRET_KEY:
        url = f"{ALPACA_DATA_URL}/stocks/{symbol}/quotes/latest"
        try:
            r = requests.get(url, headers=alpaca_headers(), params={"feed": "iex"}, timeout=8)
            r.raise_for_status()
            q = r.json().get("quote", {})
            price = q.get("ap") or q.get("bp") or 0
            if price:
                result = {"symbol": symbol, "price": round(price, 2), "time": q.get("t"), "source": "alpaca"}
                cache_set(cache_key, result)
                return result
        except Exception as exc:
            app.logger.warning(f"Alpaca quote fetch failed for {symbol}: {exc}. Using synthetic data.")

    bars, _ = get_bars(symbol, limit=2)
    last = bars[-1]["close"] if bars else 100.0
    result = {"symbol": symbol, "price": round(last, 2), "time": None, "source": "synthetic"}
    cache_set(cache_key, result)
    return result


def check_alpaca_connection():
    """Makes one real request to Alpaca to verify credentials/network."""
    if not ALPACA_KEY_ID or not ALPACA_SECRET_KEY:
        return False, "ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set."
    try:
        r = requests.get(
            f"{ALPACA_DATA_URL}/stocks/AAPL/quotes/latest",
            headers=alpaca_headers(), params={"feed": "iex"}, timeout=8,
        )
        if r.status_code == 200:
            return True, "Connected — receiving live IEX quotes."
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return False, str(exc)[:200]


def check_claude_connection():
    """Makes one real, minimal request to the Claude API to verify the key."""
    if not ANTHROPIC_API_KEY:
        return False, "ANTHROPIC_API_KEY is not set."
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        client.messages.create(
            model=CLAUDE_MODEL, max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, f"Connected — model {CLAUDE_MODEL} responded."
    except Exception as exc:
        return False, str(exc)[:200]


def _synthetic_bars(symbol, limit):
    """Deterministic pseudo-random walk, seeded by symbol, used only as a
    fallback if the Alpaca API is unreachable (bad key, no network, etc)."""
    import random
    rnd = random.Random(sum(ord(c) for c in symbol))
    base_price = 50 + (sum(ord(c) for c in symbol) % 400)
    price = float(base_price)
    bars = []
    today = datetime.now(timezone.utc).date()
    for i in range(limit, 0, -1):
        day = today - timedelta(days=i)
        drift = rnd.uniform(-0.015, 0.017)
        price = max(1.0, price * (1 + drift))
        o = price * (1 + rnd.uniform(-0.004, 0.004))
        c = price * (1 + rnd.uniform(-0.004, 0.004))
        h = max(o, c) * (1 + rnd.uniform(0.001, 0.006))
        l = min(o, c) * (1 - rnd.uniform(0.001, 0.006))
        bars.append({
            "time": day.isoformat(),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": rnd.randint(1_000_000, 20_000_000),
        })
    return bars


# --------------------------------------------------------------------------
# News (free RSS, no API key needed)
# --------------------------------------------------------------------------

def get_news(symbol=None, limit=12):
    cache_key = f"news:{symbol or 'market'}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if symbol:
        feed_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    else:
        feed_url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"

    items = []
    try:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:limit]:
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:220],
                "source": "Yahoo Finance",
            })
    except Exception as exc:
        app.logger.warning(f"News fetch failed: {exc}")

    if not items:
        items = [{
            "title": f"No live headlines available for {symbol or 'the market'} right now.",
            "link": "",
            "published": "",
            "summary": "The RSS feed may be temporarily unreachable from this environment.",
            "source": "system",
        }]

    cache_set(cache_key, items)
    return items


def filter_news_by_keywords(items, keywords):
    if not keywords:
        return items
    keywords_lower = [k.lower() for k in keywords]
    filtered = [
        it for it in items
        if any(k in (it["title"] + " " + it["summary"]).lower() for k in keywords_lower)
    ]
    return filtered or items


# --------------------------------------------------------------------------
# AI assistant — turns chat + scenario requests into structured chart commands
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an AI trading-desk assistant embedded in a live terminal UI.
You receive the user's message plus recent OHLC context for the active symbol(s).

You must respond with STRICT JSON ONLY (no markdown fences, no prose outside the JSON) matching this shape:
{
  "reply": "<short, natural-language answer for the chat panel, 1-4 sentences>",
  "commands": [
    {"action": "add_projection", "symbol": "AAPL", "label": "AI Projection", "points": [{"time": "2026-08-07", "value": 231.5}, ...]},
    {"action": "add_scenario", "symbol": "AAPL", "label": "Rate hike +0.5%", "points": [{"time": "2026-08-07", "value": 225.0}, ...]},
    {"action": "add_overlay", "symbol": "MSFT"},
    {"action": "remove_overlay", "symbol": "MSFT"},
    {"action": "set_symbol", "symbol": "AAPL"},
    {"action": "filter_news", "keywords": ["AAPL", "iPhone"]},
    {"action": "clear_projections"}
  ]
}

Rules:
- Only include commands that are actually relevant to this turn; "commands" can be an empty array.
- "points" for projections/scenarios must extend forward from the last known date, using ISO dates (YYYY-MM-DD), typically 5-10 future points.
- Base projections/scenarios on the OHLC context you were given, not invented price levels far from reality.
- Keep "reply" conversational and concise, like a sharp trading-desk analyst, not a wall of text.
"""


def _extract_json(text):
    """Best-effort extraction of a JSON object from an LLM response, in case
    the model wraps it in prose or code fences despite instructions."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None


def call_llm(user_message, context_symbol, bars):
    """Calls the Claude API (Anthropic Messages endpoint) via the official
    `anthropic` SDK. Falls back to a rule-based mock so the demo still works
    if ANTHROPIC_API_KEY is missing or the call fails."""
    last_close = bars[-1]["close"] if bars else 100.0
    last_date = bars[-1]["time"] if bars else datetime.now().date().isoformat()
    context = {
        "symbol": context_symbol,
        "last_close": last_close,
        "last_date": last_date,
        "recent_closes": [b["close"] for b in bars[-10:]],
    }

    if not ANTHROPIC_API_KEY:
        app.logger.warning("ANTHROPIC_API_KEY not set — using fallback analyst.")
        fallback = _mock_ai_response(user_message, context_symbol, bars)
        fallback["source"] = "fallback"
        return fallback

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            temperature=0.4,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"Context: {json.dumps(context)}\n\nUser message: {user_message}"},
            ],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        parsed = _extract_json(raw)
        if parsed and "reply" in parsed:
            parsed.setdefault("commands", [])
            parsed["source"] = "claude"
            return parsed
        raise ValueError("Claude response did not contain valid JSON")
    except Exception as exc:
        app.logger.warning(f"Claude API call failed, using fallback analyst: {exc}")
        fallback = _mock_ai_response(user_message, context_symbol, bars)
        fallback["source"] = "fallback"
        return fallback


def _project_points(bars, days, pct_drift_per_day):
    """Simple deterministic projection: compound a daily drift forward from
    the last close. Used both by the fallback AI and the what-if endpoint."""
    if not bars:
        return []
    last_close = bars[-1]["close"]
    try:
        last_date = datetime.fromisoformat(bars[-1]["time"][:10])
    except Exception:
        last_date = datetime.now()
    points = []
    price = last_close
    for i in range(1, days + 1):
        price = price * (1 + pct_drift_per_day)
        d = last_date + timedelta(days=i)
        points.append({"time": d.date().isoformat(), "value": round(price, 2)})
    return points


def _mock_ai_response(user_message, symbol, bars):
    """Rule-based fallback: keyword-triggers a plausible scenario so the UI
    stays fully functional without live LLM access."""
    msg = user_message.lower()
    commands = []

    if not bars:
        return {"reply": "I don't have market data loaded for that symbol yet.", "commands": []}

    recent = [b["close"] for b in bars[-10:]]
    trend = (recent[-1] - recent[0]) / recent[0] if recent[0] else 0

    if "rate" in msg or "interest" in msg:
        pts = _project_points(bars, 8, -0.006)
        commands.append({"action": "add_scenario", "symbol": symbol, "label": "Rate hike scenario", "points": pts})
        commands.append({"action": "filter_news", "keywords": ["rate", "fed", "inflation"]})
        reply = f"Modeling a rate-hike scenario for {symbol}: a sustained rate increase typically pressures growth-sensitive names, so I've drawn a downward-biased path from the last close of {recent[-1]:.2f}."
    elif "revenue" in msg or "earnings" in msg or "drop" in msg:
        pts = _project_points(bars, 8, -0.01)
        commands.append({"action": "add_scenario", "symbol": symbol, "label": "Revenue miss scenario", "points": pts})
        commands.append({"action": "filter_news", "keywords": [symbol, "earnings", "revenue"]})
        reply = f"If {symbol} misses on revenue, expect a sharper drawdown — I've plotted a steeper decline scenario starting from {recent[-1]:.2f}."
    elif "compare" in msg or "overlay" in msg or "vs" in msg:
        other = next((s for s in DEFAULT_SYMBOLS if s.lower() in msg and s != symbol), "SPY")
        commands.append({"action": "add_overlay", "symbol": other})
        reply = f"Overlaying {other} on the chart against {symbol} so you can compare relative performance."
    else:
        drift = 0.004 if trend >= 0 else -0.003
        pts = _project_points(bars, 6, drift)
        commands.append({"action": "add_projection", "symbol": symbol, "label": "AI Projection", "points": pts})
        direction = "bullish" if drift > 0 else "cautious"
        reply = f"Based on the last 10 sessions, {symbol} is trending {'up' if trend >= 0 else 'down'} ({trend*100:.1f}%). I'm leaning {direction} — projection plotted on the chart."

    return {"reply": reply, "commands": commands}


# --------------------------------------------------------------------------
# Routes — API
# --------------------------------------------------------------------------

@app.route("/api/bars/<symbol>")
def api_bars(symbol):
    timeframe = request.args.get("timeframe", "1Day")
    limit = int(request.args.get("limit", 120))
    bars, source = get_bars(symbol.upper(), timeframe=timeframe, limit=limit)
    return jsonify({"symbol": symbol.upper(), "bars": bars, "source": source})


@app.route("/api/quote/<symbol>")
def api_quote(symbol):
    return jsonify(get_latest_quote(symbol.upper()))


@app.route("/api/quotes")
def api_quotes():
    symbols = request.args.get("symbols", ",".join(DEFAULT_SYMBOLS)).split(",")
    return jsonify([get_latest_quote(s.strip().upper()) for s in symbols if s.strip()])


@app.route("/api/news")
def api_news():
    symbol = request.args.get("symbol")
    keywords = request.args.get("keywords")
    items = get_news(symbol)
    if keywords:
        items = filter_news_by_keywords(items, keywords.split(","))
    return jsonify(items)


@app.route("/api/status")
def api_status():
    """Diagnostic endpoint: makes one real call to each API and reports
    connected/error so you can debug keys without digging through logs."""
    cached = cache_get("status_check", ttl=STATUS_CACHE_TTL_SECONDS)
    if cached is not None:
        return jsonify(cached)
    alpaca_ok, alpaca_detail = check_alpaca_connection()
    claude_ok, claude_detail = check_claude_connection()
    result = {
        "alpaca": {"connected": alpaca_ok, "detail": alpaca_detail},
        "claude": {"connected": claude_ok, "detail": claude_detail},
    }
    cache_set("status_check", result)
    return jsonify(result)


@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    data = request.get_json(force=True)
    symbol = data.get("symbol", "AAPL").upper()
    scenario_type = data.get("scenario_type", "rate_change")
    magnitude = float(data.get("magnitude", 0.5))  # e.g. 0.5 => 0.5% rate move, or 10 => 10% revenue drop
    days = int(data.get("days", 8))

    bars, _ = get_bars(symbol, limit=60)

    # Simple, explainable heuristics mapping scenario magnitude -> daily drift.
    # This keeps the "what-if" path deterministic and demoable, rather than
    # depending entirely on an LLM call for a numeric chart series.
    if scenario_type == "rate_change":
        daily_drift = -0.0012 * magnitude  # each 0.5% rate move ~ -0.06%/day compounding
        label = f"Rate {'+' if magnitude >= 0 else ''}{magnitude}% scenario"
    elif scenario_type == "revenue_change":
        daily_drift = 0.0009 * magnitude  # magnitude in %, positive = growth, negative = miss
        label = f"Revenue {'+' if magnitude >= 0 else ''}{magnitude}% scenario"
    elif scenario_type == "volatility_spike":
        daily_drift = -0.0006 * abs(magnitude)
        label = f"Volatility spike scenario (x{magnitude})"
    else:
        daily_drift = 0.0005 * magnitude
        label = f"Custom scenario ({magnitude})"

    points = _project_points(bars, days, daily_drift)
    return jsonify({"symbol": symbol, "label": label, "points": points})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    message = data.get("message", "")
    symbol = data.get("symbol", "AAPL").upper()
    bars, _ = get_bars(symbol, limit=60)
    result = call_llm(message, symbol, bars)
    return jsonify(result)


# --------------------------------------------------------------------------
# Frontend — single-page Bloomberg-style terminal
# --------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Trading Terminal</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --accent: #ff9900;
    --accent-blue: #33c9ff;
    --panel: #0b0f14;
    --panel-2: #10161d;
    --border: #1c2733;
  }
  html, body { height: 100%; overflow: hidden; }
  body { background: #05070a; font-family: 'Segoe UI', ui-monospace, monospace; }
  .glow { box-shadow: 0 0 18px rgba(255,153,0,0.15); }
  .glow-blue { box-shadow: 0 0 18px rgba(51,201,255,0.15); }
  .panel { background: var(--panel); border: 1px solid var(--border); }
  .panel-2 { background: var(--panel-2); border: 1px solid var(--border); }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-thumb { background: #26333f; border-radius: 4px; }
  @keyframes ticker-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
  .ticker-track { animation: ticker-scroll 30s linear infinite; }
  @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .live-dot { animation: pulse-dot 1.4s ease-in-out infinite; }
  .fade-in { animation: fadeIn .25s ease-out; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px);} to { opacity: 1; transform: translateY(0);} }
  .scenario-btn:hover { border-color: var(--accent); color: var(--accent); }
  .chat-msg-user { background: linear-gradient(135deg,#1a2733,#0f1720); }
  .chat-msg-ai { background: linear-gradient(135deg,#1a1409,#120e06); border-left: 2px solid var(--accent); }
</style>
</head>
<body class="text-gray-200 h-screen flex flex-col overflow-hidden">

  <!-- Top bar -->
  <header class="panel-2 border-b border-[var(--border)] px-4 py-2 flex items-center justify-between shrink-0">
    <div class="flex items-center gap-3">
      <div class="w-2.5 h-2.5 rounded-full bg-orange-500 live-dot"></div>
      <h1 class="text-lg font-bold tracking-widest text-orange-400">AI TRADING TERMINAL</h1>
      <span class="text-xs text-gray-500 border border-[var(--border)] rounded px-2 py-0.5">PAPER · SIMULATED</span>
    </div>
    <div class="flex items-center gap-3">
      <span id="alpacaStatus" class="text-xs border border-[var(--border)] rounded px-2 py-0.5 text-gray-500">● ALPACA</span>
      <span id="claudeStatus" class="text-xs border border-[var(--border)] rounded px-2 py-0.5 text-gray-500">● CLAUDE</span>
      <div id="clock" class="text-xs text-cyan-300 tabular-nums"></div>
    </div>
  </header>

  <!-- Ticker tape -->
  <div class="panel-2 border-b border-[var(--border)] overflow-hidden whitespace-nowrap py-1.5 shrink-0">
    <div id="tickerTrack" class="ticker-track inline-flex gap-10 px-4 text-sm"></div>
  </div>

  <!-- Main layout: fixed grid, no page-level scroll.
       Column 1 (2/6 width): Watchlist + Chat. Column 2 (4/6 width): Chart + News. -->
  <main class="flex-1 min-h-0 grid grid-cols-[2fr_4fr] grid-rows-2 gap-3 p-3 overflow-hidden">

    <!-- Watchlist (col 1, row 1) -->
    <section class="panel glow rounded-lg p-3 flex flex-col overflow-hidden min-h-0">
      <h2 class="text-xs uppercase tracking-widest text-orange-400 mb-2 shrink-0">Watchlist</h2>
      <div class="relative mb-2 shrink-0">
        <button id="watchlistSearchBtn" type="button"
                class="absolute left-1.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-orange-400">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5">
            <circle cx="11" cy="11" r="7"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
        <input id="watchlistSearch" type="text" placeholder="Search or add symbol..."
               class="w-full bg-[#0a0e13] border border-[var(--border)] rounded pl-7 pr-2 py-1 text-xs
                      focus:outline-none focus:border-orange-400" />
      </div>
      <div id="symbolList" class="flex-1 overflow-y-auto space-y-1 min-h-0"></div>
      <div class="mt-2 pt-2 border-t border-[var(--border)] shrink-0">
        <h2 class="text-xs uppercase tracking-widest text-cyan-300 mb-1">Overlays</h2>
        <div id="overlayList" class="flex flex-wrap gap-1 text-xs"></div>
      </div>
    </section>

    <!-- Chart (col 2, row 1) -->
    <section class="panel glow rounded-lg p-3 flex flex-col overflow-hidden min-h-0">
      <div class="flex items-center justify-between mb-2 shrink-0 flex-wrap gap-1">
        <div class="flex items-center gap-2">
          <span id="activeSymbol" class="text-lg font-bold text-white">AAPL</span>
          <span id="activePrice" class="text-base text-cyan-300 tabular-nums">—</span>
          <span id="activeChange" class="text-xs tabular-nums">—</span>
          <span id="dataSourceBadge" class="text-[10px] px-1.5 py-0.5 rounded border border-gray-600 text-gray-500">—</span>
        </div>
        <div class="flex gap-1 text-xs flex-wrap">
          <button data-tf="1Day" class="tf-btn px-1.5 py-1 rounded border border-[var(--border)] hover:border-orange-400">1D</button>
          <button data-tf="1Hour" class="tf-btn px-1.5 py-1 rounded border border-[var(--border)] hover:border-orange-400">1H</button>
          <button id="clearOverlaysBtn" class="px-1.5 py-1 rounded border border-[var(--border)] hover:border-red-400 text-red-300">Clear</button>
        </div>
      </div>
      <div class="flex gap-1 mb-2 shrink-0 overflow-x-auto text-xs">
        <button class="scenario-btn panel-2 border border-[var(--border)] rounded px-2 py-1 whitespace-nowrap" data-type="rate_change" data-mag="0.5">Rates +0.5%</button>
        <button class="scenario-btn panel-2 border border-[var(--border)] rounded px-2 py-1 whitespace-nowrap" data-type="rate_change" data-mag="-0.5">Rates −0.5%</button>
        <button class="scenario-btn panel-2 border border-[var(--border)] rounded px-2 py-1 whitespace-nowrap" data-type="revenue_change" data-mag="-10">Revenue −10%</button>
        <button class="scenario-btn panel-2 border border-[var(--border)] rounded px-2 py-1 whitespace-nowrap" data-type="revenue_change" data-mag="15">Revenue +15%</button>
      </div>
      <div id="chart" class="flex-1 min-h-0"></div>
      <div id="legend" class="mt-1 flex flex-wrap gap-3 text-xs text-gray-400 shrink-0"></div>
    </section>

    <!-- AI Chat (col 1, row 2) -->
    <section class="panel glow-blue rounded-lg p-3 flex flex-col overflow-hidden min-h-0">
      <h2 class="text-xs uppercase tracking-widest text-cyan-300 mb-2 shrink-0">AI Desk Assistant</h2>
      <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1 min-h-0"></div>
      <form id="chatForm" class="flex gap-1 shrink-0">
        <input id="chatInput" type="text" placeholder="Ask about AAPL, run a scenario..."
               class="flex-1 bg-[#0a0e13] border border-[var(--border)] rounded px-2 py-1.5 text-sm focus:outline-none focus:border-cyan-400" />
        <button id="micBtn" type="button" title="Voice input"
                class="bg-[#10161d] hover:bg-[#182029] border border-[var(--border)] text-cyan-300 px-2.5 rounded flex items-center justify-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
            <line x1="12" y1="19" x2="12" y2="23"></line>
            <line x1="8" y1="23" x2="16" y2="23"></line>
          </svg>
        </button>
        <button type="submit" title="Send"
                class="bg-cyan-600 hover:bg-cyan-500 text-black px-3 rounded flex items-center justify-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-4 h-4">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path>
          </svg>
        </button>
      </form>
    </section>

    <!-- News (col 2, row 2) -->
    <section class="panel-2 rounded-lg p-3 flex flex-col overflow-hidden min-h-0">
      <h2 class="text-xs uppercase tracking-widest text-orange-400 mb-2 shrink-0">News Feed</h2>
      <div id="newsList" class="flex-1 overflow-y-auto space-y-2 text-xs pr-1 min-h-0"></div>
    </section>
  </main>

<script>
const DEFAULT_SYMBOLS = ["AAPL","MSFT","SPY","TSLA","NVDA"];
let activeSymbol = "AAPL";
let overlaySeries = {};   // symbol -> lightweight-charts line series (comparison overlay)
let extraSeries = [];     // projection / scenario dashed series
let candleSeries, chart;

// ---------- Clock ----------
function tickClock() {
  document.getElementById('clock').textContent = new Date().toUTCString().slice(0,25) + " UTC";
}
setInterval(tickClock, 1000); tickClock();

// ---------- API connection status ----------
async function refreshStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    setStatusBadge('alpacaStatus', 'ALPACA', data.alpaca.connected, data.alpaca.detail);
    setStatusBadge('claudeStatus', 'CLAUDE', data.claude.connected, data.claude.detail);
  } catch (err) {
    setStatusBadge('alpacaStatus', 'ALPACA', false, 'Status check failed');
    setStatusBadge('claudeStatus', 'CLAUDE', false, 'Status check failed');
  }
}

function setStatusBadge(elId, label, connected, detail) {
  const el = document.getElementById(elId);
  el.textContent = `● ${label}`;
  el.title = detail || '';
  el.className = 'text-xs border rounded px-2 py-0.5 ' +
    (connected ? 'border-green-600 text-green-400' : 'border-red-800 text-red-400');
}

// ---------- Chart setup ----------
function initChart() {
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#131b24' }, horzLines: { color: '#131b24' } },
    rightPriceScale: { borderColor: '#1c2733' },
    timeScale: { borderColor: '#1c2733' },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: '#22c55e', downColor: '#ef4444',
    borderVisible: false,
    wickUpColor: '#22c55e', wickDownColor: '#ef4444',
  });
}

async function loadSymbol(symbol, timeframe="1Day") {
  activeSymbol = symbol;
  document.getElementById('activeSymbol').textContent = symbol;
  const res = await fetch(`/api/bars/${symbol}?timeframe=${timeframe}&limit=120`);
  const data = await res.json();
  candleSeries.setData(data.bars.map(b => ({time: b.time, open: b.open, high: b.high, low: b.low, close: b.close})));
  clearExtraSeries();
  updatePriceHeader(data.bars, data.source);
  refreshNews(symbol);
  highlightWatchlist(symbol);
}

function updatePriceHeader(bars, source) {
  if (!bars.length) return;
  const last = bars[bars.length-1];
  const prev = bars.length > 1 ? bars[bars.length-2] : last;
  const chg = last.close - prev.close;
  const pct = prev.close ? (chg/prev.close*100) : 0;
  document.getElementById('activePrice').textContent = last.close.toFixed(2);
  const chEl = document.getElementById('activeChange');
  chEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${pct.toFixed(2)}%)`;
  chEl.className = 'text-sm tabular-nums ' + (chg >= 0 ? 'text-green-400' : 'text-red-400');
  const srcEl = document.getElementById('dataSourceBadge');
  if (srcEl) {
    srcEl.textContent = source === 'alpaca' ? 'LIVE' : 'SIMULATED';
    srcEl.className = 'text-[10px] px-1.5 py-0.5 rounded border ' +
      (source === 'alpaca' ? 'border-green-600 text-green-400' : 'border-gray-600 text-gray-500');
  }
}

function clearExtraSeries() {
  extraSeries.forEach(s => chart.removeSeries(s));
  extraSeries = [];
  document.getElementById('legend').innerHTML = '';
}

function addDashedSeries(points, label, color) {
  const series = chart.addLineSeries({
    color, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    pointMarkersVisible: true, lastValueVisible: true, priceLineVisible: false,
  });
  series.setData(points.map(p => ({time: p.time, value: p.value})));
  extraSeries.push(series);
  const legend = document.getElementById('legend');
  const chip = document.createElement('span');
  chip.className = 'flex items-center gap-1';
  chip.innerHTML = `<span class="inline-block w-2.5 h-0.5" style="background:${color}"></span> ${label}`;
  legend.appendChild(chip);
}

function addOverlay(symbol) {
  if (overlaySeries[symbol] || symbol === activeSymbol) return;
  const colors = ['#a855f7','#33c9ff','#facc15','#f97316'];
  const color = colors[Object.keys(overlaySeries).length % colors.length];
  fetch(`/api/bars/${symbol}?limit=120`).then(r => r.json()).then(data => {
    if (!data.bars.length) return;
    const base = data.bars[0].close;
    const series = chart.addLineSeries({ color, lineWidth: 1.5, priceScaleId: 'left', lastValueVisible: true });
    chart.priceScale('left').applyOptions({ visible: true, borderColor: '#1c2733' });
    series.setData(data.bars.map(b => ({time: b.time, value: +(((b.close - base)/base)*100).toFixed(2)})));
    overlaySeries[symbol] = series;
    renderOverlayChips();
  });
}

function removeOverlay(symbol) {
  if (!overlaySeries[symbol]) return;
  chart.removeSeries(overlaySeries[symbol]);
  delete overlaySeries[symbol];
  renderOverlayChips();
}

function renderOverlayChips() {
  const box = document.getElementById('overlayList');
  box.innerHTML = '';
  Object.keys(overlaySeries).forEach(sym => {
    const chip = document.createElement('button');
    chip.className = 'px-2 py-0.5 rounded border border-[var(--border)] hover:border-red-400';
    chip.textContent = sym + ' ×';
    chip.onclick = () => removeOverlay(sym);
    box.appendChild(chip);
  });
}

// ---------- Watchlist / ticker ----------
let watchlistSymbols = [...DEFAULT_SYMBOLS];

function highlightWatchlist(symbol) {
  document.querySelectorAll('.watch-item').forEach(el => {
    el.classList.toggle('border-orange-400', el.dataset.symbol === symbol);
    el.classList.toggle('text-orange-300', el.dataset.symbol === symbol);
  });
}

async function refreshQuotes() {
  const res = await fetch(`/api/quotes?symbols=${watchlistSymbols.join(',')}`);
  const quotes = await res.json();

  const listEl = document.getElementById('symbolList');
  listEl.innerHTML = '';
  quotes.forEach(q => {
    const row = document.createElement('div');
    row.className = 'watch-item panel-2 border border-[var(--border)] rounded px-2 py-1.5 flex justify-between items-center text-sm cursor-pointer hover:border-cyan-400';
    row.dataset.symbol = q.symbol;
    row.innerHTML = `<span class="font-semibold">${q.symbol}</span><span class="text-cyan-300 tabular-nums">${q.price.toFixed(2)}</span>`;
    row.onclick = () => loadSymbol(q.symbol);
    listEl.appendChild(row);
  });
  highlightWatchlist(activeSymbol);
  filterWatchlist(document.getElementById('watchlistSearch').value);

  const track = document.getElementById('tickerTrack');
  const html = quotes.map(q => `<span class="text-gray-400">${q.symbol} <span class="text-cyan-300">${q.price.toFixed(2)}</span></span>`).join('');
  track.innerHTML = html + html; // duplicate for seamless scroll
}

// ---------- Watchlist search ----------
function filterWatchlist(query) {
  const q = query.trim().toUpperCase();
  document.querySelectorAll('.watch-item').forEach(row => {
    row.style.display = !q || row.dataset.symbol.includes(q) ? '' : 'none';
  });
}

async function addToWatchlist(symbol) {
  symbol = symbol.trim().toUpperCase();
  if (!symbol) return;
  if (!watchlistSymbols.includes(symbol)) {
    watchlistSymbols.push(symbol);
  }
  await refreshQuotes();
  loadSymbol(symbol);
  document.getElementById('watchlistSearch').value = '';
  filterWatchlist('');
}

document.getElementById('watchlistSearch').addEventListener('input', (e) => {
  filterWatchlist(e.target.value);
});
document.getElementById('watchlistSearch').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const val = e.target.value.trim().toUpperCase();
    const visibleMatch = watchlistSymbols.includes(val);
    if (visibleMatch) {
      loadSymbol(val);
    } else if (val) {
      addToWatchlist(val); // not in watchlist yet -> search & add
    }
  }
});
document.getElementById('watchlistSearchBtn').addEventListener('click', () => {
  const input = document.getElementById('watchlistSearch');
  const val = input.value.trim().toUpperCase();
  if (val && !watchlistSymbols.includes(val)) addToWatchlist(val);
  else input.focus();
});

// ---------- News ----------
async function refreshNews(symbol, keywords) {
  let url = `/api/news?symbol=${symbol}`;
  if (keywords) url += `&keywords=${encodeURIComponent(keywords.join(','))}`;
  const res = await fetch(url);
  const items = await res.json();
  const box = document.getElementById('newsList');
  box.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'fade-in panel-2 border border-[var(--border)] rounded p-2';
    div.innerHTML = `<a href="${it.link}" target="_blank" class="text-orange-300 hover:underline">${it.title}</a>
                      <div class="text-gray-500 mt-0.5">${it.summary || ''}</div>`;
    box.appendChild(div);
  });
}

// ---------- Chat ----------
function appendChatMessage(role, text, source) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in rounded px-2 py-1.5 text-sm ${role === 'user' ? 'chat-msg-user text-right' : 'chat-msg-ai'}`;
  div.textContent = text;
  if (role === 'ai' && source) {
    const tag = document.createElement('div');
    tag.className = 'text-[10px] mt-1 ' + (source === 'claude' ? 'text-green-500' : 'text-gray-500');
    tag.textContent = source === 'claude' ? '● Claude API' : '● Fallback analyst (offline)';
    div.appendChild(tag);
  }
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function applyCommand(cmd) {
  switch (cmd.action) {
    case 'add_projection':
      addDashedSeries(cmd.points, cmd.label || 'AI Projection', '#33c9ff');
      break;
    case 'add_scenario':
      addDashedSeries(cmd.points, cmd.label || 'Scenario', '#ff9900');
      break;
    case 'add_overlay':
      addOverlay(cmd.symbol);
      break;
    case 'remove_overlay':
      removeOverlay(cmd.symbol);
      break;
    case 'set_symbol':
      loadSymbol(cmd.symbol);
      break;
    case 'filter_news':
      refreshNews(activeSymbol, cmd.keywords);
      break;
    case 'clear_projections':
      clearExtraSeries();
      break;
  }
}

// ---------- Voice input (mic button) ----------
(function setupMic() {
  const micBtn = document.getElementById('micBtn');
  const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognitionImpl) {
    micBtn.title = 'Voice input not supported in this browser';
    micBtn.classList.add('opacity-40', 'cursor-not-allowed');
    return;
  }
  const recognition = new SpeechRecognitionImpl();
  recognition.lang = 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  let listening = false;

  recognition.onstart = () => {
    listening = true;
    micBtn.classList.add('text-red-400', 'border-red-500');
  };
  recognition.onend = () => {
    listening = false;
    micBtn.classList.remove('text-red-400', 'border-red-500');
  };
  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    document.getElementById('chatInput').value = transcript;
  };
  recognition.onerror = () => {
    listening = false;
    micBtn.classList.remove('text-red-400', 'border-red-500');
  };

  micBtn.addEventListener('click', () => {
    if (listening) { recognition.stop(); return; }
    recognition.start();
  });
})();

document.getElementById('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;
  appendChatMessage('user', msg);
  input.value = '';
  appendChatMessage('ai', '...');
  const thinkingEl = document.getElementById('chatMessages').lastChild;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ message: msg, symbol: activeSymbol })
    });
    const data = await res.json();
    thinkingEl.textContent = data.reply;
    if (data.source) {
      const tag = document.createElement('div');
      tag.className = 'text-[10px] mt-1 ' + (data.source === 'claude' ? 'text-green-500' : 'text-gray-500');
      tag.textContent = data.source === 'claude' ? '● Claude API' : '● Fallback analyst (offline)';
      thinkingEl.appendChild(tag);
    }
    (data.commands || []).forEach(applyCommand);
  } catch (err) {
    thinkingEl.textContent = 'Connection error — check server logs.';
  }
});

// ---------- Scenario buttons ----------
document.querySelectorAll('.scenario-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const type = btn.dataset.type;
    const mag = parseFloat(btn.dataset.mag);
    const res = await fetch('/api/whatif', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ symbol: activeSymbol, scenario_type: type, magnitude: mag, days: 8 })
    });
    const data = await res.json();
    addDashedSeries(data.points, data.label, '#ff9900');
    appendChatMessage('ai', `Scenario applied: ${data.label} for ${data.symbol}.`);
  });
});

document.getElementById('clearOverlaysBtn').addEventListener('click', clearExtraSeries);

document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => loadSymbol(activeSymbol, btn.dataset.tf));
});

// ---------- Boot ----------
initChart();
loadSymbol(activeSymbol);
refreshQuotes();
refreshStatus();
setInterval(refreshQuotes, 15000);
setInterval(refreshStatus, 45000);
appendChatMessage('ai', 'AI desk assistant online. Ask me about a symbol, request a projection, or run a what-if scenario.');
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
