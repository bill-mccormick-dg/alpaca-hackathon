"""Guardrail enforcement — the single gate every order proposal must pass.

Mirrors alpaca-trader's trader/risk.py: config.yaml sets hard caps, and
check_order() never negotiates a proposal into compliance — a proposal that
violates a limit is rejected outright, with a reason, never clamped.
"""

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.models import VALID_INSTRUMENTS, VALID_SIDES, AccountState, Proposal
from bot.occ import parse_occ_symbol

EASTERN = ZoneInfo("America/New_York")

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _eastern_time(now: datetime) -> time:
    """Wall-clock time in US/Eastern. A naive `now` is assumed to already
    represent Eastern time (convenient for tests); an aware one is
    converted properly."""
    if now.tzinfo is not None:
        now = now.astimezone(EASTERN)
    return now.time()


class RiskManager:
    def __init__(self, config: dict, logs_dir: Path = LOGS_DIR, account: str | None = None):
        # account: scopes BOTH halt kinds to one account, so a challenger can
        # never stop the judged account - not by breaching its daily-loss
        # cutoff, and not by having its kill switch pressed. The one halt that
        # is still deliberately global is logs/HALT (global_halt_file), which
        # only the CLI can write (flatten.py --halt --all-accounts).
        self.account = account if account and account != "official" else None
        self.underlyings = set(config["underlyings"])
        self.max_position_usd = config["max_position_usd"]
        self.max_positions = config["max_positions"]
        self.max_contracts_per_order = config["max_contracts_per_order"]
        self.daily_loss_cutoff_pct = config["daily_loss_cutoff_pct"]
        self.min_days_to_expiration = config["min_days_to_expiration"]
        self.max_days_to_expiration = config["max_days_to_expiration"]
        self.trade_start = _parse_time(config["trade_start"])
        self.trade_end = _parse_time(config["trade_end"])
        self.last_entry = _parse_time(config["last_entry"])
        self.logs_dir = logs_dir

    # --- Session-level gates, checked once per cycle before any order ------

    def global_halt_file(self) -> Path:
        """The break-glass kill switch: halts EVERY account. Written only by
        `flatten.py --halt --all-accounts` - deliberately not reachable from
        MQTT/Home Assistant, so no dashboard tap can stop the judged account
        by accident during the scoring window."""
        return self.logs_dir / "HALT"

    def manual_halt_file(self) -> Path:
        """This account's own kill switch (`flatten.py --halt`, or the HA
        button). Scoped like daily_halt_file so the challenger's kill switch
        never touches the official account."""
        suffix = f"_{self.account}" if self.account else ""
        return self.logs_dir / f"HALT_manual{suffix}"

    def daily_halt_file(self, day: date | None = None) -> Path:
        day = day or datetime.now(EASTERN).date()
        suffix = f"_{self.account}" if self.account else ""
        return self.logs_dir / f"HALT{suffix}_{day.isoformat()}"

    def halt_state(self, now: datetime | None = None) -> str:
        """Compact token for the halt sensor / kill-switch switch state,
        derived from the files themselves rather than from journal events -
        a halted account runs no cycles, so an event-driven state would
        freeze at its last value until a human both cleared the halt AND a
        cycle happened to run. "none" when trading is allowed."""
        if self.global_halt_file().exists():
            return "global"
        if self.manual_halt_file().exists():
            return "manual"
        day = (now or datetime.now(EASTERN)).date()
        if self.daily_halt_file(day).exists():
            return "daily_loss"
        return "none"

    def halted(self, now: datetime | None = None) -> str | None:
        return {
            "none": None,
            "global": "global halt",
            "manual": "manual halt",
            "daily_loss": "daily loss halt",
        }[self.halt_state(now)]

    def in_trading_window(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(EASTERN)
        return self.trade_start <= _eastern_time(now) <= self.trade_end

    def entries_allowed(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(EASTERN)
        return self.trade_start <= _eastern_time(now) <= self.last_entry

    def daily_loss_breached(self, account: AccountState) -> bool:
        if account.start_of_day_equity <= 0:
            return False
        loss_pct = (
            (account.start_of_day_equity - account.equity) / account.start_of_day_equity * 100
        )
        return loss_pct >= self.daily_loss_cutoff_pct

    # --- Per-order gate ------------------------------------------------------

    def check_order(
        self, p: Proposal, account: AccountState, price: float, now: datetime | None = None
    ) -> tuple[bool, str]:
        now = now or datetime.now(EASTERN)

        if p.instrument not in VALID_INSTRUMENTS:
            return False, f"invalid instrument: {p.instrument!r}"
        if p.side not in VALID_SIDES:
            return False, f"invalid side: {p.side!r}"
        if p.qty <= 0:
            return False, "qty must be positive"
        if price <= 0:
            return False, "price must be positive"
        if p.whitelist_symbol not in self.underlyings:
            return False, f"{p.whitelist_symbol} not in underlyings whitelist"

        if p.instrument == "option":
            if p.qty > self.max_contracts_per_order:
                return (
                    False,
                    f"qty {p.qty} exceeds max_contracts_per_order {self.max_contracts_per_order}",
                )
            try:
                occ = parse_occ_symbol(p.symbol)
            except ValueError as exc:
                return False, str(exc)
            dte = (occ.expiration - now.date()).days
            if not (self.min_days_to_expiration <= dte <= self.max_days_to_expiration):
                return False, (
                    f"{dte} days to expiration outside "
                    f"[{self.min_days_to_expiration}, {self.max_days_to_expiration}]"
                )

        held = account.positions.get(p.symbol)
        held_qty = held.qty if held else 0

        if p.side == "sell":
            if p.qty > held_qty:
                return False, f"cannot sell {p.qty}, only {held_qty} held"
            return True, "ok"

        # buy
        if not self.entries_allowed(now):
            return False, "entries not allowed (outside window or past last_entry)"

        held_value = held.market_value if held else 0.0
        order_value = self._order_notional(p, price)
        if held_value + order_value > self.max_position_usd:
            return False, (
                f"position value {held_value + order_value:.2f} "
                f"exceeds max_position_usd {self.max_position_usd}"
            )

        if held_qty == 0 and account.open_position_count >= self.max_positions:
            return False, f"already at max_positions ({self.max_positions})"

        return True, "ok"

    def _order_notional(self, p: Proposal, price: float) -> float:
        if p.instrument == "option":
            return p.qty * price * 100  # 100 shares/contract
        return p.qty * price
