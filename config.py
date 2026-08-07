"""
Central configuration. Everything tunable lives here so models, prompts,
thresholds and endpoints can be swapped without touching business logic.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# ---------------------------------------------------------------- brokers
ALPACA_KEY_ID = os.environ.get("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")
ALPACA_TRADING_URL = os.environ.get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets/v2")

# ---------------------------------------------------------------- LLM
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://genailab.tcs.in")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "azure/genailab-maas-gpt-4.1")
LLM_TEMPERATURE = _float("LLM_TEMPERATURE", 0.2)
LLM_TIMEOUT_SECONDS = _int("LLM_TIMEOUT_SECONDS", 90)

# Fallback provider, used when the primary LLM endpoint is unavailable.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

# ---------------------------------------------------------------- guardrails
# Any single order above this size is held for human approval.
HITL_QTY_THRESHOLD = _int("HITL_QTY_THRESHOLD", 5)
# Cumulative quantity per symbol within one conversational turn. Closes the
# "split the order into smaller ones" bypass at the code level, not just in the prompt.
HITL_TURN_QTY_THRESHOLD = _int("HITL_TURN_QTY_THRESHOLD", 5)
# Notional ceiling regardless of share count (a 3-share order can still be large).
HITL_NOTIONAL_THRESHOLD = _float("HITL_NOTIONAL_THRESHOLD", 5000.0)
# Max tool iterations per agent.
MAX_ITERATIONS_SUBAGENT = _int("MAX_ITERATIONS_SUBAGENT", 5)
MAX_ITERATIONS_PM = _int("MAX_ITERATIONS_PM", 10)

# ---------------------------------------------------------------- reliability
RETRY_ATTEMPTS = _int("RETRY_ATTEMPTS", 3)
RETRY_BACKOFF_SECONDS = _float("RETRY_BACKOFF_SECONDS", 0.8)
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 10)

# ---------------------------------------------------------------- statistics
# A backtest with fewer than this many trades is not statistically meaningful.
MIN_TRADES_FOR_CONFIDENCE = _int("MIN_TRADES_FOR_CONFIDENCE", 3)
# Data older than this is considered stale and lowers confidence.
MAX_DATA_AGE_DAYS = _int("MAX_DATA_AGE_DAYS", 5)

# ---------------------------------------------------------------- caching
CACHE_TTL_SECONDS = _int("CACHE_TTL_SECONDS", 30)
CACHE_TTL_DERIVED = _int("CACHE_TTL_DERIVED", 120)
CACHE_TTL_BACKTEST = _int("CACHE_TTL_BACKTEST", 300)

# ---------------------------------------------------------------- storage
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "desk.db"))
# Audit rows older than this are purged by the retention job.
DATA_RETENTION_DAYS = _int("DATA_RETENTION_DAYS", 90)

# ---------------------------------------------------------------- demo / testing
# Deterministic synthetic market. Removes the dependency on a live feed during a
# demo and lets us reproduce crash / rally / choppy conditions on demand.
SYNTHETIC_MODE = _flag("SYNTHETIC_MODE")
SYNTHETIC_SEED = _int("SYNTHETIC_SEED", 42)

# Mutable at runtime so the UI can flip scenarios mid-demo.
RUNTIME = {
    "synthetic_mode": SYNTHETIC_MODE,
    "synthetic_scenario": os.environ.get("SYNTHETIC_SCENARIO", "normal"),
}

SYNTHETIC_SCENARIOS = {
    #                drift     volatility  description
    "normal": (0.0006, 0.012, "Mild uptrend, normal volatility"),
    "rally": (0.0045, 0.011, "Sustained bull run"),
    "crash": (-0.0075, 0.032, "Sharp drawdown, elevated volatility"),
    "choppy": (0.0000, 0.021, "Sideways, no directional edge"),
}

# ---------------------------------------------------------------- optimizer grid
OPTIMIZER_FAST_GRID = [5, 8, 10, 12, 15, 20]
OPTIMIZER_SLOW_GRID = [20, 26, 30, 40, 50, 60]
OPTIMIZER_LOOKBACK = _int("OPTIMIZER_LOOKBACK", 200)


def public_config():
    """Non-secret configuration, safe to expose to the UI and to the audit log."""
    return {
        "llm_model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "hitl_qty_threshold": HITL_QTY_THRESHOLD,
        "hitl_turn_qty_threshold": HITL_TURN_QTY_THRESHOLD,
        "hitl_notional_threshold": HITL_NOTIONAL_THRESHOLD,
        "min_trades_for_confidence": MIN_TRADES_FOR_CONFIDENCE,
        "synthetic_mode": RUNTIME["synthetic_mode"],
        "synthetic_scenario": RUNTIME["synthetic_scenario"],
        "retry_attempts": RETRY_ATTEMPTS,
    }
