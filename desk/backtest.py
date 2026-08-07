"""
Backtesting and strategy optimisation.

The problem statement's opening complaint is that *manual strategy tuning is
complex and time-consuming*. Evaluating one hardcoded parameter set does not
address that. `optimize` sweeps a grid of moving-average pairs, ranks them by a
risk-adjusted score, and returns the winner along with the evidence — which is
what turns this from "a backtest" into a strategy advisor.
"""
from . import config, indicators, market_data
from . import storage

# Fewer simulated periods than this and the result carries no information,
# however many candles were technically loaded.
MIN_TEST_WINDOW = 20


# --------------------------------------------------------------------------
# Single backtest
# --------------------------------------------------------------------------
def _simulate(closes, bars, sma_f, sma_s, start):
    """Long-only crossover simulation. Returns (trades, equity_curve)."""
    position = 0
    entry_price = 0.0
    entry_time = None
    trades = []
    equity = 0.0
    curve = [0.0]

    for i in range(start, len(closes)):
        if None in (sma_f[i], sma_s[i], sma_f[i - 1], sma_s[i - 1]):
            curve.append(equity)
            continue

        cross_up = sma_f[i - 1] <= sma_s[i - 1] and sma_f[i] > sma_s[i]
        cross_down = sma_f[i - 1] >= sma_s[i - 1] and sma_f[i] < sma_s[i]

        if cross_up and position == 0:
            position = 1
            entry_price = closes[i]
            entry_time = bars[i]["time"]
        elif cross_down and position == 1:
            pnl = closes[i] - entry_price
            equity += pnl
            trades.append({
                "entry_time": entry_time, "entry": round(entry_price, 2),
                "exit_time": bars[i]["time"], "exit": round(closes[i], 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / entry_price * 100, 2) if entry_price else 0.0,
                "status": "CLOSED",
            })
            position = 0

        # Mark-to-market equity while a position is open.
        unrealised = (closes[i] - entry_price) if position == 1 else 0.0
        curve.append(equity + unrealised)

    if position == 1:
        pnl = closes[-1] - entry_price
        equity += pnl
        trades.append({
            "entry_time": entry_time, "entry": round(entry_price, 2),
            "exit_time": bars[-1]["time"], "exit": round(closes[-1], 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / entry_price * 100, 2) if entry_price else 0.0,
            "status": "OPEN (mark-to-market)",
        })

    return trades, curve


