"""Shared data model for order proposals and account state.

Mirrors alpaca-trader's trader/risk.py Proposal/AccountState shape, extended
for options. Deliberately unchecked at construction — a model can propose
garbage, and it's bot/risk.py's job (not this module's) to validate and
reject it with a clear reason. See bot/risk.py's check_order() for why:
that's the one place semantic validation happens, so there's exactly one
gate a proposal has to pass, not two places that could disagree.
"""

from dataclasses import dataclass, field

VALID_INSTRUMENTS = ("stock", "option")
VALID_SIDES = ("buy", "sell")
VALID_ORDER_TYPES = ("market", "limit", "stop", "stop_limit")


@dataclass
class Proposal:
    instrument: str  # "stock" | "option"
    symbol: str  # ticker (stock) or OCC option symbol (option)
    side: str  # "buy" | "sell"
    qty: int  # shares or contracts
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    underlying: str | None = None  # required when instrument == "option"
    reason: str = ""

    @property
    def whitelist_symbol(self) -> str:
        """What risk checks whitelist against — the underlying for options
        (the OCC symbol itself isn't in config.yaml's underlyings list),
        the symbol itself for stock."""
        return self.underlying if self.instrument == "option" else self.symbol


@dataclass
class Position:
    symbol: str
    instrument: str
    qty: float
    market_value: float
    underlying: str | None = None
    avg_entry_price: float | None = None  # per share / per contract-share, as Alpaca reports
    current_price: float | None = None


@dataclass
class OpenOrder:
    """An order the broker still holds open (#171). Sizing against holdings
    alone under-counts between submission and fill; these are the committed
    part."""

    id: str
    symbol: str
    side: str
    qty: float
    filled_qty: float = 0.0
    order_type: str = "market"
    limit_price: float | None = None
    submitted_at: str | None = None
    client_order_id: str | None = None
    instrument: str = "option"

    @property
    def remaining(self) -> float:
        return max(self.qty - self.filled_qty, 0.0)


@dataclass
class AccountState:
    equity: float
    start_of_day_equity: float
    cash: float
    positions: dict = field(default_factory=dict)  # symbol -> Position
    # What the broker says this account IS, as opposed to what --account called
    # it. None when it could not be read - see bot/identity.py for the policy.
    account_number: str | None = None
    # Orders resting at the broker (OpenOrder). None means the lookup failed
    # or was never made - the funnel then sizes on holdings alone, as it
    # always did; an empty list means "checked, nothing resting".
    open_orders: list | None = None

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    def pending_buys(self, symbol: str | None = None) -> list:
        return [o for o in self.open_orders or [] if o.side == "buy" and o.remaining > 0 and (symbol is None or o.symbol == symbol)]

    def pending_sell_qty(self, symbol: str) -> float:
        return sum(o.remaining for o in self.open_orders or [] if o.side == "sell" and o.symbol == symbol)

    @property
    def committed_position_count(self) -> int:
        """Held positions plus symbols a resting buy would open - what
        max_positions has to be measured against, or four resting buys for
        four contracts pass a cap of four with nothing held and all fill."""
        return len(set(self.positions) | {o.symbol for o in self.pending_buys()})
