# Algorithmic Trading Strategy Advisor

Voice-driven trading terminal: live Alpaca market data, Python-computed
indicators, an LLM analyst, and a continuous voice call that drives the whole UI.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
python app.py
```

Open http://localhost:5000

## Voice: two modes

The app detects at boot which mode is available and adapts. Check with
`GET /api/voice/mode`.

### Local mode — no ElevenLabs required
Browser speech recognition -> your own LLM at `/api/chat` -> speech output.
Chrome or Edge only. Trade-off: no barge-in, and a short pause between turns.

Speech output is controlled by `TTS_PROVIDER`:

| Value | Behaviour |
|---|---|
| `auto` (default) | ElevenLabs, falling back to the browser engine on any failure |
| `browser` | Browser engine only. Free, unlimited, no API key at all |
| `elevenlabs` | ElevenLabs only, no fallback |

On `auto` the app switches to the browser voice the first time ElevenLabs
fails — out of credits, revoked key, unusual-activity block — logs why, and
stops retrying for the rest of the session. The header badge shows which
engine is live.

### Agent mode — needs an agent
Full ElevenLabs Agents platform: natural turn-taking, interruptions, and the
15 client tools. Run:

```bash
python setup_agent.py
```

It creates the tools and the agent, then writes `ELEVENLABS_AGENT_ID` into your
`.env`. Idempotent: rerun it after editing `SYSTEM_PROMPT` to update in place.

Then set an allowlist entry for `http://localhost:5000` in the agent's settings.

## Client tools

All 15 tools run **in the browser**, so they reach Flask on localhost with no
tunnel or public URL. Their names must match the `clientTools` object in
`index.html` exactly — that mismatch is the number one cause of "the agent
talks but nothing happens".

| Tool | Purpose |
|---|---|
| `search_instrument` | Fuzzy search across the Alpaca universe |
| `add_to_watchlist` / `remove_from_watchlist` | Modify the watchlist |
| `list_watchlist` | Current symbols with prices |
| `set_active_symbol` | Load a symbol on the chart |
| `get_analysis` | Pre-computed metrics — the only valid source of figures |
| `run_projection` | Plot a projection from real drift and volatility |
| `compare_symbol` | Base-100 overlay |
| `clear_chart` / `set_timeframe` | Chart controls |
| `load_news` / `open_news` / `close_news` | Topic-driven news + article reader |
| `get_recommendation` | Explicit read: bias, conviction, signals, levels |
| `scan_watchlist` | Surfaces only what is notable across the watchlist |
| `toggle_indicator` | SMA/EMA/Bollinger/VWAP/volume/RSI/Fib/S-R/log scale |
| `clear_indicators` | Strip every overlay |
| `focus_panel` / `reset_layout` | Animate quadrant sizes, or return to 2x2 |
| `propose_action` | Show a pending proposal before acting unasked |

## Architecture notes

**Accuracy.** Indicators (RSI14, ATR%, SMA20/50, annualized vol, 52w range) are
computed in Python and handed to the model. The model interprets, it never
calculates. Projections are generated server-side from the asset's real drift
and sigma; the model only picks scenario, horizon and shock.

**Data honesty.** Every payload carries `source: alpaca | synthetic`. When
Alpaca fails and synthetic bars are used, that flag reaches both the prompt and
the UI badge, so a "real" analysis is never shown on invented data.

**Live updates.** SSE at `/api/stream` with a background poller. The browser
does no polling; the server pushes only what changed. The chart updates its last
candle in place rather than reloading the series.

**Animated quadrants.** One state drives `grid-template-columns/rows`. No
absolute positioning, no DOM reordering — two layout calls can never collide.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/bars/<symbol>` | OHLCV with source flag |
| `GET /api/quotes` | Snapshots for the watchlist (one upstream request) |
| `GET /api/analysis/<symbol>` | Computed indicators |
| `GET /api/search?q=` | Instrument search |
| `GET /api/news?topic=&symbol=` | Headlines by topic or symbol |
| `GET /api/article?url=` | Article body extraction |
| `POST /api/whatif` | Projection points |
| `POST /api/chat` | LLM analyst: reply + UI commands |
| `GET /api/indicators/<symbol>?which=` | Technical overlay series and levels |
| `GET /api/strategy/<symbol>` | Full four-agent pipeline: backtest, risk, recommendation, trace |
| `GET /api/backtest/<symbol>/<strategy>` | Single strategy backtest |
| `GET /api/strategies` | Strategy catalogue with theses |
| `GET /api/audit` | Last 50 pipeline runs |
| `GET /api/recommendation/<symbol>` | Bias, conviction, signals, news tone, stop/target |
| `GET /api/briefing?symbols=` | Watchlist scan: only moves >2% and RSI extremes |
| `POST /api/tts` | ElevenLabs speech (key stays server-side) |
| `GET /api/tts/test` | Open in a browser to hear the configured voice |
| `GET /api/stream` | SSE quote stream |
| `GET /api/voice/mode` | Which voice mode is available |
| `GET /api/health` | Alpaca, universe size, voice status |

## Testing

```bash
python eval_harness.py     # 26 automated checks, exits non-zero on failure
```

See ARCHITECTURE.md for the agent pipeline, honesty guarantees and design
trade-offs.

## Known limits

- IEX free feed is delayed 15 minutes. Present this as near-real-time, not live.
- Article extraction is regex over `<p>` tags; paywalled or JS-heavy sites fall
  back to the RSS summary.
- Local voice mode requires Chrome or Edge. The browser voice is noticeably
  more robotic than ElevenLabs, but it never runs out of credits. Recognition is restarted by a
  watchdog: Chrome stops it on silence, tab blur and after ~60s without notice.
- All chart and action buttons are icon-only; hover for a tooltip.
- Browsers block audio until a user gesture. The first click on MIC unlocks it;
  if you hear nothing, check `/api/tts/test` first to isolate voice from playback.
- WebRTC: if you see `/rtc/v1` 404s in the console, pin `livekit-client` to
  2.16.1 or switch `connectionType` to `websocket`.
