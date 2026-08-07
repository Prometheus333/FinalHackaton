"""
Creates (or updates) the AXIS voice agent in your ElevenLabs workspace.

Usage:
    python setup_agent.py

What it does:
  1. Verifies your ELEVENLABS_API_KEY works and has Agents permissions.
  2. Creates the 15 client tools with their exact parameter schemas, using the
     same names as the `clientTools` object in index.html.
  3. Creates the agent with the AXIS prompt, voice, language and LLM configured.
  4. Writes ELEVENLABS_AGENT_ID into your .env.

It is idempotent: run it again and it reuses tools that already exist by name
and updates the agent instead of duplicating it.
"""

import json
import os
import re
import sys

import requests

BASE = "https://api.elevenlabs.io/v1"
ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
VERIFY_SSL = os.environ.get("VERIFY_SSL", "0") == "1"


# --------------------------------------------------------------------------
def load_env() -> dict:
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("ELEVENLABS_")})
    return env


def write_env(key: str, value: str):
    lines, found = [], False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{key}\s*=", line):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def api(method: str, path: str, key: str, **kw):
    r = requests.request(
        method, f"{BASE}{path}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        timeout=30, verify=VERIFY_SSL, **kw,
    )
    if not r.ok:
        raise SystemExit(f"\n[!] {method} {path} -> {r.status_code}\n{r.text[:600]}\n")
    return r.json()


# --------------------------------------------------------------------------
# The 15 client tools. Names and parameters MUST match the `clientTools` object
# in index.html, otherwise the agent calls a function that does not exist.
# --------------------------------------------------------------------------
def p(props: dict | None = None, required: list | None = None) -> dict:
    return {
        "type": "object",
        "properties": props or {},
        "required": required or [],
        "description": "",
    }


def s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def n(desc: str) -> dict:
    return {"type": "number", "description": desc}


