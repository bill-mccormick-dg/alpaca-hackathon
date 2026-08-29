"""Reconstruct completed round trips from broker fills - options-aware.

The journal records what the bot *decided*; the broker knows how trades
*ended*. Pairing the two answers the question the daily loop lives on:
how do trades end, and which kind of trade is working? Mirrors
alpaca-trader's trader/trades.py, with what options add: a 100-share
multiplier per contract, calls vs puts, days-to-expiration at entry, and
exits that are deterministic code (stop / take-profit / expiry) rather
than only the model or the end-of-day flatten.

Pure functions over plain dicts; no MCP imports, so it unit-tests without
credentials. trade_report.py does the fetching.
"""

from collections import defaultdict, deque
from datetime import datetime
from statistics import median

from bot.occ import parse_occ_symbol

# Exit reasons, in the order they are reported.
STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"
EXPIRY = "expiry"
MODEL = "model"
FLATTEN = "flatten"
EXIT_REASONS = (STOP_LOSS, TAKE_PROFIT, EXPIRY, MODEL, FLATTEN)

DTE_BUCKETS = ((0, 2, "0-2"), (3, 7, "3-7"), (8, 14, "8-14"), (15, 45, "15-45"), (46, 10_000, "46+"))


def classify_exit(order_id: str | None, client_order_id: str | None, journaled_sells: dict) -> str:
    """Why did this position end? `journaled_sells` maps order_id (and
    client_order_id) -> the journal's reason string for sells the bot
    submitted itself. A deterministic exit's reason starts with its rule
    name (bot/exits.py); any other journaled sell was the model's idea;
    a sell the journal never saw is the end-of-day flatten (or a manual
    close - indistinguishable, and treated the same)."""
    reason = journaled_sells.get(order_id or "") or journaled_sells.get(client_order_id or "")
    if reason is None:
        return FLATTEN
    head = str(reason).split(" ")[0].split("(")[0].strip().lower()
    if head in (STOP_LOSS, TAKE_PROFIT, EXPIRY):
        return head
    return MODEL


def contract_multiplier(symbol: str) -> int:
    try:
        parse_occ_symbol(symbol)
        return 100
    except ValueError:
        return 1


def _instrument(symbol: str) -> str:
    try:
        return parse_occ_symbol(symbol).option_type  # "call" | "put"
    except ValueError:
        return "stock"


def _underlying(symbol: str) -> str:
    try:
        return parse_occ_symbol(symbol).underlying
    except ValueError:
        return symbol


def _dte_at(symbol: str, when) -> int | None:
    try:
        exp = parse_occ_symbol(symbol).expiration
    except ValueError:
        return None
    try:
        return (exp - when.date()).days
    except AttributeError:
        return None


def _hold_minutes(entry, exit_):
    try:
        return round((exit_ - entry).total_seconds() / 60, 1)
    except (TypeError, AttributeError):
        return None


def _hour(when) -> int | None:
    return when.hour if isinstance(when, datetime) else None


