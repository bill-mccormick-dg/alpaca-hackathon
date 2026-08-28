"""Order execution — the single funnel every write path uses.

Mirrors alpaca-trader's trader/execute.py: whatever calls this (a decision
loop, an MCP tool, a manual script) is bound by risk.py's gates the same
way, because there is no other way to place an order in this codebase. The
model NEVER gets direct access to Alpaca's place_stock_order/
place_option_order MCP tools — only this function calls them, and only
after RiskManager.check_order() approves.

Alpaca's MCP server wants every numeric order field as a STRING, not a JSON
number (confirmed from its live tool schema, not just prose docs) — every
arg builder below stringifies explicitly.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from bot.alpaca_mcp import AlpacaMCPClient
from bot.models import AccountState, Proposal
from bot.risk import EASTERN, RiskManager

ZERO_QTY = "zero_qty"
REJECTED = "rejected"
DRY_RUN = "dry_run"
SUBMITTED = "submitted"
ERROR = "error"


@dataclass
class ExecutionResult:
    status: str
    proposal: Proposal
    detail: str
    order_id: str | None = None


def client_order_id(p: Proposal, now: datetime | None = None) -> str:
    """Deterministic per-proposal id, mirroring alpaca-trader's
    client_order_id() — a retry with the same proposal in the same second
    reuses it, and Alpaca rejects the duplicate rather than double-submitting."""
    now = now or datetime.now(EASTERN)
    return f"hb-{now:%Y%m%d-%H%M%S}-{p.symbol}-{p.side}"


async def place_proposal(
    client: AlpacaMCPClient,
    risk: RiskManager,
    account: AccountState,
    price: float,
    p: Proposal,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ExecutionResult:
    if p.qty <= 0:
        return ExecutionResult(ZERO_QTY, p, "qty must be positive")

    ok, reason = risk.check_order(p, account, price, now)
    if not ok:
        return ExecutionResult(REJECTED, p, reason)

    if dry_run:
        return ExecutionResult(DRY_RUN, p, "dry run — no order submitted")

    if p.instrument == "option":
        tool, args = "place_option_order", _option_order_args(p, now)
    else:
        tool, args = "place_stock_order", _stock_order_args(p, now)

    try:
        result = await client.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 - broker call must never raise past this funnel
        return ExecutionResult(ERROR, p, str(exc))

    order_id = _extract_order_id(result)
    if order_id is None:
        return ExecutionResult(ERROR, p, _result_text(result))
    return ExecutionResult(SUBMITTED, p, "submitted", order_id=order_id)


def _stock_order_args(p: Proposal, now: datetime | None) -> dict:
    args = {
        "symbol": p.symbol,
        "side": p.side,
        "qty": str(p.qty),
        "type": p.order_type,
        "time_in_force": "day",
        "client_order_id": client_order_id(p, now),
    }
    if p.limit_price is not None:
        args["limit_price"] = str(p.limit_price)
    if p.stop_price is not None:
        args["stop_price"] = str(p.stop_price)
    return args


def _option_order_args(p: Proposal, now: datetime | None) -> dict:
    # Single-leg only for now — multi-leg (legs=[...]) is a later increment.
    # Alpaca's MCP schema documents only "market"/"limit" for options (no
    # stop/stop_limit, unlike stock); an unsupported type surfaces as an
    # ERROR result rather than being pre-validated here.
    args = {
        "symbol": p.symbol,
        "side": p.side,
        "qty": str(p.qty),
        "type": p.order_type,
        "time_in_force": "day",  # options support only "day" via MCP
        "client_order_id": client_order_id(p, now),
    }
    if p.limit_price is not None:
        args["limit_price"] = str(p.limit_price)
    return args


def _result_text(result) -> str:
    parts = [block.text for block in getattr(result, "content", []) if hasattr(block, "text")]
    return "\n".join(parts)


def _extract_order_id(result) -> str | None:
    try:
        data = json.loads(_result_text(result))
    except (json.JSONDecodeError, TypeError):
        return None
    order = data.get("data", data) if isinstance(data, dict) else None
    return order.get("id") if isinstance(order, dict) else None