def run(symbol, timeframe="1Day", fast=10, slow=30, lookback=None, persist=True):
    """Runs one parameter set and returns a full metrics dictionary."""
    symbol = symbol.upper()
    lookback = lookback or config.OPTIMIZER_LOOKBACK
    key = f"backtest:{symbol}:{timeframe}:{fast}:{slow}:{lookback}"
    cached = market_data.cache_get(key)
    if cached:
        return cached

    if fast >= slow:
        return {"symbol": symbol, "error": "Fast period must be shorter than slow period."}

    bars = market_data.get_bars(symbol, timeframe=timeframe, limit=lookback + slow + 10)
    if len(bars) < slow + 10:
        return {"symbol": symbol, "error": "Not enough history to run the backtest.",
                "bars_available": len(bars), "bars_required": slow + 10}

    closes = [b["close"] for b in bars]
    sma_f = indicators.sma_list(closes, fast)
    sma_s = indicators.sma_list(closes, slow)
    start = max(slow, len(closes) - lookback)

    # Having enough candles is not the same as having a usable test window. A
    # 500-period slow average against a 10-period lookback leaves 10 bars to
    # simulate — arithmetically fine, statistically worthless.
    window = len(closes) - start
    if window < MIN_TEST_WINDOW:
        return {"symbol": symbol,
                "error": (f"Test window too short: only {window} periods remain after the "
                          f"{slow}-period warmup. At least {MIN_TEST_WINDOW} are required "
                          f"for a meaningful result."),
                "window": window, "window_required": MIN_TEST_WINDOW}

    trades, curve = _simulate(closes, bars, sma_f, sma_s, start)

    total_pnl = round(sum(t["pnl"] for t in trades), 2)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0.0

    capital_base = closes[start] if closes[start] else (closes[-1] or 1.0)
    total_pnl_pct = round(total_pnl / capital_base * 100, 2)
    buy_hold_pct = round((closes[-1] - closes[start]) / capital_base * 100, 2)

    trade_returns = [t["pnl_pct"] for t in trades]
    sharpe_val = indicators.sharpe(trade_returns)
    # Drawdown on an equity curve rebased to the capital at risk.
    rebased = [capital_base + v for v in curve]
    dd = indicators.max_drawdown(rebased)

    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss else (
        round(gross_win, 2) if gross_win else 0.0)

    age = market_data.data_age_days(bars)
    conf = confidence(len(trades), win_rate, sharpe_val, age, len(bars))

    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "fast": fast,
        "slow": slow,
        "strategy": f"SMA {fast} x SMA {slow} (long-only)",
        "periods_tested": len(closes) - start,
        "num_trades": len(trades),
        "win_rate_pct": win_rate,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "buy_and_hold_pct": buy_hold_pct,
        "beats_buy_and_hold": total_pnl_pct > buy_hold_pct,
        "excess_return_pct": round(total_pnl_pct - buy_hold_pct, 2),
        "sharpe": sharpe_val,
        "max_drawdown_pct": dd,
        "profit_factor": profit_factor,
        "best_trade": max((t["pnl"] for t in trades), default=0.0),
        "worst_trade": min((t["pnl"] for t in trades), default=0.0),
        "open_position": any(t["status"].startswith("OPEN") for t in trades),
        "data_age_days": age,
        "confidence": conf["level"],
        "confidence_score": conf["score"],
        "confidence_reasons": conf["reasons"],
        "reflection": conf["reflection"],
        "trades": trades[-8:],
    }

    market_data.cache_set(key, data, ttl=config.CACHE_TTL_BACKTEST)
    if persist:
        storage.log_backtest(data)
    return data


# --------------------------------------------------------------------------
# Confidence scoring (replaces the hardcoded "MEDIUM")
# --------------------------------------------------------------------------
def confidence(num_trades, win_rate, sharpe_val, age_days, sample_size):
    """
    Derives a confidence level from evidence rather than asserting one.
    Returns level, 0-1 score, the reasons behind it, and a reflection flag
    telling the CRO when its own result is too thin to act on.
    """
    score = 0.0
    reasons = []

    # Sample size of trades matters most: three crossovers prove nothing.
    adequate_sample = num_trades >= config.MIN_TRADES_FOR_CONFIDENCE
    if num_trades >= 10:
        score += 0.35
        reasons.append(f"{num_trades} trades is an adequate sample")
    elif adequate_sample:
        score += 0.18
        reasons.append(f"{num_trades} trades is a thin but usable sample")
    else:
        reasons.append(f"only {num_trades} trades — below the "
                       f"{config.MIN_TRADES_FOR_CONFIDENCE}-trade minimum")

    # Win rate and Sharpe are only meaningful once the sample supports them.
    # A 100% win rate on one trade is not evidence of anything, and awarding it
    # points is how a system talks itself into a confident wrong answer.
    if adequate_sample:
        edge = abs(win_rate - 50)
        if edge >= 20:
            score += 0.25
            reasons.append(f"win rate {win_rate}% is well away from chance")
        elif edge >= 8:
            score += 0.14
            reasons.append(f"win rate {win_rate}% shows a modest edge")
        else:
            reasons.append(f"win rate {win_rate}% is close to a coin flip")

        if sharpe_val >= 1.0:
            score += 0.25
            reasons.append(f"Sharpe proxy {sharpe_val} indicates consistent returns")
        elif sharpe_val >= 0.3:
            score += 0.12
            reasons.append(f"Sharpe proxy {sharpe_val} is positive but modest")
        else:
            reasons.append(f"Sharpe proxy {sharpe_val} shows inconsistent returns")
    else:
        reasons.append(f"win rate {win_rate}% and Sharpe {sharpe_val} carry no weight "
                       f"at this sample size")

    # Data freshness and depth.
    if age_days is not None and age_days <= config.MAX_DATA_AGE_DAYS:
        score += 0.1
        reasons.append(f"data is {age_days} day(s) old")
    elif age_days is not None:
        reasons.append(f"data is stale ({age_days} days old)")
    else:
        reasons.append("data recency could not be determined")

    if sample_size >= 120:
        score += 0.05
        reasons.append(f"{sample_size} candles of history")
    else:
        reasons.append(f"only {sample_size} candles of history")

    score = round(min(score, 1.0), 2)
    level = "HIGH" if score >= 0.7 else ("MEDIUM" if score >= 0.4 else "LOW")

    reflection = None
    if num_trades < config.MIN_TRADES_FOR_CONFIDENCE:
        reflection = (
            f"REFLECTION: this backtest produced only {num_trades} trade(s), below the "
            f"{config.MIN_TRADES_FOR_CONFIDENCE}-trade minimum for a statistically meaningful "
            f"result. Do not issue a confident verdict from it — either request a longer "
            f"lookback window or state explicitly that the evidence is insufficient.")

    return {"level": level, "score": score, "reasons": reasons, "reflection": reflection}


