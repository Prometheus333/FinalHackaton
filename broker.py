"""
Order routing and the human-approval queue.

Every order in the system passes through `submit`, which consults the
authorization gate before anything touches the broker. There is deliberately
no code path that lets an agent skip it.
"""
import uuid
import threading
from datetime import datetime

import requests

from . import config, market_data, regulation, storage, trace

_pending = {}
_pending_lock = threading.Lock()


def headers():
    return {
        "APCA-API-KEY-ID": config.ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }


# --------------------------------------------------------------------------
# Broker I/O
# --------------------------------------------------------------------------
def place_order(symbol, side, qty):
    """Sends a market order. Returns (ok, payload_or_message)."""
    symbol = symbol.upper()
    side = side.lower()

    if config.RUNTIME.get("synthetic_mode"):
        quote = market_data.get_latest_quote(symbol)
        return True, {
            "id": f"sim-{uuid.uuid4().hex[:8]}", "symbol": symbol, "qty": qty,
            "side": side, "status": "accepted", "filled_avg_price": quote["price"],
            "simulated": True,
        }

    payload = {"symbol": symbol, "qty": qty, "side": side,
               "type": "market", "time_in_force": "day"}

    def _post():
        r = requests.post(f"{config.ALPACA_TRADING_URL}/orders", headers=headers(),
                          json=payload, timeout=config.HTTP_TIMEOUT)
        if r.status_code in (200, 201):
            return r.json()
        try:
            msg = r.json().get("message", r.text)
        except Exception:
            msg = r.text
        raise RuntimeError(msg)

    ok, res = market_data.with_retry(_post, label=f"alpaca:order:{symbol}")
    return (True, res) if ok else (False, str(res))


def account():
    if config.RUNTIME.get("synthetic_mode"):
        return {"equity": "100000.00", "buying_power": "200000.00", "simulated": True}
    ok, res = market_data.with_retry(
        lambda: requests.get(f"{config.ALPACA_TRADING_URL}/account",
                             headers=headers(), timeout=config.HTTP_TIMEOUT).json(),
        label="alpaca:account")
    return res if ok else {"error": str(res)}


def positions():
    if config.RUNTIME.get("synthetic_mode"):
        rows = storage.query(
            """SELECT symbol, SUM(CASE WHEN side='buy' THEN qty ELSE -qty END) q
               FROM trades WHERE status='executed' GROUP BY symbol HAVING q > 0""")
        out = []
        for r in rows:
            price = market_data.get_latest_quote(r["symbol"])["price"]
            out.append({"symbol": r["symbol"], "qty": str(r["q"]),
                        "unrealized_pl": "0.00", "current_price": str(price),
                        "simulated": True})
        return out
    ok, res = market_data.with_retry(
        lambda: requests.get(f"{config.ALPACA_TRADING_URL}/positions",
                             headers=headers(), timeout=config.HTTP_TIMEOUT).json(),
        label="alpaca:positions")
    return res if ok else {"error": str(res)}


def open_orders():
    if config.RUNTIME.get("synthetic_mode"):
        return []
    ok, res = market_data.with_retry(
        lambda: requests.get(f"{config.ALPACA_TRADING_URL}/orders?status=open",
                             headers=headers(), timeout=config.HTTP_TIMEOUT).json(),
        label="alpaca:open_orders")
    return res if ok else {"error": str(res)}


# --------------------------------------------------------------------------
# Approval queue
# --------------------------------------------------------------------------
def queue_for_approval(symbol, side, qty, reason):
    ticket = uuid.uuid4().hex[:8]
    record = {
        "id": ticket, "symbol": symbol.upper(), "side": side.lower(), "qty": qty,
        "status": "pending", "reason": reason,
        "created": datetime.now().strftime("%H:%M:%S"),
    }
    with _pending_lock:
        _pending[ticket] = record
    storage.log_approval_created(ticket, symbol.upper(), side.lower(), qty, reason)
    storage.log_trade(symbol.upper(), side.lower(), qty, "held_for_approval",
                      source="agent", cro_verdict=trace.auth().cro_verdicts.get(symbol.upper()),
                      session_id=trace.current().session_id,
                      turn_id=trace.current().turn_id)
    return record


def get_pending(ticket=None):
    with _pending_lock:
        if ticket:
            return _pending.get(ticket)
        return [t for t in _pending.values() if t["status"] == "pending"]


