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