"""
SQLite persistence layer.

Everything the desk does that a compliance officer might later need to
reconstruct is written here: orders, approvals, backtests, and the full
agent chain of thought. In-memory state disappears on restart; an audit
trail must not.
"""
import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta

from . import config

_lock = threading.Lock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    session_id      TEXT,
    turn_id         TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    status          TEXT NOT NULL,
    source          TEXT NOT NULL,
    approved_by     TEXT,
    cro_verdict     TEXT,
    broker_response TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT,
    strategy        TEXT,
    fast            INTEGER,
    slow            INTEGER,
    num_trades      INTEGER,
    win_rate_pct    REAL,
    total_pnl_pct   REAL,
    sharpe          REAL,
    max_drawdown_pct REAL,
    buy_and_hold_pct REAL,
    beats_buy_hold  INTEGER,
    payload         TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket          TEXT UNIQUE NOT NULL,
    ts_created      TEXT NOT NULL,
    ts_resolved     TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    reason          TEXT,
    decision        TEXT,
    outcome         TEXT
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    session_id      TEXT,
    turn_id         TEXT,
    step_index      INTEGER,
    level           INTEGER,
    agent           TEXT,
    tool            TEXT,
    input           TEXT,
    output          TEXT,
    latency_ms      INTEGER,
    tokens          INTEGER
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_created      TEXT NOT NULL,
    due_at          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    quality_score   INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    resolved_price  REAL,
    outcome         TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    event           TEXT NOT NULL,
    severity        TEXT,
    session_id      TEXT,
    detail          TEXT
);

