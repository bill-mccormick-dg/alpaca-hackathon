"""Pure helpers for interpreting broker responses around a flatten.

No network, no MCP - unit-tests without credentials. Mirrors alpaca-trader's
trader/orders.py, which exists because its first flatten path reported
success from the *intended* position list and never looked at what the
broker actually said.
"""

FLAT = "flat"
RESTING = "resting"
FILLING = "filling"
INCOMPLETE = "incomplete"


def classify_close_results(results) -> tuple[list[dict], list[dict]]:
    """Split close_all_positions output into (closed, failed).

    Alpaca returns one entry per position carrying an HTTP status; a non-2xx
    entry means that position did NOT close. Anything that is not a list (an
    error string, None) is a total failure - never treated as success.
    """
    if not isinstance(results, list):
        return [], [{"symbol": "*", "status": None, "body": str(results)}]

    closed, failed = [], []
    for r in results:
        get = r.get if isinstance(r, dict) else lambda k, _r=r: getattr(_r, k, None)
        status = get("status")
        entry = {"symbol": get("symbol"), "status": status, "body": get("body")}
        try:
            ok = 200 <= int(status) < 300
        except (TypeError, ValueError):
            ok = False
        (closed if ok else failed).append(entry)
    return closed, failed


def unprotected_positions(remaining: list[str], pending_symbols: set[str]) -> list[str]:
    """Positions still held with no closing order working for them. Outside
    market hours a close is accepted but rests until the open, so `remaining`
    alone isn't failure - only a symbol nothing is working to close is."""
    return sorted(s for s in remaining if s not in pending_symbols)


def describe_flatten_outcome(
    remaining: list[str],
    pending_symbols: set[str],
    market_open: bool | None,
    waited_sec: float,
    attempted: int,
) -> tuple[str, str]:
    """Classify a verified post-flatten state into (state, message). Whether
    a still-listed position is resting overnight or merely still filling is
    a question about the clock, so market_open is an input here."""
    unprotected = unprotected_positions(remaining, pending_symbols)
    if unprotected:
        return INCOMPLETE, f"FLATTEN INCOMPLETE - still holding {unprotected} with no closing order"
    if not remaining:
        return FLAT, f"flattened {attempted} position(s), cancelled open orders"

    working = sorted(pending_symbols)
    if market_open is False:
        return RESTING, (
            f"close orders resting for {working} (market closed - they fill at the next open); "
            f"attempted {attempted}"
        )
    where = "market open" if market_open else "market state unknown"
    return FILLING, (
        f"close orders still working for {working} after {waited_sec:.0f}s ({where}); "
        f"attempted {attempted}"
    )
