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

# Importaciones de LangChain y herramientas
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_tool_calling_agent, AgentExecutor
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

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-taPdt4_aNdzmFCX3nP0GiA")
LLM_MODEL = os.environ.get("LLM_MODEL", "azure/genailab-maas-gpt-4.1")

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "SPY", "TSLA", "NVDA"]

app = Flask(__name__)

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 20

def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["t"]) < CACHE_TTL_SECONDS:
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

def get_bars(symbol, timeframe="1Day", limit=250):
    cache_key = f"bars:{symbol}:{timeframe}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{ALPACA_DATA_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "feed": "iex",
        "adjustment": "raw",
    }
    try:
        r = requests.get(url, headers=alpaca_headers(), params=params, timeout=10)
        r.raise_for_status()
        raw_bars = r.json().get("bars", [])
        if not raw_bars:
            raise ValueError("No bars returned from API")
        bars = [
            {
                "time": b["t"][:10] if timeframe in ["1Day", "1Week"] else b["t"],
                "open": b["o"],
                "high": b["h"],
                "low": b["l"],
                "close": b["c"],
                "volume": b["v"],
            }
            for b in raw_bars
        ]
        cache_set(cache_key, bars)
        return bars
    except Exception as exc:
        bars = _synthetic_bars(symbol, limit)
        cache_set(cache_key, bars)
        app.logger.warning(f"Alpaca bars fetch failed for {symbol}: {exc}. Using synthetic data.")
        return bars

def get_latest_quote(symbol):
    cache_key = f"quote:{symbol}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{ALPACA_DATA_URL}/stocks/{symbol}/quotes/latest"
    try:
        r = requests.get(url, headers=alpaca_headers(), params={"feed": "iex"}, timeout=8)
        r.raise_for_status()
        q = r.json().get("quote", {})
        price = q.get("ap") or q.get("bp") or 0
        if not price:
            bars = get_bars(symbol, limit=2)
            price = bars[-1]["close"] if bars else 100.0
        result = {"symbol": symbol, "price": round(float(price), 2), "time": q.get("t")}
        cache_set(cache_key, result)
        return result
    except Exception:
        bars = get_bars(symbol, limit=2)
        last = bars[-1]["close"] if bars else 100.0
        result = {"symbol": symbol, "price": round(last, 2), "time": None}
        cache_set(cache_key, result)
        return result

def _synthetic_bars(symbol, limit):
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
# News
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
            "summary": "The RSS feed may be temporarily unreachable.",
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
# LangChain Multi-Agent Integration
# --------------------------------------------------------------------------
import httpx
client_httpx = httpx.Client(verify=False)

llm = ChatOpenAI(
    base_url=LLM_BASE_URL,
    model=LLM_MODEL,
    api_key=LLM_API_KEY,
    http_client=client_httpx,
    temperature=0.2
)

@tool
def obtener_datos_ohlcv(simbolo: str) -> str:
    """Extracts OHLCV historical data for the last 30 days of a stock."""
    bars = get_bars(simbolo, limit=30)
    return f"Data for {simbolo}:\n{str(bars[-10:])}"

herramientas = [obtener_datos_ohlcv]

prompt_datos = ChatPromptTemplate.from_messages([
    ("system", "You are a data engineer. Extract raw data and deliver it structured."),
    ("user", "{input}"),
    ("placeholder", "{agent_scratchpad}")
])
prompt_riesgos = ChatPromptTemplate.from_messages([
    ("system", "You are a Quantitative Risk Analyst. Generate a quick report evaluating volatility and state an explicit RISK LEVEL (LOW, MEDIUM, HIGH)."),
    ("user", "Analyze the following data:\n\n{datos_mercado}")
])
prompt_estratega = ChatPromptTemplate.from_messages([
    ("system", "You are a senior algorithmic trader. Recommend a clear action (BUY, SELL, HOLD) and a brief technical justification."),
    ("user", "DATA:\n{datos_mercado}\n\nRISK:\n{reporte_riesgo}")
])

agente_extractor = create_tool_calling_agent(llm, herramientas, prompt_datos)
extractor_executor = AgentExecutor(agent=agente_extractor, tools=herramientas, verbose=False)
cadena_riesgos = prompt_riesgos | llm
cadena_estratega = prompt_estratega | llm

def _project_points(bars, days, pct_drift_per_day):
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

