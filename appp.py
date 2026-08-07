"""
AI Trading Terminal — single-file Flask app for the hackathon.

Arquitectura multi-agente (A2A):
    Portfolio Manager (agente principal)
      ├── data_analyst_agent      -> precios, noticias, técnicos
      └── cro_risk_agent          -> backtesting, riesgo, niveles

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""

import os
import re
import json
import time
import uuid
import threading
from datetime import datetime, timedelta, timezone

import requests
import feedparser
from flask import Flask, request, jsonify, Response

# LangChain y herramientas (LangChain v1 + langchain-classic)
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.caches import InMemoryCache
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor

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

ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")
ALPACA_TRADING_URL = os.environ.get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets/v2")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "azure/genailab-maas-gpt-4.1")

# Guardrail: órdenes por encima de este tamaño requieren aprobación humana (HITL)
HITL_QTY_THRESHOLD = int(os.environ.get("HITL_QTY_THRESHOLD", "5"))

app = Flask(__name__)

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 30

def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["t"]) < entry["ttl"]:
            return entry["v"]
    return None

def cache_set(key, value, ttl_override=None):
    with _cache_lock:
        _cache[key] = {"t": time.time(), "ttl": ttl_override or CACHE_TTL_SECONDS, "v": value}

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

def _sma_list(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            out.append(sum(window) / period)
    return out

def _stddev_list(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            mean = sum(window) / period
            var = sum((x - mean) ** 2 for x in window) / period
            out.append(var ** 0.5)
    return out

def _rsi_list(values, period=14):
    n = len(values)
    out = [None] * n
    if n <= period:
        return out
    gains = [0.0] * (n - 1)
    losses = [0.0] * (n - 1)
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gains[i - 1] = max(change, 0)
        losses[i - 1] = max(-change, 0)

    def _rsi_from(avg_gain, avg_loss):
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from(avg_gain, avg_loss)
    return out

def _vwap_list(bars):
    out = []
    cum_pv = 0.0
    cum_vol = 0.0
    for b in bars:
        typical = (b["high"] + b["low"] + b["close"]) / 3
        vol = b.get("volume") or 0
        cum_pv += typical * vol
        cum_vol += vol
        out.append(round(cum_pv / cum_vol, 2) if cum_vol else b["close"])
    return out

def compute_indicators(bars):
    """Devuelve series listas para graficar: SMA20/50, EMA12, Bandas de Bollinger 20/2, VWAP, RSI14 y volumen."""
    if not bars: return {}
    closes = [b["close"] for b in bars]
    times = [b["time"] for b in bars]

    sma20 = _sma_list(closes, 20)
    sma50 = _sma_list(closes, 50)
    ema12 = _ema_list(closes, 12)
    std20 = _stddev_list(closes, 20)
    bb_upper = [round(m + 2 * s, 2) if (m is not None and s is not None) else None for m, s in zip(sma20, std20)]
    bb_lower = [round(m - 2 * s, 2) if (m is not None and s is not None) else None for m, s in zip(sma20, std20)]
    vwap = _vwap_list(bars)
    rsi14 = _rsi_list(closes, 14)

    def series(values):
        return [{"time": t, "value": round(v, 2)} for t, v in zip(times, values) if v is not None]

    return {
        "sma20": series(sma20),
        "sma50": series(sma50),
        "ema12": series(ema12),
        "bb_upper": series(bb_upper),
        "bb_middle": series(sma20),
        "bb_lower": series(bb_lower),
        "vwap": series(vwap),
        "rsi14": series(rsi14),
        "volume": [
            {"time": b["time"], "value": b.get("volume") or 0,
             "color": "#10b98166" if b["close"] >= b["open"] else "#ef444466"}
            for b in bars
        ],
    }

def compute_levels(bars):
    """Soporte/resistencia y niveles de retroceso de Fibonacci sobre el rango de velas cargado."""
    if not bars: return {}
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    swing_high = max(highs)
    swing_low = min(lows)
    diff = swing_high - swing_low
    fib = {
        "0.0": round(swing_high, 2),
        "0.236": round(swing_high - diff * 0.236, 2),
        "0.382": round(swing_high - diff * 0.382, 2),
        "0.5": round(swing_high - diff * 0.5, 2),
        "0.618": round(swing_high - diff * 0.618, 2),
        "0.786": round(swing_high - diff * 0.786, 2),
        "1.0": round(swing_low, 2),
    }
    return {"support": round(swing_low, 2), "resistance": round(swing_high, 2), "fibonacci": fib}

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
        except Exception:
            pass

    cache_set(cache_key, items, ttl_override=120)
    return items

# --------------------------------------------------------------------------
# BACKTESTING ENGINE — cruce de medias móviles sobre las últimas N velas
# --------------------------------------------------------------------------
def run_backtest(symbol, timeframe="1Day", fast=10, slow=30, lookback=60):
    """
    Simula una estrategia long-only de cruce de medias móviles (SMA fast x SMA slow)
    sobre las últimas `lookback` velas y devuelve el PnL teórico.

    Reglas:
      - Cruce alcista (fast cruza por encima de slow) -> abrir posición larga
      - Cruce bajista (fast cruza por debajo de slow) -> cerrar posición
      - Posición abierta al final -> se valúa a precio de cierre (mark-to-market)
    """
    cache_key = f"backtest:{symbol}:{timeframe}:{fast}:{slow}:{lookback}"
    cached = cache_get(cache_key)
    if cached: return cached

    bars = get_bars(symbol, timeframe=timeframe, limit=lookback + slow + 10)
    if len(bars) < slow + 10:
        return {"symbol": symbol, "error": "Historial insuficiente para correr el backtest."}

    closes = [b["close"] for b in bars]
    sma_f = _sma_list(closes, fast)
    sma_s = _sma_list(closes, slow)

    start = max(slow, len(closes) - lookback)
    position = 0
    entry_price = 0.0
    entry_time = None
    trades = []

    for i in range(start, len(closes)):
        if None in (sma_f[i], sma_s[i], sma_f[i - 1], sma_s[i - 1]):
            continue
        cross_up = sma_f[i - 1] <= sma_s[i - 1] and sma_f[i] > sma_s[i]
        cross_down = sma_f[i - 1] >= sma_s[i - 1] and sma_f[i] < sma_s[i]

        if cross_up and position == 0:
            position = 1
            entry_price = closes[i]
            entry_time = bars[i]["time"]
        elif cross_down and position == 1:
            pnl = closes[i] - entry_price
            trades.append({
                "entry_time": entry_time, "entry": round(entry_price, 2),
                "exit_time": bars[i]["time"], "exit": round(closes[i], 2),
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / entry_price * 100, 2),
                "status": "CLOSED",
            })
            position = 0

    open_trade = None
    if position == 1:
        pnl = closes[-1] - entry_price
        open_trade = {
            "entry_time": entry_time, "entry": round(entry_price, 2),
            "exit_time": bars[-1]["time"], "exit": round(closes[-1], 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / entry_price * 100, 2),
            "status": "OPEN (mark-to-market)",
        }
        trades.append(open_trade)

    total_pnl = round(sum(t["pnl"] for t in trades), 2)
    wins = [t for t in trades if t["pnl"] > 0]
    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0.0
    capital_base = closes[start] if closes[start] else closes[-1]
    total_pnl_pct = round(total_pnl / capital_base * 100, 2) if capital_base else 0.0
    buy_hold_pct = round((closes[-1] - closes[start]) / capital_base * 100, 2) if capital_base else 0.0

    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": f"SMA {fast} x SMA {slow} (long-only)",
        "periods_tested": len(closes) - start,
        "num_trades": len(trades),
        "win_rate_pct": win_rate,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "buy_and_hold_pct": buy_hold_pct,
        "beats_buy_and_hold": total_pnl_pct > buy_hold_pct,
        "best_trade": max((t["pnl"] for t in trades), default=0.0),
        "worst_trade": min((t["pnl"] for t in trades), default=0.0),
        "open_position": open_trade is not None,
        "trades": trades[-6:],
    }
    cache_set(cache_key, data, ttl_override=300)
    return data

def _backtest_summary_text(bt):
    """Resumen compacto del backtest para que el LLM lo consuma sin gastar contexto de más."""
    if bt.get("error"):
        return f"Backtest de {bt.get('symbol')}: {bt['error']}"
    veredicto = "SUPERA" if bt["beats_buy_and_hold"] else "NO supera"
    return (
        f"BACKTEST {bt['symbol']} | Estrategia: {bt['strategy']} | "
        f"Periodos: {bt['periods_tested']} | Operaciones: {bt['num_trades']} | "
        f"Win rate: {bt['win_rate_pct']}% | PnL teorico: ${bt['total_pnl']} ({bt['total_pnl_pct']}%) | "
        f"Buy & hold: {bt['buy_and_hold_pct']}% | La estrategia {veredicto} a buy & hold. "
        f"Mejor operacion: ${bt['best_trade']} | Peor operacion: ${bt['worst_trade']}."
    )

# --------------------------------------------------------------------------
# TRACEABILITY — registro del chain of thought entre agentes
# --------------------------------------------------------------------------
_trace_ctx = threading.local()

def trace_reset():
    _trace_ctx.steps = []
    _trace_ctx.pending = None

def trace_add(level, agent, tool_name, tool_input="", output=""):
    if not hasattr(_trace_ctx, "steps"):
        _trace_ctx.steps = []
    _trace_ctx.steps.append({
        "level": level,
        "agent": agent,
        "tool": tool_name,
        "input": str(tool_input)[:400],
        "output": str(output)[:700],
        "ts": datetime.now().strftime("%H:%M:%S"),
    })

def trace_get():
    return getattr(_trace_ctx, "steps", [])

def trace_set_pending(payload):
    _trace_ctx.pending = payload

def trace_get_pending():
    return getattr(_trace_ctx, "pending", None)

def _absorb_subagent_steps(agent_name, result):
    """Vuelca los pasos internos de un sub-agente al trace global."""
    for action, observation in result.get("intermediate_steps", []):
        trace_add(1, agent_name, getattr(action, "tool", "?"), getattr(action, "tool_input", ""), observation)
    trace_add(1, agent_name, "final_answer", "", result.get("output", ""))

# --------------------------------------------------------------------------
# HUMAN-IN-THE-LOOP — cola de órdenes que requieren aprobación
# --------------------------------------------------------------------------
pending_trades = {}
_pending_lock = threading.Lock()

def _place_order(symbol, side, qty):
    """Manda la orden real a Alpaca. Devuelve (ok, payload_o_mensaje)."""
    payload = {
        "symbol": symbol.upper(), "qty": qty, "side": side.lower(),
        "type": "market", "time_in_force": "day",
    }
    try:
        r = requests.post(f"{ALPACA_TRADING_URL}/orders", headers=alpaca_headers(), json=payload, timeout=8)
        if r.status_code in (200, 201):
            return True, r.json()
        try:
            return False, r.json().get("message", r.text)
        except Exception:
            return False, r.text
    except Exception as e:
        return False, str(e)

# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
import httpx
client_httpx = httpx.Client(verify=False)

llm = ChatOpenAI(
    base_url=LLM_BASE_URL,
    model=LLM_MODEL,
    api_key=LLM_API_KEY,
    http_client=client_httpx,
    temperature=0.2,
    cache=InMemoryCache(),
)

# --------------------------------------------------------------------------
# NIVEL 1 — Herramientas del DATA ANALYST
# --------------------------------------------------------------------------
@tool
def get_price_tool(symbol: str) -> str:
    """Returns the latest stock price and short historical context for a given symbol."""
    quote = get_latest_quote(symbol.upper())
    bars = get_bars(symbol.upper(), limit=5)
    hist = ", ".join([str(b["close"]) for b in bars])
    return f"Current Price of {symbol}: ${quote['price']}. Last closes: {hist}"

@tool
def get_news_tool(symbol: str) -> str:
    """Returns the latest financial news headlines and their sentiment for a given stock symbol."""
    news = get_news(symbol.upper(), limit=3)
    if not news: return f"No recent news found for {symbol}."
    return "\n".join([f"- {n['title']} (Sentiment: {n['sentiment']}) : {n['summary']}" for n in news])

@tool
def get_technicals_tool(symbol: str) -> str:
    """Returns the current technical picture: last close, SMA20, SMA50, RSI14, support and resistance."""
    bars = get_bars(symbol.upper(), limit=120)
    if not bars: return f"No market data available for {symbol}."
    ind = compute_indicators(bars)
    lv = compute_levels(bars)
    def last(series):
        return series[-1]["value"] if series else "n/a"
    return (
        f"{symbol.upper()} | Close: {bars[-1]['close']} | SMA20: {last(ind.get('sma20', []))} | "
        f"SMA50: {last(ind.get('sma50', []))} | RSI14: {last(ind.get('rsi14', []))} | "
        f"Support: {lv.get('support')} | Resistance: {lv.get('resistance')}"
    )

analyst_tools = [get_price_tool, get_news_tool, get_technicals_tool]

# --------------------------------------------------------------------------
# NIVEL 1 — Herramientas del CRO (RIESGO)
# --------------------------------------------------------------------------
@tool
def run_backtest_tool(symbol: str) -> str:
    """Runs a historical backtest of an SMA-crossover strategy over the last 60 candles and returns the theoretical PnL, win rate and comparison against buy & hold."""
    return _backtest_summary_text(run_backtest(symbol.upper()))

@tool
def get_risk_levels_tool(symbol: str) -> str:
    """Returns the proposed entry price, stop-loss, take-profit and risk/reward ratio for a symbol."""
    s = generate_strategy(symbol.upper())
    if s.get("error"): return f"{symbol}: {s['error']}"
    return (
        f"{symbol.upper()} | Signal: {s['signal']} | Entry: {s['entry']} | "
        f"Stop-loss: {s['stop_loss']} | Take-profit: {s['take_profit']} | R:R {s['risk_reward']} | "
        f"Rationale: {s['rationale']}"
    )

@tool
def get_ai_recommendation_tool(symbol: str) -> str:
    """Returns the algorithmic momentum signal (BUY/SELL/HOLD) with its confidence and rationale."""
    rec = generate_ai_recommendation(symbol.upper())
    return f"AI Recommendation for {symbol}: SIGNAL={rec['signal']}, Confidence={rec['confidence']}. Rationale: {rec['rationale']}"

cro_tools = [run_backtest_tool, get_risk_levels_tool, get_ai_recommendation_tool]

# --------------------------------------------------------------------------
# SUB-AGENTES
# --------------------------------------------------------------------------
analyst_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "You are the DATA ANALYST agent of a trading desk. Your job is to gather and summarise FACTS: "
     "prices, news sentiment and technical indicators. Use your tools to get real data — never invent numbers. "
     "You do NOT give buy/sell advice and you do NOT execute trades. "
     "Reply with a dense, factual briefing of at most 6 lines."),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

cro_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "You are the CHIEF RISK OFFICER (CRO) agent of a trading desk. Your job is to assess RISK. "
     "You must ALWAYS run the backtest tool before giving a verdict — historical evidence is mandatory. "
     "Then check the risk levels (entry, stop-loss, take-profit, R:R) and the momentum signal. "
     "End with an explicit verdict line: 'RISK VERDICT: APPROVE' or 'RISK VERDICT: REJECT', plus the "
     "theoretical PnL from the backtest and the stop-loss you require. Maximum 7 lines."),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

data_analyst_agent = create_tool_calling_agent(llm, analyst_tools, analyst_prompt)
data_analyst_executor = AgentExecutor(
    agent=data_analyst_agent, tools=analyst_tools,
    verbose=True, return_intermediate_steps=True, max_iterations=6,
)

cro_risk_agent = create_tool_calling_agent(llm, cro_tools, cro_prompt)
cro_risk_executor = AgentExecutor(
    agent=cro_risk_agent, tools=cro_tools,
    verbose=True, return_intermediate_steps=True, max_iterations=6,
)

# --------------------------------------------------------------------------
# NIVEL 0 — Herramientas del PORTFOLIO MANAGER (delegación A2A + ejecución)
# --------------------------------------------------------------------------
@tool
def delegate_to_data_analyst(request: str) -> str:
    """
    Delegates a research question to the Data Analyst sub-agent.
    Use it to obtain prices, news sentiment or technical indicators for one or more symbols.
    Pass a full natural-language request, e.g. 'Give me the price, news sentiment and technicals for AAPL'.
    """
    trace_add(0, "portfolio_manager", "delegate_to_data_analyst", request, "→ delegando...")
    try:
        res = data_analyst_executor.invoke({"input": request})
        _absorb_subagent_steps("data_analyst_agent", res)
        return f"[DATA ANALYST REPORT]\n{res['output']}"
    except Exception as e:
        trace_add(1, "data_analyst_agent", "error", request, str(e))
        return f"[DATA ANALYST ERROR] {e}"

@tool
def delegate_to_cro(symbol: str) -> str:
    """
    Delegates a risk assessment to the Chief Risk Officer sub-agent for a single symbol.
    The CRO will run a historical backtest, compute stop-loss / take-profit levels and return an
    explicit APPROVE or REJECT verdict. ALWAYS call this before executing any trade.
    """
    trace_add(0, "portfolio_manager", "delegate_to_cro", symbol, "→ delegando...")
    try:
        res = cro_risk_executor.invoke({
            "input": f"Assess the risk of taking a position in {symbol.upper()} right now. "
                     f"Run the backtest first, then the risk levels, then give your verdict."
        })
        _absorb_subagent_steps("cro_risk_agent", res)
        return f"[CRO RISK ASSESSMENT]\n{res['output']}"
    except Exception as e:
        trace_add(1, "cro_risk_agent", "error", symbol, str(e))
        return f"[CRO ERROR] {e}"

@tool
def execute_trade_tool(symbol: str, side: str, qty: int = 1) -> str:
    """
    Executes a market order (Paper Trading) to buy or sell a stock.
    Side MUST be 'buy' or 'sell'. Only call this AFTER the CRO has approved the risk.
    Orders above the desk's auto-execution limit are queued for human approval instead of being sent.
    """
    symbol = symbol.upper()
    side = side.lower()

    if side not in ("buy", "sell"):
        trace_add(0, "portfolio_manager", "execute_trade_tool", f"{side} {qty} {symbol}", "rechazado: side inválido")
        return "❌ Invalid side. It must be 'buy' or 'sell'."

    try:
        qty = int(qty)
    except Exception:
        return "❌ Invalid qty. It must be an integer."

    # ---------------- GUARDRAIL / HUMAN-IN-THE-LOOP ----------------
    if qty > HITL_QTY_THRESHOLD:
        tid = uuid.uuid4().hex[:8]
        record = {
            "id": tid, "symbol": symbol, "side": side, "qty": qty,
            "status": "pending", "created": datetime.now().strftime("%H:%M:%S"),
        }
        with _pending_lock:
            pending_trades[tid] = record
        trace_set_pending(record)
        trace_add(0, "portfolio_manager", "execute_trade_tool [GUARDRAIL]", f"{side} {qty} {symbol}",
                  f"BLOQUEADO — excede el límite de {HITL_QTY_THRESHOLD}. Encolado como {tid} para aprobación humana.")
        return (
            f"⛔ GUARDRAIL TRIGGERED: an order for {qty} shares of {symbol} exceeds the desk's "
            f"auto-execution limit of {HITL_QTY_THRESHOLD} shares. The order was NOT sent. "
            f"It is queued as ticket {tid} and is AWAITING HUMAN APPROVAL. "
            f"Do NOT retry and do NOT split the order into smaller ones. "
            f"Tell the user the order is on hold and that they must approve or reject it using the buttons below."
        )

    ok, payload = _place_order(symbol, side, qty)
    if ok:
        trace_add(0, "portfolio_manager", "execute_trade_tool", f"{side} {qty} {symbol}", "orden enviada a Alpaca")
        return f"✅ SUCCESS: Executed {side.upper()} order for {qty} shares of {symbol}."
    trace_add(0, "portfolio_manager", "execute_trade_tool", f"{side} {qty} {symbol}", f"rechazada: {payload}")
    return f"❌ FAILED to execute trade: {payload}"

pm_tools = [delegate_to_data_analyst, delegate_to_cro, execute_trade_tool]
chat_memory = []

# --------------------------------------------------------------------------
# AGENTE PRINCIPAL — PORTFOLIO MANAGER
# --------------------------------------------------------------------------
prompt_chat = ChatPromptTemplate.from_messages([
    ("system",
     "You are the PORTFOLIO MANAGER of an autonomous AI trading desk. You do NOT gather data yourself and "
     "you do NOT assess risk yourself — you COORDINATE two specialist sub-agents:\n"
     "  • delegate_to_data_analyst — facts: prices, news sentiment, technical indicators.\n"
     "  • delegate_to_cro — risk: historical backtest, stop-loss/take-profit levels, APPROVE/REJECT verdict.\n\n"
     "MANDATORY WORKFLOW for any trading decision:\n"
     "  1. Delegate the research to the Data Analyst.\n"
     "  2. Delegate the risk assessment to the CRO. Never skip this step.\n"
     "  3. Only if the CRO returns APPROVE may you call execute_trade_tool.\n"
     "  4. If the CRO returns REJECT, do not trade and explain why to the user.\n\n"
     f"Orders above {HITL_QTY_THRESHOLD} shares are automatically held for human approval by a guardrail. "
     "When that happens, never retry and never split the order — simply tell the user it is on hold.\n\n"
     "In your final answer, always state: (a) what the Data Analyst found, (b) the CRO's verdict including the "
     "backtested PnL, and (c) the action taken. Be professional and concise."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

agente_conversacional = create_tool_calling_agent(llm, pm_tools, prompt_chat)
agent_executor = AgentExecutor(
    agent=agente_conversacional, tools=pm_tools,
    verbose=True, return_intermediate_steps=True, max_iterations=10,
)

# --------------------------------------------------------------------------
# Señales algorítmicas y estrategia
# --------------------------------------------------------------------------
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
    except Exception: last_date = datetime.now()

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

def generate_strategy(symbol, timeframe="1Day"):
    """
    Genera una estrategia de entrada/salida para un símbolo:
    - señal BUY / SELL / HOLD
    - precio de entrada, stop-loss (escenario negativo) y take-profit (escenario positivo)
    - relación riesgo/beneficio
    - narrativa de ambos escenarios
    """
    cache_key = f"strategy:{symbol}:{timeframe}"
    cached = cache_get(cache_key)
    if cached: return cached

    bars = get_bars(symbol, timeframe=timeframe, limit=60)
    if not bars or len(bars) < 10:
        return {"signal": "N/A", "error": "Datos insuficientes para generar una estrategia."}

    closes = [b["close"] for b in bars]
    last_close = closes[-1]
    last_time = bars[-1]["time"]

    lookback = bars[-20:] if len(bars) >= 20 else bars
    swing_high = max(b["high"] for b in lookback)
    swing_low = min(b["low"] for b in lookback)

    ema20_series = _ema_list(closes, 20)
    ema20 = ema20_series[-1] if ema20_series else last_close
    ref_idx = -10 if len(closes) >= 10 else 0
    momentum = last_close / (closes[ref_idx] if closes[ref_idx] else last_close)

    if momentum > 1.015 and last_close > ema20:
        signal = "BUY"
    elif momentum < 0.985 and last_close < ema20:
        signal = "SELL"
    else:
        signal = "HOLD"

    risk_pct = 0.03
    reward_ratio = 2.0
    entry = round(last_close, 2)

    if signal == "BUY":
        stop = round(min(swing_low, entry * (1 - risk_pct)), 2)
        risk = max(entry - stop, 0.01)
        target = round(entry + risk * reward_ratio, 2)
        positive = f"Si el precio se mantiene sobre la entrada de ${entry}, el impulso alcista podria llevarlo hacia el objetivo de ${target}."
        negative = f"Si el precio rompe por debajo del soporte de ${stop}, se invalida la idea de compra; conviene salir para limitar la perdida."
    elif signal == "SELL":
        stop = round(max(swing_high, entry * (1 + risk_pct)), 2)
        risk = max(stop - entry, 0.01)
        target = round(entry - risk * reward_ratio, 2)
        positive = f"Si el precio se mantiene bajo la entrada de ${entry}, la presion bajista podria llevarlo hacia ${target}."
        negative = f"Si el precio rompe por encima de la resistencia de ${stop}, se invalida la idea bajista; conviene salir para limitar la perdida."
    else:
        stop = round(swing_low, 2)
        target = round(swing_high, 2)
        risk = max(entry - stop, 0.01)
        positive = f"Mientras se mantenga por encima de ${stop}, el sesgo sigue siendo neutral a favorable; vigilar ruptura por encima de ${target}."
        negative = f"Una ruptura por debajo de ${stop} cambiaria el sesgo a bajista; no hay señal clara de entrada por ahora."

    rr = round(abs(target - entry) / risk, 2) if risk else None

    data = {
        "symbol": symbol,
        "signal": signal,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "risk_reward": rr,
        "entry_time": last_time,
        "positive_scenario": positive,
        "negative_scenario": negative,
        "rationale": f"Estrategia basada en momentum de {len(bars)} periodos ({timeframe}), EMA20 (${round(ema20, 2)}) y rango de soporte/resistencia de las ultimas {len(lookback)} velas.",
    }
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
    bars = get_bars(symbol.upper(), tf, 250)
    return jsonify({
        "symbol": symbol.upper(),
        "bars": bars,
        "indicators": compute_indicators(bars),
        "levels": compute_levels(bars),
    })

@app.route("/api/quotes")
def api_quotes():
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    return jsonify([get_latest_quote(s) for s in symbols])

@app.route("/api/recommend/<symbol>")
def api_recommend(symbol):
    return jsonify(generate_ai_recommendation(symbol.upper(), request.args.get("timeframe", "1Day")))

@app.route("/api/strategy/<symbol>")
def api_strategy(symbol):
    return jsonify(generate_strategy(symbol.upper(), request.args.get("timeframe", "1Day")))

@app.route("/api/backtest/<symbol>")
def api_backtest(symbol):
    return jsonify(run_backtest(symbol.upper(), request.args.get("timeframe", "1Day")))

@app.route("/api/news")
def api_news_endpoint():
    return jsonify(get_news(request.args.get("symbol")))

@app.route("/api/chat", methods=["POST"])
def api_chat():
    global chat_memory
    data = request.get_json(force=True)
    user_msg = data.get("message", "")
    trace_reset()
    try:
        res = agent_executor.invoke({"input": user_msg, "chat_history": chat_memory})
        reply = res["output"]
        chat_memory.extend([HumanMessage(content=user_msg), AIMessage(content=reply)])
        if len(chat_memory) > 20: chat_memory = chat_memory[-20:]
        return jsonify({
            "reply": reply,
            "trace": trace_get(),
            "pending_approval": trace_get_pending(),
        })
    except Exception as exc:
        app.logger.exception("Agent failure")
        return jsonify({
            "reply": "⚠️ Connection error with agents. Please check endpoint status.",
            "trace": trace_get(),
            "pending_approval": trace_get_pending(),
            "error": str(exc),
        })

@app.route("/api/trade/pending")
def api_trade_pending():
    with _pending_lock:
        return jsonify([t for t in pending_trades.values() if t["status"] == "pending"])

@app.route("/api/trade/resolve", methods=["POST"])
def api_trade_resolve():
    """Human-in-the-loop: el usuario aprueba o rechaza una orden retenida por el guardrail."""
    body = request.get_json(force=True)
    tid = body.get("id")
    decision = (body.get("decision") or "").lower()

    with _pending_lock:
        record = pending_trades.get(tid)
        if not record:
            return jsonify({"error": "Ticket no encontrado."}), 404
        if record["status"] != "pending":
            return jsonify({"error": f"Este ticket ya fue {record['status']}."}), 409
        if decision not in ("approve", "reject"):
            return jsonify({"error": "Decisión inválida."}), 400
        record["status"] = "processing" if decision == "approve" else "rejected"

    if decision == "reject":
        chat_memory.append(AIMessage(
            content=f"[HITL] El usuario RECHAZÓ la orden {tid}: {record['side'].upper()} {record['qty']} {record['symbol']}. No se ejecutó."))
        return jsonify({"id": tid, "status": "rejected",
                        "message": f"Orden {record['side'].upper()} {record['qty']} {record['symbol']} rechazada por el usuario."})

    ok, payload = _place_order(record["symbol"], record["side"], record["qty"])
    with _pending_lock:
        record["status"] = "executed" if ok else "failed"
        record["result"] = payload if isinstance(payload, str) else "ok"

    if ok:
        chat_memory.append(AIMessage(
            content=f"[HITL] El usuario APROBÓ la orden {tid}: {record['side'].upper()} {record['qty']} {record['symbol']}. Ejecutada."))
        return jsonify({"id": tid, "status": "executed",
                        "message": f"Orden {record['side'].upper()} {record['qty']} {record['symbol']} enviada al broker."})
    return jsonify({"id": tid, "status": "failed", "message": f"El broker rechazó la orden: {payload}"}), 400

@app.route("/api/account")
def api_account():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/account", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/positions")
def api_positions():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/positions", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

# Permite ver órdenes pendientes cuando el mercado está cerrado
@app.route("/api/orders_pending")
def api_orders_pending():
    try: return jsonify(requests.get(f"{ALPACA_TRADING_URL}/orders?status=open", headers=alpaca_headers(), timeout=5).json())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.json
    ok, payload = _place_order(data["symbol"], data["side"], data.get("qty", 1))
    if ok: return jsonify(payload), 200
    return jsonify({"error": payload}), 400

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
  .chat-msg-user { background: #111827; text-align: right; border-left: 2px solid #3b82f6; padding: 8px; }
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

  .ind-btn { padding: 2px 7px; border: 1px solid var(--border); font-size: 9px; cursor: pointer; background: transparent; color: #9ca3af; white-space: nowrap; }
  .ind-btn.active { background: #1e293b; color: #34d399; border-color: #10b981; font-weight: bold; }

  /* Agent Trace */
  .trace-box { margin-top: 8px; border: 1px solid var(--border); background: #000; white-space: normal; }
  .trace-box > summary { cursor: pointer; padding: 4px 8px; font-size: 9px; letter-spacing: .08em; text-transform: uppercase; color: #64748b; list-style: none; user-select: none; }
  .trace-box > summary:hover { color: #34d399; }
  .trace-box[open] > summary { border-bottom: 1px solid var(--border); color: #34d399; }
  .trace-step { padding: 5px 8px; border-bottom: 1px dashed #111827; font-size: 9px; }
  .trace-step:last-child { border-bottom: none; }
  .trace-head { display: flex; gap: 6px; align-items: center; margin-bottom: 2px; }
  .trace-agent { color: #a78bfa; font-weight: bold; }
  .trace-tool { color: #22d3ee; }
  .trace-ts { color: #374151; margin-left: auto; }
  .trace-io { color: #6b7280; line-height: 1.35; word-break: break-word; }
  .trace-label { display: inline-block; min-width: 22px; color: #374151; }
  .trace-rail { border-left: 2px solid #1e293b; }

  /* Human-in-the-loop card */
  .hitl-card { margin-top: 8px; border: 1px solid #78350f; background: #1c1207; padding: 10px; white-space: normal; }
  .hitl-title { color: #fbbf24; font-weight: bold; font-size: 10px; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 4px; }
  .hitl-detail { color: #d1d5db; font-size: 10px; margin-bottom: 8px; }
  .hitl-btn { padding: 4px 14px; font-size: 10px; font-weight: bold; cursor: pointer; border: none; }
  .hitl-approve { background: #10b981; color: #000; }
  .hitl-approve:hover { background: #34d399; }
  .hitl-reject { background: #ef4444; color: #fff; }
  .hitl-reject:hover { background: #f87171; }
  .hitl-btn:disabled { opacity: .4; cursor: not-allowed; }

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
      <span class="text-[10px] text-violet-400 border border-violet-900 rounded px-2">MULTI-AGENT A2A</span>
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
            <button id="strategyBtn" onclick="runStrategy()" class="bg-blue-600 hover:bg-blue-500 text-white font-bold px-3 py-1 text-xs">📊 ESTRATEGIA</button>
            <button id="backtestBtn" onclick="runBacktest()" class="bg-violet-600 hover:bg-violet-500 text-white font-bold px-3 py-1 text-xs">⏱ BACKTEST</button>
          </div>
        </div>
        <div id="strategyPanel" class="hidden absolute top-14 right-4 z-30 w-72 panel border border-blue-800 p-3 glow"></div>
        <div id="backtestPanel" class="hidden absolute top-14 right-4 z-30 w-80 panel border border-violet-800 p-3 glow"></div>

        <div class="flex flex-wrap items-center gap-1 mb-1 shrink-0 bg-black p-1.5 border border-[var(--border)] relative z-20">
          <span class="text-[9px] text-gray-500 uppercase mr-1">Indicadores:</span>
          <button class="ind-btn" data-ind="sma20">SMA 20</button>
          <button class="ind-btn" data-ind="sma50">SMA 50</button>
          <button class="ind-btn" data-ind="ema12">EMA 12</button>
          <button class="ind-btn" data-ind="bb">Bollinger 20/2</button>
          <button class="ind-btn" data-ind="vwap">VWAP</button>
          <button class="ind-btn" data-ind="volume">Volumen</button>
          <button class="ind-btn" data-ind="rsi">RSI 14</button>
          <button class="ind-btn" data-ind="fib">Fibonacci</button>
          <button class="ind-btn" data-ind="sr">Soporte/Resistencia</button>
          <button class="ind-btn" data-ind="log">Escala Log</button>
        </div>

        <div id="chart" class="flex-1 min-h-0 relative z-0"></div>
        <div id="rsiPanel" class="hidden h-[70px] shrink-0 border-t border-[var(--border)] mt-1 pt-1">
          <div class="flex justify-between text-[9px] text-gray-500 px-1"><span>RSI (14)</span><span id="rsiValue">—</span></div>
          <div id="rsiChart" class="w-full h-[52px]"></div>
        </div>
      </section>

      <!-- BOTTOM: News and Agent Chat Split -->
      <div class="flex-1 flex gap-2 overflow-hidden">
        <section class="panel w-1/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold">News Stream</h2>
          <div id="newsList" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
        </section>

        <section class="panel w-2/3 flex flex-col overflow-hidden p-3">
          <h2 class="uppercase tracking-widest text-emerald-400 mb-2 font-bold flex gap-2 items-center">
            Portfolio Manager Desk
            <span class="bg-violet-900 text-violet-300 px-1 rounded text-[9px]">A2A · ANALYST + CRO</span>
            <span class="bg-yellow-900 text-yellow-300 px-1 rounded text-[9px]">HITL GUARDRAIL</span>
          </h2>
          <div id="chatMessages" class="flex-1 overflow-y-auto space-y-2 mb-2 pr-1"></div>
          <form id="chatForm" class="flex gap-1 shrink-0">
            <input id="chatInput" type="text" placeholder="Ej. 'Evalúa AAPL con el analista y el CRO, y si aprueban compra 2 acciones'..."
                   class="flex-1 bg-black border border-[var(--border)] px-2 py-1.5 focus:outline-none focus:border-emerald-400 text-white" />
            <button class="bg-emerald-600 hover:bg-emerald-500 text-black font-bold px-4">SEND</button>
          </form>
        </section>
      </div>
    </div>
  </main>

<script>
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(title, message, type='success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const borderColor = type === 'success' ? '#10b981' : (type === 'error' ? '#ef4444' : '#3b82f6');
  toast.className = 'toast border border-[var(--border)]';
  toast.style.borderLeftColor = borderColor;
  toast.innerHTML = `<div class="font-bold text-white text-xs mb-1 uppercase tracking-wider">${esc(title)}</div><div class="text-[10px] text-gray-400">${esc(message)}</div>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}

let watchlist = JSON.parse(localStorage.getItem('tcs_wl')) || ["AAPL", "MSFT", "TSLA"];
let activeSymbol = watchlist[0];
let activeTimeframe = "1Day";

let chart, candleSeries;
let extraSeries = [];
let strategyLines = [];

let indicatorState = { sma20:false, sma50:false, ema12:false, bb:false, vwap:false, volume:false, rsi:false, fib:false, sr:false, log:false };
let indicatorSeries = {};
let volumeSeries = null;
let fibLines = [];
let srLines = [];
let lastIndicatorsData = {};
let lastLevelsData = {};
let rsiChart, rsiSeries;

function clearStrategy() {
  strategyLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
  strategyLines = [];
  candleSeries.setMarkers([]);
  document.getElementById('strategyPanel').classList.add('hidden');
  document.getElementById('backtestPanel').classList.add('hidden');
}

function initChart() {
  chart = LightweightCharts.createChart(document.getElementById('chart'), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  candleSeries = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#ef4444' });

  chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (range && rsiChart) { try { rsiChart.timeScale().setVisibleLogicalRange(range); } catch(e) {} }
  });
}

function initRsiChart() {
  rsiChart = LightweightCharts.createChart(document.getElementById('rsiChart'), {
    layout: { background: { color: 'transparent' }, textColor: '#9fb3c8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: { visible: false, borderVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    handleScroll: false, handleScale: false,
  });
  rsiSeries = rsiChart.addLineSeries({ color: '#a78bfa', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
  rsiSeries.createPriceLine({ price: 70, color: '#ef444488', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '70' });
  rsiSeries.createPriceLine({ price: 30, color: '#10b98188', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '30' });
}

function clearExtraSeries() { extraSeries.forEach(s => chart.removeSeries(s)); extraSeries = []; }

function addDashedSeries(points, color) {
  const series = chart.addLineSeries({
    color, lineWidth: 2, lineStyle: 2, pointMarkersVisible: true, lastValueVisible: true, priceLineVisible: false,
  });
  series.setData(points); extraSeries.push(series);
}

function ensureVolumeSeries() {
  if (!volumeSeries) {
    volumeSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  }
  return volumeSeries;
}

function toggleLine(key, data, color, on) {
  if (on) {
    if (indicatorSeries[key]) return;
    const s = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true });
    s.setData(data || []);
    indicatorSeries[key] = s;
  } else if (indicatorSeries[key]) {
    chart.removeSeries(indicatorSeries[key]);
    delete indicatorSeries[key];
  }
}

function toggleBollinger(on) {
  if (on) {
    if (indicatorSeries.bb) return;
    const upper = chart.addLineSeries({ color: '#818cf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const mid = chart.addLineSeries({ color: '#818cf880', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
    const lower = chart.addLineSeries({ color: '#818cf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    upper.setData(lastIndicatorsData.bb_upper || []);
    mid.setData(lastIndicatorsData.bb_middle || []);
    lower.setData(lastIndicatorsData.bb_lower || []);
    indicatorSeries.bb = [upper, mid, lower];
  } else if (indicatorSeries.bb) {
    indicatorSeries.bb.forEach(s => chart.removeSeries(s));
    delete indicatorSeries.bb;
  }
}

function toggleVolume(on) {
  if (on) {
    const s = ensureVolumeSeries();
    s.setData(lastIndicatorsData.volume || []);
  } else if (volumeSeries) {
    chart.removeSeries(volumeSeries);
    volumeSeries = null;
  }
}

function toggleRsi(on) {
  const panel = document.getElementById('rsiPanel');
  if (on) {
    panel.classList.remove('hidden');
    rsiSeries.setData(lastIndicatorsData.rsi14 || []);
    const rsiVals = lastIndicatorsData.rsi14 || [];
    document.getElementById('rsiValue').textContent = rsiVals.length ? rsiVals[rsiVals.length - 1].value.toFixed(1) : '—';
    setTimeout(() => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
    }, 0);
  } else {
    panel.classList.add('hidden');
  }
}

function toggleFib(on) {
  if (on) {
    if (!lastLevelsData.fibonacci) return;
    Object.entries(lastLevelsData.fibonacci).forEach(([level, price]) => {
      fibLines.push(candleSeries.createPriceLine({
        price, color: '#eab308', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true, title: `Fib ${level}`
      }));
    });
  } else {
    fibLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
    fibLines = [];
  }
}

function toggleSr(on) {
  if (on) {
    if (!lastLevelsData.resistance) return;
    srLines.push(candleSeries.createPriceLine({ price: lastLevelsData.resistance, color: '#ef4444', lineWidth: 2, axisLabelVisible: true, title: 'Resistencia' }));
    srLines.push(candleSeries.createPriceLine({ price: lastLevelsData.support, color: '#10b981', lineWidth: 2, axisLabelVisible: true, title: 'Soporte' }));
  } else {
    srLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
    srLines = [];
  }
}

function toggleLog(on) {
  chart.priceScale('right').applyOptions({ mode: on ? LightweightCharts.PriceScaleMode.Logarithmic : LightweightCharts.PriceScaleMode.Normal });
}

function applyIndicator(key, on) {
  if (key === 'sma20') toggleLine('sma20', lastIndicatorsData.sma20, '#60a5fa', on);
  else if (key === 'sma50') toggleLine('sma50', lastIndicatorsData.sma50, '#f59e0b', on);
  else if (key === 'ema12') toggleLine('ema12', lastIndicatorsData.ema12, '#f472b6', on);
  else if (key === 'bb') toggleBollinger(on);
  else if (key === 'vwap') toggleLine('vwap', lastIndicatorsData.vwap, '#22d3ee', on);
  else if (key === 'volume') toggleVolume(on);
  else if (key === 'rsi') toggleRsi(on);
  else if (key === 'fib') toggleFib(on);
  else if (key === 'sr') toggleSr(on);
  else if (key === 'log') toggleLog(on);
}

function refreshActiveIndicators() {
  Object.keys(indicatorSeries).forEach(key => {
    const val = indicatorSeries[key];
    if (Array.isArray(val)) val.forEach(s => chart.removeSeries(s));
    else chart.removeSeries(val);
  });
  indicatorSeries = {};
  if (volumeSeries) { chart.removeSeries(volumeSeries); volumeSeries = null; }
  fibLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} }); fibLines = [];
  srLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} }); srLines = [];

  Object.entries(indicatorState).forEach(([key, on]) => {
    if (on && key !== 'log') applyIndicator(key, true);
  });
}

document.querySelectorAll('.ind-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.ind;
    indicatorState[key] = !indicatorState[key];
    btn.classList.toggle('active', indicatorState[key]);
    applyIndicator(key, indicatorState[key]);
  });
});

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
  clearStrategy();

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

  lastIndicatorsData = data.indicators || {};
  lastLevelsData = data.levels || {};
  refreshActiveIndicators();

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
        <span class="font-bold">${esc(q.symbol)}</span>
        <div class="flex items-center gap-2">
            <span>${q.price > 0 ? q.price.toFixed(2) : '—'}</span>
            <button class="text-red-500 hover:text-red-300 font-bold px-1" onclick="removeFromWatchlist('${esc(q.symbol)}', event)">×</button>
        </div>
    `;
    listEl.appendChild(row);
  });
  highlightWatchlist();

  const track = document.getElementById('tickerTrack');
  if (track) {
      const html = quotes.map(q => `<span class="text-gray-400">${esc(q.symbol)} <span class="text-emerald-300">${q.price.toFixed(2)}</span></span>`).join('');
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
        div.innerHTML = `<span class="font-bold text-emerald-400">${esc(m.symbol)}</span> <span class="text-gray-400 truncate ml-2">${esc(m.name)}</span>`;
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

  const ordersRes = await fetch('/api/orders_pending');
  const orders = await ordersRes.json();

  const listEl = document.getElementById('positionsList');
  listEl.innerHTML = '';

  let hasItems = false;

  if(!pos.error && pos.length > 0) {
    hasItems = true;
    pos.forEach(p => {
      const pl = parseFloat(p.unrealized_pl);
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-[var(--border)]';
      row.innerHTML = `
        <div><span class="font-bold text-white">${esc(p.symbol)}</span> <span class="text-gray-500">${esc(p.qty)} sh</span></div>
        <div class="${pl>=0?'text-emerald-400':'text-red-400'}">${pl>=0?'+':''}${pl.toFixed(2)}</div>
      `;
      listEl.appendChild(row);
    });
  }

  if(!orders.error && orders.length > 0) {
    hasItems = true;
    orders.forEach(o => {
      const row = document.createElement('div');
      row.className = 'flex justify-between items-center text-[10px] p-1 bg-black border border-yellow-900/50 mt-1';
      row.innerHTML = `
        <div><span class="font-bold text-yellow-500">${esc(o.symbol)}</span> <span class="text-gray-500">${esc(o.qty)} sh (${esc(o.side.toUpperCase())})</span></div>
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
        <a href="${esc(it.link)}" target="_blank" class="text-emerald-300 font-bold hover:underline text-[10px] leading-tight flex-1">${esc(it.title)}</a>
        <span class="badge ${badgeClass} text-[8px] px-1">${esc(it.sentiment)}</span>
      </div>
      <div class="text-gray-500 text-[9px] leading-tight">${esc(it.summary || '')}</div>
    `;
    box.appendChild(div);
  });
}

async function runStrategy() {
  const btn = document.getElementById('strategyBtn');
  const original = btn.textContent;
  btn.textContent = 'ANALIZANDO...';
  btn.disabled = true;
  clearStrategy();

  try {
    const res = await fetch(`/api/strategy/${activeSymbol}?timeframe=${activeTimeframe}`);
    const s = await res.json();

    if (s.error) { showToast('ESTRATEGIA', s.error, 'error'); return; }

    strategyLines.push(candleSeries.createPriceLine({
      price: s.entry, color: '#3b82f6', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true, title: `ENTRADA ${s.entry}`
    }));
    strategyLines.push(candleSeries.createPriceLine({
      price: s.take_profit, color: '#10b981', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: `TP (+) ${s.take_profit}`
    }));
    strategyLines.push(candleSeries.createPriceLine({
      price: s.stop_loss, color: '#ef4444', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: `SL (-) ${s.stop_loss}`
    }));

    const color = s.signal === 'BUY' ? '#10b981' : (s.signal === 'SELL' ? '#ef4444' : '#9ca3af');
    const shape = s.signal === 'BUY' ? 'arrowUp' : (s.signal === 'SELL' ? 'arrowDown' : 'circle');
    const position = s.signal === 'SELL' ? 'aboveBar' : 'belowBar';
    candleSeries.setMarkers([{ time: s.entry_time, position, color, shape, text: s.signal }]);

    showStrategyPanel(s);
  } catch (e) {
    showToast('ESTRATEGIA', 'No se pudo generar la estrategia. Revisa la conexión.', 'error');
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function showStrategyPanel(s) {
  const panel = document.getElementById('strategyPanel');
  const badgeClass = s.signal === 'BUY' ? 'bg-buy' : (s.signal === 'SELL' ? 'bg-sell' : 'bg-hold');
  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="badge ${badgeClass}">${esc(s.signal)}</span>
      <span class="text-[9px] text-gray-500">R:R ${s.risk_reward ?? '—'}</span>
      <button onclick="clearStrategy()" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>
    <div class="grid grid-cols-3 gap-1 text-center text-[9px] mb-2">
      <div><div class="text-gray-500">ENTRADA</div><div class="text-blue-400 font-bold">${s.entry}</div></div>
      <div><div class="text-gray-500">TP (+)</div><div class="text-emerald-400 font-bold">${s.take_profit}</div></div>
      <div><div class="text-gray-500">SL (-)</div><div class="text-red-400 font-bold">${s.stop_loss}</div></div>
    </div>
    <div class="text-[9px] text-gray-400 mb-2 leading-snug">${esc(s.rationale)}</div>
    <div class="text-[9px] text-emerald-300 mb-1 leading-snug">▲ ${esc(s.positive_scenario)}</div>
    <div class="text-[9px] text-red-300 leading-snug">▼ ${esc(s.negative_scenario)}</div>
  `;
  panel.classList.remove('hidden');
}

async function runBacktest() {
  const btn = document.getElementById('backtestBtn');
  const original = btn.textContent;
  btn.textContent = 'SIMULANDO...';
  btn.disabled = true;
  document.getElementById('strategyPanel').classList.add('hidden');

  try {
    const res = await fetch(`/api/backtest/${activeSymbol}?timeframe=${activeTimeframe}`);
    const bt = await res.json();
    if (bt.error) { showToast('BACKTEST', bt.error, 'error'); return; }
    showBacktestPanel(bt);
  } catch (e) {
    showToast('BACKTEST', 'No se pudo correr la simulación.', 'error');
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function showBacktestPanel(bt) {
  const panel = document.getElementById('backtestPanel');
  const pnlColor = bt.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
  const beats = bt.beats_buy_and_hold;
  const rows = (bt.trades || []).map(t => `
    <div class="flex justify-between text-[9px] border-b border-[#111827] py-0.5">
      <span class="text-gray-500">${esc(t.entry_time)} → ${esc(t.exit_time)}</span>
      <span class="${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}">${t.pnl >= 0 ? '+' : ''}${t.pnl} (${t.pnl_pct}%)</span>
    </div>`).join('');

  panel.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <span class="text-violet-300 font-bold text-[10px] uppercase tracking-wider">Backtest · ${esc(bt.symbol)}</span>
      <button onclick="document.getElementById('backtestPanel').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm leading-none">×</button>
    </div>
    <div class="text-[9px] text-gray-500 mb-2">${esc(bt.strategy)} · ${bt.periods_tested} periodos</div>
    <div class="grid grid-cols-3 gap-1 text-center text-[9px] mb-2">
      <div><div class="text-gray-500">PnL TEÓRICO</div><div class="${pnlColor} font-bold">$${bt.total_pnl}</div></div>
      <div><div class="text-gray-500">RETORNO</div><div class="${pnlColor} font-bold">${bt.total_pnl_pct}%</div></div>
      <div><div class="text-gray-500">WIN RATE</div><div class="text-white font-bold">${bt.win_rate_pct}%</div></div>
    </div>
    <div class="text-[9px] mb-2 p-1 border ${beats ? 'border-emerald-800 text-emerald-300' : 'border-red-900 text-red-300'}">
      ${beats ? '▲' : '▼'} Estrategia ${bt.total_pnl_pct}% vs Buy &amp; Hold ${bt.buy_and_hold_pct}%
    </div>
    <div class="text-[9px] text-gray-500 mb-1">${bt.num_trades} operaciones · mejor $${bt.best_trade} · peor $${bt.worst_trade}</div>
    <div class="max-h-24 overflow-y-auto">${rows || '<div class="text-gray-600 italic text-[9px]">Sin cruces en el periodo.</div>'}</div>
  `;
  panel.classList.remove('hidden');
}

// ------------------------------------------------------------------
// CHAT + AGENT TRACE + HUMAN-IN-THE-LOOP
// ------------------------------------------------------------------
function appendChat(role, text) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `fade-in ${role==='user'?'chat-msg-user':'chat-msg-ai'}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

function buildTraceBox(trace) {
  const det = document.createElement('details');
  det.className = 'trace-box';
  const agentsUsed = [...new Set(trace.map(t => t.agent))].length;
  const steps = trace.map(t => `
    <div class="trace-step ${t.level > 0 ? 'trace-rail' : ''}" style="margin-left:${t.level * 12}px">
      <div class="trace-head">
        <span class="trace-agent">${esc(t.agent)}</span>
        <span class="trace-tool">${esc(t.tool)}</span>
        <span class="trace-ts">${esc(t.ts)}</span>
      </div>
      ${t.input ? `<div class="trace-io"><span class="trace-label">IN</span>${esc(t.input)}</div>` : ''}
      ${t.output ? `<div class="trace-io"><span class="trace-label">OUT</span>${esc(t.output)}</div>` : ''}
    </div>`).join('');
  det.innerHTML = `<summary>▸ Agent Trace · ${trace.length} pasos · ${agentsUsed} agentes</summary>${steps}`;
  return det;
}

function buildHitlCard(p) {
  const card = document.createElement('div');
  card.className = 'hitl-card';
  card.innerHTML = `
    <div class="hitl-title">⚠ Guardrail · Se requiere aprobación humana</div>
    <div class="hitl-detail">
      El agente intentó ejecutar <b class="text-white">${esc(p.side.toUpperCase())} ${p.qty} ${esc(p.symbol)}</b>,
      por encima del límite de ejecución automática. La orden NO se envió al broker.
      <span class="text-gray-500">Ticket ${esc(p.id)} · ${esc(p.created)}</span>
    </div>
    <div class="flex gap-2">
      <button class="hitl-btn hitl-approve">✓ APROBAR</button>
      <button class="hitl-btn hitl-reject">✕ RECHAZAR</button>
    </div>
    <div class="hitl-status text-[9px] mt-2"></div>
  `;
  const [approveBtn, rejectBtn] = card.querySelectorAll('.hitl-btn');
  const status = card.querySelector('.hitl-status');

  async function resolve(decision) {
    approveBtn.disabled = true; rejectBtn.disabled = true;
    status.textContent = 'Procesando...';
    status.className = 'hitl-status text-[9px] mt-2 text-gray-400';
    try {
      const res = await fetch('/api/trade/resolve', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ id: p.id, decision })
      });
      const data = await res.json();
      if (data.error) {
        status.textContent = data.error;
        status.className = 'hitl-status text-[9px] mt-2 text-red-400';
        return;
      }
      status.textContent = data.message;
      status.className = `hitl-status text-[9px] mt-2 ${data.status === 'executed' ? 'text-emerald-400' : 'text-yellow-400'}`;
      showToast('HUMAN-IN-THE-LOOP', data.message, data.status === 'executed' ? 'success' : 'info');
      if (data.status === 'executed') {
        setTimeout(fetchPortfolio, 1000);
        setTimeout(fetchPortfolio, 4000);
      }
    } catch (e) {
      status.textContent = 'Error de conexión al resolver el ticket.';
      status.className = 'hitl-status text-[9px] mt-2 text-red-400';
      approveBtn.disabled = false; rejectBtn.disabled = false;
    }
  }

  approveBtn.onclick = () => resolve('approve');
  rejectBtn.onclick = () => resolve('reject');
  return card;
}

function renderAgentReply(data) {
  const box = document.getElementById('chatMessages');
  const wrap = document.createElement('div');
  wrap.className = 'fade-in chat-msg-ai';

  const body = document.createElement('div');
  body.textContent = data.reply;
  wrap.appendChild(body);

  if (data.trace && data.trace.length) wrap.appendChild(buildTraceBox(data.trace));
  if (data.pending_approval) wrap.appendChild(buildHitlCard(data.pending_approval));

  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
}

document.getElementById('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if(!msg) return;
  appendChat('user', msg);
  input.value = '';
  const thinking = appendChat('ai', 'Portfolio Manager coordinando al Data Analyst y al CRO...');

  try {
    const res = await fetch('/api/chat', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ message: msg }) });
    const data = await res.json();
    thinking.remove();
    renderAgentReply(data);
  } catch (err) {
    thinking.textContent = '⚠️ Error de conexión con el desk de agentes.';
  }

  setTimeout(fetchPortfolio, 2000);
  setTimeout(fetchPortfolio, 5000);
});

initChart();
initRsiChart();
loadSymbol(activeSymbol, "1Day");
updateWatchlistUI();
fetchPortfolio();
appendChat('ai', 'TCS AI Desk listo. Soy el Portfolio Manager: coordino al Data Analyst (precios, noticias, técnicos) y al CRO (backtest y riesgo). Puedo ejecutar operaciones, pero las órdenes grandes requieren tu aprobación.');
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
