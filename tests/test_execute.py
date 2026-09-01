import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bot import execute
from bot.models import AccountState, Proposal
from bot.risk import RiskManager

UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]


def make_config(**overrides):
    config = {
        "underlyings": UNDERLYINGS,
        "max_position_usd": 5000,
        "max_positions": 4,
        "max_contracts_per_order": 10,
        "daily_loss_cutoff_pct": 2.0,
        "min_days_to_expiration": 1,
        "eod_close_dte": 0,
        "max_days_to_expiration": 45,
        "trade_start": "09:45",
        "trade_end": "15:45",
        "last_entry": "15:15",
    }
    config.update(overrides)
    return config


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeMCPClient:
    """Hand-written stub, not a mocking library — mirrors alpaca-trader's
    FakeBroker style. Records every call_tool invocation for assertions."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response if response is not None else {"data": {"id": "order-123"}}
        self.raise_exc = raise_exc
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raise_exc:
            raise self.raise_exc
        return FakeResult(json.dumps(self.response))


class PlaceProposalTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.risk = RiskManager(make_config(), logs_dir=Path(self.tmpdir.name))
        self.account = AccountState(equity=100000, start_of_day_equity=100000, cash=100000)
        self.mid_session = datetime(2026, 1, 15, 12, 0)
        self.client = FakeMCPClient()


class GuardrailShortCircuitTest(PlaceProposalTestBase):
    async def test_zero_qty_short_circuits_without_calling_client(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=0)
        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, now=self.mid_session
        )
        self.assertEqual(result.status, execute.ZERO_QTY)
        self.assertEqual(self.client.calls, [])

    async def test_rejected_proposal_never_calls_client(self):
        p = Proposal(instrument="stock", symbol="TSLA", side="buy", qty=1)  # not whitelisted
        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, now=self.mid_session
        )
        self.assertEqual(result.status, execute.REJECTED)
        self.assertEqual(self.client.calls, [])

    async def test_dry_run_never_calls_client(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, dry_run=True, now=self.mid_session
        )
        self.assertEqual(result.status, execute.DRY_RUN)
        self.assertEqual(self.client.calls, [])


class StockOrderSubmissionTest(PlaceProposalTestBase):
    async def test_calls_place_stock_order_with_stringified_args(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=5, order_type="market")
        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, now=self.mid_session
        )

        self.assertEqual(result.status, execute.SUBMITTED)
        self.assertEqual(result.order_id, "order-123")
        self.assertEqual(len(self.client.calls), 1)
        tool_name, args = self.client.calls[0]
        self.assertEqual(tool_name, "place_stock_order")
        self.assertEqual(args["symbol"], "AAPL")
        self.assertEqual(args["side"], "buy")
        self.assertEqual(args["qty"], "5")  # stringified, not int 5
        self.assertEqual(args["time_in_force"], "day")
        self.assertIn("client_order_id", args)

    async def test_limit_and_stop_prices_stringified_when_present(self):
        p = Proposal(
            instrument="stock",
            symbol="AAPL",
            side="buy",
            qty=1,
            order_type="stop_limit",
            limit_price=150.5,
            stop_price=149.0,
        )
        await execute.place_proposal(self.client, self.risk, self.account, 100, p, now=self.mid_session)

        _, args = self.client.calls[0]
        self.assertEqual(args["limit_price"], "150.5")
        self.assertEqual(args["stop_price"], "149.0")

    async def test_omits_limit_and_stop_price_when_absent(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        await execute.place_proposal(self.client, self.risk, self.account, 100, p, now=self.mid_session)

        _, args = self.client.calls[0]
        self.assertNotIn("limit_price", args)
        self.assertNotIn("stop_price", args)


class OptionOrderSubmissionTest(PlaceProposalTestBase):
    async def test_calls_place_option_order_with_occ_symbol_and_day_tif(self):
        p = Proposal(
            instrument="option",
            symbol="AAPL260204C00200000",
            side="buy",
            qty=2,
            underlying="AAPL",
        )
        result = await execute.place_proposal(
            self.client, self.risk, self.account, 2.0, p, now=self.mid_session
        )

        self.assertEqual(result.status, execute.SUBMITTED)
        tool_name, args = self.client.calls[0]
        self.assertEqual(tool_name, "place_option_order")
        self.assertEqual(args["symbol"], "AAPL260204C00200000")
        self.assertEqual(args["qty"], "2")
        self.assertEqual(args["time_in_force"], "day")


class ErrorHandlingTest(PlaceProposalTestBase):
    async def test_broker_exception_returns_error_status_not_raised(self):
        self.client = FakeMCPClient(raise_exc=RuntimeError("broker unavailable"))
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)

        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, now=self.mid_session
        )

        self.assertEqual(result.status, execute.ERROR)
        self.assertIn("broker unavailable", result.detail)

    async def test_response_without_order_id_returns_error(self):
        self.client = FakeMCPClient(response={"data": {"message": "something odd, no id"}})
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)

        result = await execute.place_proposal(
            self.client, self.risk, self.account, 100, p, now=self.mid_session
        )

        self.assertEqual(result.status, execute.ERROR)


class ClientOrderIdTest(unittest.TestCase):
    def test_deterministic_for_same_proposal_and_second(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        now = datetime(2026, 1, 15, 12, 0, 0)
        self.assertEqual(execute.client_order_id(p, now), execute.client_order_id(p, now))

    def test_differs_by_symbol(self):
        now = datetime(2026, 1, 15, 12, 0, 0)
        p1 = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        p2 = Proposal(instrument="stock", symbol="MSFT", side="buy", qty=1)
        self.assertNotEqual(execute.client_order_id(p1, now), execute.client_order_id(p2, now))


if __name__ == "__main__":
    unittest.main()
