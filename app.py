"""
AI Trading Terminal - Capital Markets Algorithmic Trading Strategy Advisor.

Agentic architecture:
    Portfolio Manager (orchestrator)
      |-- data_analyst_agent        ingestion: prices, screened news, technicals
      |-- strategy_optimizer_agent  parameter sweep across the strategy grid
      +-- cro_risk_agent            independent risk verdict + required stop-loss

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000

Demo without a live feed:
    SYNTHETIC_MODE=1 python app.py
"""
import os
import threading
import warnings
from datetime import datetime, timedelta

import urllib3
from flask import Flask, request, jsonify, Response

from desk import (config, backtest, broker, indicators, market_data,
                  regulation, storage, strategy, trace, ui, advisor)

# --------------------------------------------------------------------------
# Corporate proxy tolerance (TCS network)
# --------------------------------------------------------------------------
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", module="urllib3")

import requests  # noqa: E402

_original_request = requests.Session.request


def _patched_request(self, method, url, **kwargs):
    kwargs["verify"] = False
    return _original_request(self, method, url, **kwargs)


requests.Session.request = _patched_request

app = Flask(__name__)
storage.init()
storage.log_audit("service_start", config.public_config())

# The agent layer is imported lazily so the data/API surface still works if the
# LLM endpoint or langchain-classic is unavailable - a demo should degrade, not die.
agents = None
AGENTS_ERROR = None
try:
    from desk import agents as _agents
    agents = _agents
except Exception as e:  # pragma: no cover
    AGENTS_ERROR = str(e)
    app.logger.warning(f"Agent layer unavailable: {e}")

chat_memory = []
threading.Thread(target=lambda: market_data.load_all_assets(broker.headers),
                 daemon=True).start()


# ==========================================================================
# Market data
# ==========================================================================
@app.route("/api/config")
def api_config():
    cfg = config.public_config()
    cfg["agents_available"] = agents is not None
    cfg["agents_error"] = AGENTS_ERROR
    cfg["cache"] = market_data.cache_stats()
    return jsonify(cfg)


@app.route("/api/search")
def api_search():
    return jsonify(market_data.search_assets(request.args.get("q", "")))


@app.route("/api/bars/<symbol>")
def api_bars(symbol):
    tf = request.args.get("timeframe", "1Day")
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    bars = market_data.get_bars(symbol.upper(), tf, 250, force_refresh=refresh)
    return jsonify({
        "symbol": symbol.upper(),
        "timeframe": tf,
        "intraday": tf in ("5Min", "15Min", "1Hour"),
        "bars": bars,
        "indicators": indicators.compute_indicators(bars),
        "levels": indicators.compute_levels(bars),
        "data_age_days": market_data.data_age_days(bars),
        "quote": market_data.get_latest_quote(symbol.upper(), force_refresh=refresh),
    })


@app.route("/api/quotes")
def api_quotes():
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    return jsonify([market_data.get_latest_quote(s, force_refresh=refresh) for s in symbols])


