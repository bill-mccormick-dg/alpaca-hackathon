"""Close everything, verified against the broker - over the MCP server.

Mirrors alpaca-trader's Broker.flatten_all()/wait_until_flat(). Two waits
are load-bearing, both learned there the hard way:

- Alpaca's bulk cancel is asynchronous: cancel_all_orders returns before
  the orders leave the book, and a resting leg holds its shares until it
  does. Closing immediately fails "insufficient qty" and leaves the
  position open. So: cancel, WAIT for open orders to hit zero, then close.
- A market close takes seconds to fill. Snapshotting positions right after
  submitting reports every one as still open. So: poll until flat (or a
  timeout), and report what is *actually* still held.

This is a write path, but it only ever reduces exposure (cancel + close),
so it deliberately does not go through risk.check_order().
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

from bot.alpaca_mcp import AlpacaMCPClient
from bot.orders import (
    classify_close_results,
    describe_flatten_outcome,
    unprotected_positions,
)


def _result_list(result) -> list:
    text = "\n".join(b.text for b in getattr(result, "content", []) if hasattr(b, "text"))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text  # let classify_close_results treat it as a total failure
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return data.get("result", data) if isinstance(data, dict) else data


async def open_order_symbols(client: AlpacaMCPClient) -> set[str]:
    orders = _result_list(await client.call_tool("get_orders", {"status": "open"}))
    return {o.get("symbol") for o in orders if isinstance(o, dict)} if isinstance(orders, list) else set()


async def position_symbols(client: AlpacaMCPClient) -> list[str]:
    positions = _result_list(await client.call_tool("get_all_positions"))
    return [p.get("symbol") for p in positions if isinstance(p, dict)] if isinstance(positions, list) else []


async def market_is_open(client: AlpacaMCPClient) -> bool | None:
    try:
        clock = _result_list(await client.call_tool("get_clock"))
        return bool(clock.get("is_open")) if isinstance(clock, dict) else None
    except Exception:  # noqa: BLE001 - informational only, never fail a flatten over it
        return None


@dataclass
class FlattenOutcome:
    attempted: list[str]
    cancels_settled: bool
    closed: list[dict]
    failed: list[dict]
    remaining: list[str]
    pending_close: list[str]
    unprotected: list[str]
    market_open: bool | None
    verify_wait_sec: float
    state: str
    message: str
    extra: dict = field(default_factory=dict)


async def flatten_all(
    client: AlpacaMCPClient,
    settle_timeout_sec: float = 10.0,
    verify_timeout_sec: float = 30.0,
    poll_sec: float = 0.5,
) -> FlattenOutcome:
    attempted = await position_symbols(client)

    await client.call_tool("cancel_all_orders")
    deadline = time.monotonic() + settle_timeout_sec
    settled = False
    while True:
        if not await open_order_symbols(client):
            settled = True
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_sec)

    results = _result_list(await client.call_tool("close_all_positions", {"cancel_orders": True}))
    closed, failed = classify_close_results(results)

    start = time.monotonic()
    while True:
        remaining = await position_symbols(client)
        waited = time.monotonic() - start
        if not remaining or waited >= verify_timeout_sec:
            break
        await asyncio.sleep(poll_sec)
    pending = await open_order_symbols(client) if remaining else set()
    market_open = await market_is_open(client) if remaining else None
    state, message = describe_flatten_outcome(remaining, pending, market_open, waited, len(attempted))

    return FlattenOutcome(
        attempted=attempted,
        cancels_settled=settled,
        closed=closed,
        failed=failed,
        remaining=remaining,
        pending_close=sorted(pending),
        unprotected=unprotected_positions(remaining, pending),
        market_open=market_open,
        verify_wait_sec=round(waited, 2),
        state=state,
        message=message,
    )
