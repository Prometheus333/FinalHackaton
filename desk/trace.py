"""
Per-turn observability.

Each conversational turn gets a thread-local context holding the chain of
thought, the cost and latency meters, the authorization state, and any
escalation raised. Flask serves requests on threads, so thread-local keeps
concurrent users from reading each other's traces.
"""
import time
import uuid
import threading
from datetime import datetime

from . import security, storage

_ctx = threading.local()


class TurnContext:
    def __init__(self, session_id=None):
        self.session_id = session_id or "anon"
        self.turn_id = uuid.uuid4().hex[:12]
        self.steps = []
        self.pending_approval = None
        self.auth = security.TurnAuthorization()
        self.started = time.time()
        self.tokens_in = 0
        self.tokens_out = 0
        self.llm_calls = 0
        self.injection_blocks = 0
        self.escalations = []

    def elapsed_ms(self):
        return int((time.time() - self.started) * 1000)

    def metrics(self):
        return {
            "turn_id": self.turn_id,
            "total_ms": self.elapsed_ms(),
            "llm_calls": self.llm_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_in + self.tokens_out,
            "steps": len(self.steps),
            "agents": len({s["agent"] for s in self.steps}) if self.steps else 0,
            "injection_blocks": self.injection_blocks,
            "escalations": len(self.escalations),
        }


def start(session_id=None):
    _ctx.current = TurnContext(session_id)
    return _ctx.current


def current():
    ctx = getattr(_ctx, "current", None)
    if ctx is None:
        ctx = start()
    return ctx


def add(level, agent, tool, tool_input="", output="", latency_ms=None, tokens=None):
    ctx = current()
    ctx.steps.append({
        "level": level,
        "agent": agent,
        "tool": tool,
        "input": str(tool_input)[:500],
        "output": str(output)[:900],
        "latency_ms": latency_ms,
        "tokens": tokens,
        "ts": datetime.now().strftime("%H:%M:%S"),
    })
    return ctx.steps[-1]


def add_escalation(reason, detail=None):
    ctx = current()
    ctx.escalations.append({"reason": reason, "detail": detail})
    add(0, "guardrail", "escalation", reason, detail or "held for human review")
    storage.log_audit("escalation", {"reason": reason, "detail": detail},
                      severity="warning", session_id=ctx.session_id)


def steps():
    return current().steps


def set_pending(payload):
    current().pending_approval = payload


def pending():
    return current().pending_approval


def auth():
    return current().auth


def finish():
    """Persists the turn and returns the payload the UI needs."""
    ctx = current()
    storage.log_trace(ctx.steps, session_id=ctx.session_id, turn_id=ctx.turn_id)
    return {
        "trace": ctx.steps,
        "metrics": ctx.metrics(),
        "pending_approval": ctx.pending_approval,
        "authorization": ctx.auth.snapshot(),
        "escalations": ctx.escalations,
    }


class timed:
    """
    Context manager measuring a tool call.

        with timed(0, "portfolio_manager", "delegate_to_cro", symbol) as t:
            result = ...
            t.output = result
    """

    def __init__(self, level, agent, tool, tool_input=""):
        self.level = level
        self.agent = agent
        self.tool = tool
        self.input = tool_input
        self.output = ""
        self.tokens = None
        self._t0 = None

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = int((time.time() - self._t0) * 1000)
        if exc is not None:
            add(self.level, self.agent, self.tool, self.input,
                f"ERROR: {exc}", latency_ms=ms)
            return False
        add(self.level, self.agent, self.tool, self.input, self.output,
            latency_ms=ms, tokens=self.tokens)
        return False


def record_llm_usage(tokens_in=0, tokens_out=0):
    ctx = current()
    ctx.llm_calls += 1
    ctx.tokens_in += tokens_in or 0
    ctx.tokens_out += tokens_out or 0


def record_injection_block(count=1):
    current().injection_blocks += count
