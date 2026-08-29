"""Deterministic exits - checked every cycle BEFORE the model is consulted.

The model decides entries; code decides when a position is done. Three
rules, all from config.yaml, all producing plain sell Proposals that go
through the same execute.place_proposal() funnel as everything else:

- expiry:      an option with <= expiry_close_dte days left is closed, full
               stop. A long option carried into expiration is either
               auto-exercised (a surprise stock position) or expires worthless.
- stop_loss:   position has lost stop_loss_pct of its entry price.
- take_profit: position has gained take_profit_pct over its entry price.

Percent moves are measured on Alpaca's own avg_entry_price vs current_price
for the position, so they work identically for options (per-contract
premium) and stock. A position missing either price simply can't trigger
the price rules (never a crash) - the expiry rule needs neither.
"""

from datetime import date

from bot.models import Position, Proposal
from bot.occ import parse_occ_symbol

EXPIRY = "expiry"
STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"


def days_to_expiration(p: Position, today: date) -> int | None:
    if p.instrument != "option":
        return None
    try:
        return (parse_occ_symbol(p.symbol).expiration - today).days
    except ValueError:
        return None


def pnl_pct(p: Position) -> float | None:
    if not p.avg_entry_price or p.current_price is None or p.avg_entry_price <= 0:
        return None
    return (p.current_price - p.avg_entry_price) / p.avg_entry_price * 100


def exit_reason(p: Position, today: date, config: dict) -> str | None:
    """Which rule, if any, says this position must be closed now. Expiry is
    checked first: it's the one that can't be argued with."""
    dte = days_to_expiration(p, today)
    if dte is not None and dte <= int(config.get("expiry_close_dte", 0)):
        return EXPIRY

    move = pnl_pct(p)
    if move is None:
        return None
    stop = config.get("stop_loss_pct")
    if stop is not None and move <= -abs(float(stop)):
        return STOP_LOSS
    take = config.get("take_profit_pct")
    if take is not None and move >= abs(float(take)):
        return TAKE_PROFIT
    return None


def check_exits(positions: dict, today: date, config: dict) -> list[Proposal]:
    """Sell proposals for every held position an exit rule fires on. Whole
    position, market order - an exit is not the place to be clever about
    fills."""
    proposals = []
    for p in positions.values():
        reason = exit_reason(p, today, config)
        if reason is None:
            continue
        qty = int(p.qty)
        if qty <= 0:
            continue
        move = pnl_pct(p)
        detail = f"{reason}" if move is None else f"{reason} ({move:+.1f}% vs entry)"
        proposals.append(
            Proposal(
                instrument=p.instrument,
                symbol=p.symbol,
                side="sell",
                qty=qty,
                underlying=p.underlying,
                reason=detail,
            )
        )
    return proposals
