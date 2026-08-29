"""Black-Scholes option pricing, implied volatility, and Greeks.

Alpaca's indicative options feed carries no Greeks or IV at all (no OPRA
subscription — see bot/snapshot.py). This derives them instead: solve for
the volatility that makes the Black-Scholes price match a contract's real
market price (implied volatility), then compute the standard Greeks from
that solved volatility.

European exercise, no dividend yield, and a fixed approximate risk-free
rate — simplifications. This project's whitelist is broad ETFs/large-caps
on short (<=45 day) expirations, close enough to the model for that window;
American-exercise early-assignment risk and dividend effects are not
modeled.
"""

import math
from dataclasses import dataclass

RISK_FREE_RATE = 0.04  # approximate short-term Treasury yield; not fetched live


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def black_scholes_price(
    spot: float, strike: float, years: float, rate: float, sigma: float, option_type: str
) -> float:
    """European option price. `years` is time to expiration in years,
    `sigma` is annualized volatility as a decimal (0.25 = 25%)."""
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        raise ValueError("spot, strike, years, and sigma must all be positive")
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * years) * _norm_cdf(d2)
    if option_type == "put":
        return strike * math.exp(-rate * years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    years: float,
    option_type: str,
    rate: float = RISK_FREE_RATE,
) -> float | None:
    """Solves for sigma via bisection. Bisection (not Newton-Raphson) is
    deliberate: Black-Scholes price is monotonic increasing in sigma, so
    bisection is guaranteed to converge, including for far OTM/ITM
    contracts where Newton's method can misbehave (near-zero vega). Returns
    None if market_price falls outside the no-arbitrage range spanned by
    sigma in (0, 5] — a sign of a stale or bad quote rather than a real
    price — or if years<=0."""
    if years <= 0 or market_price <= 0:
        return None

    lo, hi = 1e-4, 5.0
    price_lo = black_scholes_price(spot, strike, years, rate, lo, option_type) - market_price
    price_hi = black_scholes_price(spot, strike, years, rate, hi, option_type) - market_price
    if price_lo > 0 or price_hi < 0:
        return None

    for _ in range(100):
        mid = (lo + hi) / 2
        price_mid = black_scholes_price(spot, strike, years, rate, mid, option_type) - market_price
        if abs(price_mid) < 1e-6:
            return mid
        if price_mid > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


@dataclass
class Greeks:
    implied_vol: float
    delta: float
    gamma: float
    theta: float  # dollars per calendar day, per share
    vega: float  # dollars per 1 percentage point of volatility, per share


def greeks(
    market_price: float,
    spot: float,
    strike: float,
    years: float,
    option_type: str,
    rate: float = RISK_FREE_RATE,
) -> Greeks | None:
    """Derives IV then the standard Greeks from it. Returns None if IV
    can't be solved (see implied_volatility)."""
    sigma = implied_volatility(market_price, spot, strike, years, option_type, rate)
    if sigma is None:
        return None

    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (spot * sigma * math.sqrt(years))
    vega = spot * pdf_d1 * math.sqrt(years) / 100  # per 1 vol point, not per full unit

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta_per_year = (
            -(spot * pdf_d1 * sigma) / (2 * math.sqrt(years))
            - rate * strike * math.exp(-rate * years) * _norm_cdf(d2)
        )
    else:
        delta = _norm_cdf(d1) - 1
        theta_per_year = (
            -(spot * pdf_d1 * sigma) / (2 * math.sqrt(years))
            + rate * strike * math.exp(-rate * years) * _norm_cdf(-d2)
        )

    return Greeks(
        implied_vol=sigma,
        delta=delta,
        gamma=gamma,
        theta=theta_per_year / 365,
        vega=vega,
    )
