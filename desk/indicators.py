"""Pure technical-indicator math. No I/O, no dependencies — trivially testable."""


def ema_list(values, period):
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append((values[i] - out[-1]) * k + out[-1])
    return out


def sma_list(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
        else:
            window = values[i - period + 1:i + 1]
            out.append(sum(window) / period)
    return out


def stddev_list(values, period):
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


def rsi_list(values, period=14):
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
        if avg_loss == 0:
            return 100.0
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


def vwap_list(bars):
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


def atr_list(bars, period=14):
    """Wilder ATR: a price-unit volatility measure, not a percentage."""
    out = [None] * len(bars)
    if len(bars) < period:
        return out
    trs = []
    for i, bar in enumerate(bars):
        previous = bars[i - 1]["close"] if i else bar["close"]
        trs.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous),
                       abs(bar["low"] - previous)))
    atr = sum(trs[:period]) / period
    out[period - 1] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def stochastic_list(bars, period=14, smooth=3):
    k = [None] * len(bars)
    for i in range(period - 1, len(bars)):
        window = bars[i - period + 1:i + 1]
        high, low = max(b["high"] for b in window), min(b["low"] for b in window)
        k[i] = 100 * (bars[i]["close"] - low) / (high - low) if high != low else 50.0
    d = []
    for i in range(len(k)):
        values = [v for v in k[max(0, i - smooth + 1):i + 1] if v is not None]
        d.append(sum(values) / smooth if len(values) == smooth else None)
    return k, d


def max_drawdown(equity_curve):
    """Largest peak-to-trough decline, as a positive percentage."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return round(worst * 100, 2)


def sharpe(returns):
    """Annualisation-free Sharpe proxy: mean/stdev scaled by sample size."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = var ** 0.5
    if sd == 0:
        return 0.0
    return round((mean / sd) * (n ** 0.5), 3)


def compute_indicators(bars):
    """Chart-ready series: SMA20/50, EMA12, Bollinger 20/2, VWAP, RSI14, volume."""
    if not bars:
        return {}
    closes = [b["close"] for b in bars]
    times = [b["time"] for b in bars]

    sma20 = sma_list(closes, 20)
    sma50 = sma_list(closes, 50)
    ema12 = ema_list(closes, 12)
    ema26 = ema_list(closes, 26)
    std20 = stddev_list(closes, 20)
    bb_upper = [round(m + 2 * s, 2) if (m is not None and s is not None) else None
                for m, s in zip(sma20, std20)]
    bb_lower = [round(m - 2 * s, 2) if (m is not None and s is not None) else None
                for m, s in zip(sma20, std20)]
    vwap = vwap_list(bars)
    rsi14 = rsi_list(closes, 14)
    macd = [a - b for a, b in zip(ema12, ema26)]
    macd_signal = ema_list(macd, 9)
    macd_hist = [a - b for a, b in zip(macd, macd_signal)]
    stoch_k, stoch_d = stochastic_list(bars)
    atr14 = atr_list(bars)

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
        "macd": series(macd),
        "macd_signal": series(macd_signal),
        "macd_histogram": [
            {"time": t, "value": round(v, 4), "color": "#10b98199" if v >= 0 else "#ef444499"}
            for t, v in zip(times, macd_hist)
        ],
        "stoch_k": series(stoch_k),
        "stoch_d": series(stoch_d),
        "atr14": series(atr14),
        "volume": [
            {"time": b["time"], "value": b.get("volume") or 0,
             "color": "#10b98166" if b["close"] >= b["open"] else "#ef444466"}
            for b in bars
        ],
    }


def compute_levels(bars):
    """Support/resistance and Fibonacci retracements over the loaded range."""
    if not bars:
        return {}
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