def pair_round_trips(fills: list[dict]) -> tuple[list[dict], list[dict]]:
    """FIFO-match sell fills against earlier buy fills, per symbol.

    fills: {symbol, side, qty, price, filled_at (datetime), reason?}.
    Returns (round_trips, still_open). Lots are split so one sell can close
    several entries and vice versa. Long-only: a sell with no matching lot
    is dropped, never invented as a short. P&L includes the contract
    multiplier, so it is in dollars for options and stock alike."""
    lots: dict[str, deque] = defaultdict(deque)
    trips: list[dict] = []

    for f in sorted(fills, key=lambda x: (x["filled_at"], x["side"] == "sell")):
        sym, qty = f["symbol"], float(f["qty"])
        if qty <= 0:
            continue
        if f["side"] == "buy":
            lots[sym].append({"price": float(f["price"]), "time": f["filled_at"], "remaining": qty})
            continue

        mult = contract_multiplier(sym)
        while qty > 0 and lots[sym]:
            lot = lots[sym][0]
            take = min(qty, lot["remaining"])
            exit_price = float(f["price"])
            pnl = take * (exit_price - lot["price"]) * mult
            trips.append(
                {
                    "symbol": sym,
                    "underlying": _underlying(sym),
                    "instrument": _instrument(sym),
                    "qty": take,
                    "multiplier": mult,
                    "entry_price": lot["price"],
                    "entry_time": lot["time"],
                    "entry_hour": _hour(lot["time"]),
                    "dte_at_entry": _dte_at(sym, lot["time"]),
                    "exit_price": exit_price,
                    "exit_time": f["filled_at"],
                    "exit_reason": f.get("reason") or FLATTEN,
                    "hold_minutes": _hold_minutes(lot["time"], f["filled_at"]),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((exit_price / lot["price"] - 1) * 100, 2) if lot["price"] else None,
                }
            )
            lot["remaining"] -= take
            qty -= take
            if lot["remaining"] <= 1e-9:
                lots[sym].popleft()

    still_open = [
        {"symbol": s, "qty": lot["remaining"], "entry_price": lot["price"], "entry_time": lot["time"]}
        for s, dq in lots.items()
        for lot in dq
        if lot["remaining"] > 1e-9
    ]
    return trips, still_open


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "total": 0.0, "avg": None, "median": None}
    return {
        "n": len(values),
        "total": round(sum(values), 2),
        "avg": round(sum(values) / len(values), 2),
        "median": round(median(values), 2),
    }


def _cut(trips: list[dict], key) -> dict:
    """P&L / count / win breakdown by a grouping function, sorted by group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trips:
        groups[str(key(t))].append(t)
    out = {}
    for name in sorted(groups):
        subset = groups[name]
        pnls = [t["pnl"] for t in subset]
        holds = [t["hold_minutes"] for t in subset if t.get("hold_minutes") is not None]
        out[name] = {
            "trades": len(subset),
            "pnl": round(sum(pnls), 2),
            "wins": sum(1 for p in pnls if p > 0),
            "avg_pnl": round(sum(pnls) / len(subset), 2),
            "median_hold_min": round(median(holds), 1) if holds else None,
        }
    return out


def _dte_bucket(t: dict) -> str:
    d = t.get("dte_at_entry")
    if d is None:
        return "stock"
    for lo, hi, label in DTE_BUCKETS:
        if lo <= d <= hi:
            return label
    return "<0"


def summarize(trips: list[dict]) -> dict:
    """Diagnostics a few days of paper trading can support - exit mix and
    trade shape by the cuts that matter for options - not a verdict."""
    wins = [t["pnl"] for t in trips if t["pnl"] > 0]
    losses = [t["pnl"] for t in trips if t["pnl"] < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    holds_all = [t["hold_minutes"] for t in trips if t.get("hold_minutes") is not None]

    by_reason = {}
    for reason in EXIT_REASONS:
        subset = [t for t in trips if t["exit_reason"] == reason]
        pnls = [t["pnl"] for t in subset]
        holds = [t["hold_minutes"] for t in subset if t.get("hold_minutes") is not None]
        by_reason[reason] = {
            "trades": len(subset),
            "share_pct": round(len(subset) / len(trips) * 100, 1) if trips else None,
            "pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(subset), 2) if subset else None,
            "median_hold_min": round(median(holds), 1) if holds else None,
        }

    return {
        "trades": len(trips),
        "pnl": round(sum(t["pnl"] for t in trips), 2),
        "win_rate_pct": round(len(wins) / len(trips) * 100, 1) if trips else None,
        "wins": _stats(wins),
        "losses": _stats(losses),
        # None when nothing has been lost yet: on a small sample that means
        # "not enough data", not "infinite edge".
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "median_hold_min": round(median(holds_all), 1) if holds_all else None,
        "by_exit_reason": by_reason,
        "by_underlying": _cut(trips, lambda t: t["underlying"]),
        "by_instrument": _cut(trips, lambda t: t["instrument"]),
        "by_dte_at_entry": _cut(trips, _dte_bucket),
        "by_entry_hour": _cut(trips, lambda t: f"{t['entry_hour']:02d}" if t.get("entry_hour") is not None else "?"),
    }