@app.route("/api/advisor/<symbol>")
def api_advisor(symbol):
    tf = request.args.get("timeframe", "1Day")
    mode = request.args.get("strategy", "momentum")
    result = advisor.assess(symbol.upper(), tf, mode)
    if request.args.get("track") in ("1", "true") and result.get("trade_setup", {}).get("signal") in ("BUY", "SELL"):
        minutes = {"5Min": 25, "15Min": 75, "1Hour": 300, "1Day": 60 * 24 * 5, "1Week": 60 * 24 * 28}.get(tf, 60 * 24 * 5)
        storage.record_prediction(symbol.upper(), tf, mode, result["trade_setup"]["signal"], result["facts"]["price"], result["quality_score"], (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds"))
    return jsonify(result)


@app.route("/api/advisor/<symbol>/challenge")
def api_advisor_challenge(symbol):
    return jsonify(advisor.challenge(symbol.upper(), request.args.get("timeframe", "1Day"), request.args.get("strategy", "momentum")))


@app.route("/api/prediction-metrics")
def api_prediction_metrics():
    storage.resolve_predictions(market_data.get_latest_quote)
    return jsonify(storage.prediction_metrics())


@app.route("/api/risk/calculate", methods=["POST"])
def api_risk_calculate():
    body = request.get_json(force=True)
    try:
        result = advisor.risk_calculation(*[float(body.get(key)) for key in
            ("account_size", "risk_pct", "entry", "stop", "target")])
    except (TypeError, ValueError):
        result = {"error": "All risk inputs must be valid numbers."}
    return jsonify(result)


@app.route("/api/news")
def api_news():
    inject = request.args.get("inject") in ("1", "true", "yes")
    sentiment_fn = agents._score_sentiment if agents else None
    items = market_data.get_news(request.args.get("symbol"),
                                 sentiment_fn=sentiment_fn, inject_demo=inject)
    return jsonify(items)


# ==========================================================================
# Strategy, backtesting, optimisation
# ==========================================================================
@app.route("/api/recommend/<symbol>")
def api_recommend(symbol):
    return jsonify(strategy.recommendation(symbol.upper(),
                                           request.args.get("timeframe", "1Day")))


@app.route("/api/strategy/<symbol>")
def api_strategy(symbol):
    return jsonify(strategy.levels(symbol.upper(), request.args.get("timeframe", "1Day")))


@app.route("/api/backtest/<symbol>")
def api_backtest(symbol):
    return jsonify(backtest.run(
        symbol.upper(),
        timeframe=request.args.get("timeframe", "1Day"),
        fast=int(request.args.get("fast", 10)),
        slow=int(request.args.get("slow", 30)),
    ))


@app.route("/api/optimize/<symbol>")
def api_optimize(symbol):
    return jsonify(backtest.optimize(symbol.upper(),
                                     timeframe=request.args.get("timeframe", "1Day")))


@app.route("/api/backtest/history/<symbol>")
def api_backtest_history(symbol):
    return jsonify(storage.backtest_history(symbol.upper()))


# ==========================================================================
# Chat / agents
# ==========================================================================
@app.route("/api/chat", methods=["POST"])
def api_chat():
    global chat_memory
    body = request.get_json(force=True)
    user_msg = body.get("message", "")
    compare = bool(body.get("compare_baseline"))
    session_id = body.get("session_id") or request.remote_addr or "anon"

    ctx = trace.start(session_id=session_id)
    storage.log_audit("chat_turn", {"turn_id": ctx.turn_id, "compare": compare},
                      session_id=session_id)

    if agents is None:
        payload = trace.finish()
        payload["reply"] = (
            "The agent layer is not available. Install it with "
            "`python -m pip install langchain-classic langchain-openai` and restart. "
            f"Details: {AGENTS_ERROR}")
        return jsonify(payload)

    from langchain_core.messages import HumanMessage, AIMessage

    try:
        reply = agents.run_desk(user_msg, chat_memory)
        chat_memory.extend([HumanMessage(content=user_msg), AIMessage(content=reply)])
        if len(chat_memory) > 20:
            chat_memory = chat_memory[-20:]
    except Exception as exc:
        app.logger.exception("Agent failure")
        storage.log_audit("agent_failure", {"error": str(exc)},
                          severity="error", session_id=session_id)
        payload = trace.finish()
        payload["reply"] = (f"The desk could not complete this request: {exc}. "
                            f"No order was placed.")
        payload["error"] = str(exc)
        return jsonify(payload)

    baseline = agents.run_baseline(user_msg) if compare else None

    payload = trace.finish()
    payload["reply"] = reply
    payload["baseline"] = baseline
    return jsonify(payload)


# ==========================================================================
# Human-in-the-loop
# ==========================================================================
@app.route("/api/trade/pending")
def api_trade_pending():
    return jsonify(broker.get_pending())


@app.route("/api/trade/resolve", methods=["POST"])
def api_trade_resolve():
    body = request.get_json(force=True)
    status, payload = broker.resolve(body.get("id"), body.get("decision"))
    return jsonify(payload), status


# ==========================================================================
# Portfolio
# ==========================================================================
@app.route("/api/account")
def api_account():
    return jsonify(broker.account())


@app.route("/api/positions")
def api_positions():
    return jsonify(broker.positions())


@app.route("/api/orders_pending")
def api_orders_pending():
    return jsonify(broker.open_orders())


@app.route("/api/order", methods=["POST"])
def api_order():
    """
    Manual order from the dashboard buttons. This is a human acting directly, so
    it does not require a CRO verdict - but it is still logged and still subject
    to the size ceiling.
    """
    data = request.get_json(force=True)
    symbol = data["symbol"].upper()
    side = data["side"]
    qty = int(data.get("qty", 1))

    allowed, reg_reason = regulation.check(symbol, side, qty)
    if not allowed:
        storage.log_trade(symbol, side, qty, "regulation_denied", source="manual_ui")
        storage.log_audit("regulation_denied", {"symbol": symbol, "side": side,
                                                 "qty": qty, "reason": reg_reason},
                          severity="warning")
        return jsonify({"error": reg_reason}), 403

    if qty > config.HITL_QTY_THRESHOLD:
        record = broker.queue_for_approval(
            symbol, side, qty,
            f"Manual order of {qty} shares exceeds the {config.HITL_QTY_THRESHOLD}-share limit.")
        return jsonify({"error": f"Order held for approval (ticket {record['id']}).",
                        "pending_approval": record}), 202

    ok, payload = broker.place_order(symbol, side, qty)
    storage.log_trade(symbol, side, qty, "executed" if ok else "failed",
                      source="manual_ui", approved_by="human", broker_response=payload)
    return (jsonify(payload), 200) if ok else (jsonify({"error": payload}), 400)


# ==========================================================================
# Regulation
# ==========================================================================
@app.route("/api/regulations", methods=["GET", "POST"])
def api_regulations():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        rule_id, error = regulation.add_rule(data.get("rule_type"), data.get("symbol"),
                                             data.get("param"))
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"id": rule_id}), 201
    return jsonify({"rules": regulation.list_rules(), "pdt": regulation.pdt_status()})


