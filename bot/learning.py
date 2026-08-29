"""Learning loop - feed recent outcomes back into the prompt (issue #31).

Each cycle is stateless; without this a losing pattern repeats all day
and the model keeps re-proposing what risk.py refuses. The block is
FACTS ONLY, windowed (last few sessions, last N trades), never all-time,
so one bad morning cannot permanently spook the model out of a whole
entry class. The journal still records the reasoning, so the loop itself
stays evaluable. Pure functions; run_cycle.py gathers the inputs.
"""

from collections import Counter

MAX_CHARS = 2400


def _rule(detail: str) -> str:
    """Same grouping as bot/review.py so today's rejections read as rules."""
    d = str(detail or "").lower()
    for key in ("not in underlyings whitelist", "exceeds max_position_usd", "max_positions", "max_contracts_per_order",
                "days to expiration", "entries not allowed", "cannot sell", "invalid", "qty must be", "price must be",
                "not a valid occ"):
        if key in d:
            return key
    return d[:50] or "unknown"


def recent_trips_lines(trips: list[dict], limit: int) -> list[str]:
    """Most recent closed round trips, one line each: what, when, how long,
    P&L, and how it ended - the facts, no verdict."""
    out = []
    for t in sorted(trips, key=lambda x: str(x.get("exit_time")), reverse=True)[:limit]:
        entry = str(t.get("entry_time"))[:16].replace("T", " ")
        held = f"{t['hold_minutes']:.0f}m" if t.get("hold_minutes") is not None else "?"
        pct = f"{t['pnl_pct']:+.1f}%" if t.get("pnl_pct") is not None else ""
        dte = f", {t['dte_at_entry']} DTE at entry" if t.get("dte_at_entry") is not None else ""
        out.append(f"- {t['symbol']} x{t['qty']:.0f} entered {entry} @ {t['entry_price']:.2f}{dte}; held {held}; "
                   f"P&L {t['pnl']:+.0f} ({pct}); exit: {t['exit_reason']}")
    return out


def aggregate_lines(trips: list[dict]) -> list[str]:
    if not trips:
        return []
    wins = sum(1 for t in trips if t["pnl"] > 0)
    by_exit = Counter(t["exit_reason"] for t in trips)
    by_inst = Counter(t.get("instrument") for t in trips)
    pnl = sum(t["pnl"] for t in trips)
    line = (
        f"- {len(trips)} closed trades in the window: net {pnl:+.0f}, {wins} winners; "
        f"exits {dict(by_exit)}; instruments {dict(by_inst)}"
    )
    return [line]


def open_positions_lines(positions: list[dict]) -> list[str]:
    out = []
    for p in positions:
        entry, cur = p.get("avg_entry_price"), p.get("current_price")
        move = f", {((cur / entry) - 1) * 100:+.1f}% vs entry" if entry and cur else ""
        out.append(f"- holding {p['symbol']} x{p['qty']:.0f} @ {entry if entry is not None else '?'}{move}")
    return out


def rejection_lines(records_today: list[dict]) -> list[str]:
    rejected = [r for r in records_today if r.get("event") == "order_rejected"]
    if not rejected:
        return []
    by_rule = Counter(_rule(r.get("detail")) for r in rejected)
    by_symbol = Counter(r.get("symbol") for r in rejected)
    line = (
        f"- {len(rejected)} proposals REJECTED by the guardrails today: by rule {dict(by_rule)}; "
        f"by symbol {dict(by_symbol.most_common(5))}. These rules will not change - do not re-propose the same idea."
    )
    return [line]


def build_learning_block(trips: list[dict], positions: list[dict], records_today: list[dict],
                         max_trades: int = 15) -> str:
    """The prompt block, or "" when there is nothing to say yet."""
    lines = []
    lines += aggregate_lines(trips)
    lines += recent_trips_lines(trips, max_trades)
    lines += open_positions_lines(positions)
    lines += rejection_lines(records_today)
    if not lines:
        return ""
    header = ("RECENT OUTCOMES (facts from the journal and broker fills over the last few sessions - "
              "draw your own conclusions; this is a window, not a verdict):")
    text = header + "\n" + "\n".join(lines) + "\n\n"
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 20] + "\n(...)\n\n"
