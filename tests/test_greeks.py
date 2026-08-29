import unittest

from bot import greeks


class BlackScholesPriceTest(unittest.TestCase):
    def test_call_price_matches_known_reference(self):
        # Textbook reference case: S=100, K=100, T=1yr, r=5%, sigma=20%
        # -> call ~10.4506 (Hull, "Options, Futures, and Other Derivatives").
        price = greeks.black_scholes_price(100, 100, 1.0, 0.05, 0.20, "call")
        self.assertAlmostEqual(price, 10.4506, places=3)

    def test_put_price_matches_known_reference(self):
        # Same inputs, put ~5.5735 via put-call parity.
        price = greeks.black_scholes_price(100, 100, 1.0, 0.05, 0.20, "put")
        self.assertAlmostEqual(price, 5.5735, places=3)

    def test_put_call_parity_holds(self):
        import math

        spot, strike, years, rate, sigma = 250, 240, 0.25, 0.04, 0.30
        call = greeks.black_scholes_price(spot, strike, years, rate, sigma, "call")
        put = greeks.black_scholes_price(spot, strike, years, rate, sigma, "put")
        # C - P = S - K*e^(-rT)
        self.assertAlmostEqual(call - put, spot - strike * math.exp(-rate * years), places=6)

    def test_rejects_invalid_option_type(self):
        with self.assertRaises(ValueError):
            greeks.black_scholes_price(100, 100, 1.0, 0.05, 0.20, "straddle")

    def test_rejects_nonpositive_years(self):
        with self.assertRaises(ValueError):
            greeks.black_scholes_price(100, 100, 0, 0.05, 0.20, "call")


class ImpliedVolatilityTest(unittest.TestCase):
    def test_recovers_known_sigma_for_call(self):
        true_sigma = 0.35
        market_price = greeks.black_scholes_price(150, 145, 0.5, 0.04, true_sigma, "call")
        solved = greeks.implied_volatility(market_price, 150, 145, 0.5, "call")
        self.assertAlmostEqual(solved, true_sigma, places=4)

    def test_recovers_known_sigma_for_put(self):
        true_sigma = 0.22
        market_price = greeks.black_scholes_price(400, 410, 0.1, 0.04, true_sigma, "put")
        solved = greeks.implied_volatility(market_price, 400, 410, 0.1, "put")
        self.assertAlmostEqual(solved, true_sigma, places=4)

    def test_deep_itm_call_still_converges(self):
        # Deep ITM: price is almost pure intrinsic value, low vega -> the
        # case Newton-Raphson struggles with; bisection should not.
        true_sigma = 0.25
        market_price = greeks.black_scholes_price(500, 300, 0.5, 0.04, true_sigma, "call")
        solved = greeks.implied_volatility(market_price, 500, 300, 0.5, "call")
        self.assertAlmostEqual(solved, true_sigma, places=3)

    def test_price_below_intrinsic_floor_returns_none(self):
        # A call can never trade below max(0, S-K) without arbitrage.
        solved = greeks.implied_volatility(0.01, 500, 100, 0.5, "call")
        self.assertIsNone(solved)

    def test_zero_or_negative_years_returns_none(self):
        self.assertIsNone(greeks.implied_volatility(5.0, 100, 100, 0, "call"))
        self.assertIsNone(greeks.implied_volatility(5.0, 100, 100, -0.1, "call"))

    def test_nonpositive_price_returns_none(self):
        self.assertIsNone(greeks.implied_volatility(0, 100, 100, 0.5, "call"))


class GreeksTest(unittest.TestCase):
    def test_call_delta_between_zero_and_one(self):
        market_price = greeks.black_scholes_price(100, 100, 0.5, 0.04, 0.25, "call")
        g = greeks.greeks(market_price, 100, 100, 0.5, "call")
        self.assertGreater(g.delta, 0)
        self.assertLess(g.delta, 1)

    def test_put_delta_between_negative_one_and_zero(self):
        market_price = greeks.black_scholes_price(100, 100, 0.5, 0.04, 0.25, "put")
        g = greeks.greeks(market_price, 100, 100, 0.5, "put")
        self.assertGreater(g.delta, -1)
        self.assertLess(g.delta, 0)

    def test_atm_call_delta_is_roughly_half(self):
        market_price = greeks.black_scholes_price(100, 100, 0.5, 0.04, 0.25, "call")
        g = greeks.greeks(market_price, 100, 100, 0.5, "call")
        self.assertAlmostEqual(g.delta, 0.5, delta=0.15)

    def test_gamma_and_vega_are_positive_and_shared_by_call_and_put(self):
        # Gamma and vega don't depend on option_type in Black-Scholes.
        call_price = greeks.black_scholes_price(200, 195, 0.3, 0.04, 0.3, "call")
        put_price = greeks.black_scholes_price(200, 195, 0.3, 0.04, 0.3, "put")
        gc = greeks.greeks(call_price, 200, 195, 0.3, "call")
        gp = greeks.greeks(put_price, 200, 195, 0.3, "put")
        self.assertGreater(gc.gamma, 0)
        self.assertGreater(gc.vega, 0)
        self.assertAlmostEqual(gc.gamma, gp.gamma, places=6)
        self.assertAlmostEqual(gc.vega, gp.vega, places=6)

    def test_long_option_theta_is_negative(self):
        # Time decay: a held long option loses value as expiration nears
        # (ignoring the rare deep ITM put case where theta can flip sign).
        market_price = greeks.black_scholes_price(100, 100, 0.25, 0.04, 0.3, "call")
        g = greeks.greeks(market_price, 100, 100, 0.25, "call")
        self.assertLess(g.theta, 0)

    def test_returns_none_when_iv_unsolvable(self):
        g = greeks.greeks(0.01, 500, 100, 0.5, "call")
        self.assertIsNone(g)


if __name__ == "__main__":
    unittest.main()
