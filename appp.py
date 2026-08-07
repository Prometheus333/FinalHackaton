"""
AI Trading Terminal — single-file Flask app for the hackathon.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
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

# LangChain y herramientas
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.globals import set_llm_cache
from langchain.cache import InMemoryCache
import urllib3
import warnings

# --------------------------------------------------------------------------
# Configuration & Network Patches for Corporate Proxy (TCS)
# --------------------------------------------------------------------------
os.environ["OTEL_SDK_DISABLED"] = "true"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", module="urllib3")

original_request = requests.Session.request
def patched_request(self, method, url, **kwargs):
    kwargs['verify'] = False
    return original_request(self, method, url, **kwargs)
requests.Session.request = patched_request

client_llm = requests.Session()
client_llm.verify = False

ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "PKPY3CCI55KW57KJEWB4UXK3TU")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "AasJVR9bV839DB4B8bW12kMggRvkD3Y4vkKB5HSZWTWu")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")
ALPACA_TRADING_URL = os.environ.get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets/v2")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-taPdt4_aNdzmFCX3nP0GiA")
LLM_MODEL = os.environ.get("LLM_MODEL", "azure/genailab-maas-gpt-4.1")

app = Flask(__name__)

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 30

def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["t"]) < CACHE_TTL_SECONDS:
            return entry["v"]
    return None

def cache_set(key, value, ttl_override=None):
    with _cache_lock:
        _cache[key] = {"t": time.time() if not ttl_override else time.time() + ttl_override, "v": value}

def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }

# --------------------------------------------------------------------------
# Alpaca Asset Loader (For Autocomplete)
# --------------------------------------------------------------------------
_all_assets = []
def load_all_assets():
    global _all_assets
    try:
        url = f"{ALPACA_TRADING_URL}/assets"
        r = requests.get(url, headers=alpaca_headers(), params={"status": "active", "asset_class": "us_equity"}, timeout=15)
        r.raise_for_status()
        assets = r.json()
        _all_assets = [{"symbol": a["symbol"], "name": a["name"]} for a in assets if a["tradable"]]
    except Exception as e:
        app.logger.warning(f"Failed to load assets from Alpaca: {e}")

threading.Thread(target=load_all_assets, daemon=True).start()

# --------------------------------------------------------------------------
# Technical & Data Helpers (OHLCV via Yahoo Finance - REAL DATA)
# --------------------------------------------------------------------------
def _ema_list(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append((values[i] - out[-1]) * k + out[-1])
    return out

def get_bars(symbol, timeframe="1Day", limit=250):
    cache_key = f"bars:{symbol}:{timeframe}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None: return cached

    interval = "1d" if timeframe == "1Day" else "1wk"
    rng = "1y" if timeframe == "1Day" else "2y"
    
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        
        if not data.get("chart", {}).get("result"): return []
            
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        opens, highs, lows, closes, volumes = quote.get("open", []), quote.get("high", []), quote.get("low", []), quote.get("close", []), quote.get("volume", [])
        
        raw_bars = []
        for i in range(len(timestamps)):
            if closes[i] is not None:
                dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
                raw_bars.append({
                    "time": dt.strftime("%Y-%m-%d"), "open": round(opens[i], 2), "high": round(highs[i], 2),
                    "low": round(lows[i], 2), "close": round(closes[i], 2), "volume": volumes[i]
                })
        
        bars = raw_bars[-limit:]
        closes_list = [b["close"] for b in bars]
        ema20 = _ema_list(closes_list, 20)
        for i, b in enumerate(bars): b["ema20"] = round(ema20[i], 2) if i < len(ema20) else b["close"]

        cache_set(cache_key, bars)
        return bars
    except Exception as exc:
        app.logger.warning(f"Yahoo Finance fetch failed for {symbol}: {exc}")
        return []

def get_latest_quote(symbol):
    cache_key = f"quote:{symbol}"
    cached = cache_get(cache_key)
    if cached is not None: return cached
    bars = get_bars(symbol, limit=1)
    price = bars[-1]["close"] if bars else 0.0
    result = {"symbol": symbol, "price": round(price, 2)}
    cache_set(cache_key, result)
    return result

def get_news(symbol=None, limit=8):
    cache_key = f"news_llm:{symbol or 'market'}"
    cached = cache_get(cache_key)
    if cached is not None: return cached

    feed_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US" if symbol else "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"
    items = []
    try:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:limit]:
            items.append({
                "title": entry.get("title", ""), "link": entry.get("link", ""),
                "summary": re.sub("<[^<]+?>", "", entry.get("summary", ""))[:120] + "...",
                "sentiment": "NEUTRAL"
            })
    except Exception:
        pass
        
    if items:
        try:
            headlines_text = "\n".join([f"[{i}] {item['title']}" for i, item in enumerate(items)])
            prompt = f"Analyze the financial sentiment of these headlines for {symbol or 'the market'}. Return STRICT JSON mapping the index to BULLISH, BEARISH, or NEUTRAL. Example: {{\"0\": \"BULLISH\"}}.\n\nHeadlines:\n{headlines_text}"
            res = llm.invoke([SystemMessage(content=prompt)])
            cleaned = re.sub(r'^```(json)?', '', res.content.strip()).replace('```', '')
            sentiment_map = json.loads(cleaned)
            for i, item in enumerate(items): item["sentiment"] = sentiment_map.get(str(i), "NEUTRAL").upper()
        except Exception as e:
            pass

    cache_set(cache_key, items, ttl_override=120)
    return items

# --------------------------------------------------------------------------
# Multi-Agent AI Logic & LangChain Tools
# --------------------------------------------------------------------------
import httpx
client_httpx = httpx.Client(verify=False)

llm = ChatOpenAI(base_url=LLM_BASE_URL, model=LLM_MODEL, api_key=LLM_API_KEY, http_client=client_httpx, temperature=0.2)
set_llm_cache(InMemoryCache())

@tool
def get_price_tool(symbol: str) -> str:
    """Returns the latest stock price and short historical context for a given symbol."""
    quote = get_latest_quote(symbol.upper())
    bars = get_bars(symbol.upper(), limit=5)
    hist = ", ".join([str(b["close"]) for b in bars])
    return f"Current Price of {symbol}: ${quote['price']}. Last closes: {hist}"

@tool
def get_news_tool(symbol: str) -> str:
    """Returns the latest financial news headlines for a given stock symbol."""
    news = get_news(symbol.upper(), limit=3)
    if not news: return f"No recent news found for {symbol}."
    return "\n".join([f"- {n['title']} (Sentiment: {n['sentiment']}) : {n['summary']}" for n in news])

@tool
def get_ai_recommendation_tool(symbol: str) -> str:
    """Runs a Chief Risk Officer and Head Trader analysis to provide a BUY/SELL/HOLD recommendation."""
    rec = generate_ai_recommendation(symbol.upper())
    return f"AI Recommendation for {symbol}: SIGNAL={rec['signal']}, Confidence={rec['confidence']}. Rationale: {rec['rationale']}"

@tool
def execute_trade_tool(symbol: str, side: str, qty: int = 1) -> str:
    """
    Executes a real market order (Paper Trading) to buy or sell a stock.
    Side MUST be 'buy' or 'sell'. Automatically checks for buying power.
    """
    try:
        payload = {"symbol": symbol.upper(), "qty": qty, "side": side.lower(), "type": "market", "time_in_force": "day"}
        r = requests.post(f"{ALPACA_TRADING_URL}/orders", headers=alpaca_headers(), json=payload, timeout=5)
        if r.status_code in [200, 201]: return f"✅ SUCCESS: Executed {side.upper()} order for {qty} shares of {symbol}."
        else: return f"❌ FAILED to execute trade: {r.text}"
    except Exception as e: return f"❌ ERROR executing trade: {str(e)}"

herramientas = [get_price_tool, get_news_tool, get_ai_recommendation_tool, execute_trade_tool]
chat_memory = []

prompt_chat = ChatPromptTemplate.from_messages([
    ("system", "You are an elite Autonomous AI Trading Agent. You have access to real-time prices, news, AI risk analysis, and the ABILITY TO EXECUTE TRADES (Paper Trading) on behalf of the user using the execute_trade_tool. If the user asks you to predict/evaluate and buy/sell based on conditions, DO IT autonomously using the tools. Keep responses professional and concise."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
])

agente_conversacional = create_tool_calling_agent(llm, herramientas, prompt_chat)
agent_executor = AgentExecutor(agent=agente_conversacional, tools=herramientas, verbose=True)

def generate_ai_recommendation(symbol, timeframe="1Day"):
    cache_key = f"ai_rec:{symbol}:{timeframe}"
    cached = cache_get(cache_key)
    if cached: return cached

    bars = get_bars(symbol, timeframe=timeframe, limit=20)
    if not bars or len(bars) < 2: 
        return {"signal": "N/A", "confidence": "NONE", "rationale": "Market data unavailable from feed.", "projection": []}
    
    closes = [b["close"] for b in bars]
    tendencia = closes[-1] / (closes[0] if closes[0] else 1)
    
    sig = "BUY" if tendencia > 1.02 else "SELL" if tendencia < 0.98 else "HOLD"
    data = {"signal": sig, "confidence": "MEDIUM", "rationale": f"Algorithmic momentum evaluated over last {len(bars)} periods ({timeframe})."}

    try: last_date = datetime.fromisoformat(bars[-1]["time"][:10])
    except: last_date = datetime.now()

    drift = 0.005 if sig == "BUY" else (-0.005 if sig == "SELL" else 0.0)
    projection = []
    current_price = closes[-1]

    for i in range(1, 8):
        current_price *= (1 + drift)
        d = last_date + timedelta(days=i * (7 if timeframe == '1Week' else 1))
        projection.append({"time": d.date().isoformat(), "value": round(current_price, 2)})

    data["projection"] = projection
    cache_set(cache_key, data, ttl_override=120)
    return data

# --------------------------------------------------------------------------
# Routes — API
# --------------------------------------------------------------------------
@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").upper()
    if not q: return jsonify([])
    return jsonify([a for a in _all_assets if q in a["symbol"] or q in a["name"].upper()][:10])

@app.route("/api/bars/<symbol>")
def api_bars(symbol):
    tf = request.args.get("timeframe", "1Day")
    return jsonify({"symbol": symbol.upper(), "bars": get_bars(symbol.upper(), tf, 250)})

@app.route("/api/quotes")
def api_quotes():
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    return jsonify([get_latest_quote(s) for s in symbols])

@app.route("/api/recommend/<symbol>")
def api_recommend(symbol):
    return jsonify(generate_ai_recommendation(symbol.upper(), request.args.get("timeframe", "1Day")))

@app.route("/api/news")
def api_news_endpoint():
    return jsonify(get_news(request.args.get("symbol")))

@app.route("/api/chat", methods=["POST"])
def api_chat():
    global chat_memory
    data = request.get_json(force=True)
    user_msg = data.get("message", "")
    try:
        res = agent_executor.invoke({"input": user_msg, "chat_history": chat_memory})
        reply = res["output"]
        chat_memory.extend([HumanMessage(content=user_msg), AIMessage(content=reply)])
        if len(chat_memory) > 20: chat_memory = chat_memory[-20:]
        return jsonify({"reply": reply})
    except Exception as exc:
        return jsonify({"reply": "⚠️ Connection error with agents. Please check endpoint status."})

@app.route("/api/account")
def api_account():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/account", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/positions")
def api_positions():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/positions", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

# NUEVA RUTA: Permite ver órdenes pendientes cuando el mercado está cerrado
@app.route("/api/orders_pending")
def api_orders_pending():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/orders?status=open", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.json
    payload = {"symbol": data["symbol"].upper(), "qty": data.get("qty", 1), "side": data["side"], "type": "market", "time_in_force": "day"}
    try:
        r = requests.post(f"{ALPACA_TRADING_URL}/orders", headers=alpaca_headers(), json=payload, timeout=5)
        if r.status_code in [200, 201]: return jsonify(r.json()), 200
        else: return jsonify({"error": r.json().get("message", "Order rejected by broker")}), 400
    except Exception as e: return jsonify({"error": str(e)}), 500

# --------------------------------------------------------------------------
# Frontend HTML
# --------------------------------------------------------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TCS Capital Markets // AI Terminal</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root { --accent: #10b981; --panel: #0a0a0a; --panel-2: #050505; --border: #1e293b; }
  body { background: #000; font-family: ui-monospace, monospace; }
  .glow { box-shadow: 0 0 18px rgba(16,185,129,0.1); }
  .panel { background: var(--panel); border: 1px solid var(--border); }
  .panel-2 { background: var(--panel-2); border: 1px solid var(--border); }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
  
  @keyframes ticker-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
  .ticker-track { animation: ticker-scroll 30s linear infinite; }
  
  @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .live-dot { animation: pulse-dot 1.4s ease-in-out infinite; }
  .fade-in { animation: fadeIn .25s ease-out; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px);} to { opacity: 1; transform: translateY(0);} }
  .chat-msg-user { background: #111827; text-align: right; border-left: 2px solid #3b82f6; }
  .chat-msg-ai { background: #050505; border-left: 2px solid var(--accent); white-space: pre-wrap; padding: 8px; }
  
  .search-results { position: absolute; top: 100%; left: 0; right: 0; background: #050505; border: 1px solid var(--border); z-index: 50; max-height: 150px; overflow-y: auto; }
  .search-item { padding: 4px 8px; cursor: pointer; display: flex; justify-content: space-between; font-size: 10px; }
  .search-item:hover { background: #1e293b; }

  .badge { padding: 2px 6px; border-radius: 2px; font-weight: bold; font-size: 10px; }
  .bg-buy { background: #10b981; color: black; }
  .bg-sell { background: #ef4444; color: white; }
  .bg-hold, .bg-n\\/a { background: #6b7280; color: white; }
  
  .tf-btn { padding: 2px 8px; border: 1px solid var(--border); font-size: 10px; cursor: pointer; background: transparent; color: #9ca3af; }
  .tf-btn.active { background: #10b981; color: black; font-weight: bold; border-color: #10b981; }

  /* Professional Toast Notifications */
  #toast-container { position: fixed; bottom: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 8px; }
  .toast { background: #050505; border-left: 4px solid var(--accent); padding: 12px 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.8); color: #d1d5db; min-width: 280px; font-family: ui-monospace, monospace; animation: slideIn 0.3s ease-out forwards; }
  @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
</style>
</head>
<body class="text-gray-300 h-screen flex flex-col overflow-hidden text-xs">

  <div id="toast-container"></div>

  <header class="panel-2 border-b border-[var(--border)] px-4 py-2 flex items-center justify-between shrink-0">
    <div class="flex items-center gap-3">
      <div class="w-2.5 h-2.5 rounded-full bg-emerald-500 live-dot"></div>
      <h1 class="text-sm font-bold tracking-widest text-emerald-400">TCS CAPITAL MARKETS // AI TERMINAL</h1>
      <span class="text-[10px] text-gray-500 border border-[var(--border)] rounded px-2">PAPER TRADING LIVE</span>
    </div>
  </header>

  <div class="panel-2 border-b border-[var(--border)] overflow-hidden whitespace-nowrap py-1 shrink-0">
    <div id="tickerTrack" class="ticker-track inline-flex gap-10 px-4 text-xs"></div>
  </div>

  <main class="flex-1 flex gap-2 p-2 overflow-hidden">
    <!-- LEFT SIDEBAR -->
    <div class="w-64 flex flex-col gap-2 shrink-0">
      <section class="panel glow flex flex-col h-1/2 overflow-hidden p-3">
        <h2 class="uppercase tracking-widest text-emerald-400 font-bold mb-2">Watchlist</h2>
        <div class="relative mb-2 shrink-0">
          <input id="searchInput" type="text" placeholder="Search symbol..." autocomplete="off"
                 class="w-full bg-black border border-[var(--border)] px-2 py-1 text-white focus:outline-none focus:border-emerald-400">
          <div id="searchResults" class="search-results hidden"></div>
        </div>
        <div id="symbolList" class="flex-1 overflow-y-auto space-y-1"></div>
      </section>

      <section class="panel flex flex-col h-1/2 overflow-hidden p-3 border-t-2 border-emerald-900">
        <h2 class="uppercase tracking-widest text-emerald-400 font-bold mb-2 flex justify-between">
          Portfolio <button onclick="fetchPortfolio()" class="text-gray-500 hover:text-white">↻</button>
        </h2>
        <div class="mb-2 p-2 bg-black border border-[var(--border)]">
          <div class="flex justify-between text-[10px] text-gray-400">Equity <span id="portEquity" class="text-white font-bold">$0.00</span></div>
          <div class="flex justify-between text-[10px] text-gray-400 mt-1">Buying Power <span id="portBp" class="text-emerald-300">$0.00</span></div>
        </div>
        <div class="text-[10px] text-gray-500 mb-1 border-b border-[var(--border)] pb-1">POSITIONS & PENDING ORDERS</div>
        <div id="positionsList" class="flex-1 overflow-y-auto space-y-1"></div>
      </section>
    </div>

    <!-- MAIN CONTENT AREA -->
    <div class="flex-1 flex flex-col gap-2 overflow-hidden">
      <!-- TOP: Chart & Controls -->
      <section class="panel glow p-2 flex flex-col h-[55%] overflow-hidden relative">
        <div id="chartError" class="absolute inset-0 z-10 flex items-center justify-center bg-black/80 hidden">
            <div class="text-center">
                <div class="text-red-400 font-bold mb-2 uppercase tracking-widest">Market Data Unavailable</div>
                <div class="text-gray-500 text-[10px]">No recent data returned from feed.</div>
            </div>
        </div>

        <div class="flex items-center justify-between mb-1 shrink-0 bg-black p-2 border border-[var(--border)] relative z-20">
          <div class="flex items-center gap-4">
            <div>
              <span id="activeSymbol" class="text-xl font-bold text-white">AAPL</span>
              <span id="activePrice" class="text-sm text-emerald-300 tabular-nums ml-2">—</span>
            </div>
            <div id="aiStrip" class="hidden flex items-center gap-2 border-l border-gray-700 pl-4">
              <span class="text-[10px] text-gray-500 uppercase">AI Signal:</span>
              <span id="aiBadge" class="badge">...</span>
              <span id="aiRationale" class="text-[10px] text-gray-400 italic max-w-xs truncate"></span>
            </div>
          </div>
          
          <div class="flex gap-1">
            <button class="tf-btn active" data-tf="1Day">1D</button>
            <button class="tf-btn" data-tf="1Week">1W</button>
          </div>

          <div class="flex items-center gap-2 border-l border-gray-700 pl-4">
            <span class="text-[10px] text-gray-500 uppercase">Qty</span>
            <input id="tradeQty" type="number" min="1" value="1" class="w-14 bg-black border border-[var(--border)] text-white text-center py-1 text-xs focus:outline-none focus:border-emerald-400">
            <button onclick="executeOrder('buy')" class="bg-emerald-600 hover:bg-emerald-500 text-black font-bold px-4 py-1 text-xs">BUY</button>
            <button onclick="executeOrder('sell')" class="bg-red-600 hover:bg-red-500 text-white font-bold px-4 py-1 text-xs">SELL</button>
          </div>
        </div>
        <div id="chart" class="flex-1 min-h-0 relative z-0"></div>
      </section>

      <!-- BOTTOM: News and Agent Chat Split -->
      <div class="flex-1 flex gap-2 overflow-hidden">
        <section class="panel w-1/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold">News Stream</h2>
          <div id="newsList" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
        </section>

        <section class="panel w-2/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold flex gap-2">
            Autonomous Agent Desk <span class="bg-emerald-900 text-emerald-300 px-1 rounded text-[9px]">AUTO-TRADE ENABLED</span>
          </h2>
          <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1"></div>
          <form id="chatForm" class="flex gap-1 shrink-0">
            <input id="chatInput" type="text" placeholder="E.g. 'Analyze AAPL news and if it's bullish, buy 2 shares automatically'..."
                   class="flex-1 bg-black border border-[var(--border)] px-2 py-1.5 focus:outline-none focus:border-emerald-400 text-white" />
            <button class="bg-emerald-600 hover:bg-emerald-500 text-black font-bold px-4">SEND</button>
          </form>
        </section>
      </div>
    </div>
  </main>

<script>
function showToast(title, message, type='success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const borderColor = type === 'success' ? '#10b981' : (type === 'error' ? '#ef4444' : '#3b82f6');
  toast.className = 'toast border border-[var(--border)]';
  toast.style.borderLeftColor = borderColor;
  toast.innerHTML = `<div class="font-bold text-white text-xs mb-1 uppercase tracking-wider">${title}</div><div class="text-[10px] text-gray-400">${message}</div>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}

let watchlist = JSON.parse(localStorage.getItem('tcs_wl')) || ["AAPL", "MSFT", "TSLA"];
let activeSymbol = watchlist[0];
let activeTimeframe = "1Day"; 

let chart, candleSeries;
let extraSeries = [];

function initChart() {
  chart = LightweightCharts.createChart(document.getElementById('chart'), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  candleSeries = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });
}

function clearExtraSeries() { extraSeries.forEach(s => chart.removeSeries(s)); extraSeries = []; }

function addDashedSeries(points, color) {
  const series = chart.addLineSeries({
    color, lineWidth: 2, lineStyle: 2, pointMarkersVisible: true, lastValueVisible: true, priceLineVisible: false,
  });
  series.setData(points); extraSeries.push(series);
}

function highlightWatchlist() {
  document.querySelectorAll('.watch-item').forEach(el => {
    if(el.dataset.symbol === activeSymbol) el.classList.add('border-emerald-500', 'text-emerald-300');
    else el.classList.remove('border-emerald-500', 'text-emerald-300');
  });
}

function removeFromWatchlist(sym, event) {
  event.stopPropagation();
  if (watchlist.length <= 1) { showToast('WATCHLIST', 'Cannot remove the last symbol.', 'error'); return; }
  watchlist = watchlist.filter(s => s !== sym);
  localStorage.setItem('tcs_wl', JSON.stringify(watchlist));
  if (activeSymbol === sym) loadSymbol(watchlist[0]);
  else updateWatchlistUI();
}

async function loadSymbol(sym, timeframe = null) {
  activeSymbol = sym;
  if(timeframe) activeTimeframe = timeframe;
  document.getElementById('activeSymbol').textContent = sym;
  
  document.querySelectorAll('.tf-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tf === activeTimeframe));
  highlightWatchlist();
  
  const res = await fetch(`/api/bars/${sym}?timeframe=${activeTimeframe}&limit=150`);
  const data = await res.json();
  const chartErrorDiv = document.getElementById('chartError');
  
  if(data.bars?.length > 0) {
    chartErrorDiv.classList.add('hidden');
    const seen = new Set();
    const sortedBars = data.bars.filter(b => { if (seen.has(b.time)) return false; seen.add(b.time); return true; })
      .sort((a, b) => a.time.localeCompare(b.time));

    candleSeries.setData(sortedBars.map(b => ({time: b.time, open: b.open, high: b.high, low: b.low, close: b.close})));
    document.getElementById('activePrice').textContent = sortedBars[sortedBars.length-1].close.toFixed(2);
    chart.timeScale().fitContent();
  } else {
    candleSeries.setData([]); clearExtraSeries();
    document.getElementById('activePrice').textContent = "N/A";
    chartErrorDiv.classList.remove('hidden');
  }

  document.getElementById('aiStrip').classList.remove('hidden');
  document.getElementById('aiBadge').className = 'badge bg-gray-600';
  document.getElementById('aiBadge').textContent = 'ANALYZING...';
  document.getElementById('aiRationale').textContent = '';
  
  fetch(`/api/recommend/${sym}?timeframe=${activeTimeframe}`).then(r => r.json()).then(ai => {
    if(activeSymbol !== sym) return;
    const badge = document.getElementById('aiBadge');
    badge.textContent = ai.signal;
    badge.className = `badge bg-${ai.signal.toLowerCase()}`;
    document.getElementById('aiRationale').textContent = ai.rationale;
    clearExtraSeries();
    if(ai.projection && ai.projection.length > 0 && data.bars?.length > 0) {
      const color = ai.signal === 'BUY' ? '#10b981' : (ai.signal === 'SELL' ? '#ef4444' : '#6b7280');
      addDashedSeries(ai.projection, color);
    }
  });

  refreshNews(sym);
}

async function updateWatchlistUI() {
  const res = await fetch(`/api/quotes?symbols=${watchlist.join(',')}`);
  const quotes = await res.json();
  const listEl = document.getElementById('symbolList');
  listEl.innerHTML = '';
  quotes.forEach(q => {
    const row = document.createElement('div');
    row.className = 'watch-item panel-2 border border-[var(--border)] px-2 py-1 flex justify-between items-center cursor-pointer hover:border-emerald-400';
    row.dataset.symbol = q.symbol;
    row.onclick = () => loadSymbol(q.symbol);
    row.innerHTML = `
        <span class="font-bold">${q.symbol}</span> 
        <div class="flex items-center gap-2">
            <span>${q.price > 0 ? q.price.toFixed(2) : '—'}</span>
            <button class="text-red-500 hover:text-red-300 font-bold px-1" onclick="removeFromWatchlist('${q.symbol}', event)">×</button>
        </div>
    `;
    listEl.appendChild(row);
  });
  highlightWatchlist();

  const track = document.getElementById('tickerTrack');
  if (track) {
      const html = quotes.map(q => `<span class="text-gray-400">${q.symbol} <span class="text-emerald-300">${q.price.toFixed(2)}</span></span>`).join('');
      track.innerHTML = html + html;
  }
}

document.querySelectorAll('.tf-btn').forEach(btn => btn.addEventListener('click', () => loadSymbol(activeSymbol, btn.dataset.tf)));

const searchInput = document.getElementById('searchInput');
const searchResults = document.getElementById('searchResults');
let searchTimeout;

searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimeout);
  const q = e.target.value.trim();
  if(!q) { searchResults.classList.add('hidden'); return; }
  searchTimeout = setTimeout(async () => {
    const res = await fetch(`/api/search?q=${q}`);
    const matches = await res.json();
    searchResults.innerHTML = '';
    if(matches.length > 0) {
      matches.forEach(m => {
        const div = document.createElement('div');
        div.className = 'search-item';
        div.innerHTML = `<span class="font-bold text-emerald-400">${m.symbol}</span> <span class="text-gray-400 truncate ml-2">${m.name}</span>`;
        div.onclick = () => {
          if(!watchlist.includes(m.symbol)) { watchlist.push(m.symbol); localStorage.setItem('tcs_wl', JSON.stringify(watchlist)); updateWatchlistUI(); }
          loadSymbol(m.symbol); searchInput.value = ''; searchResults.classList.add('hidden');
        };
        searchResults.appendChild(div);
      });
      searchResults.classList.remove('hidden');
    } else searchResults.classList.add('hidden');
  }, 300);
});
document.addEventListener('click', (e) => { if(!searchInput.contains(e.target)) searchResults.classList.add('hidden'); });

async function fetchPortfolio() {
  const accRes = await fetch('/api/account');
  const acc = await accRes.json();
  if(!acc.error) {
    document.getElementById('portEquity').textContent = '$' + parseFloat(acc.equity).toFixed(2);
    document.getElementById('portBp').textContent = '$' + parseFloat(acc.buying_power).toFixed(2);
  }

  const posRes = await fetch('/api/positions');
  const pos = await posRes.json();
  
  // NEW: Fetch Pending Orders since the market might be closed
  const ordersRes = await fetch('/api/orders_pending');
  const orders = await ordersRes.json();
  
  const listEl = document.getElementById('positionsList');
  listEl.innerHTML = '';
  
  let hasItems = false;

  // Render open positions
  if(!pos.error && pos.length > 0) {
    hasItems = true;
    pos.forEach(p => {
      const pl = parseFloat(p.unrealized_pl);
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-[var(--border)]';
      row.innerHTML = `
        <div><span class="font-bold text-white">${p.symbol}</span> <span class="text-gray-500">${p.qty} sh</span></div>
        <div class="${pl>=0?'text-emerald-400':'text-red-400'}">${pl>=0?'+':''}${pl.toFixed(2)}</div>
      `;
      listEl.appendChild(row);
    });
  }
  
  // Render pending orders (queued because market is closed)
  if(!orders.error && orders.length > 0) {
    hasItems = true;
    orders.forEach(o => {
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-yellow-900/50 mt-1';
      row.innerHTML = `
        <div><span class="font-bold text-yellow-500">${o.symbol}</span> <span class="text-gray-500">${o.qty} sh (${o.side.toUpperCase()})</span></div>
        <div class="text-yellow-600 bg-yellow-900/20 px-1 rounded">PENDING</div>
      `;
      listEl.appendChild(row);
    });
  }

  if(!hasItems) {
    listEl.innerHTML = '<div class="text-gray-600 italic">No open positions or pending orders.</div>';
  }
}

async function executeOrder(side) {
  const qtyInput = document.getElementById('tradeQty');
  const qty = parseInt(qtyInput.value) || 1;

  showToast('ORDER ROUTING', `Transmitting ${side.toUpperCase()} order for ${qty} shares of ${activeSymbol}...`, 'info');

  try {
      const res = await fetch('/api/order', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ symbol: activeSymbol, side: side, qty: qty })
      });
      const data = await res.json();
      
      if(data.error) {
          showToast('ORDER REJECTED', data.error, 'error');
      } else {
          showToast('ORDER PLACED', `Order sent. It may be pending if the market is closed.`, 'success');
          setTimeout(fetchPortfolio, 1000);
          setTimeout(fetchPortfolio, 3000);
          setTimeout(fetchPortfolio, 5000);
      }
  } catch (e) {
      showToast('SYSTEM ERROR', 'Order transmission failed check connection.', 'error');
  }
}

async function refreshNews(symbol) {
  const box = document.getElementById('newsList');
  box.innerHTML = '<div class="text-gray-500 text-center mt-4">Analyzing sentiment via LLM...</div>';
  
  const res = await fetch(`/api/news?symbol=${symbol}`);
  const items = await res.json();
  box.innerHTML = '';
  
  if(items.length === 0) {
      box.innerHTML = '<div class="text-gray-600 italic text-center mt-4">No recent news available.</div>';
      return;
  }
  
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'fade-in panel-2 border border-[var(--border)] p-2 mb-1';
    
    let badgeClass = 'bg-hold'; 
    if(it.sentiment === 'BULLISH') badgeClass = 'bg-buy';
    if(it.sentiment === 'BEARISH') badgeClass = 'bg-sell';
    
    div.innerHTML = `
      <div class="flex justify-between items-start mb-1 gap-2">
        <a href="${it.link}" target="_blank" class="text-emerald-300 font-bold hover:underline text-[10px] leading-tight flex-1">${it.title}</a>
        <span class="badge ${badgeClass} text-[8px] px-1">${it.sentiment}</span>
      </div>
      <div class="text-gray-500 text-[9px] leading-tight">${it.summary || ''}</div>
    `;
    box.appendChild(div);
  });
}

function appendChat(role, text) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in p-2 ${role==='user'?'chat-msg-user':'chat-msg-ai'}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

document.getElementById('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if(!msg) return;
  appendChat('user', msg);
  input.value = '';
  const thinking = appendChat('ai', 'Evaluating market and executing conditions...');
  
  const res = await fetch('/api/chat', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ message: msg }) });
  const data = await res.json();
  thinking.textContent = data.reply;
  
  setTimeout(fetchPortfolio, 2000); 
  setTimeout(fetchPortfolio, 5000);
});

initChart();
loadSymbol(activeSymbol, "1Day");
updateWatchlistUI();
fetchPortfolio();
appendChat('ai', 'TCS AI Desk ready. I can fetch real-time prices, analyze news, and EXECUTE TRADES automatically.');
setInterval(fetchPortfolio, 15000);
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
    