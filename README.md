# Capital Markets — Algorithmic Trading Strategy Advisor

A multi-agent desk that tunes, backtests, risk-scores and (conditionally) executes
algorithmic trading strategies. Built for AI Friday Season 2 — Regional Finale.

---

## Quick start

```bash
python -m pip install -r requirements.txt

# Demo mode — deterministic synthetic market, no live feed or broker needed
SYNTHETIC_MODE=1 python app.py

# Live mode
export ALPACA_KEY_ID=...  ALPACA_SECRET_KEY=...  LLM_API_KEY=...
python app.py
```

Windows PowerShell:

```powershell
$env:SYNTHETIC_MODE="1"; python app.py
```

Then open <http://localhost:5000>.

Run the evaluation suite (no LLM endpoint required):

```bash
python eval_harness.py
python eval_harness.py --json results.json
```

---

## Architecture

```
                        ┌──────────────────────────┐
   trader  ──────────►  │   PORTFOLIO MANAGER      │   supervisor / orchestrator
                        │   (holds no data tools)  │
                        └────────────┬─────────────┘
                                     │ explicit tool-call handoffs
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
   │ DATA ANALYST       │ │ STRATEGY OPTIMIZER │ │ CHIEF RISK OFFICER │
   │ prices, screened   │ │ 35-combination     │ │ independent        │
   │ news, technicals   │ │ parameter sweep    │ │ verification,      │
   │ → ANALYST BIAS     │ │ → RECOMMENDED      │ │ → RISK VERDICT     │
   └────────────────────┘ │   PARAMETERS       │ └────────────────────┘
                          └────────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  AUTHORIZATION GATE      │  enforced in code
                        │  allow / escalate / deny │  (not in the prompt)
                        └────────────┬─────────────┘
                              ┌──────┴──────┐
                              ▼             ▼
                        broker API    human approval queue
```

The Portfolio Manager deliberately has **no data tools of its own**. Every number in
its answer must have arrived through a specialist, which means every number appears
in the trace. There is no path by which it can assert an unsourced fact.

### Module layout

| Module | Responsibility |
|---|---|
| `desk/config.py` | All thresholds, endpoints, model names, feature flags |
| `desk/storage.py` | SQLite: trades, backtests, approvals, traces, audit log |
| `desk/security.py` | Injection filter + the trade authorization gate |
| `desk/indicators.py` | Pure indicator math (no I/O) |
| `desk/market_data.py` | Ingestion, retry, cache, synthetic feed, news screening |
| `desk/backtest.py` | Backtest engine, parameter sweep, confidence scoring |
| `desk/strategy.py` | Deterministic signals and risk levels |
| `desk/trace.py` | Per-turn chain of thought, token and latency meters |
| `desk/broker.py` | Order routing, approval queue |
| `desk/agents.py` | The only module that imports LangChain |
| `desk/ui.py` | Frontend |
| `app.py` | Flask routes only |
| `eval_harness.py` | 49 automated scenarios |

Swapping the model, the broker or the vector store touches `config.py` and one
module. `app.py` never imports LangChain, so the whole data and API surface still
works if the LLM endpoint is down — the desk degrades instead of dying.

---

## What each checklist item maps to

**Strategy optimization.** `backtest.optimize()` sweeps 35 moving-average pairs and
ranks them by a risk-adjusted score (Sharpe, excess return over buy & hold, drawdown
penalty). Configurations below the trade minimum are pushed down rather than dropped,
so the leaderboard shows *why* they lost. The `⚙ OPTIMIZE` button surfaces the whole
table; the Strategy Optimizer agent calls the same function.

**Backtesting.** Long-only crossover simulation with mark-to-market on the open
position. Reports PnL, win rate, Sharpe, max drawdown, profit factor and excess
return versus buy & hold.

**Human approval checkpoints.** Four independent escalation triggers, all enforced in
`security.TurnAuthorization`, not in a prompt:

- single order above the share limit
- **cumulative** quantity per symbol per turn — closes the "split it into three
  smaller orders" bypass that a prompt instruction alone cannot
- notional ceiling regardless of share count
- **agent disagreement** — analyst bearish while the CRO approves a long is an
  uncertain decision, so a human decides

Plus two hard denials: no CRO verdict on record, or a CRO REJECT.