@app.route("/api/regulations/<int:rule_id>", methods=["DELETE"])
def api_regulation_delete(rule_id):
    regulation.remove_rule(rule_id)
    return jsonify({"ok": True})


@app.route("/api/regulations/<int:rule_id>/toggle", methods=["POST"])
def api_regulation_toggle(rule_id):
    data = request.get_json(force=True) or {}
    regulation.toggle_rule(rule_id, bool(data.get("enabled", True)))
    return jsonify({"ok": True})


@app.route("/api/regulations/violations")
def api_regulation_violations():
    return jsonify(storage.query(
        "SELECT * FROM audit_log WHERE event='regulation_denied' ORDER BY id DESC LIMIT 30"))


# ==========================================================================
# Observability / governance
# ==========================================================================
@app.route("/api/audit/tail")
def api_audit_tail():
    return jsonify({
        "stats": storage.stats(),
        "recent_trades": storage.recent_trades(15),
        "recent_approvals": storage.recent_approvals(15),
        "audit": storage.audit_tail(40),
    })


@app.route("/api/trace/<turn_id>")
def api_trace(turn_id):
    return jsonify(storage.trace_for_turn(turn_id))


@app.route("/api/health")
def api_health():
    bars = market_data.get_bars("AAPL", limit=5)
    return jsonify({
        "status": "ok" if bars else "degraded",
        "market_data": bool(bars),
        "agents": agents is not None,
        "storage": storage.stats(),
        "config": config.public_config(),
    })


# ==========================================================================
# Demo controls
# ==========================================================================
@app.route("/api/sim/scenario", methods=["POST"])
def api_sim_scenario():
    scenario = (request.get_json(force=True).get("scenario") or "live").lower()

    if scenario == "live":
        config.RUNTIME["synthetic_mode"] = False
        message = "Switched to the live market feed."
    elif scenario in config.SYNTHETIC_SCENARIOS:
        config.RUNTIME["synthetic_mode"] = True
        config.RUNTIME["synthetic_scenario"] = scenario
        message = f"Synthetic market: {config.SYNTHETIC_SCENARIOS[scenario][2]}."
    else:
        return jsonify({"error": f"Unknown scenario '{scenario}'."}), 400

    market_data.cache_clear()
    storage.log_audit("scenario_switch", {"scenario": scenario})
    return jsonify({"scenario": scenario, "message": message,
                    "config": config.public_config()})


@app.route("/")
def index():
    return Response(ui.INDEX_HTML, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    mode = "SYNTHETIC" if config.RUNTIME["synthetic_mode"] else "LIVE"
    print(f"  AI Trading Desk starting on :{port}  [{mode} market]")
    print(f"  agents: {'ready' if agents else 'UNAVAILABLE - ' + str(AGENTS_ERROR)}")
    print(f"  database: {config.DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