# --------------------------------------------------------------------------
# Parameter sweep
# --------------------------------------------------------------------------
def _rank_score(bt):
    """
    Risk-adjusted ranking. Sharpe leads because raw PnL rewards a single lucky
    trade; excess return over buy & hold breaks ties; drawdown penalises.
    Configurations below the trade minimum are pushed down rather than dropped,
    so the UI can still show why they lost.
    """
    if bt.get("error"):
        return -999.0
    penalty = 0.0
    if bt["num_trades"] < config.MIN_TRADES_FOR_CONFIDENCE:
        penalty += 2.0
    return round(
        bt["sharpe"] * 1.0
        + (bt["excess_return_pct"] / 100.0) * 1.5
        - (bt["max_drawdown_pct"] / 100.0) * 0.8
        - penalty, 4)


def optimize(symbol, timeframe="1Day", lookback=None, fast_grid=None, slow_grid=None):
    """
    Sweeps the parameter grid and returns the ranked results.

    This is the piece that answers "backtest performance improvements" as a
    measurable success metric: we can state how many configurations were tested
    and by how much the best one beat the default.
    """
    symbol = symbol.upper()
    lookback = lookback or config.OPTIMIZER_LOOKBACK
    fast_grid = fast_grid or config.OPTIMIZER_FAST_GRID
    slow_grid = slow_grid or config.OPTIMIZER_SLOW_GRID

    key = f"optimize:{symbol}:{timeframe}:{lookback}:{len(fast_grid)}x{len(slow_grid)}"
    cached = market_data.cache_get(key)
    if cached:
        return cached

    results = []
    errors = 0
    for f in fast_grid:
        for s in slow_grid:
            if f >= s:
                continue
            bt = run(symbol, timeframe=timeframe, fast=f, slow=s,
                     lookback=lookback, persist=False)
            if bt.get("error"):
                errors += 1
                continue
            bt["rank_score"] = _rank_score(bt)
            results.append(bt)

    if not results:
        return {"symbol": symbol, "error": "No parameter combination could be evaluated.",
                "combinations_attempted": errors}

    results.sort(key=lambda b: b["rank_score"], reverse=True)
    best = results[0]
    default = next((r for r in results if r["fast"] == 10 and r["slow"] == 30), None)

    def slim(b):
        return {
            "fast": b["fast"], "slow": b["slow"], "strategy": b["strategy"],
            "num_trades": b["num_trades"], "win_rate_pct": b["win_rate_pct"],
            "total_pnl_pct": b["total_pnl_pct"], "excess_return_pct": b["excess_return_pct"],
            "sharpe": b["sharpe"], "max_drawdown_pct": b["max_drawdown_pct"],
            "profit_factor": b["profit_factor"], "confidence": b["confidence"],
            "rank_score": b["rank_score"],
        }

    improvement = None
    if default:
        improvement = round(best["total_pnl_pct"] - default["total_pnl_pct"], 2)

    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "lookback": lookback,
        "combinations_tested": len(results),
        "combinations_failed": errors,
        "buy_and_hold_pct": best["buy_and_hold_pct"],
        "best": slim(best),
        "best_full": best,
        "default": slim(default) if default else None,
        "improvement_vs_default_pct": improvement,
        "leaderboard": [slim(r) for r in results[:8]],
        "worst": slim(results[-1]),
        "heatmap": [
            {"fast": r["fast"], "slow": r["slow"], "score": r["rank_score"],
             "pnl_pct": r["total_pnl_pct"]}
            for r in results
        ],
    }

    market_data.cache_set(key, data, ttl=config.CACHE_TTL_BACKTEST)
    storage.log_backtest(best)
    storage.log_audit("optimizer_run", {
        "symbol": symbol, "tested": len(results),
        "best": f"{best['fast']}x{best['slow']}",
        "improvement_vs_default_pct": improvement})
    return data


