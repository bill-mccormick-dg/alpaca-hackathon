import unittest

from bot.models import AccountState, Position, Proposal


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
