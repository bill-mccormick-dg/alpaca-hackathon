import unittest
from datetime import date

from bot.occ import parse_occ_symbol


class ParseOCCSymbolTest(unittest.TestCase):
    def test_parses_call(self):
        result = parse_occ_symbol("AAPL250117C00150000")
        self.assertEqual(result.underlying, "AAPL")
        self.assertEqual(result.expiration, date(2025, 1, 17))
        self.assertEqual(result.option_type, "call")
        self.assertEqual(result.strike, 150.0)

    def test_parses_put(self):
        result = parse_occ_symbol("SPY250620P00500500")
        self.assertEqual(result.underlying, "SPY")
        self.assertEqual(result.expiration, date(2025, 6, 20))
        self.assertEqual(result.option_type, "put")
        self.assertEqual(result.strike, 500.5)

    def test_multi_character_underlying(self):
        result = parse_occ_symbol("GOOGL250117C00150000")
        self.assertEqual(result.underlying, "GOOGL")

    def test_rejects_malformed_symbol(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol("not-a-symbol")

    def test_rejects_plain_stock_ticker(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol("AAPL")


if __name__ == "__main__":
    unittest.main()