# --------------------------------------------------------------------------
# LLM-facing summaries (compact, so they do not eat the context window)
# --------------------------------------------------------------------------
def summary_text(bt):
    if bt.get("error"):
        return f"Backtest for {bt.get('symbol')}: {bt['error']}"
    verdict = "BEATS" if bt["beats_buy_and_hold"] else "does NOT beat"
    text = (
        f"BACKTEST {bt['symbol']} | {bt['strategy']} | Periods: {bt['periods_tested']} | "
        f"Trades: {bt['num_trades']} | Win rate: {bt['win_rate_pct']}% | "
        f"Theoretical PnL: ${bt['total_pnl']} ({bt['total_pnl_pct']}%) | "
        f"Buy & hold: {bt['buy_and_hold_pct']}% | The strategy {verdict} buy & hold "
        f"(excess {bt['excess_return_pct']}%) | Sharpe: {bt['sharpe']} | "
        f"Max drawdown: {bt['max_drawdown_pct']}% | Profit factor: {bt['profit_factor']} | "
        f"CONFIDENCE: {bt['confidence']} (score {bt['confidence_score']}) because "
        f"{'; '.join(bt['confidence_reasons'])}."
    )
    if bt.get("reflection"):
        text += f"\n{bt['reflection']}"
    return text


def optimizer_summary_text(opt):
    if opt.get("error"):
        return f"Optimizer for {opt.get('symbol')}: {opt['error']}"
    b = opt["best"]
    lines = [
        f"STRATEGY SWEEP {opt['symbol']} | {opt['combinations_tested']} parameter "
        f"combinations tested over {opt['lookback']} candles.",
        f"BEST: SMA {b['fast']}x{b['slow']} | Return {b['total_pnl_pct']}% | "
        f"Excess vs buy & hold {b['excess_return_pct']}% | Trades {b['num_trades']} | "
        f"Win rate {b['win_rate_pct']}% | Sharpe {b['sharpe']} | "
        f"Max drawdown {b['max_drawdown_pct']}% | Confidence {b['confidence']}.",
    ]
    if opt.get("default"):
        d = opt["default"]
        lines.append(
            f"DEFAULT (SMA {d['fast']}x{d['slow']}) returned {d['total_pnl_pct']}%. "
            f"Tuning improved return by {opt['improvement_vs_default_pct']} percentage points.")
    lines.append(
        "Runner-ups: " + "; ".join(
            f"SMA {r['fast']}x{r['slow']} ({r['total_pnl_pct']}%)"
            for r in opt["leaderboard"][1:4]))
    lines.append(
        "These are historical simulations on past candles, not a forecast. Past "
        "performance does not guarantee future results.")
    return "\n".join(lines)
