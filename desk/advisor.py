"""Evidence-based strategy-advisor assessments built from available OHLCV data."""

from . import indicators, market_data
from . import strategy


def _volume_ratio(bars, period=20):
    if len(bars) < 2:
        return None
    volumes = [b.get("volume") or 0 for b in bars[-period:]]
    average = sum(volumes[:-1]) / max(1, len(volumes) - 1)
    return round(volumes[-1] / average, 2) if average else None


STRATEGIES = {
    "momentum": ("Momentum alignment", ["20-period price momentum", "Price relative to EMA20", "RSI exhaustion check", "Latest volume versus 20-period average", "ATR volatility and setup R:R"]),
    "breakout": ("Breakout confirmation", ["Close must exceed the prior 20-period high", "Latest volume should be at least 1.2× average", "ATR determines whether the move is too extended"]),
    "pullback": ("Trend pullback", ["Price must be above the EMA20", "RSI should be between 40 and 60", "Price should be within 2% of the EMA20"]),
    "mean_reversion": ("Mean reversion", ["RSI should be at or below 30 for a long reversal", "Price should be below the EMA20", "Volatility must be manageable"]),
}


def assess(symbol, timeframe="1Day", strategy_mode="momentum"):
    """Return a transparent assessment; no claim is made when data is insufficient."""
    bars = market_data.get_bars(symbol, timeframe=timeframe, limit=80)
    if len(bars) < 30:
        return {"symbol": symbol.upper(), "error": "Not enough market history for an assessment."}

    closes = [b["close"] for b in bars]
    last = closes[-1]
    ema20 = indicators.ema_list(closes, 20)[-1]
    rsi = indicators.rsi_list(closes, 14)[-1]
    atr = indicators.atr_list(bars, 14)[-1]
    change20 = (last / closes[-21] - 1) * 100
    ema_slope = (ema20 / indicators.ema_list(closes, 20)[-6] - 1) * 100
    volume_ratio = _volume_ratio(bars)
    atr_pct = (atr / last * 100) if atr and last else 0
    levels = indicators.compute_levels(bars[-20:])
    setup = strategy.levels(symbol, timeframe)

    uptrend = change20 > 2 and last > ema20 and ema_slope > 0
    downtrend = change20 < -2 and last < ema20 and ema_slope < 0
    volatility = "High Volatility" if atr_pct >= 3 else "Low Volatility" if atr_pct <= 1 else "Normal Volatility"
    direction = "Trending Up" if uptrend else "Trending Down" if downtrend else "Range Bound"
    regime = f"{volatility} {direction}" if direction.startswith("Trending") else direction

    bullish, bearish, conflicts = [], [], []
    if last > ema20:
        bullish.append(f"Price (${last:.2f}) is above the 20-period EMA (${ema20:.2f}).")
    else:
        bearish.append(f"Price (${last:.2f}) is below the 20-period EMA (${ema20:.2f}).")
    if change20 > 0:
        bullish.append(f"20-period price momentum is +{change20:.1f}%.")
    else:
        bearish.append(f"20-period price momentum is {change20:.1f}%.")
    if volume_ratio is not None:
        (bullish if volume_ratio >= 1.2 else bearish if volume_ratio < 0.8 else conflicts).append(
            f"Latest volume is {volume_ratio:.2f}× its recent average.")
    if rsi is not None:
        if rsi >= 70:
            bearish.append(f"RSI ({rsi:.1f}) is extended; upside may be less forgiving.")
        elif rsi <= 30:
            bullish.append(f"RSI ({rsi:.1f}) is oversold; downside momentum may be exhausted.")
        else:
            conflicts.append(f"RSI ({rsi:.1f}) is neutral rather than decisive.")
    if atr_pct >= 3:
        conflicts.append(f"ATR is {atr_pct:.1f}% of price, so stops may need more room and size may need to be lower.")

    strategy_mode = strategy_mode if strategy_mode in STRATEGIES else "momentum"
    score = 50
    score += 18 if uptrend else -18 if downtrend else 0
    score += 10 if (volume_ratio or 0) >= 1.2 else -8 if (volume_ratio or 1) < 0.8 else 0
    score += 8 if rsi is not None and 45 <= rsi <= 65 else -8 if rsi is not None and (rsi >= 75 or rsi <= 25) else 0
    score += 7 if setup.get("risk_reward", 0) >= 2 else -7 if setup.get("risk_reward", 0) < 1.25 else 0
    score -= 8 if atr_pct >= 4 else 0
    prior_high = max(b["high"] for b in bars[-21:-1])
    near_ema = abs(last - ema20) / ema20 <= 0.02 if ema20 else False
    if strategy_mode == "breakout":
        score = 35 + (30 if last > prior_high else 0) + (20 if (volume_ratio or 0) >= 1.2 else 0) - (10 if atr_pct >= 4 else 0)
    elif strategy_mode == "pullback":
        score = 30 + (30 if last > ema20 else 0) + (25 if rsi is not None and 40 <= rsi <= 60 else 0) + (15 if near_ema else 0)
    elif strategy_mode == "mean_reversion":
        score = 25 + (40 if rsi is not None and rsi <= 30 else 0) + (20 if last < ema20 else 0) + (15 if atr_pct < 3 else 0)
    score = max(0, min(100, score))

    confirmation = (f"A close above ${levels['resistance']:.2f} with volume above its recent average."
                    if setup.get("signal") != "SELL" else
                    f"A break below ${levels['support']:.2f} with volume above its recent average.")
    invalidation = (f"A sustained break below ${setup.get('stop_loss', levels['support']):.2f}."
                    if setup.get("signal") != "SELL" else
                    f"A sustained break above ${setup.get('stop_loss', levels['resistance']):.2f}.")
    return {
        "symbol": symbol.upper(), "timeframe": timeframe, "quality_score": score,
        "quality_label": "Strong" if score >= 70 else "Mixed" if score >= 45 else "Weak",
        "strategy": {"id": strategy_mode, "name": STRATEGIES[strategy_mode][0],
                     "rules": STRATEGIES[strategy_mode][1]},
        "regime": regime, "facts": {"price": round(last, 2), "ema20": round(ema20, 2),
        "rsi14": round(rsi, 1) if rsi is not None else None, "atr14": round(atr, 2) if atr else None,
        "atr_pct": round(atr_pct, 2), "volume_ratio": volume_ratio, "momentum_20_pct": round(change20, 2)},
        "bull_case": bullish, "bear_case": bearish, "conflicting_signals": conflicts,
        "confirmation_needed": confirmation, "invalidation": invalidation, "trade_setup": setup,
        "disclaimer": "Strategy Quality measures alignment of available technical evidence; it is not a probability of profit.",
    }