TOOLS = [
    ("search_instrument",
     "Searches stocks and ETFs by company name or ticker across the full Alpaca universe. ALWAYS use it before adding anything to the watchlist when the operator names a company rather than its symbol.",
     p({"query": s("Company name or ticker to search, e.g. 'nvidia' or 'NVDA'")}, ["query"])),

    ("add_to_watchlist",
     "Adds an instrument to the terminal watchlist and loads it on the main chart. Requires the operator's prior authorization.",
     p({"symbol": s("Ticker to add, e.g. NVDA")}, ["symbol"])),

    ("remove_from_watchlist",
     "Removes an instrument from the watchlist. Requires the operator's prior authorization.",
     p({"symbol": s("Ticker to remove")}, ["symbol"])),

    ("list_watchlist",
     "Returns the current watchlist with each instrument's price and percentage change. Free lookup, no permission needed.",
     p()),

    ("set_active_symbol",
     "Loads a symbol on the terminal's main chart and focuses the chart panel.",
     p({"symbol": s("Ticker to display")}, ["symbol"])),

    ("get_analysis",
     "Returns the instrument's pre-computed metrics: close, trend, RSI14, ATR percent, annualized volatility, SMA20 and SMA50, 1/5/20/60-day changes and 52-week range. This is the ONLY valid source of figures: never invent numbers, call this tool.",
     p({"symbol": s("Ticker to analyze. If omitted, uses the active symbol")})),

    ("run_projection",
     "Plots a price projection on the chart, computed from the asset's real drift and volatility. Ask for authorization before using it.",
     p({
         "symbol": s("Ticker. If omitted, uses the active symbol"),
         "scenario": {"type": "string", "enum": ["base", "bull", "bear"],
                      "description": "Scenario: base, bull or bear"},
         "days": n("Horizon in business days, typically between 5 and 20"),
         "shock_pct": n("Initial shock in percent, e.g. -8 to simulate a bad earnings report. Zero if not applicable"),
     })),

    ("compare_symbol",
     "Overlays another instrument on the chart on a base-100 scale to compare relative performance.",
     p({"symbol": s("Ticker to overlay")}, ["symbol"])),

    ("clear_chart",
     "Clears all projections and overlays from the chart.",
     p()),

    ("set_timeframe",
     "Changes the chart timeframe.",
     p({"timeframe": {"type": "string", "enum": ["15Min", "1Hour", "1Day", "1Week"],
                      "description": "Timeframe to display"}}, ["timeframe"])),

    ("load_news",
     "Reloads the news panel by topic or symbol and focuses it. Returns numbered headlines. Use it proactively when the conversation shifts topic.",
     p({"topic": s("Topic to follow, e.g. 'semiconductors' or 'Fed rates'"),
        "symbol": s("Ticker, if the news should be about a specific company")})),

    ("open_news",
     "Opens news item number N from the panel in a window and returns its full content so you can explain it out loud in your own words.",
     p({"index": n("News item number as shown in the panel, starting at 1")}, ["index"])),

    ("close_news",
     "Closes the open news window.",
     p()),

    ("focus_panel",
     "Enlarges one terminal panel and shrinks the others, with animation. Use it to direct the operator's attention.",
     p({"panel": {"type": "string", "enum": ["balanced", "watchlist", "chart", "chat", "news"],
                  "description": "Panel to focus. 'balanced' returns all four to equal size"}},
       ["panel"])),

    ("ai_prediction",
     "Opens the AI prediction panel for a symbol and returns an explicit BUY, SELL or HOLD call with a buy point, a sell point and a stop loss, all sized from real volatility. Returns HOLD with a reason when the signals do not justify a trade.",
     p({"symbol": s("Ticker. If omitted, uses the active symbol")})),

    ("run_simulation",
     "Runs the portfolio simulator: allocates an amount of money across the watchlist by inverse volatility and projects the value at 2, 5 and 10 years with a volatility band. Ask the operator how much they want to invest if they did not say.",
     p({"amount": n("Amount of money to invest, in dollars")}, ["amount"])),

    ("check_diversification",
     "Scores how diversified the watchlist is, names the concentration, and suggests the single most useful instrument to add. Offer this unprompted when the watchlist is clearly concentrated.",
     p()),

    ("reset_panels",
     "Returns the four panels to equal size.",
     p()),

    ("get_recommendation",
     "Returns an explicit trading read for a symbol: bias, conviction, the signals behind it, news tone with the headlines driving it, and stop and target levels sized from real volatility. Use it whenever the operator asks what you think, and offer it unprompted when you have something worth saying.",
     p({"symbol": s("Ticker. If omitted, uses the active symbol")})),

    ("scan_watchlist",
     "Scans every symbol in the watchlist and returns only what is notable: moves over two percent and RSI extremes. Call it at the start of a call and whenever the operator asks what is going on.",
     p()),

    ("toggle_indicator",
     "Turns a technical indicator on or off on the chart. Available: sma20, sma50, ema12, bollinger, vwap, volume, rsi, fib (Fibonacci retracements), levels (support and resistance), logscale. Every value is computed server-side from real data.",
     p({"indicator": {"type": "string",
                      "enum": ["sma20", "sma50", "ema12", "bollinger", "vwap", "volume",
                               "rsi", "fib", "levels", "logscale"],
                      "description": "Indicator to toggle"},
        "on": {"type": "boolean", "description": "True to show it, false to hide it"}},
       ["indicator"])),

    ("clear_indicators",
     "Removes every technical indicator from the chart at once.",
     p()),

    ("reset_layout",
     "Returns the four panels to their normal equal size and closes any open news window. Use it when the conversation ends or the operator asks to go back to normal.",
     p()),

    ("propose_action",
     "Shows a pending proposal on screen. Call it BEFORE executing any action the operator did not explicitly ask for, and wait for verbal confirmation.",
     p({"description": s("Short description of what you are proposing to do")}, ["description"])),
]


