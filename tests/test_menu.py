"""bot/menu.py - the menu offers strike distance on every name (#159)."""

import random
import unittest
from datetime import date, timedelta

from bot import menu

TODAY = date(2026, 9, 1)


def occ(underlying, dte, cp, strike):
    exp = TODAY + timedelta(days=dte)
    return f"{underlying}{exp:%y%m%d}{cp}{round(strike * 1000):08d}"


def contract(delta=None):
    raw = {"latestQuote": {"bp": 1.0, "ap": 1.1}}
    if delta is not None:
        raw["greeks"] = {"delta": delta}
    return raw


def nvda_chain(dtes=(2, 4, 9, 16, 23, 30), strikes=(210, 212.5, 215, 217.5, 220, 222.5, 225, 227.5, 230)):
    """Coarse $2.50 strikes, weekly expiries, no Greeks - the 2026-08-31 menu
    that came out as twelve K=220 rows."""
    return {occ("NVDA", d, cp, k): contract() for d in dtes for cp in "CP" for k in strikes}


def shape(selected):
    out = []
    for symbol, _ in selected:
        o = menu.parse_occ_symbol(symbol)
        out.append(((o.expiration - TODAY).days, o.strike, o.option_type))
    return out


class ExpiryBucketsTest(unittest.TestCase):
    def test_nearest_distinct_expiry_to_each_target(self):
        pool = menu.parse_pool(nvda_chain(), TODAY)
        self.assertEqual([(e - TODAY).days for e in menu.expiry_buckets(pool)], [2, 9, 16])

    def test_fewer_expiries_than_targets_collapse(self):
        pool = menu.parse_pool(nvda_chain(dtes=(5,)), TODAY)
        self.assertEqual([(e - TODAY).days for e in menu.expiry_buckets(pool)], [5])


class SelectMenuTest(unittest.TestCase):
    def test_coarse_strikes_get_atm_and_otm_across_three_expiries(self):
        """The #159 case. Spot 220.4: ATM is 220 on both sides; with no delta
        the OTM pick is the next strike out - 222.5 call, 217.5 put."""
        chosen = shape(menu.select_menu(nvda_chain(), 220.4, TODAY, 12))
        expected = [(d, k, t) for d in (2, 9, 16) for k, t in ((217.5, "put"), (220.0, "call"), (220.0, "put"), (222.5, "call"))]
        self.assertEqual(chosen, expected)
        self.assertEqual(len({k for _, k, _ in chosen}), 3, "three strikes, not one")

    def test_fine_strikes_pick_the_otm_strike_by_delta_not_adjacency(self):
        """SPY-style $1 strikes with Alpaca's deltas: the 0.40-delta call is
        770, three strikes out, not the 768 neighbour."""
        spot = 767.3
        chain = {}
        for k in range(760, 776):
            chain[occ("SPY", 7, "C", k)] = contract(delta=round(0.5 - (k - spot) * 0.035, 3))
            chain[occ("SPY", 7, "P", k)] = contract(delta=round(-0.5 - (k - spot) * 0.035, 3))
        chosen = shape(menu.select_menu(chain, spot, TODAY, 4))
        self.assertEqual(chosen, [(7, 764.0, "put"), (7, 767.0, "call"), (7, 767.0, "put"), (7, 770.0, "call")])

    def test_atm_for_every_expiry_comes_before_any_otm(self):
        chosen = shape(menu.select_menu(nvda_chain(), 220.4, TODAY, 6))
        self.assertEqual(chosen, [(d, 220.0, t) for d in (2, 9, 16) for t in ("call", "put")])

    def test_leftover_slots_fall_back_to_nearest_the_money(self):
        """One expiry, calls only, four slots: ATM, the OTM step, then the
        old rule fills the rest from what is left."""
        chain = {occ("AAPL", 20, "C", k): contract() for k in (180, 190, 200, 210, 220)}
        chosen = shape(menu.select_menu(chain, 200.0, TODAY, 4))
        self.assertEqual(chosen, [(20, 180.0, "call"), (20, 190.0, "call"), (20, 200.0, "call"), (20, 210.0, "call")])

    def test_below_the_entry_floor_and_expired_are_never_offered(self):
        chain = nvda_chain(dtes=(0, 1, 2, 9))
        chosen = shape(menu.select_menu(chain, 220.4, TODAY, 12, min_dte=2))
        self.assertTrue(all(d >= 2 for d, _, _ in chosen))
        self.assertIn(2, {d for d, _, _ in chosen})

    def test_deterministic_regardless_of_dict_order(self):
        items = list(nvda_chain().items())
        random.Random(7).shuffle(items)
        self.assertEqual(menu.select_menu(dict(items), 220.4, TODAY, 12), menu.select_menu(nvda_chain(), 220.4, TODAY, 12))

    def test_garbage_and_empty(self):
        self.assertEqual(menu.select_menu({"not-occ": contract()}, 200.0, TODAY, 12), [])
        self.assertEqual(menu.select_menu({}, 200.0, TODAY, 12), [])
        self.assertEqual(menu.select_menu(nvda_chain(), 220.4, TODAY, 0), [])

    def test_returns_the_raw_snapshot_for_the_summariser(self):
        chain = nvda_chain(dtes=(2,), strikes=(220,))
        [(symbol, raw)] = menu.select_menu(chain, 220.4, TODAY, 1)
        self.assertIs(raw, chain[symbol])


if __name__ == "__main__":
    unittest.main()
