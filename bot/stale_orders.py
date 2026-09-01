"""Cancel the model's unfilled entry buys from a previous cycle (#171).

Every order this bot places is time_in_force "day", and a cycle is its own
process, so any open buy carrying the bot's client_order_id prefix at the
top of a cycle was sent by an EARLIER cycle - a thesis the model has, by
construction, not re-examined. Left resting it can fill hours later into a
market that has changed, and on 2026-09-01 it did worse: the 12:00 limit
sat away from the market, the 12:10 cycle saw positions 0, re-bought the
same idea at a neighbouring strike, and the account then carried both.

Only entries. A resting SELL still wants to fill - it is an exit the model
or exits.py asked for - and an order without the bot's prefix belongs to a
human. Cancels go through the broker one at a time so a failure names the
order it failed on; the caller journals each outcome as order_canceled.
"""

from bot.alpaca_mcp import AlpacaMCPClient

CLIENT_ID_PREFIX = "hb-"  # bot/execute.py::client_order_id


def is_stale_entry(order: dict) -> bool:
    return (
        str(order.get("side") or "").lower() == "buy"
        and str(order.get("client_order_id") or "").startswith(CLIENT_ID_PREFIX)
    )


async def cancel_stale_entries(client: AlpacaMCPClient, open_orders: list[dict] | None) -> list[dict]:
    """Cancel every stale entry in `open_orders`; one result dict per
    attempt, ok=False with the broker's text when a cancel did not land
    (an order that filled in the meantime is the usual reason)."""
    results = []
    for order in open_orders or []:
        if not is_stale_entry(order):
            continue
        entry = {k: order.get(k) for k in ("id", "client_order_id", "symbol", "qty", "limit_price", "submitted_at")}
        try:
            result = await client.call_tool("cancel_order_by_id", {"order_id": str(order.get("id"))})
            text = "\n".join(b.text for b in getattr(result, "content", []) if hasattr(b, "text")).strip()
            failed = "error" in text.lower() or "not cancelable" in text.lower()
            entry.update(ok=not failed, detail=text[:200] if failed else "cancelled")
        except Exception as exc:  # noqa: BLE001 - one bad cancel must not stop the cycle
            entry.update(ok=False, detail=f"{type(exc).__name__}: {exc}"[:200])
        results.append(entry)
    return results
