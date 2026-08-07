"""
strategy_engine.py — backtesting, risk scoring and strategy selection.

Kept as its own module so the AI layer, the market-data layer and the strategy
layer stay separable: each can be swapped or tested without touching the others.

Nothing here calls an LLM. Every number a model later reads out loud is produced
by this file, deterministically, from real bars. That separation is the whole
point: the model explains, it does not compute.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

TRADING_DAYS = 252
# Round-trip cost assumption. Backtests that ignore costs flatter every
# high-frequency strategy, which is the classic way to overfit a demo.
COST_BPS = 5.0


# --------------------------------------------------------------------------
# Indicators (local, so the module has no dependency on app.py)
# --------------------------------------------------------------------------
def _sma(v, n, i):
    return sum(v[i - n + 1: i + 1]) / n if i >= n - 1 else None


def _rsi_at(v, i, n=14):
    if i < n:
        return None
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        d = v[j] - v[j - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


# --------------------------------------------------------------------------
# Strategies — each returns a target position (1 = long, 0 = flat) per bar.
# Long/flat only: shorting needs borrow assumptions this prototype cannot make.
# --------------------------------------------------------------------------
def sig_buy_hold(bars, **kw):
    return [1] * len(bars)


def sig_sma_crossover(bars, fast=20, slow=50, **kw):
    c = [b["close"] for b in bars]
    out = []
    for i in range(len(c)):
        f, s = _sma(c, fast, i), _sma(c, slow, i)
        out.append(1 if (f is not None and s is not None and f > s) else 0)
    return out


def sig_rsi_mean_reversion(bars, period=14, low=30, high=60, **kw):
    c = [b["close"] for b in bars]
    out, pos = [], 0
    for i in range(len(c)):
        r = _rsi_at(c, i, period)
        if r is not None:
            if r < low:
                pos = 1          # oversold: enter
            elif r > high:
                pos = 0          # recovered: exit
        out.append(pos)
    return out


def sig_breakout(bars, lookback=20, **kw):
    out, pos = [], 0
    for i in range(len(bars)):
        if i < lookback:
            out.append(0)
            continue
        window = bars[i - lookback:i]
        hi = max(b["high"] for b in window)
        lo = min(b["low"] for b in window)
        if bars[i]["close"] > hi:
            pos = 1
        elif bars[i]["close"] < lo:
            pos = 0
        out.append(pos)
    return out


def sig_momentum(bars, lookback=60, **kw):
    c = [b["close"] for b in bars]
    out = []
    for i in range(len(c)):
        if i < lookback or not c[i - lookback]:
            out.append(0)
            continue
        out.append(1 if c[i] / c[i - lookback] - 1 > 0 else 0)
    return out


STRATEGIES = {
    "buy_hold": {
        "fn": sig_buy_hold, "label": "Buy and hold",
        "family": "baseline", "params": {},
        "thesis": "Own the asset throughout. The benchmark every other strategy must beat.",
    },
    "sma_crossover": {
        "fn": sig_sma_crossover, "label": "SMA crossover (20/50)",
        "family": "trend", "params": {"fast": 20, "slow": 50},
        "thesis": "Hold while the 20-day sits above the 50-day. Captures sustained trends, "
                  "whipsaws in ranges.",
    },
    "rsi_mean_reversion": {
        "fn": sig_rsi_mean_reversion, "label": "RSI mean reversion (14, 30/60)",
        "family": "mean-reversion", "params": {"period": 14, "low": 30, "high": 60},
        "thesis": "Buy oversold, exit on recovery. Works in ranges, gets run over in "
                  "sustained downtrends.",
    },
    "breakout": {
        "fn": sig_breakout, "label": "Donchian breakout (20)",
        "family": "trend", "params": {"lookback": 20},
        "thesis": "Enter on a 20-day high, exit on a 20-day low. Few trades, fat tails.",
    },
    "momentum": {
        "fn": sig_momentum, "label": "Momentum (60d)",
        "family": "trend", "params": {"lookback": 60},
        "thesis": "Hold while the 60-day return is positive. Slow to turn, low turnover.",
    },
}


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------
def backtest(bars: list[dict], strategy: str, params: dict | None = None) -> dict:
    """Runs one strategy over the bars and reports performance and risk.

    Positions are applied with a one-bar lag: the signal computed on today's
    close can only be traded from tomorrow. Without that lag a backtest is
    reading the future, which is the single most common way these are wrong.
    """
    spec = STRATEGIES.get(strategy)
    if not spec:
        return {"error": f"unknown strategy '{strategy}'"}
    if len(bars) < 80:
        return {"error": f"need at least 80 bars, got {len(bars)}"}

    p = {**spec["params"], **(params or {})}
    raw = spec["fn"](bars, **p)
    positions = [0] + raw[:-1]                      # one-bar execution lag

    closes = [b["close"] for b in bars]
    equity, cash_curve = 1.0, [1.0]
    trades, wins, gross_win, gross_loss = [], 0, 0.0, 0.0
    entry_price, bars_in_market = None, 0
    cost = COST_BPS / 10000.0

    for i in range(1, len(closes)):
        if closes[i - 1]:
            ret = closes[i] / closes[i - 1] - 1
        else:
            ret = 0.0
        if positions[i]:
            equity *= 1 + ret
            bars_in_market += 1
        if positions[i] != positions[i - 1]:
            equity *= 1 - cost                      # charged on every switch
            if positions[i] == 1:
                entry_price = closes[i]
            elif entry_price:
                pnl = closes[i] / entry_price - 1
                trades.append(round(pnl * 100, 2))
                if pnl > 0:
                    wins += 1
                    gross_win += pnl
                else:
                    gross_loss += abs(pnl)
                entry_price = None
        cash_curve.append(equity)

    # Open position at the end is marked to market so results are not flattered.
    if entry_price:
        pnl = closes[-1] / entry_price - 1
        trades.append(round(pnl * 100, 2))
        if pnl > 0:
            wins += 1
            gross_win += pnl
        else:
            gross_loss += abs(pnl)

    years = max(len(closes) / TRADING_DAYS, 1e-9)
    total_return = equity - 1
    cagr = (equity ** (1 / years) - 1) if equity > 0 else -1.0

    daily = [cash_curve[i] / cash_curve[i - 1] - 1
             for i in range(1, len(cash_curve)) if cash_curve[i - 1]]
    mean = sum(daily) / len(daily) if daily else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in daily) / len(daily)) if len(daily) > 1 else 0.0
    sharpe = (mean / sd * math.sqrt(TRADING_DAYS)) if sd else 0.0
    downside = [r for r in daily if r < 0]
    dsd = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0.0
    sortino = (mean / dsd * math.sqrt(TRADING_DAYS)) if dsd else 0.0

    peak, max_dd = cash_curve[0], 0.0
    for v in cash_curve:
        peak = max(peak, v)
        if peak:
            max_dd = max(max_dd, (peak - v) / peak)

    return {
        "strategy": strategy,
        "label": spec["label"],
        "family": spec["family"],
        "thesis": spec["thesis"],
        "params": p,
        "bars": len(bars),
        "period": {"from": bars[0]["time"][:10], "to": bars[-1]["time"][:10]},
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "volatility_pct": round(sd * math.sqrt(TRADING_DAYS) * 100, 1),
        "trades": len(trades),
        "win_rate_pct": round(wins / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "exposure_pct": round(bars_in_market / len(closes) * 100, 1),
        "best_trade_pct": max(trades) if trades else None,
        "worst_trade_pct": min(trades) if trades else None,
        "equity_curve": [
            {"time": bars[i]["time"], "value": round(cash_curve[i], 4)}
            for i in range(0, len(cash_curve), max(1, len(cash_curve) // 180))
        ],
        "cost_assumption_bps": COST_BPS,
        "execution_lag_bars": 1,
    }


# --------------------------------------------------------------------------
# Risk scoring
# --------------------------------------------------------------------------
def risk_score(bt: dict) -> dict:
    """0-100, higher is riskier. Every component is stated so a trader can
    disagree with the weighting rather than with a black box."""
    if bt.get("error"):
        return {"error": bt["error"]}

    dd = bt["max_drawdown_pct"]
    vol = bt["volatility_pct"]
    sharpe = bt["sharpe"]
    trades = bt["trades"]

    comp = {
        "drawdown": min(dd / 50 * 40, 40),                   # 50% DD saturates
        "volatility": min(vol / 60 * 30, 30),                # 60% annual vol saturates
        "risk_adjusted": max(0.0, min((1.5 - sharpe) / 1.5 * 20, 20)),
        "sample": 10.0 if trades < 10 else (5.0 if trades < 25 else 0.0),
    }
    score = round(sum(comp.values()))
    band = "LOW" if score < 30 else "MODERATE" if score < 55 else "HIGH"

    warnings = []
    if trades < 10:
        warnings.append(f"only {trades} trades — not statistically meaningful")
    if dd > 35:
        warnings.append(f"max drawdown {dd}% would be hard to sit through")
    if bt["exposure_pct"] < 20:
        warnings.append(f"in the market only {bt['exposure_pct']}% of the time")
    if sharpe < 0:
        warnings.append("negative Sharpe: the strategy lost money risk-adjusted")

    return {"score": score, "band": band,
            "components": {k: round(v, 1) for k, v in comp.items()},
            "warnings": warnings}


# --------------------------------------------------------------------------
# Market regime — context for why a family fits
# --------------------------------------------------------------------------
def detect_regime(bars: list[dict]) -> dict:
    closes = [b["close"] for b in bars]
    if len(closes) < 60:
        return {"regime": "unknown", "reason": "not enough history"}

    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    slope = (sma20 / (sum(closes[-40:-20]) / 20) - 1) * 100 if len(closes) >= 40 else 0.0

    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 60, len(closes))
            if closes[i - 1]]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    vol_ann = sd * math.sqrt(TRADING_DAYS) * 100

    hi, lo = max(closes[-60:]), min(closes[-60:])
    span = (hi - lo) / lo * 100 if lo else 0.0

    if abs(slope) > 3 and sma20 > sma50:
        regime, favours = "trending up", ["trend"]
    elif abs(slope) > 3 and sma20 < sma50:
        regime, favours = "trending down", ["trend"]
    elif span < 15:
        regime, favours = "range bound", ["mean-reversion"]
    else:
        regime, favours = "choppy", ["mean-reversion", "baseline"]

    return {"regime": regime, "favours_families": favours,
            "sma20_slope_pct": round(slope, 2),
            "annualized_vol_pct": round(vol_ann, 1),
            "60d_range_pct": round(span, 1),
            "reason": f"20-day slope {round(slope, 2)}%, 60-day range {round(span, 1)}%, "
                      f"volatility {round(vol_ann, 1)}%"}


def forecast_path(bars: list[dict], bt: dict, days: int = 30) -> dict:
    """Projects the price forward using the backtested strategy's own return and
    volatility profile. This is the forward-looking view: the equity curve says
    what the strategy DID, this says what it implies from here.
    """
    if bt.get("error") or not bars:
        return {"points": [], "error": bt.get("error", "no bars")}

    last = bars[-1]["close"]
    cagr = bt["cagr_pct"] / 100.0
    vol = bt["volatility_pct"] / 100.0
    daily = (1 + cagr) ** (1 / TRADING_DAYS) - 1

    try:
        d0 = datetime.fromisoformat(bars[-1]["time"][:10])
    except Exception:  # noqa: BLE001
        d0 = datetime.now()

    mid, hi, lo = [], [], []
    price, i, added = last, 1, 0
    while added < days and i < days * 3:
        d = d0 + timedelta(days=i)
        i += 1
        if d.weekday() >= 5:
            continue
        price *= 1 + daily
        band = vol * math.sqrt(added / TRADING_DAYS)
        iso = d.date().isoformat()
        mid.append({"time": iso, "value": round(price, 2)})
        hi.append({"time": iso, "value": round(price * math.exp(band), 2)})
        lo.append({"time": iso, "value": round(price * math.exp(-band), 2)})
        added += 1

    return {
        "from_price": last,
        "days": added,
        "median": mid, "high": hi, "low": lo,
        "end_median": mid[-1]["value"] if mid else None,
        "end_low": lo[-1]["value"] if lo else None,
        "end_high": hi[-1]["value"] if hi else None,
        "implied_return_pct": round((mid[-1]["value"] / last - 1) * 100, 2) if mid else None,
        "basis": (f"{bt['label']}: {bt['cagr_pct']}% a year with {bt['volatility_pct']}% "
                  f"volatility, measured over {bt['bars']} bars"),
    }


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------
def rank_strategies(bars: list[dict], symbol: str) -> dict:
    """Backtests every strategy, scores risk, and picks one — with the reason
    and an explicit confidence, including the case for NOT trading."""
    regime = detect_regime(bars)
    results = []
    for name in STRATEGIES:
        bt = backtest(bars, name)
        if bt.get("error"):
            continue
        rk = risk_score(bt)
        # Risk-adjusted utility: Sharpe, penalised by drawdown, small bonus for
        # matching the regime. Deliberately simple and fully inspectable.
        fit = 0.25 if bt["family"] in regime.get("favours_families", []) else 0.0
        utility = bt["sharpe"] - (bt["max_drawdown_pct"] / 100) + fit
        bt["risk"] = rk
        bt["regime_fit"] = fit > 0
        bt["utility"] = round(utility, 3)
        results.append(bt)

    if not results:
        return {"symbol": symbol, "error": "no strategy could be evaluated"}

    results.sort(key=lambda x: -x["utility"])
    best = results[0]
    baseline = next((r for r in results if r["strategy"] == "buy_hold"), None)

    edge = None
    if baseline and best["strategy"] != "buy_hold":
        edge = round(best["cagr_pct"] - baseline["cagr_pct"], 2)

    # Confidence is earned, not asserted.
    conf, why = 50, []
    if best["trades"] >= 25:
        conf += 15
        why.append(f"{best['trades']} trades in sample")
    elif best["trades"] < 10:
        conf -= 20
        why.append(f"only {best['trades']} trades")
    if best["bars"] >= 400:
        conf += 10
        why.append(f"{best['bars']} bars of history")
    else:
        conf -= 10
        why.append(f"short history ({best['bars']} bars)")
    if edge is not None and edge > 3:
        conf += 15
        why.append(f"beats buy-and-hold by {edge}pp a year")
    elif edge is not None and edge <= 0:
        conf -= 25
        why.append("does not beat buy-and-hold")
    if best["risk"]["band"] == "HIGH":
        conf -= 10
        why.append("high risk band")
    conf = max(5, min(95, conf))

    recommend = best["strategy"]
    caveat = None
    if best["strategy"] == "buy_hold":
        caveat = ("No active strategy beat buy-and-hold on this history, so holding "
                  "is the recommendation. That is a result, not a failure.")
    if edge is not None and edge <= 0:
        recommend = "buy_hold"
        caveat = ("No active strategy beat buy-and-hold on this history. "
                  "Holding is the honest recommendation.")
    if best["cagr_pct"] <= 0:
        # Beating buy-and-hold while still losing money is not a recommendation.
        caveat = (f"The best available strategy still lost money over this period "
                  f"({best['cagr_pct']}% a year). Losing less than buy-and-hold is not "
                  f"a reason to trade this name.")
    if conf < 35:
        caveat = caveat or ("Confidence is low — treat this as a starting point for "
                            "research, not a signal.")

    override = None
    if recommend != best["strategy"]:
        override = {
            "top_ranked": best["strategy"],
            "top_ranked_label": best["label"],
            "why": (f"{best['label']} ranked highest on risk-adjusted utility, but it "
                    f"did not beat simply holding the asset. Trading costs and risk "
                    f"without an edge is worse than doing nothing."),
        }

    return {
        "symbol": symbol,
        "as_of": bars[-1]["time"][:10],
        "override": override,
        "regime": regime,
        "recommended": recommend,
        "recommended_label": STRATEGIES[recommend]["label"],
        "confidence_pct": conf,
        "confidence_drivers": why,
        "edge_vs_buy_hold_pp": edge,
        "caveat": caveat,
        "ranking": [
            {k: r[k] for k in ("strategy", "label", "family", "cagr_pct", "sharpe",
                               "max_drawdown_pct", "trades", "win_rate_pct",
                               "exposure_pct", "utility", "regime_fit")}
            | {"risk_band": r["risk"]["band"], "risk_score": r["risk"]["score"]}
            for r in results
        ],
        "best_detail": best,
        "method": {
            "utility": "sharpe - (max_drawdown/100) + 0.25 if family matches regime",
            "execution_lag_bars": 1,
            "cost_bps_per_switch": COST_BPS,
            "position": "long/flat only",
        },
    }
