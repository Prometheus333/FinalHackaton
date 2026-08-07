"""
Regulatory rules engine.

Distinct from the HITL guardrails in security.py: those react to what the
agents concluded this turn (risk tolerance). These are standing compliance
constraints set up in advance and apply to every order - agent or human,
regardless of any CRO verdict.

Two rule types, editable from the UI:
    restricted_symbol - trading is blocked outright for the symbol.
    position_cap      - the resulting position may not exceed N shares.

Plus one rule that isn't editable because it isn't a policy choice: the
FINRA Pattern Day Trader restriction (Rule 4210) on accounts under $25k
equity, limited to 3 day trades per rolling 5 trading sessions.
"""
from datetime import datetime, timedelta

from . import storage

PDT_EQUITY_FLOOR = 25000.0
PDT_ROUND_TRIP_LIMIT = 3
PDT_WINDOW_DAYS = 5

RULE_TYPES = ("restricted_symbol", "position_cap")


# --------------------------------------------------------------------------
# Rule CRUD
# --------------------------------------------------------------------------
def list_rules():
    return storage.list_regulations()


def add_rule(rule_type, symbol=None, param=None):
    rule_type = (rule_type or "").strip().lower()
    if rule_type not in RULE_TYPES:
        return None, f"Unknown rule type. Must be one of: {', '.join(RULE_TYPES)}."

    symbol = (symbol or "").strip().upper() or None
    if not symbol:
        return None, "A symbol is required."

    if rule_type == "position_cap":
        try:
            param = int(param)
            if param <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return None, "Position cap needs a positive whole-share limit."
    else:
        param = (param or "").strip() or None

    rule_id = storage.add_regulation(rule_type, symbol, param)
    storage.log_audit("regulation_added",
                      {"rule_type": rule_type, "symbol": symbol, "param": param})
    return rule_id, None


def remove_rule(rule_id):
    storage.remove_regulation(rule_id)
    storage.log_audit("regulation_removed", {"id": rule_id})


def toggle_rule(rule_id, enabled):
    storage.toggle_regulation(rule_id, enabled)
    storage.log_audit("regulation_toggled", {"id": rule_id, "enabled": bool(enabled)})


# --------------------------------------------------------------------------
# Pattern Day Trader status
# --------------------------------------------------------------------------
def _day_trade_count():
    """Counts calendar days in the trailing window where a symbol was both
    bought and sold - a day trade - across executed trades."""
    cutoff = (datetime.now() - timedelta(days=PDT_WINDOW_DAYS)).isoformat(timespec="seconds")
    rows = storage.query(
        "SELECT symbol, side, ts FROM trades WHERE status='executed' AND ts >= ?",
        (cutoff,))
    sides_by_day = {}
    for r in rows:
        try:
            day = datetime.fromisoformat(r["ts"]).date()
        except ValueError:
            continue
        key = (r["symbol"], day)
        sides_by_day.setdefault(key, set()).add(r["side"])
    return sum(1 for sides in sides_by_day.values() if {"buy", "sell"} <= sides)


def pdt_status():
    from . import broker
    acct = broker.account()
    try:
        equity = float(acct.get("equity", 0))
    except (TypeError, ValueError):
        equity = 0.0
    count = _day_trade_count()
    restricted = equity < PDT_EQUITY_FLOOR
    return {
        "equity": equity,
        "equity_floor": PDT_EQUITY_FLOOR,
        "day_trades_used": count,
        "day_trade_limit": PDT_ROUND_TRIP_LIMIT,
        "window_days": PDT_WINDOW_DAYS,
        "restricted": restricted,
        "at_limit": restricted and count >= PDT_ROUND_TRIP_LIMIT,
    }


# --------------------------------------------------------------------------
# The gate every order passes through, before HITL risk logic ever runs
# --------------------------------------------------------------------------
def check(symbol, side, qty):
    """Returns (allowed, reason). A regulatory stop is absolute - it is never
    escalated for human override, only reported."""
    from . import broker

    symbol = symbol.upper()
    side = (side or "").lower()
    rules = [r for r in list_rules() if r["enabled"]]

    for r in rules:
        if r["rule_type"] == "restricted_symbol" and r["symbol"] == symbol:
            why = f" ({r['param']})" if r["param"] else ""
            return False, f"{symbol} is on the restricted-symbol list{why}. Regulation blocks all trading in this name."

    for r in rules:
        if r["rule_type"] == "position_cap" and r["symbol"] == symbol:
            current = _position_qty(symbol)
            prospective = current + qty if side == "buy" else current - qty
            cap = int(r["param"])
            if abs(prospective) > cap:
                return False, (f"This order would take the {symbol} position to {prospective:g} shares, "
                               f"above the {cap}-share regulatory position cap.")

    pdt = pdt_status()
    if pdt["at_limit"]:
        return False, (f"Pattern Day Trader limit reached: {pdt['day_trades_used']} day trades in the "
                       f"trailing {PDT_WINDOW_DAYS} sessions on ${pdt['equity']:,.0f} equity, below the "
                       f"${PDT_EQUITY_FLOOR:,.0f} FINRA minimum required to day-trade freely. Further "
                       f"same-day round trips are blocked until the window rolls off.")

    return True, "No regulatory rule applies."


def _position_qty(symbol):
    from . import broker
    for p in broker.positions():
        if isinstance(p, dict) and p.get("symbol") == symbol:
            try:
                return float(p.get("qty", 0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0