CREATE TABLE IF NOT EXISTS regulations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    rule_type       TEXT NOT NULL,
    symbol          TEXT,
    param           TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_traces_turn ON agent_traces(turn_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init(db_path=None):
    """Opens the database and creates the schema. Safe to call repeatedly."""
    global _conn
    path = db_path or config.DB_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _lock:
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def _c():
    if _conn is None:
        init()
    return _conn


def _write(sql, params=()):
    try:
        with _lock:
            cur = _c().execute(sql, params)
            _c().commit()
            return cur.lastrowid
    except Exception:
        # Persistence must never take the desk down.
        return None


def query(sql, params=()):
    try:
        with _lock:
            return [dict(r) for r in _c().execute(sql, params).fetchall()]
    except Exception:
        return []


# ------------------------------------------------------------------ writers
def log_audit(event, detail=None, severity="info", session_id=None):
    return _write(
        "INSERT INTO audit_log (ts, event, severity, session_id, detail) VALUES (?,?,?,?,?)",
        (_now(), event, severity, session_id,
         json.dumps(detail, default=str) if detail is not None else None),
    )


def log_trade(symbol, side, qty, status, source, approved_by=None,
              cro_verdict=None, broker_response=None, session_id=None, turn_id=None):
    return _write(
        """INSERT INTO trades (ts, session_id, turn_id, symbol, side, qty, status,
                               source, approved_by, cro_verdict, broker_response)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (_now(), session_id, turn_id, symbol, side, qty, status, source,
         approved_by, cro_verdict,
         json.dumps(broker_response, default=str)[:2000] if broker_response else None),
    )


def log_backtest(bt):
    if bt.get("error"):
        return None
    return _write(
        """INSERT INTO backtests (ts, symbol, timeframe, strategy, fast, slow, num_trades,
                                  win_rate_pct, total_pnl_pct, sharpe, max_drawdown_pct,
                                  buy_and_hold_pct, beats_buy_hold, payload)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (_now(), bt.get("symbol"), bt.get("timeframe"), bt.get("strategy"),
         bt.get("fast"), bt.get("slow"), bt.get("num_trades"), bt.get("win_rate_pct"),
         bt.get("total_pnl_pct"), bt.get("sharpe"), bt.get("max_drawdown_pct"),
         bt.get("buy_and_hold_pct"), 1 if bt.get("beats_buy_and_hold") else 0,
         json.dumps(bt, default=str)[:8000]),
    )


def log_approval_created(ticket, symbol, side, qty, reason):
    return _write(
        """INSERT OR IGNORE INTO approvals (ticket, ts_created, symbol, side, qty, reason, decision)
           VALUES (?,?,?,?,?,?,?)""",
        (ticket, _now(), symbol, side, qty, reason, "pending"),
    )


def log_approval_resolved(ticket, decision, outcome):
    return _write(
        "UPDATE approvals SET ts_resolved=?, decision=?, outcome=? WHERE ticket=?",
        (_now(), decision, outcome, ticket),
    )


def log_trace(steps, session_id=None, turn_id=None):
    """Persists a whole turn's chain of thought in one transaction."""
    if not steps:
        return
    rows = [
        (_now(), session_id, turn_id, i, s.get("level", 0), s.get("agent"),
         s.get("tool"), str(s.get("input", ""))[:2000], str(s.get("output", ""))[:4000],
         s.get("latency_ms"), s.get("tokens"))
        for i, s in enumerate(steps)
    ]
    try:
        with _lock:
            _c().executemany(
                """INSERT INTO agent_traces (ts, session_id, turn_id, step_index, level,
                                             agent, tool, input, output, latency_ms, tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
            _c().commit()
    except Exception:
        pass


# ------------------------------------------------------------------ readers
def recent_trades(limit=20):
    return query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))


def recent_approvals(limit=20):
    return query("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,))


def trace_for_turn(turn_id):
    return query("SELECT * FROM agent_traces WHERE turn_id=? ORDER BY step_index", (turn_id,))


def backtest_history(symbol, limit=10):
    return query("SELECT * FROM backtests WHERE symbol=? ORDER BY id DESC LIMIT ?",
                 (symbol.upper(), limit))


def audit_tail(limit=50):
    return query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


def stats():
    def one(sql):
        r = query(sql)
        return r[0]["n"] if r else 0
    return {
        "trades": one("SELECT COUNT(*) n FROM trades"),
        "backtests": one("SELECT COUNT(*) n FROM backtests"),
        "approvals": one("SELECT COUNT(*) n FROM approvals"),
        "trace_steps": one("SELECT COUNT(*) n FROM agent_traces"),
        "audit_events": one("SELECT COUNT(*) n FROM audit_log"),
    }


def record_prediction(symbol, timeframe, strategy, direction, entry_price, quality_score, due_at):
    """Store only actionable directional calls; avoid duplicate clicks for one setup."""
    recent = query("""SELECT id FROM prediction_outcomes WHERE symbol=? AND timeframe=?
                      AND strategy=? AND status='pending' ORDER BY id DESC LIMIT 1""",
                   (symbol, timeframe, strategy))
    if recent:
        return recent[0]["id"]
    return _write("""INSERT INTO prediction_outcomes
        (ts_created, due_at, symbol, timeframe, strategy, direction, entry_price, quality_score)
        VALUES (?,?,?,?,?,?,?,?,?)""", (_now(), due_at, symbol, timeframe, strategy,
        direction, entry_price, quality_score))


def resolve_predictions(quote_fn):
    now = _now()
    pending = query("SELECT * FROM prediction_outcomes WHERE status='pending' AND due_at <= ?", (now,))
    for p in pending:
        quote = quote_fn(p["symbol"])
        price = quote.get("price", 0) if quote else 0
        if price <= 0:
            continue
        won = (p["direction"] == "BUY" and price > p["entry_price"]) or (p["direction"] == "SELL" and price < p["entry_price"])
        _write("UPDATE prediction_outcomes SET status='resolved', resolved_price=?, outcome=? WHERE id=?",
               (price, "win" if won else "loss", p["id"]))


def prediction_metrics():
    rows = query("SELECT status, outcome, quality_score FROM prediction_outcomes")
    resolved = [r for r in rows if r["status"] == "resolved"]
    wins = sum(1 for r in resolved if r["outcome"] == "win")
    return {"tracked_calls": len(rows), "resolved_calls": len(resolved), "pending_calls": len(rows) - len(resolved),
            "wins": wins, "success_pct": round(wins / len(resolved) * 100, 1) if resolved else None,
            "average_quality": round(sum(r["quality_score"] or 0 for r in rows) / len(rows), 1) if rows else None}


def add_regulation(rule_type, symbol, param):
    return _write(
        "INSERT INTO regulations (ts, rule_type, symbol, param, enabled) VALUES (?,?,?,?,1)",
        (_now(), rule_type, symbol, str(param) if param is not None else None),
    )


def remove_regulation(rule_id):
    return _write("DELETE FROM regulations WHERE id=?", (rule_id,))


def toggle_regulation(rule_id, enabled):
    return _write("UPDATE regulations SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))


def list_regulations():
    return query("SELECT * FROM regulations ORDER BY id DESC")


def enforce_retention(days=None):
    """Deletes audit and trace rows past the retention window."""
    days = days or config.DATA_RETENTION_DAYS
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    _write("DELETE FROM agent_traces WHERE ts < ?", (cutoff,))
    _write("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
    return cutoff