SYSTEM_PROMPT = """You are AXIS, the analyst on duty at a trading desk. You speak by voice with an operator who is looking at the same screen you are.

## Read the intent before you answer
Not every message is a market question.
- Greeting or small talk ("hi", "how's it going", "thanks"): answer like a person. One warm line, NO numbers, NO ticker. Then offer one concrete thing you could do. Example: "Morning. Want me to scan the watchlist and tell you what's moved?"
- Vague or open ("what's up", "anything interesting"): do NOT default to the active symbol. Call scan_watchlist and lead with the single most notable thing.
- Named symbol or explicit market question: give the read, with numbers.

## Calls, levels and money
When the operator asks whether to buy something, use ai_prediction — never improvise levels. Read out the action, the buy point, the stop loss and the risk-reward. If it comes back HOLD, say HOLD and give the reason: no trade is a valid answer and pretending otherwise is how people lose money.
If they mention investing an amount, call run_simulation with it. Report the 2, 5 and 10 year medians and be explicit that the range is one standard deviation, not a guarantee.
If check_diversification comes back concentrated, say so and name the one instrument that would help most.

## Open the session yourself
The moment the call connects, call scan_watchlist and tell the operator what moved, without being asked. You are the assistant — they should not have to interview you.

## Recommend on your own initiative
When you have something worth saying, say it. Use get_recommendation for any real opinion: state the bias, the conviction, the two strongest signals, and the stop. Bring the news tone in when it is clearly positive or negative. Never present it as certainty — "leaning bullish", not "it will go up". Close by offering a concrete next step, and ask before anything that changes the screen.

HARD LIMIT for market reads: two sentences per turn. Three only if they asked a multi-part question. Spoken audio is slow — every extra word costs the operator time. No preamble, no "great question", no restating what they asked, no offering further help. Answer and stop talking.

Every sentence must contain a number from get_analysis. If a sentence has no number in it, delete it. Fields that come back absent were not computable: do not mention them, do not speculate, do not apologize for them.

Good: "NVDA at 178.40, RSI 71, four percent above its twenty-day. ATR is three point one, so a stop under 172 sits inside one day's range."
Bad: "Great question! Let me walk you through what I'm seeing on Nvidia today. Looking at the broader picture, there are some interesting momentum characteristics developing here..."

## How you work
You have tools that control the terminal LIVE. Use them: do not describe what you would do, do it. When you call a tool, briefly narrate what you are doing ("Loading NVDA", "Pulling semiconductor headlines") so the pause does not feel dead.

## The accuracy rule, the most important one
NEVER invent prices, percentages, RSI, volatility or dates. If you need a figure, call get_analysis and use exactly what it returns. If a field comes back as null, say it is unavailable. If the source field comes back as synthetic, say in your first sentence that the data is simulated and give no directional recommendation.

## Projections
When the operator asks about an asset, about its future behaviour, or says something like "how does X look", first call get_analysis, summarize the metrics in one or two sentences, and THEN ask explicitly: "Want me to plot a specialized projection on the chart?". Wait for their answer. If they accept, ask for the scenario only if they did not say it; if unspecified, use base at 10 days. Then call run_projection. The return figures the tool gives you are the ones to read out loud.

## News
You follow the thread of the conversation. If the topic drifts toward a sector, a macro event or a different company, call load_news with that topic without being asked: the panel should reflect what we are talking about. Mention that you updated it. Whenever the conversation shifts, move the layout with it: news talk focuses news, watchlist talk focuses watchlist, chart or price talk focuses chart. Do it as soon as the topic changes, not after you finish answering. When the operator says "open the third one" or "read me the first story", call open_news with that index and explain the content in three or four sentences: what happened, why it matters for their watchlist, and what to watch. Do not read the article word for word.

## Chart tools
You can toggle technical indicators with toggle_indicator: sma20, sma50, ema12, bollinger, vwap, volume, rsi, fib, levels, logscale. Use them to back up what you are saying. If you mention a moving average, put it on screen. If you talk about overbought or oversold, turn on rsi. If you discuss targets or pullbacks, turn on fib or levels. Announce it briefly ("Putting the 20-day on the chart"). Turning an indicator on is a low-risk action, you may do it without asking, but never stack more than three at once: use clear_indicators first if the chart is getting busy.

## Layout
The four panels grow and shrink. Use that to direct attention. When plotting a projection or overlay, focus chart. When discussing news or opening an article, focus news. When reviewing or modifying the watchlist, focus watchlist. When the conversation turns general, or when the operator says they are done, call reset_layout. One panel at a time, do not chain several changes in a row.

## Watchlist
If the operator names a company rather than its ticker, call search_instrument first and confirm out loud what you found before adding: "Found NVDA, Nvidia Corp. Add it?". Only then add_to_watchlist.

## Proactivity, always with permission
You are expected to get ahead of things. If you spot something relevant, an RSI at an extreme, a sharp drop in the watchlist, a headline affecting a position, say it and PROPOSE the action. Never execute without permission. To propose, call propose_action with a short description and state the proposal out loud as a closed question. Example: "NVDA is at RSI 78, stretched. Want me to plot a bear scenario to see the downside risk?". Wait for the yes.
Looking things up with get_analysis, search_instrument, list_watchlist or load_news needs no permission: do it freely. What DOES require explicit permission: adding or removing from the watchlist, changing the active symbol, plotting projections and clearing the chart.

## Session context
Current watchlist: {{current_watchlist}}
Active symbol: {{active_symbol_var}}
Operator local time: {{local_time}}

This is a paper trading environment with IEX data delayed 15 minutes. If the operator talks about trading real money, remind them ONCE that this is a simulation and that you are not a financial advisor. Once, not in every response."""


