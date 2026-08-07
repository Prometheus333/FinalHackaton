"""
Deterministic strategy and signal generation.

Kept separate from the agents so it can be tested, replayed and audited
without an LLM in the loop — and so the agents' claims can be checked against
it for groundedness.
"""
from datetime import datetime, timedelta

from . import market_data
from . import config, indicators


def recommendation(symbol, timeframe="1Day"):
    """Momentum signal plus a forward projection, with a derived confidence."""
    symbol = symbol.upper()
    key = f"ai_rec:{symbol}:{timeframe}"
    cached = market_data.cache_get(key)
    if cached:
        return cached

    bars = market_data.get_bars(symbol, timeframe=timeframe, limit=60)
    if not bars or len(bars) < 20:
        return {"signal": "N/A", "confidence": "NONE",
                "rationale": "Market data unavailable from the feed.",
                "confidence_reasons": ["no usable candles returned"],
                "projection": []}

    closes = [b["close"] for b in bars]
    window = closes[-20:]
    trend = window[-1] / (window[0] if window[0] else 1)

    sig = "BUY" if trend > 1.02 else "SELL" if trend < 0.98 else "HOLD"

    # Confidence derived from evidence rather than hardcoded.
    ema20 = indicators.ema_list(closes, 20)[-1]
    rsi = indicators.rsi_list(closes, 14)[-1]
    age = market_data.data_age_days(bars)

    reasons = []
    score = 0.0
    strength = abs(trend - 1) * 100
    if strength >= 5:
        score += 0.35
        reasons.append(f"20-period move of {strength:.1f}% is a clear trend")
    elif strength >= 2:
        score += 0.2
        reasons.append(f"20-period move of {strength:.1f}% is a modest trend")
    else:
        reasons.append(f"20-period move of {strength:.1f}% is close to flat")

    aligned = (sig == "BUY" and closes[-1] > ema20) or (sig == "SELL" and closes[-1] < ema20)
    if aligned:
        score += 0.25
        reasons.append("price and EMA20 agree with the signal")
    elif sig != "HOLD":
        reasons.append("price and EMA20 disagree with the signal")

    if rsi is not None:
        if (sig == "BUY" and rsi < 70) or (sig == "SELL" and rsi > 30) or sig == "HOLD":
            score += 0.2
            reasons.append(f"RSI {rsi:.0f} is not stretched against the signal")
        else:
            reasons.append(f"RSI {rsi:.0f} is stretched against the signal")

    if age is not None and age <= config.MAX_DATA_AGE_DAYS:
        score += 0.2
        reasons.append(f"data is {age} day(s) old")
    else:
        reasons.append("data recency is poor or unknown")

    score = round(min(score, 1.0), 2)
    level = "HIGH" if score >= 0.7 else ("MEDIUM" if score >= 0.4 else "LOW")

    data = {
        "signal": sig,
        "confidence": level,
        "confidence_score": score,
        "confidence_reasons": reasons,
        "rationale": (f"Momentum over the last 20 periods ({timeframe}); "
                      f"close {closes[-1]} vs EMA20 {round(ema20, 2)}, RSI "
                      f"{round(rsi, 1) if rsi is not None else 'n/a'}."),
        "assumptions": [
            "Momentum is measured on closing prices only; intraday moves are ignored.",
            "The projection is a constant-drift extrapolation, not a forecast model.",
        ],
    }

    try:
        last_time = bars[-1]["time"]
        last_date = (datetime.fromtimestamp(last_time) if isinstance(last_time, (int, float))
                     else datetime.fromisoformat(last_time[:10]))
    except Exception:
        last_date = datetime.now()

    drift = 0.005 if sig == "BUY" else (-0.005 if sig == "SELL" else 0.0)
    projection = []
    price = closes[-1]
    for i in range(1, 8):
        price *= (1 + drift)
        if timeframe in ("5Min", "15Min", "1Hour"):
            minutes = {"5Min": 5, "15Min": 15, "1Hour": 60}[timeframe]
            d = last_date + timedelta(minutes=i * minutes)
            time = int(d.timestamp())
        else:
            d = last_date + timedelta(days=i * (7 if timeframe == "1Week" else 1))
            time = d.date().isoformat()
        projection.append({"time": time, "value": round(price, 2)})
    data["projection"] = projection

    market_data.cache_set(key, data, ttl=config.CACHE_TTL_DERIVED)
    return data


def levels(symbol, timeframe="1Day"):
    """Entry, stop-loss, take-profit and risk/reward for a symbol."""
    symbol = symbol.upper()
    key = f"strategy:{symbol}:{timeframe}"
    cached = market_data.cache_get(key)
    if cached:
        return cached

    bars = market_data.get_bars(symbol, timeframe=timeframe, limit=60)
    if not bars or len(bars) < 10:
        return {"signal": "N/A", "error": "Not enough data to build a strategy."}

    closes = [b["close"] for b in bars]
    last_close = closes[-1]
    last_time = bars[-1]["time"]

    lookback = bars[-20:] if len(bars) >= 20 else bars
    swing_high = max(b["high"] for b in lookback)
    swing_low = min(b["low"] for b in lookback)

    ema20 = indicators.ema_list(closes, 20)[-1]
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
        positive = (f"If price holds above the ${entry} entry, bullish momentum could "
                    f"carry it toward the ${target} target.")
        negative = (f"If price breaks below the ${stop} support, the long idea is "
                    f"invalidated; exit to cap the loss.")
    elif signal == "SELL":
        stop = round(max(swing_high, entry * (1 + risk_pct)), 2)
        risk = max(stop - entry, 0.01)
        target = round(entry - risk * reward_ratio, 2)
        positive = (f"If price stays below the ${entry} entry, bearish pressure could "
                    f"drive it toward ${target}.")
        negative = (f"If price breaks above the ${stop} resistance, the short idea is "
                    f"invalidated; exit to cap the loss.")
    else:
        stop = round(swing_low, 2)
        target = round(swing_high, 2)
        risk = max(entry - stop, 0.01)
        positive = (f"While it holds above ${stop}, the bias stays neutral-to-constructive; "
                    f"watch for a break above ${target}.")
        negative = (f"A break below ${stop} would flip the bias bearish; there is no clean "
                    f"entry signal for now.")

    rr = round(abs(target - entry) / risk, 2) if risk else None

    data = {
        "symbol": symbol, "signal": signal, "entry": entry,
        "stop_loss": stop, "take_profit": target, "risk_reward": rr,
        "entry_time": last_time,
        "positive_scenario": positive, "negative_scenario": negative,
        "rationale": (f"Based on {len(bars)}-period momentum ({timeframe}), EMA20 "
                      f"(${round(ema20, 2)}) and the support/resistance range of the "
                      f"last {len(lookback)} candles."),
        "facts": {
            "last_close": last_close, "ema20": round(ema20, 2),
            "swing_high": round(swing_high, 2), "swing_low": round(swing_low, 2),
        },
        "assumptions": [
            f"Risk sized at {risk_pct * 100:.0f}% or the recent swing, whichever is wider.",
            f"Target set at a {reward_ratio}:1 reward-to-risk ratio.",
        ],
    }
    market_data.cache_set(key, data, ttl=config.CACHE_TTL_DERIVED)
    return data
