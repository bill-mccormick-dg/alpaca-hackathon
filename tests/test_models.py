import unittest

from bot.models import AccountState, OpenOrder, Position, Proposal


class ProposalWhitelistSymbolTest(unittest.TestCase):
    def test_stock_proposal_whitelists_on_symbol(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=10)
        self.assertEqual(p.whitelist_symbol, "AAPL")

    def test_option_proposal_whitelists_on_underlying_not_occ_symbol(self):
        p = Proposal(
            instrument="option",
            symbol="AAPL250117C00150000",
            side="buy",
            qty=1,
            underlying="AAPL",
        )
        self.assertEqual(p.whitelist_symbol, "AAPL")


class AccountStateTest(unittest.TestCase):
    def test_open_position_count_reflects_positions(self):
        account = AccountState(
            equity=100000,
            start_of_day_equity=100000,
            cash=100000,
            positions={
                "AAPL": Position(symbol="AAPL", instrument="stock", qty=10, market_value=1500),
            },
        )
        self.assertEqual(account.open_position_count, 1)

    def test_open_position_count_zero_when_empty(self):
        account = AccountState(equity=100000, start_of_day_equity=100000, cash=100000)
        self.assertEqual(account.open_position_count, 0)


if __name__ == "__main__":
    unittest.main()


class OpenOrdersOnAccountTest(unittest.TestCase):
    """#171: resting orders are committed exposure the funnel has to count."""

    def _account(self, orders=None, positions=None):
        from bot.models import AccountState, Position

        held = {s: Position(symbol=s, instrument="option", qty=4, market_value=1000) for s in positions or []}
        return AccountState(equity=1, start_of_day_equity=1, cash=1, positions=held, open_orders=orders)

    def test_remaining_is_qty_less_filled(self):
        self.assertEqual(OpenOrder(id="a", symbol="X", side="buy", qty=4, filled_qty=1).remaining, 3)
        self.assertEqual(OpenOrder(id="a", symbol="X", side="buy", qty=4, filled_qty=4).remaining, 0)

    def test_pending_buys_and_sells_are_split_by_side_and_symbol(self):
        acct = self._account([
            OpenOrder(id="a", symbol="X", side="buy", qty=4),
            OpenOrder(id="b", symbol="X", side="sell", qty=2),
            OpenOrder(id="c", symbol="Y", side="buy", qty=1, filled_qty=1),
        ])
        self.assertEqual([o.id for o in acct.pending_buys()], ["a"])
        self.assertEqual([o.id for o in acct.pending_buys("X")], ["a"])
        self.assertEqual(acct.pending_buys("Y"), [])
        self.assertEqual(acct.pending_sell_qty("X"), 2)
        self.assertEqual(acct.pending_sell_qty("Y"), 0)

    def test_committed_count_unions_held_and_resting_buy_symbols(self):
        acct = self._account([OpenOrder(id="a", symbol="X", side="buy", qty=4), OpenOrder(id="b", symbol="Z", side="buy", qty=4)],
                             positions=["X", "Y"])
        self.assertEqual(acct.open_position_count, 2)
        self.assertEqual(acct.committed_position_count, 3)

    def test_unknown_open_orders_behave_as_none_resting(self):
        acct = self._account(None, positions=["X"])
        self.assertIsNone(acct.open_orders)
        self.assertEqual((acct.pending_buys(), acct.pending_sell_qty("X"), acct.committed_position_count), ([], 0, 1))