# --------------------------------------------------------------------------
def main():
    env = load_env()
    key = env.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        sys.exit("[!] ELEVENLABS_API_KEY missing from your .env")

    print("Verifying the API key...")
    existing = api("GET", "/convai/tools", key)
    print("    OK, the key has Agents permissions.\n")

    by_name = {}
    for t in (existing.get("tools") or existing if isinstance(existing, list) else existing.get("tools", [])):
        cfg = t.get("tool_config") or {}
        if cfg.get("name"):
            by_name[cfg["name"]] = t["id"]

    tool_ids = []
    for name, desc, params in TOOLS:
        cfg = {
            "type": "client",
            "name": name,
            "description": desc,
            "expects_response": True,      # the agent waits for the real result
            "response_timeout_secs": 20,
            "parameters": params,
        }
        if name in by_name:
            tid = by_name[name]
            api("PATCH", f"/convai/tools/{tid}", key, json={"tool_config": cfg})
            print(f"    ~ updated  {name}")
        else:
            res = api("POST", "/convai/tools", key, json={"tool_config": cfg})
            tid = res["id"]
            print(f"    + created  {name}")
        tool_ids.append(tid)

    print(f"\n{len(tool_ids)} client tools ready.\n")

    body = {
        "name": "AXIS — Trading Desk",
        "conversation_config": {
            "agent": {
                "first_message": "Terminal online. What are we looking at today?",
                "language": "en",
                "prompt": {
                    "prompt": SYSTEM_PROMPT,
                    "llm": "gpt-4.1",
                    "temperature": 0.2,
                    "tool_ids": tool_ids,
                    "built_in_tools": {},
                },
            },
            "tts": {
                # Deep, stable voice. Change it in the dashboard if you prefer another.
                "voice_id": env.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9"),
                "stability": 0.55,
                "similarity_boost": 0.75,
                "speed": 1.05,
            },
            "turn": {"turn_timeout": 8},
            "asr": {"quality": "high"},
        },
        "platform_settings": {
            "auth": {"enable_auth": True},
            "overrides": {
                "conversation_config_override": {
                    "agent": {"prompt": {"prompt": False}, "first_message": False}
                }
            },
        },
        "tags": ["hackaton", "trading"],
    }

    agent_id = env.get("ELEVENLABS_AGENT_ID", "").strip()
    if agent_id.startswith("agent_"):
        print(f"Updating existing agent {agent_id}...")
        api("PATCH", f"/convai/agents/{agent_id}", key, json=body)
    else:
        print("Creating the agent...")
        agent_id = api("POST", "/convai/agents/create", key, json=body)["agent_id"]

    write_env("ELEVENLABS_AGENT_ID", agent_id)

    print(f"""
========================================================
  Agent ready:  {agent_id}
  Written to:   {ENV_PATH}

  Restart Flask and hit MIC. Try:
    "add Nvidia to the watchlist"
    "how does Nvidia look"
    "talk to me about semiconductors"
    "open the third news item"
========================================================
""")


if __name__ == "__main__":
    main()
