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


@dataclass
class AccountState:
    equity: float
    start_of_day_equity: float
    cash: float
    positions: dict = field(default_factory=dict)  # symbol -> Position

    @property
    def open_position_count(self) -> int:
        return len(self.positions)