def challenge(symbol, timeframe="1Day", strategy_mode="momentum"):
    assessment = assess(symbol, timeframe, strategy_mode)
    if assessment.get("error"):
        return assessment
    setup = assessment["trade_setup"]
    weakness = assessment["bear_case"][0] if assessment["bear_case"] else "No decisive bearish technical evidence was found."
    missing = assessment["conflicting_signals"][0] if assessment["conflicting_signals"] else "Wait for volume confirmation before treating the move as confirmed."
    alternative = ("A failed breakout that returns below resistance would favor a wait-or-short scenario."
                   if setup.get("signal") != "SELL" else "A reclaim of resistance would invalidate the short premise.")
    return {**assessment, "strategy_options": [
        {"id": key, "name": val[0]} for key, val in STRATEGIES.items()], "challenge": {
        "strongest_weakness": weakness,
        "biggest_risk": f"{assessment['facts']['atr_pct']:.1f}% ATR can make a tight stop unreliable.",
        "missing_confirmation": missing,
        "invalidation_condition": assessment["invalidation"],
        "alternative_scenario": alternative,
        "suggested_improvement": assessment["confirmation_needed"],
    }}


def risk_calculation(account_size, risk_pct, entry, stop, target):
    values = [account_size, risk_pct, entry, stop, target]
    if any(v is None or v <= 0 for v in values) or entry == stop:
        return {"error": "Enter positive account, risk, entry, stop, and target values; entry and stop must differ."}
    max_risk = account_size * risk_pct / 100
    per_share_risk = abs(entry - stop)
    shares = int(max_risk // per_share_risk)
    position_value = shares * entry
    potential_profit = shares * abs(target - entry)
    return {"account_size": round(account_size, 2), "max_risk_pct": round(risk_pct, 2),
            "max_risk_amount": round(max_risk, 2), "position_size": shares,
            "position_value": round(position_value, 2), "maximum_loss": round(shares * per_share_risk, 2),
            "potential_profit": round(potential_profit, 2),
            "risk_reward": round(abs(target - entry) / per_share_risk, 2),
            "target_pct": round((target - entry) / entry * 100, 2),
            "stop_pct": round((stop - entry) / entry * 100, 2)}
