"""Which way an account is pointing on an underlying, and when it turns round (#284).

Day 1 of the baseline run (2026-09-09) settled that end-of-day equity cannot
compare two configs: `base_a` and `base_b` share one config.yaml and finished
$1,424.65 apart, 71% of the daily-loss budget, with one halted and the other
not. Detecting a 1%/day difference through P&L needs ~25 trading days; the run
does not have them.

What did reproduce was conduct. All three accounts spent the day reversing
their own directional view on the same underlying - `base_a` ran QQQ
put -> call -> put in forty minutes, `base_b` flipped NVDA in ten - 10 times
across 38 entries, of which 27 were on an underlying the account already had
a view on. 10 of those 27 turned it round: 37%. A rate over ~27 events a day
resolves a 37% -> 22% shift in about five trading days instead of twenty-five.

The denominator matters and is easy to get wrong: dividing by all 38 entries
gives 27%, but an entry on an underlying touched for the first time that day
could not have reversed anything, so it does not belong in the base.

So this module counts the events rather than the dollars. It is pure
measurement: nothing here is consulted by the funnel, and no order is shaped
by it. That is deliberate and load-bearing - docs/baseline.md lets journaling
ship mid-run precisely because recording a decision cannot change it, while a
guard or a prompt would end the replicate pair.

A "stance" is only ever taken by a long option entry: buying a call is
bullish, buying a put is bearish. Selling is not a stance - an exit says the
thesis is over, not that the opposite one has begun - which is what keeps a
routine close-then-reassess from being counted as a reversal. Stock and
anything unparseable have no stance and are skipped.
"""

from bot.occ import parse_occ_symbol

BULLISH = "bullish"
BEARISH = "bearish"


def direction_of(symbol: str, side: str | None) -> str | None:
    """The directional view an order expresses, or None when it expresses
    none. Only a long option entry takes a side: a sell closes a view rather
    than opening its opposite, and stock carries no call/put signal."""
    if (side or "").lower() != "buy":
        return None
    try:
        parsed = parse_occ_symbol(symbol or "")
    except ValueError:
        return None
    return BULLISH if parsed.option_type == "call" else BEARISH


def underlying_of(symbol: str) -> str | None:
    try:
        return parse_occ_symbol(symbol or "").underlying
    except ValueError:
        return None


def entry_stances(records) -> dict[str, dict]:
    """The latest entry per underlying, from journal records in time order.

    Reads `order_submitted` only: a rejected or dry-run proposal never reached
    the market, and counting intent as a stance would make the rate depend on
    how often the funnel said no - which differs between accounts running the
    same config (9 rejections on base_a, 0 on base_b, same file, same day)."""
    latest: dict[str, dict] = {}
    for record in records or []:
        if record.get("event") != "order_submitted":
            continue
        symbol = record.get("symbol") or ""
        direction = direction_of(symbol, record.get("side"))
        if direction is None:
            continue
        underlying = underlying_of(symbol)
        if underlying is None:
            continue
        latest[underlying] = {
            "direction": direction,
            "symbol": symbol,
            "ts": record.get("ts"),
            "reason": record.get("reason"),
        }
    return latest


def _minutes_between(earlier: str | None, later: str | None) -> float | None:
    from datetime import datetime
    if not earlier or not later:
        return None
    try:
        return round((datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 60, 1)
    except ValueError:
        return None


def stance_change(previous: dict | None, symbol: str, side: str | None,
                  ts: str | None, reason: str | None) -> dict | None:
    """Journal fields for a reversal, or None when this entry is not one.

    Not a reversal: the first entry on an underlying (nothing to reverse), an
    entry in the same direction as the last one (adding to a view, however
    unwisely), or anything without a direction."""
    direction = direction_of(symbol, side)
    if direction is None or not previous:
        return None
    if previous.get("direction") == direction:
        return None
    # A stance is per underlying, so a `previous` from a different one is a
    # caller bug. Refuse it here rather than emit a record claiming NVDA
    # turned round with a SPY symbol as its evidence.
    underlying = underlying_of(symbol)
    if underlying_of(previous.get("symbol") or "") != underlying:
        return None
    return {
        "underlying": underlying,
        "from_direction": previous.get("direction"),
        "to_direction": direction,
        "from_symbol": previous.get("symbol"),
        "to_symbol": symbol,
        "from_ts": previous.get("ts"),
        "to_ts": ts,
        "minutes_since": _minutes_between(previous.get("ts"), ts),
        "from_reason": previous.get("reason"),
        "reason": reason,
    }


def rate(records) -> dict:
    """Stance changes as a share of entries, for the digest and the review.

    The denominator is entries that could have reversed something - the first
    entry on an underlying is excluded, because it had no stance to turn."""
    latest: dict[str, dict] = {}
    entries = reversible = changes = 0
    details = []
    for record in records or []:
        if record.get("event") != "order_submitted":
            continue
        symbol = record.get("symbol") or ""
        direction = direction_of(symbol, record.get("side"))
        if direction is None:
            continue
        underlying = underlying_of(symbol)
        if underlying is None:
            continue
        entries += 1
        previous = latest.get(underlying)
        if previous:
            reversible += 1
            change = stance_change(previous, symbol, record.get("side"),
                                   record.get("ts"), record.get("reason"))
            if change:
                changes += 1
                details.append(change)
        latest[underlying] = {
            "direction": direction,
            "symbol": symbol,
            "ts": record.get("ts"),
            "reason": record.get("reason"),
        }
    return {
        "entries": entries,
        "reversible": reversible,
        "changes": changes,
        "pct": round(100.0 * changes / reversible, 1) if reversible else None,
        "details": details,
    }


def describe(summary: dict) -> str:
    """One line for the digest. Says the denominator out loud, because
    '3 stance changes' means different things on 18 cycles and on 36."""
    if not summary or not summary.get("entries"):
        return "no option entries to judge"
    if not summary.get("reversible"):
        return f"{summary['entries']} entr(ies), none on an underlying already held a view on"
    return (f"{summary['changes']} stance change(s) in {summary['reversible']} reversible "
            f"entr(ies) ({summary['pct']}%) of {summary['entries']} total")
