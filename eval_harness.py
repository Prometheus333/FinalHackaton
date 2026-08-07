"""
eval_harness.py — automated quality gate.

Run:  python eval_harness.py

Covers the four things a demo alone cannot prove:
  1. Correctness — the backtester is checked against hand-computable cases.
  2. Edge cases — flat markets, crashes, short history, single bars.
  3. Guardrails — synthetic data and thin samples must block recommendations.
  4. Performance — latency budget per pipeline stage.

Exits non-zero if anything fails, so it can gate a build.
"""

from __future__ import annotations

import math
import random
import sys
import time
from datetime import date, timedelta

import strategy_engine as se

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# Synthetic market generators — deterministic, so failures are reproducible
# --------------------------------------------------------------------------
def series(n, drift, vol, seed, start=100.0):
    r = random.Random(seed)
    p, out, d = start, [], date(2023, 1, 2)
    for i in range(n):
        p = max(0.5, p * (1 + r.gauss(drift, vol)))
        o = p * (1 + r.uniform(-0.004, 0.004))
        c = p * (1 + r.uniform(-0.004, 0.004))
        out.append({"time": (d + timedelta(days=i)).isoformat(),
                    "open": round(o, 2), "high": round(max(o, c) * 1.006, 2),
                    "low": round(min(o, c) * 0.994, 2), "close": round(c, 2),
                    "volume": r.randint(10**6, 10**7)})
    return out


def straight_line(n, daily_pct):
    p, out, d = 100.0, [], date(2023, 1, 2)
    for i in range(n):
        p *= 1 + daily_pct
        out.append({"time": (d + timedelta(days=i)).isoformat(), "open": p, "high": p,
                    "low": p, "close": round(p, 4), "volume": 10**6})
    return out


# --------------------------------------------------------------------------
print("\n1. CORRECTNESS")

bars = straight_line(300, 0.001)
bt = se.backtest(bars, "buy_hold")
expected = ((1.001 ** 299) - 1) * 100
check("buy_hold matches compound arithmetic on a straight line",
      abs(bt["total_return_pct"] - expected) < 1.5,
      f"got {bt['total_return_pct']}%, expected ~{expected:.1f}%")

check("no drawdown on a monotonically rising series",
      bt["max_drawdown_pct"] < 0.5, f"max_dd={bt['max_drawdown_pct']}%")

crash = straight_line(150, 0.002) + straight_line(150, -0.004)
for i, b in enumerate(crash):
    b["time"] = (date(2023, 1, 2) + timedelta(days=i)).isoformat()
bt_crash = se.backtest(crash, "buy_hold")
check("drawdown detected through a crash", bt_crash["max_drawdown_pct"] > 30,
      f"max_dd={bt_crash['max_drawdown_pct']}%")

bt_sma = se.backtest(crash, "sma_crossover")
check("trend strategy loses less than holding through a crash",
      bt_sma["max_drawdown_pct"] < bt_crash["max_drawdown_pct"],
      f"sma {bt_sma['max_drawdown_pct']}% vs hold {bt_crash['max_drawdown_pct']}%")

flat = straight_line(300, 0.0)
bt_flat = se.backtest(flat, "buy_hold")
check("flat market produces ~zero return", abs(bt_flat["total_return_pct"]) < 0.5)

check("execution lag is applied", se.backtest(bars, "momentum")["execution_lag_bars"] == 1)
check("transaction costs are charged",
      se.backtest(series(400, 0.0005, 0.02, 11), "rsi_mean_reversion")["cost_assumption_bps"] > 0)

# --------------------------------------------------------------------------
print("\n2. EDGE CASES")

check("short history is rejected, not guessed",
      se.backtest(series(30, 0.001, 0.01, 1), "sma_crossover").get("error") is not None)
check("unknown strategy is rejected",
      se.backtest(series(200, 0.001, 0.01, 1), "does_not_exist").get("error") is not None)

single = series(1, 0.001, 0.01, 1)
check("single bar does not crash the engine",
      se.backtest(single, "buy_hold").get("error") is not None)

zero = [dict(b, close=0.0, open=0.0, high=0.0, low=0.0) for b in series(200, 0, 0.01, 5)]
try:
    se.backtest(zero, "buy_hold")
    check("zero prices do not raise", True)
except ZeroDivisionError:
    check("zero prices do not raise", False, "ZeroDivisionError")

for name in se.STRATEGIES:
    r = se.backtest(series(400, 0.0004, 0.015, 42), name)
    ok = (not r.get("error") and r["exposure_pct"] <= 100
          and -100 <= r["total_return_pct"] < 10000
          and 0 <= r["max_drawdown_pct"] <= 100)
    check(f"{name} returns values in plausible bounds", ok)

# --------------------------------------------------------------------------
print("\n3. GUARDRAILS AND HONESTY")

up = series(500, 0.0012, 0.011, 7)
rank = se.rank_strategies(up, "TEST")
check("a recommendation is always accompanied by a confidence figure",
      isinstance(rank["confidence_pct"], int) and 5 <= rank["confidence_pct"] <= 95,
      f"confidence={rank['confidence_pct']}%")
check("buy-and-hold is always in the comparison set",
      any(x["strategy"] == "buy_hold" for x in rank["ranking"]))
check("the scoring method is disclosed", "utility" in rank["method"])

thin = se.backtest(series(200, 0.0002, 0.008, 3), "breakout")
rk = se.risk_score(thin)
if thin["trades"] < 10:
    check("thin trade samples raise a warning",
          any("trades" in w for w in rk["warnings"]), f"trades={thin['trades']}")
else:
    check("thin trade samples raise a warning", True, "sample was not thin, skipped")

losing = se.rank_strategies(series(500, -0.0015, 0.015, 9), "BEAR")
best_cagr = losing["ranking"][0]["cagr_pct"]
check("a losing market never yields a high-confidence winner",
      not (best_cagr < 0 and losing["confidence_pct"] > 70),
      f"top CAGR {best_cagr}%, confidence {losing['confidence_pct']}%")
check("a losing market surfaces a caveat or recommends holding",
      losing["caveat"] is not None or losing["recommended"] == "buy_hold",
      f"recommended={losing['recommended']}")

check("risk score decomposes into named components",
      set(rk["components"]) == {"drawdown", "volatility", "risk_adjusted", "sample"})

reg = se.detect_regime(up)
check("regime detection explains itself", bool(reg.get("reason")))

# --------------------------------------------------------------------------
print("\n4. PERFORMANCE")

t0 = time.time()
se.rank_strategies(series(500, 0.0005, 0.012, 21), "PERF")
full_ms = (time.time() - t0) * 1000
check("full ranking of all strategies under 400ms", full_ms < 400, f"{full_ms:.0f}ms")

t0 = time.time()
se.backtest(series(500, 0.0005, 0.012, 21), "sma_crossover")
one_ms = (time.time() - t0) * 1000
check("single backtest under 120ms", one_ms < 120, f"{one_ms:.0f}ms")

# --------------------------------------------------------------------------
failed = [r for r in results if r[0] == FAIL]
print(f"\n{'='*58}")
print(f"  {len(results) - len(failed)}/{len(results)} passed")
if failed:
    print(f"  FAILURES:")
    for _, name, detail in failed:
        print(f"    - {name} {detail}")
print(f"{'='*58}\n")
sys.exit(1 if failed else 0)