def call_multi_agents(user_message, symbol, bars):
    try:
        res_ext = extractor_executor.invoke({"input": f"Extract last 30 days for {symbol}."})
        datos = res_ext["output"]

        res_riesgo = cadena_riesgos.invoke({"datos_mercado": datos})
        riesgo = res_riesgo.content

        res_estratega = cadena_estratega.invoke({"datos_mercado": datos, "reporte_riesgo": riesgo})
        estrategia = res_estratega.content

        reply = f"【 MULTI-AGENT ANALYSIS: {symbol} 】\n\n📌 RISK AUDIT:\n{riesgo}\n\n💡 STRATEGY:\n{estrategia}"
        
        recent = [b["close"] for b in bars[-10:]]
        trend = (recent[-1] - recent[0]) / recent[0] if recent[0] else 0
        drift = 0.004 if trend >= 0 else -0.003
        pts = _project_points(bars, 8, drift)

        commands = [
            {"action": "add_projection", "symbol": symbol, "label": "AI Multi-Agent Target", "points": pts}
        ]
        return {"reply": reply, "commands": commands}
    except Exception as exc:
        app.logger.warning(f"Agent execution failed for {symbol}: {exc}")
        return {"reply": f"Agent execution warning for {symbol}: Unable to synthesize live multi-agent response. Please check API key or network status.", "commands": []}

# --------------------------------------------------------------------------
# Routes — API
# --------------------------------------------------------------------------
@app.route("/api/bars/<symbol>")
def api_bars(symbol):
    timeframe = request.args.get("timeframe", "1Day")
    limit = int(request.args.get("limit", 250))
    bars = get_bars(symbol.upper(), timeframe=timeframe, limit=limit)
    return jsonify({"symbol": symbol.upper(), "bars": bars})

@app.route("/api/quote/<symbol>")
def api_quote(symbol):
    return jsonify(get_latest_quote(symbol.upper()))

@app.route("/api/quotes")
def api_quotes():
    symbols_str = request.args.get("symbols", ",".join(DEFAULT_SYMBOLS))
    symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
    return jsonify([get_latest_quote(s) for s in symbols])

@app.route("/api/validate/<symbol>")
def api_validate(symbol):
    try:
        quote = get_latest_quote(symbol.upper())
        if quote and quote.get("price", 0) > 0:
            return jsonify({"exists": True, "symbol": symbol.upper()})
        return jsonify({"exists": False}), 404
    except Exception:
        return jsonify({"exists": False}), 404

@app.route("/api/news")
def api_news():
    symbol = request.args.get("symbol")
    keywords = request.args.get("keywords")
    items = get_news(symbol)
    if keywords:
        items = filter_news_by_keywords(items, keywords.split(","))
    return jsonify(items)

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    message = data.get("message", "")
    symbol = data.get("symbol", "AAPL").upper()
    bars = get_bars(symbol, limit=60)
    result = call_multi_agents(message, symbol, bars)
    return jsonify(result)

