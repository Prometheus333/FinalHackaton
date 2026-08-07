"""
Preflight + launcher.

Checks all three external dependencies before the desk comes up, so a bad key
or an unreachable endpoint fails here with a readable message instead of
surfacing later as an empty chart or a broken agent turn.

    python start.py           check everything, then start the server
    python start.py --check   check everything and exit (CI-friendly)
"""
import os
import sys

import requests

from desk import config


def _mask(key):
    return (key[:6] + "..." + key[-4:]) if len(key) > 12 else (key or "MISSING")


def check_llm():
    print("\n[1/3] LLM - genailab")
    print(f"      endpoint : {config.LLM_BASE_URL}")
    print(f"      model    : {config.LLM_MODEL}")
    print(f"      api key  : {_mask(config.LLM_API_KEY)}")

    if not config.LLM_API_KEY:
        print("      FAIL: LLM_API_KEY is empty in .env")
        return False
    if not config.LLM_API_KEY.startswith("sk-"):
        print("      WARN: key does not start with 'sk-'. Check for a typo.")

    try:
        from desk import agents
        res = agents._primary_llm.invoke("Reply with the single word: online")
        print(f"      OK: model replied {(res.content or '').strip()!r}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL: {exc}")
        print("      Check: on the TCS network/VPN? key still valid?")
        return False


def check_alpaca():
    print("\n[2/3] Alpaca - account + market data")
    print(f"      trading  : {config.ALPACA_TRADING_URL}")
    print(f"      data     : {config.ALPACA_DATA_URL}  (feed={config.ALPACA_FEED})")
    print(f"      key id   : {_mask(config.ALPACA_KEY_ID)}")

    if not (config.ALPACA_KEY_ID and config.ALPACA_SECRET_KEY):
        print("      FAIL: Alpaca credentials missing in .env")
        return False

    from desk import market_data
    headers = market_data.alpaca_headers()

    try:
        r = requests.get(f"{config.ALPACA_TRADING_URL}/account",
                         headers=headers, timeout=15)
        r.raise_for_status()
        acct = r.json()
        print(f"      OK: account {acct.get('status')} | "
              f"equity ${float(acct.get('equity', 0)):,.2f} | "
              f"buying power ${float(acct.get('buying_power', 0)):,.2f}")
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL (trading API): {exc}")
        return False

    try:
        bars = market_data._fetch_alpaca_bars("AAPL", "1Day", 5)
        print(f"      OK: {len(bars)} AAPL daily bars, last close ${bars[-1]['close']}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL (market data): {exc}")
        print("      The desk will fall back to Yahoo for prices.")
        return False


def check_news():
    print("\n[3/3] News")
    from desk import market_data
    try:
        items = market_data._fetch_alpaca_news("AAPL", 3)
        print(f"      OK: {len(items)} headlines from Alpaca")
        for i in items[:2]:
            print(f"         - {i['title'][:70]}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      Alpaca news unavailable: {exc}")
    try:
        items = market_data._fetch_rss("AAPL", 3)
        print(f"      OK: {len(items)} headlines from Yahoo RSS (fallback)")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL: no news source reachable: {exc}")
        return False


if __name__ == "__main__":
    print("=" * 62)
    print("  A2A Trade Terminal - preflight")
    print("=" * 62)

    results = {"LLM": check_llm(), "Alpaca": check_alpaca(), "News": check_news()}

    print("\n" + "-" * 62)
    for name, ok in results.items():
        print(f"  {name:<8} {'OK' if ok else 'UNAVAILABLE'}")
    print("-" * 62)

    if "--check" in sys.argv:
        sys.exit(0 if all(results.values()) else 1)

    if not all(results.values()):
        print("\nSome services are unavailable. The desk still runs:")
        print("  - no LLM     -> charts, backtests and guardrails work; chat does not")
        print("  - no Alpaca  -> prices fall back to Yahoo; orders are unavailable")
        print("  - nothing    -> set SYNTHETIC_MODE=1 for a fully offline demo")
        if input("\nStart anyway? [y/N] ").strip().lower() != "y":
            sys.exit(1)

    from app import app

    port = int(os.environ.get("PORT", 5000))
    mode = "SYNTHETIC" if config.RUNTIME["synthetic_mode"] else "LIVE"
    print(f"\n  A2A Trade Terminal -> http://localhost:{port}  [{mode} market]\n")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
