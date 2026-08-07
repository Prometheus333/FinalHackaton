# Architecture — Algorithmic Trading Strategy Advisor

## The problem, restated

A trader cannot evaluate a strategy quickly. Pulling history, coding a backtest,
computing risk metrics and comparing against a baseline is a day of work per
idea, so most ideas are never tested. This system does that loop in under a
second and explains its reasoning well enough to be argued with.

---

## 1. Agentic architecture

Four specialists in a sequential pipeline with explicit handoffs. Sequential,
not parallel, because each stage genuinely depends on the previous one — a
parallel graph here would be architecture theatre.

| Agent | Role | Input | Output | Failure behaviour |
|---|---|---|---|---|
| **DataAgent** | Fetch and validate market data | symbol, timeframe | OHLCV + quality report | Aborts the run; no downstream agent sees partial data |
| **BacktestAgent** | Simulate every strategy over real history | bars | ranked results + regime | Returns error; pipeline stops rather than guessing |
| **RiskAgent** | Score risk, apply approval gates | best result | risk band, warnings, gate | Degrades to `review_required` |
| **StrategyAgent** | Select a strategy, set the approval path | ranking + risk | decision + confidence | Defaults to proposing, never auto-acting |

Every step records agent, role, status, duration and output. The trace is
returned to the UI and written to an audit log at `/api/audit`.

**Autonomy boundary.** The system recommends; it never executes. Read-only
operations (analysis, backtests, news) run freely. Anything that changes the
workspace or would place an order requires explicit human approval. The
`RiskAgent` raises `review_required` on drawdown above 35%, fewer than 10
trades, confidence below 40%, or stale data — and the UI renders a sign-off
button rather than acting.

**Escalation.** Synthetic or insufficient data blocks strategy recommendations
entirely. A backtest on invented prices is worse than no backtest, because it
looks authoritative.

---

## 2. Where the numbers come from

The single most important design decision: **the LLM never calculates.**

```
Alpaca OHLCV ──► strategy_engine.py ──► deterministic metrics ──► LLM ──► narration
                 (pure Python, no AI)                             (explains only)
```

Indicators, backtests, risk scores, allocations and projections are computed in
Python. The model receives finished numbers and turns them into language. It
cannot invent a Sharpe ratio, because it never sees the price series.

This is testable in a way that a prompt is not: `eval_harness.py` checks the
backtester against hand-computable cases, and passes 26/26.

**Grounding and provenance.** Every payload carries `source: alpaca | synthetic`
and a staleness flag, which reach both the prompt and the UI badge. Absent
fields are omitted rather than sent as null, so the model has nothing to
speculate about. Recommendations ship with the signals that produced them, a
confidence figure with its drivers, and the scoring formula in plain text.

---

## 3. Honesty guarantees

These were the hardest constraints to enforce and are the ones most demos skip.

- **Buy-and-hold is always in the comparison set.** If nothing beats holding,
  the system recommends holding and says so.
- **Losing less is not winning.** If the best strategy still has a negative
  CAGR, that is stated as a caveat. This case was caught by the eval harness,
  not by inspection.
- **One-bar execution lag.** Signals computed on today's close trade tomorrow.
  Without it a backtest reads the future.
- **Transaction costs on every switch** (5 bps). Ignoring them flatters
  high-frequency strategies.
- **Open positions marked to market**, so results are not flattered by an
  unclosed winner.
- **Confidence is earned**: it moves with sample size, history length, edge over
  baseline and risk band, and is capped at 95%.
- **HOLD is a valid output.** An engine that always finds a trade is broken.

---

## 4. Technical implementation

**Modularity.** `strategy_engine.py` has no dependency on Flask, the LLM, or
Alpaca — it takes bars and returns metrics, so it can be tested in isolation and
swapped out. `app.py` owns data access and orchestration. The UI is a single
file with no build step.

**Configurability.** Model, base URL, data feed, voice provider and TTS engine
are all environment variables. The LLM speaks an OpenAI-compatible API, so
swapping providers is a URL change. Voice degrades from ElevenLabs to the
browser engine automatically.

**Context engineering.** Per-datatype TTL caching (3s snapshots, 30s intraday,
5min daily, 3min news). One LLM call per turn with pre-computed context, JSON
mode, temperature 0.1, 300 max tokens. Conversation memory is an 8-turn deque
per session. The instrument universe (~11k assets) is loaded once at boot and
matched locally, so search costs nothing per query.

**Live updates.** SSE with a server-side poller pushing only deltas; the browser
never polls. The chart updates its last candle in place.

**Security.** No secret reaches the browser: the ElevenLabs key, the LLM key and
the Alpaca credentials all stay server-side, with voice tokens minted per
session. Voice tools run as browser client tools, so no public tunnel is needed
and the server is never exposed. Audit log retains the last 200 runs.

---

## 5. Testing

`python eval_harness.py` — 26 automated checks across four categories:

| Category | What it proves |
|---|---|
| Correctness | Backtester matches hand-computable compound returns; drawdown detected through a crash; trend strategy beats holding in a crash |
| Edge cases | Short history, single bar, zero prices, unknown strategy, bounds on every strategy |
| Guardrails | Confidence always present, baseline always compared, thin samples warned, bear markets caveated |
| Performance | Full ranking under 400ms (actual: 10ms), single backtest under 120ms (actual: 3ms) |

Exits non-zero on failure, so it can gate a build.

---

## 6. AI versus a conventional approach

| | Conventional | This system |
|---|---|---|
| Test one strategy | Hours of coding | 10 ms |
| Compare five strategies | A day | One click, ranked with risk |
| Understand why | Read the code | Signals, regime fit and confidence drivers in plain language |
| Ask a follow-up | Rewrite the script | Ask out loud, mid-call |
| Non-quant access | None | Conversational |

The AI contribution is not the numbers — those are deterministic and always
were. It is the *interface to reasoning*: a trader can interrogate five
backtests conversationally while watching the chart, without writing code.

---

## 7. Known limits

Stated because a system that hides them cannot be trusted with money.

- IEX free feed is delayed 15 minutes. Near-real-time, not live.
- Long/flat only. Shorting needs borrow-cost assumptions this prototype cannot make.
- No slippage or market-impact model beyond flat basis points.
- Backtests are in-sample: no walk-forward validation or parameter optimisation,
  which means overfitting is possible and is not currently measured.
- Sector classification is a curated lookup, partial by construction.
- The simulator projects historical drift forward with a volatility band. It is
  not a forecast.
- Paper trading only. No order execution path exists, by design.

**Path to enterprise.** Walk-forward validation and out-of-sample splits; a
proper time-series store instead of in-memory caching; per-user identity and
permissions on the audit log; SIP feed for true real-time; and a broker
execution adapter behind a mandatory human approval gate.