# --------------------------------------------------------------------------
# Frontend HTML (Bloomberg Terminal Style - Cross Layout 2x2 - English)
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
  :root {
    --accent: #10b981;
    --accent-blue: #33c9ff;
    --panel: #0a0a0a;
    --panel-2: #050505;
    --border: #1e293b;
  }
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
  .chat-msg-ai { background: #050505; border-left: 2px solid var(--accent); white-space: pre-wrap; }
  .tf-btn.active { background: #10b981; color: #000; font-weight: bold; border-color: #10b981; }
</style>
</head>
<body class="text-gray-300 h-screen flex flex-col overflow-hidden">

  <!-- Top bar -->
  <header class="panel-2 border-b border-[var(--border)] px-4 py-2 flex items-center justify-between shrink-0">
    <div class="flex items-center gap-3">
      <div class="w-2.5 h-2.5 rounded-full bg-emerald-500 live-dot"></div>
      <h1 class="text-base font-bold tracking-widest text-emerald-400">TCS CAPITAL MARKETS // AI TERMINAL</h1>
      <span class="text-xs text-gray-500 border border-[var(--border)] rounded px-2 py-0.5">GENAI LAB CONNECTED</span>
    </div>
    <div id="clock" class="text-xs text-emerald-300 tabular-nums"></div>
  </header>

  <!-- Ticker tape -->
  <div class="panel-2 border-b border-[var(--border)] overflow-hidden whitespace-nowrap py-1 shrink-0">
    <div id="tickerTrack" class="ticker-track inline-flex gap-10 px-4 text-xs"></div>
  </div>

  <!-- Main layout: Cross Layout (Grid 2x2) -->
  <main class="flex-1 grid grid-cols-2 grid-rows-2 gap-2 p-2 overflow-hidden">

    <!-- CUADRANTE 1 (Top-Left): Watchlist Dinámica con Add/Remove -->
    <section class="panel glow rounded-none p-3 flex flex-col overflow-hidden">
      <div class="flex justify-between items-center mb-2 shrink-0">
        <h2 class="text-xs uppercase tracking-widest text-emerald-400 font-bold">Watchlist</h2>
        <form id="addForm" class="flex gap-1">
          <input id="addInput" type="text" placeholder="TICKER" class="w-16 bg-black border border-[var(--border)] px-1 text-[10px] text-white uppercase focus:outline-none focus:border-emerald-400">
          <button class="bg-emerald-600 hover:bg-emerald-500 text-black text-xs px-2 font-bold">+</button>
        </form>
      </div>
      <div id="symbolList" class="flex-1 overflow-y-auto space-y-1"></div>
      <div class="mt-2 pt-2 border-t border-[var(--border)] shrink-0">
        <h2 class="text-[10px] uppercase tracking-widest text-cyan-300 mb-1 font-bold">Overlays</h2>
        <div id="overlayList" class="flex flex-wrap gap-1 text-[10px]"></div>
      </div>
    </section>

    <!-- CUADRANTE 2 (Top-Right): Gráfico de Precios con Múltiples Timeframes -->
    <section class="panel glow rounded-none p-3 flex flex-col overflow-hidden">
      <div class="flex items-center justify-between mb-1 shrink-0">
        <div class="flex items-center gap-2">
          <span id="activeSymbol" class="text-lg font-bold text-white">AAPL</span>
          <span id="activePrice" class="text-sm text-emerald-300 tabular-nums">—</span>
          <span id="activeChange" class="text-xs tabular-nums">—</span>
        </div>
        <div class="flex gap-1 text-[10px]">
          <button data-tf="1Min" class="tf-btn px-1.5 py-0.5 border border-[var(--border)] hover:border-emerald-400">1H</button>
          <button data-tf="1Day" class="tf-btn active px-1.5 py-0.5 border border-[var(--border)] hover:border-emerald-400">1D</button>
          <button data-tf="1Week" class="tf-btn px-1.5 py-0.5 border border-[var(--border)] hover:border-emerald-400">1W</button>
          <button data-tf="1Month" class="tf-btn px-1.5 py-0.5 border border-[var(--border)] hover:border-emerald-400">1M</button>
          <button id="clearOverlaysBtn" class="px-1.5 py-0.5 border border-[var(--border)] text-red-400 hover:border-red-400">Clear</button>
        </div>
      </div>
      <div id="chart" class="flex-1 min-h-0"></div>
      <div id="legend" class="mt-1 flex flex-wrap gap-2 text-[10px] text-gray-400 shrink-0"></div>
    </section>

    <!-- CUADRANTE 3 (Bottom-Left): Multi-Agent AI Desk (Chat) -->
    <section class="panel rounded-none p-3 flex flex-col overflow-hidden border-emerald-900/40">
      <h2 class="text-xs uppercase tracking-widest text-emerald-400 mb-2 font-bold">Multi-Agent AI Desk</h2>
      <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1 text-xs"></div>
      <form id="chatForm" class="flex gap-1 shrink-0">
        <input id="chatInput" type="text" placeholder="Consult agents..."
               class="flex-1 bg-black border border-[var(--border)] px-2 py-1.5 text-xs focus:outline-none focus:border-emerald-400 text-white" />
        <button class="bg-emerald-600 hover:bg-emerald-500 text-black text-xs font-bold px-3">RUN</button>
      </form>
    </section>

    <!-- CUADRANTE 4 (Bottom-Right): News Stream -->
    <section class="panel-2 rounded-none p-3 flex flex-col overflow-hidden">
      <h2 class="text-xs uppercase tracking-widest text-emerald-400 mb-2 font-bold">News Stream</h2>
      <div id="newsList" class="flex-1 overflow-y-auto space-y-2 text-xs pr-1"></div>
    </section>

  </main>

<script>
let watchlist = ["AAPL", "MSFT", "SPY", "TSLA", "NVDA"];
let activeSymbol = "AAPL";
let activeTimeframe = "1Day";
let overlaySeries = {};
let extraSeries = [];
let candleSeries, chart;

function tickClock() {
  document.getElementById('clock').textContent = new Date().toUTCString().slice(0,25) + " UTC";
}
setInterval(tickClock, 1000); tickClock();

function initChart() {
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    rightPriceScale: { borderColor: '#1e293b' },
    timeScale: { borderColor: '#1e293b' },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: '#10b981', downColor: '#ef4444',
    borderVisible: false,
    wickUpColor: '#10b981', wickDownColor: '#ef4444',
  });
}