def resolve(ticket, decision):
    """Human approves or rejects a held order. Returns (http_status, payload)."""
    decision = (decision or "").lower()
    if decision not in ("approve", "reject"):
        return 400, {"error": "Invalid decision."}

    with _pending_lock:
        record = _pending.get(ticket)
        if not record:
            return 404, {"error": "Ticket not found."}
        if record["status"] != "pending":
            return 409, {"error": f"This ticket was already {record['status']}."}
        record["status"] = "processing" if decision == "approve" else "rejected"

    if decision == "reject":
        storage.log_approval_resolved(ticket, "reject", "not_executed")
        storage.log_trade(record["symbol"], record["side"], record["qty"],
                          "rejected_by_human", source="hitl", approved_by="human")
        storage.log_audit("hitl_reject", record, severity="info")
        return 200, {
            "id": ticket, "status": "rejected",
            "message": (f"Order {record['side'].upper()} {record['qty']} "
                        f"{record['symbol']} rejected by the user."),
        }

    ok, payload = place_order(record["symbol"], record["side"], record["qty"])
    with _pending_lock:
        record["status"] = "executed" if ok else "failed"

    storage.log_approval_resolved(ticket, "approve", "executed" if ok else "failed")
    storage.log_trade(record["symbol"], record["side"], record["qty"],
                      "executed" if ok else "failed", source="hitl",
                      approved_by="human", broker_response=payload)
    storage.log_audit("hitl_approve", {"ticket": ticket, "ok": ok},
                      severity="info")

    if ok:
        return 200, {
            "id": ticket, "status": "executed",
            "message": (f"Order {record['side'].upper()} {record['qty']} "
                        f"{record['symbol']} sent to the broker."),
        }
    return 400, {"id": ticket, "status": "failed",
                 "message": f"The broker rejected the order: {payload}"}


# --------------------------------------------------------------------------
# The single authorized entry point for agent-initiated orders
# --------------------------------------------------------------------------
def submit(symbol, side, qty, source="agent"):
    """
    Runs the authorization gate, then either executes, escalates, or denies.
    Returns a message for the agent — never raises.
    """
    symbol = symbol.upper()
    side = (side or "").lower()

    if side not in ("buy", "sell"):
        return "Invalid side. It must be 'buy' or 'sell'."
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        return "Invalid quantity. It must be a whole number of shares."
    if qty <= 0:
        return "Invalid quantity. It must be greater than zero."

    allowed, reg_reason = regulation.check(symbol, side, qty)
    if not allowed:
        trace.add(0, "guardrail", "regulation_denied", f"{side} {qty} {symbol}", reg_reason)
        storage.log_trade(symbol, side, qty, "regulation_denied", source=source)
        storage.log_audit("regulation_denied", {"symbol": symbol, "side": side,
                                                 "qty": qty, "reason": reg_reason},
                          severity="warning")
        return (f"ORDER DENIED BY REGULATION. {reg_reason} This is a compliance stop, not a "
                f"risk judgement - it cannot be escalated for approval. Explain the refusal "
                f"to the user.")

    quote = market_data.get_latest_quote(symbol)
    price = quote.get("price") or None

    decision, reason = trace.auth().authorize(
        symbol, side, qty, price=price,
        qty_threshold=config.HITL_QTY_THRESHOLD,
        turn_qty_threshold=config.HITL_TURN_QTY_THRESHOLD,
        notional_threshold=config.HITL_NOTIONAL_THRESHOLD,
    )
    trace.auth().register_quantity(symbol, qty)

    if decision == "deny":
        trace.add(0, "guardrail", "authorization_denied",
                  f"{side} {qty} {symbol}", reason)
        storage.log_trade(symbol, side, qty, "denied", source=source,
                          cro_verdict=trace.auth().cro_verdicts.get(symbol))
        storage.log_audit("order_denied", {"symbol": symbol, "side": side,
                                           "qty": qty, "reason": reason},
                          severity="warning")
        return (f"ORDER DENIED. {reason} Do not attempt to place this order again "
                f"in this turn. Explain the refusal to the user.")

    if decision == "escalate":
        record = queue_for_approval(symbol, side, qty, reason)
        trace.set_pending(record)
        trace.add_escalation(reason, f"{side} {qty} {symbol} -> ticket {record['id']}")
        return (f"ORDER HELD FOR HUMAN APPROVAL. {reason} The order was NOT sent to the "
                f"broker. It is queued as ticket {record['id']}. Do NOT retry, do NOT "
                f"split it into smaller orders — cumulative quantity is tracked and "
                f"splitting will be blocked too. Tell the user the order is on hold and "
                f"awaiting their decision in the approval card.")

    ok, payload = place_order(symbol, side, qty)
    trace.add(0, "portfolio_manager", "execute_trade", f"{side} {qty} {symbol}",
              "order sent to broker" if ok else f"broker rejected: {payload}")
    storage.log_trade(symbol, side, qty, "executed" if ok else "failed",
                      source=source, approved_by="auto",
                      cro_verdict=trace.auth().cro_verdicts.get(symbol),
                      broker_response=payload,
                      session_id=trace.current().session_id,
                      turn_id=trace.current().turn_id)

    if ok:
        return (f"EXECUTED: {side.upper()} {qty} shares of {symbol}. "
                f"Authorized automatically because: {reason}")
    return f"ORDER FAILED at the broker: {payload}"