**Prompt injection.** News headlines are third-party text flowing toward an agent that
can place orders. They are scanned against 14 pattern families, redacted, stripped of
zero-width smuggling characters, and wrapped in a data-only envelope with the reminder
repeated *after* the content. Blocked items never enter the model context; the UI shows
the block and the audit log records it. Tick **injection test** in the header to
demonstrate it live.

**Groundedness.** Every agent prompt carries the same grounding rule: numbers come from
tools or are not stated. Tools return an explicit `NO DATA` string rather than empty,
so the model cannot quietly fill the gap. Facts and assumptions are separate fields.

**Confidence.** Derived from evidence — trade count, distance of win rate from chance,
Sharpe, data age, sample depth — never hardcoded. Below the trade minimum, win rate and
Sharpe earn **zero** credit: a 100% win rate on one trade is not evidence, and awarding
it points is how a system talks itself into a confident wrong answer.

**Reflection.** A backtest under the trade minimum attaches a REFLECTION warning, and
the CRO's prompt requires REJECT when it sees one. An unproven edge is not a tradeable
edge.

**Retry.** Exponential backoff on every external call, with the retry recorded in the
trace. Sub-agent invocation retries once before the turn fails.

**Traceability.** Every step — agent, tool, input, output, latency, tokens — is captured
per turn, rendered as a collapsible panel, and persisted to SQLite. `/api/trace/<turn_id>`
reconstructs any past turn.

**Security & governance.** No credentials in code; `.env.example` ships empty. Every
order is written with the CRO verdict that authorised it. Approval tickets are
replay-protected. `enforce_retention()` purges past the retention window.
`/api/audit/tail` exposes the log.

**Context engineering.** Tool outputs are compact single-line summaries rather than raw
JSON; trace inputs truncate at 500 chars and outputs at 900; chat memory is capped at 20
messages; three cache tiers by volatility (30s quotes, 120s derived, 300s backtests);
token and latency counters are shown per turn, not estimated.

**Testing.** 49 scenarios across accuracy, edge cases, groundedness, safety, latency,
optimisation, reliability and traceability. None require the LLM endpoint — the
guardrails that matter must not depend on a model behaving well on the day. The suite
found two real bugs during development: a backtest that accepted a statistically
meaningless 10-period window, and a confidence score that rated a single lucky trade
MEDIUM.

**Demo readiness.** Four synthetic regimes (normal / rally / crash / choppy), switchable
from the header, deterministic and reproducible. The **vs baseline** toggle runs the
same question through a single tool-less model side by side.

---

## Demo script (6 minutes)

1. **Set the scene.** Header → `Sim: normal`. Show the dashboard.
2. **The conventional way.** Tick **vs baseline**, ask
   *"Should I buy AAPL right now?"* — the baseline answers confidently with no data.
   The desk answers with sourced findings, tuned parameters, a risk verdict and a stop.
3. **Strategy tuning — the actual problem.** Click `⚙ OPTIMIZE`. 35 combinations
   backtested in ~30 ms; the tuned parameters beat the default by ~10 percentage points.
   This is the "manual tuning is slow" complaint, answered.
4. **Risk behaviour under stress.** Switch to `Sim: crash`, re-run OPTIMIZE. The tuned
   strategy returns roughly **-1.5%** where buy & hold loses **-88%**.
5. **The guardrail.** Ask *"buy 20 shares of AAPL"* → held for approval. Then ask
   *"fine, just do four orders of five"* → blocked by cumulative tracking. This is the
   moment worth rehearsing: a prompt cannot enforce this, code can.
6. **Injection.** Tick **injection test**, reload the news panel. The hostile headline
   shows as BLOCKED with its detected categories, and never reaches the model.
7. **Evidence.** Open **Agent Trace** — every hop with latency and tokens. Then
   `python eval_harness.py` — 49/49.

---

## Known limits

- Long-only crossover strategies. No shorting, options, or portfolio-level optimisation.
- The parameter sweep is exhaustive over a fixed grid; it does not walk-forward
  validate, so it is exposed to overfitting on the lookback window. State this plainly
  rather than letting a judge find it.
- Sentiment classification is a single LLM call with no human label set behind it.
- SQLite suits a single-node prototype; a multi-node deployment needs Postgres.
- Synthetic mode is for demonstration and testing, not for evaluating real strategies.