async function loadSymbol(symbol, timeframe=null) {
  activeSymbol = symbol.toUpperCase();
  if(timeframe) activeTimeframe = timeframe;
  document.getElementById('activeSymbol').textContent = activeSymbol;

  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tf === activeTimeframe);
  });

  const res = await fetch(`/api/bars/${activeSymbol}?timeframe=${activeTimeframe}&limit=250`);
  const data = await res.json();
  if(data.bars && data.bars.length > 0) {
    candleSeries.setData(data.bars.map(b => ({time: b.time, open: b.open, high: b.high, low: b.low, close: b.close})));
    clearExtraSeries();
    updatePriceHeader(data.bars);
    refreshNews(activeSymbol);
    highlightWatchlist(activeSymbol);
    chart.timeScale().fitContent();
  }
}

function updatePriceHeader(bars) {
  if (!bars.length) return;
  const last = bars[bars.length-1];
  const prev = bars.length > 1 ? bars[bars.length-2] : last;
  const chg = last.close - prev.close;
  const pct = prev.close ? (chg/prev.close*100) : 0;
  document.getElementById('activePrice').textContent = last.close.toFixed(2);
  const chEl = document.getElementById('activeChange');
  chEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${pct.toFixed(2)}%)`;
  chEl.className = 'text-xs tabular-nums ' + (chg >= 0 ? 'text-emerald-400' : 'text-red-400');
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
  chip.innerHTML = `<span class="inline-block w-2 h-0.5" style="background:${color}"></span> ${label}`;
  legend.appendChild(chip);
}

function highlightWatchlist(symbol) {
  document.querySelectorAll('.watch-item').forEach(el => {
    el.classList.toggle('border-emerald-400', el.dataset.symbol === symbol);
    el.classList.toggle('text-emerald-300', el.dataset.symbol === symbol);
  });
}

async function updateWatchlistUI() {
  const res = await fetch(`/api/quotes?symbols=${watchlist.join(',')}`);
  const quotes = await res.json();
  const listEl = document.getElementById('symbolList');
  listEl.innerHTML = '';
  
  quotes.forEach(q => {
    const row = document.createElement('div');
    row.className = 'watch-item panel-2 border border-[var(--border)] px-2 py-1.5 flex justify-between items-center text-xs cursor-pointer hover:border-emerald-400';
    row.dataset.symbol = q.symbol;
    row.innerHTML = `
      <span class="font-semibold" onclick="loadSymbol('${q.symbol}')">${q.symbol}</span>
      <div class="flex items-center gap-2">
        <span class="text-emerald-300 tabular-nums" onclick="loadSymbol('${q.symbol}')">${q.price.toFixed(2)}</span>
        <button class="text-red-500 hover:text-red-300 px-1 font-bold" onclick="removeFromWatchlist('${q.symbol}', event)">×</button>
      </div>
    `;
    listEl.appendChild(row);
  });
  highlightWatchlist(activeSymbol);

  const track = document.getElementById('tickerTrack');
  const html = quotes.map(q => `<span class="text-gray-400">${q.symbol} <span class="text-emerald-300">${q.price.toFixed(2)}</span></span>`).join('');
  track.innerHTML = html + html;
}

function removeFromWatchlist(sym, event) {
  event.stopPropagation();
  if (watchlist.length <= 1) {
    alert("Watchlist must contain at least one symbol.");
    return;
  }
  watchlist = watchlist.filter(s => s !== sym);
  if (activeSymbol === sym) {
    loadSymbol(watchlist[0]);
  }
  updateWatchlistUI();
}

document.getElementById('addForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('addInput');
  const sym = input.value.trim().toUpperCase();
  if(!sym) return;

  const validateRes = await fetch(`/api/validate/${sym}`);
  if(validateRes.ok) {
    if(!watchlist.includes(sym)) watchlist.push(sym);
    updateWatchlistUI();
    input.value = '';
    loadSymbol(sym);
  } else {
    alert(`Ticker "${sym}" not found or invalid market data.`);
  }
});

async function refreshNews(symbol, keywords) {
  let url = `/api/news?symbol=${symbol}`;
  if (keywords) url += `&keywords=${encodeURIComponent(keywords.join(','))}`;
  const res = await fetch(url);
  const items = await res.json();
  const box = document.getElementById('newsList');
  box.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'fade-in panel-2 border border-[var(--border)] p-2';
    div.innerHTML = `<a href="${it.link}" target="_blank" class="text-emerald-300 hover:underline">${it.title}</a>
                     <div class="text-gray-500 text-[10px] mt-0.5">${it.summary || ''}</div>`;
    box.appendChild(div);
  });
}

function appendChatMessage(role, text) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in px-2 py-1.5 text-xs ${role === 'user' ? 'chat-msg-user text-right' : 'chat-msg-ai'}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function applyCommand(cmd) {
  switch (cmd.action) {
    case 'add_projection':
      addDashedSeries(cmd.points, cmd.label || 'AI Target', '#10b981');
      break;
    case 'clear_projections':
      clearExtraSeries();
      break;
  }
}

document.getElementById('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;
  appendChatMessage('user', msg);
  input.value = '';
  appendChatMessage('ai', `Running agents for ${activeSymbol}...`);
  const thinkingEl = document.getElementById('chatMessages').lastChild;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ message: msg, symbol: activeSymbol })
    });
    const data = await res.json();
    thinkingEl.textContent = data.reply;
    (data.commands || []).forEach(applyCommand);
  } catch (err) {
    thinkingEl.textContent = 'Connection error with agents. Please try again.';
  }
});

document.getElementById('clearOverlaysBtn').addEventListener('click', clearExtraSeries);

document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => loadSymbol(activeSymbol, btn.dataset.tf));
});

initChart();
loadSymbol(activeSymbol, "1Day");
updateWatchlistUI();
setInterval(updateWatchlistUI, 20000);
appendChatMessage('ai', 'Multi-agent terminal online. Select or add a ticker from the watchlist to begin.');
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
"""
AI Trading Terminal — single-file Flask app for the hackathon.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000

SECURITY NOTE:
All secrets below are read from environment variables first, with the
hackathon-provided values as a fallback so the app runs out of the box.
Before sharing this repo or deploying anywhere public, rotate the Alpaca
secret key and the LLM API key, and set real env vars instead of relying
on the fallback constants.
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
# Configuration
# --------------------------------------------------------------------------

ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "PKPY3CCI55KW57KJEWB4UXK3TU")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "AasJVR9bV839DB4B8bW12kMggRvkD3Y4vkKB5HSZWTWu")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")

# LLM endpoint. Defaults to an OpenAI-compatible custom gateway; swap the
# base URL / model via env vars if you're pointing at a different provider.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-taPdt4_aNdzmFCX3nP0GiA")
LLM_MODEL = os.environ.get("LLM_MODEL", "azure/genailab-maas-gpt-4.1")

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "SPY", "TSLA", "NVDA"]

app = Flask(__name__)

# Simple in-memory cache so we don't hammer Alpaca / RSS on every poll.
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 20


def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["t"]) < CACHE_TTL_SECONDS:
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
    """Fetch OHLC bars for a symbol from Alpaca's free IEX feed."""
    cache_key = f"bars:{symbol}:{timeframe}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

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
        cache_set(cache_key, bars)
        return bars
    except Exception as exc:
        # Fall back to a deterministic synthetic series so the demo never
        # breaks on stage because of API-key / network issues.
        bars = _synthetic_bars(symbol, limit)
        cache_set(cache_key, bars)
        app.logger.warning(f"Alpaca bars fetch failed for {symbol}: {exc}. Using synthetic data.")
        return bars


def get_latest_quote(symbol):
    cache_key = f"quote:{symbol}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{ALPACA_DATA_URL}/stocks/{symbol}/quotes/latest"
    try:
        r = requests.get(url, headers=alpaca_headers(), params={"feed": "iex"}, timeout=8)
        r.raise_for_status()
        q = r.json().get("quote", {})
        price = q.get("ap") or q.get("bp") or 0
        result = {"symbol": symbol, "price": round(price, 2), "time": q.get("t")}
        cache_set(cache_key, result)
        return result
    except Exception:
        bars = get_bars(symbol, limit=2)
        last = bars[-1]["close"] if bars else 100.0
        result = {"symbol": symbol, "price": round(last, 2), "time": None}
        cache_set(cache_key, result)
        return result


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
    """Calls the configured OpenAI-compatible LLM. Falls back to a
    rule-based mock so the demo still works if the endpoint/key is down."""
    last_close = bars[-1]["close"] if bars else 100.0
    last_date = bars[-1]["time"] if bars else datetime.now().date().isoformat()
    context = {
        "symbol": context_symbol,
        "last_close": last_close,
        "last_date": last_date,
        "recent_closes": [b["close"] for b in bars[-10:]],
    }

    try:
        from openai import OpenAI
        client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        completion = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Context: {json.dumps(context)}\n\nUser message: {user_message}"},
            ],
            temperature=0.4,
            max_tokens=800,
        )
        raw = completion.choices[0].message.content
        parsed = _extract_json(raw)
        if parsed and "reply" in parsed:
            parsed.setdefault("commands", [])
            return parsed
        raise ValueError("LLM response did not contain valid JSON")
    except Exception as exc:
        app.logger.warning(f"LLM call failed, using fallback analyst: {exc}")
        return _mock_ai_response(user_message, context_symbol, bars)


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
    bars = get_bars(symbol.upper(), timeframe=timeframe, limit=limit)
    return jsonify({"symbol": symbol.upper(), "bars": bars})


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


@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    data = request.get_json(force=True)
    symbol = data.get("symbol", "AAPL").upper()
    scenario_type = data.get("scenario_type", "rate_change")
    magnitude = float(data.get("magnitude", 0.5))  # e.g. 0.5 => 0.5% rate move, or 10 => 10% revenue drop
    days = int(data.get("days", 8))

    bars = get_bars(symbol, limit=60)

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
    bars = get_bars(symbol, limit=60)
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
<body class="text-gray-200 min-h-screen flex flex-col">

  <!-- Top bar -->
  <header class="panel-2 border-b border-[var(--border)] px-4 py-2 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-2.5 h-2.5 rounded-full bg-orange-500 live-dot"></div>
      <h1 class="text-lg font-bold tracking-widest text-orange-400">AI TRADING TERMINAL</h1>
      <span class="text-xs text-gray-500 border border-[var(--border)] rounded px-2 py-0.5">PAPER · SIMULATED</span>
    </div>
    <div id="clock" class="text-xs text-cyan-300 tabular-nums"></div>
  </header>

  <!-- Ticker tape -->
  <div class="panel-2 border-b border-[var(--border)] overflow-hidden whitespace-nowrap py-1.5">
    <div id="tickerTrack" class="ticker-track inline-flex gap-10 px-4 text-sm"></div>
  </div>

  <!-- Main layout -->
  <main class="flex-1 grid grid-cols-12 gap-3 p-3 overflow-hidden">

    <!-- Left: symbol list -->
    <aside class="col-span-2 panel glow rounded-lg p-3 flex flex-col overflow-hidden">
      <h2 class="text-xs uppercase tracking-widest text-orange-400 mb-2">Watchlist</h2>
      <div id="symbolList" class="flex-1 overflow-y-auto space-y-1"></div>
      <div class="mt-3 pt-3 border-t border-[var(--border)]">
        <h2 class="text-xs uppercase tracking-widest text-cyan-300 mb-2">Overlays</h2>
        <div id="overlayList" class="flex flex-wrap gap-1 text-xs"></div>
      </div>
    </aside>

    <!-- Center: chart + scenarios -->
    <section class="col-span-7 flex flex-col gap-3 overflow-hidden">
      <div class="panel glow rounded-lg p-3 flex-1 flex flex-col min-h-0">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <span id="activeSymbol" class="text-xl font-bold text-white">AAPL</span>
            <span id="activePrice" class="text-lg text-cyan-300 tabular-nums">—</span>
            <span id="activeChange" class="text-sm tabular-nums">—</span>
          </div>
          <div class="flex gap-1 text-xs">
            <button data-tf="1Day" class="tf-btn px-2 py-1 rounded border border-[var(--border)] hover:border-orange-400">1D</button>
            <button data-tf="1Hour" class="tf-btn px-2 py-1 rounded border border-[var(--border)] hover:border-orange-400">1H</button>
            <button id="clearOverlaysBtn" class="px-2 py-1 rounded border border-[var(--border)] hover:border-red-400 text-red-300">Clear Projections</button>
          </div>
        </div>
        <div id="chart" class="flex-1 min-h-0"></div>
        <div id="legend" class="mt-2 flex flex-wrap gap-3 text-xs text-gray-400"></div>
      </div>

      <div class="panel-2 rounded-lg p-3">
        <h2 class="text-xs uppercase tracking-widest text-orange-400 mb-2">What-if Scenario Modeling</h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
          <button class="scenario-btn panel border border-[var(--border)] rounded px-2 py-2 text-xs text-left"
                  data-type="rate_change" data-mag="0.5">
            Rates +0.5%
          </button>
          <button class="scenario-btn panel border border-[var(--border)] rounded px-2 py-2 text-xs text-left"
                  data-type="rate_change" data-mag="-0.5">
            Rates −0.5%
          </button>
          <button class="scenario-btn panel border border-[var(--border)] rounded px-2 py-2 text-xs text-left"
                  data-type="revenue_change" data-mag="-10">
            Revenue −10%
          </button>
          <button class="scenario-btn panel border border-[var(--border)] rounded px-2 py-2 text-xs text-left"
                  data-type="revenue_change" data-mag="15">
            Revenue +15%
          </button>
        </div>
      </div>
    </section>

    <!-- Right: AI chat + news -->
    <section class="col-span-3 flex flex-col gap-3 overflow-hidden">
      <div class="panel glow-blue rounded-lg p-3 flex flex-col flex-1 min-h-0">
        <h2 class="text-xs uppercase tracking-widest text-cyan-300 mb-2">AI Desk Assistant</h2>
        <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1"></div>
        <form id="chatForm" class="flex gap-1">
          <input id="chatInput" type="text" placeholder="Ask about AAPL, run a scenario..."
                 class="flex-1 bg-[#0a0e13] border border-[var(--border)] rounded px-2 py-1.5 text-sm focus:outline-none focus:border-cyan-400" />
          <button class="bg-cyan-600 hover:bg-cyan-500 text-black text-xs font-bold px-3 rounded">SEND</button>
        </form>
      </div>

      <div class="panel-2 rounded-lg p-3 flex flex-col" style="max-height: 38%;">
        <h2 class="text-xs uppercase tracking-widest text-orange-400 mb-2">News Feed</h2>
        <div id="newsList" class="overflow-y-auto space-y-2 text-xs pr-1"></div>
      </div>
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
  updatePriceHeader(data.bars);
  refreshNews(symbol);
  highlightWatchlist(symbol);
}

function updatePriceHeader(bars) {
  if (!bars.length) return;
  const last = bars[bars.length-1];
  const prev = bars.length > 1 ? bars[bars.length-2] : last;
  const chg = last.close - prev.close;
  const pct = prev.close ? (chg/prev.close*100) : 0;
  document.getElementById('activePrice').textContent = last.close.toFixed(2);
  const chEl = document.getElementById('activeChange');
  chEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${pct.toFixed(2)}%)`;
  chEl.className = 'text-sm tabular-nums ' + (chg >= 0 ? 'text-green-400' : 'text-red-400');
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
function highlightWatchlist(symbol) {
  document.querySelectorAll('.watch-item').forEach(el => {
    el.classList.toggle('border-orange-400', el.dataset.symbol === symbol);
    el.classList.toggle('text-orange-300', el.dataset.symbol === symbol);
  });
}

async function refreshQuotes() {
  const res = await fetch(`/api/quotes?symbols=${DEFAULT_SYMBOLS.join(',')}`);
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

  const track = document.getElementById('tickerTrack');
  const html = quotes.map(q => `<span class="text-gray-400">${q.symbol} <span class="text-cyan-300">${q.price.toFixed(2)}</span></span>`).join('');
  track.innerHTML = html + html; // duplicate for seamless scroll
}

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
function appendChatMessage(role, text) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in rounded px-2 py-1.5 text-sm ${role === 'user' ? 'chat-msg-user text-right' : 'chat-msg-ai'}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
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
setInterval(refreshQuotes, 15000);
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
