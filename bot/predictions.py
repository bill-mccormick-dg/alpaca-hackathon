"""Prediction-market prior from Kalshi's public market data (issue #44).

Read-only, no key, never traded. Kalshi lists daily "range" markets on the
S&P 500 and Nasdaq-100 close (series KXINX / KXNASDAQ100): one YES contract
per price bucket, so the set of YES prices is a crowd-implied distribution
of today's close. The previous day's settled market carries
`expiration_value` - the actual index close - which is the reference
level. From those two we give the model a few facts it cannot get from
the option chain: the implied median close, P(close above yesterday), and
P(|move| > 1%), with the volume behind them.

This is a PRIOR handed to the model with an explanation, not a rule the
code follows. Any failure (network, shape change, no quotes yet) yields an
empty result and the cycle proceeds without it.
"""

import json
import math
import time
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

import httpx

from bot.risk import LOGS_DIR

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
CACHE_FILE = LOGS_DIR / "predictions-cache.json"
CACHE_TTL_SEC = 300
DEFAULT_SERIES = {"SPY": "KXINX", "QQQ": "KXNASDAQ100"}

# A range market that has barely traded still quotes every bucket, and the
# midpoints of thirty wide spreads are noise. Normalising noise does not make
# it a belief - it makes a FLAT distribution that looks authoritative, which is
# strictly worse than showing the model nothing.
#
# Measured on the 2026-08-31 event the evening before it opened: SPY on 70
# contracts of volume put 0.065 in its modal bucket against a uniform 0.033,
# and implied P(|move| > 1%) = 0.64 for a single session - roughly triple the
# real base rate. QQQ was worse on 14.6 contracts.
#
# So two gates, measuring two different failures:
#   volume   - nobody has traded it, so there is no crowd to imply anything
#   flatness - Shannon entropy of the bucket distribution over log(n), so 1.0
#              is perfectly uniform and lower is more peaked. Normalising by
#              log(n) is what makes it comparable across events: Kalshi splits
#              some days into 6 buckets and some into 30, and any raw measure
#              (modal bucket as a multiple of uniform, say) rates a genuinely
#              peaked 6-bucket market the same as a flat 30-bucket one.
MIN_VOLUME = 250.0
# 0.93. Modelled against well-priced 30-bucket distributions (normal, spread
# over a +/-4% range) this passes a calm-to-normal session and suppresses the
# unpriced weekend market that motivated the gate:
#
#     daily sigma 0.5%  -> 0.602      live SPY, 70 contracts   -> 0.957
#     daily sigma 0.8%  -> 0.740      live QQQ, 14.6 contracts -> 0.941
#     daily sigma 1.2%  -> 0.858      perfectly uniform        -> 1.000
#
# KNOWN BLIND SPOT, and it is the important line in this file: flatness cannot
# tell "these quotes carry no information" apart from "the crowd genuinely
# expects a wide day". The same table continues:
#
#     daily sigma 1.8%  -> 0.948   suppressed
#     daily sigma 2.5%  -> 0.983   suppressed
#
# So a correctly-priced high-volatility session is withheld precisely when a
# second opinion is worth most. VOLUME is therefore the load-bearing gate;
# treat this one as a backstop against flat quotes, and if it starts
# suppressing liquid days, raise it rather than assuming the market is broken.
# The real fix is to measure quote WIDTH rather than distribution shape - an
# unpriced market is one where every bucket has a wide bid/ask, which stays
# true however volatile the day is. See issue for that.
MAX_FLATNESS = 0.93

# How stale the reference close may be before we withhold it entirely.
#
# The reference is the previous session's actual index close, and every figure
# derived from it - implied_move_pct, P(above prior close), P(|move| > 1%) - is
# measured against it. Pointing it at the wrong day biases all four in the same
# direction at once, which is the failure this constant and latest_settlement()
# exist to stop.
#
# Measured on 2026-08-31, before the fix: the code took the FIRST settled market
# the API returned, and Kalshi's status=settled page is not ordered by date. It
# returned Thursday Aug 27 (7730.99) while Friday Aug 28 (7711.76) sat further
# down the page. A 0.25% error in the reference moved the numbers handed to the
# model like this:
#
#                        SPY  Thu ref -> Fri ref     QQQ  Thu ref -> Fri ref
#     implied move      -0.56%  ->  -0.31%          -0.98%  ->  -0.28%
#     P(above prior)     0.153  ->   0.297           0.323  ->   0.490
#     P(down > 1%)       0.356  ->   0.287           0.445  ->   0.323
#
# QQQ went from a clearly bearish prior to a coin flip. Nothing looked wrong:
# the distribution was real, the volume gate passed, the flatness gate passed.
# Only the yardstick was off.
#
# 5 days covers the longest ordinary gap (Thursday's close before a Friday
# holiday and a long weekend). Beyond that the feed has a problem, and no
# reference is better than a confidently wrong one.
MAX_REFERENCE_AGE_DAYS = 5.0


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _prob(m: dict) -> float | None:
    """YES probability for a market: bid/ask mid in dollars, else last."""
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if bid is not None and ask is not None and ask >= bid and ask > 0:
        return (bid + ask) / 2
    return _f(m.get("last_price_dollars"))


def _bucket(m: dict) -> tuple[float | None, float | None]:
    return _f(m.get("floor_strike")), _f(m.get("cap_strike"))


def nearest_event(markets: list[dict], now: datetime) -> list[dict]:
    """Markets of the open event with the earliest close_time still ahead."""
    by_event: dict[str, list[dict]] = {}
    for m in markets:
        if m.get("status") not in (None, "open", "active"):
            continue
        by_event.setdefault(m.get("event_ticker") or "", []).append(m)
    best, best_close = None, None
    for lst in by_event.values():
        try:
            close = datetime.fromisoformat(str(lst[0].get("close_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if close <= now:
            continue
        if best_close is None or close < best_close:
            best, best_close = lst, close
    return best or []


def _flatness(ps: list[float]) -> float:
    """Normalised Shannon entropy: 1.0 for a uniform distribution, lower the
    more peaked it is. Comparable across events with different bucket counts,
    which a raw modal-bucket measure is not."""
    ps = [p for p in ps if p > 0]
    if len(ps) < 2:
        return 0.0
    return -sum(p * math.log(p) for p in ps) / math.log(len(ps))


def summarize_range_event(markets: list[dict], reference: float | None) -> dict | None:
    """Turn one range event's markets into a compact distribution summary."""
    rows = []
    for m in markets:
        p = _prob(m)
        lo, hi = _bucket(m)
        if p is None or (lo is None and hi is None):
            continue
        mid = hi if lo is None else lo if hi is None else (lo + hi) / 2
        rows.append({"lo": lo, "hi": hi, "mid": mid, "p": p, "volume": _f(m.get("volume_fp")) or 0.0})
    if not rows:
        return None
    rows.sort(key=lambda r: r["mid"])
    total = sum(r["p"] for r in rows)
    if total <= 0:
        return None
    for r in rows:
        r["p"] = r["p"] / total  # normalise; overround means raw YES prices sum > 1

    # implied median: bucket where cumulative probability crosses 0.5
    cum, median = 0.0, rows[-1]["mid"]
    for r in rows:
        cum += r["p"]
        if cum >= 0.5:
            median = r["mid"]
            break

    out = {
        "close_time": markets[0].get("close_time"),
        "event": markets[0].get("event_ticker"),
        "reference_close": reference,
        "implied_median": round(median, 2),
        "buckets": len(rows),
        "volume": round(sum(r["volume"] for r in rows), 1),
        "flatness": round(_flatness([r["p"] for r in rows]), 3),
        "top_buckets": [
            {"range": f"{r['lo'] or '<'}-{r['hi'] or '>'}", "p": round(r["p"], 3)}
            for r in sorted(rows, key=lambda r: -r["p"])[:4]
        ],
    }
    if reference:
        out["p_above_reference"] = round(sum(r["p"] for r in rows if r["mid"] > reference), 3)
        out["p_up_over_1pct"] = round(sum(r["p"] for r in rows if r["mid"] > reference * 1.01), 3)
        out["p_down_over_1pct"] = round(sum(r["p"] for r in rows if r["mid"] < reference * 0.99), 3)
        out["implied_move_pct"] = round((median / reference - 1) * 100, 2)
    return out


async def _get(client: httpx.AsyncClient, path: str, **params) -> dict:
    r = await client.get(path, params=params)
    r.raise_for_status()
    return r.json()


def latest_settlement(markets: list[dict], now: datetime,
                      max_age_days: float = MAX_REFERENCE_AGE_DAYS) -> float | None:
    """The most recently settled index close, chosen by close_time.

    Never by position in the response. Kalshi's /markets page for
    status=settled is not ordered by date, and taking the first row that
    carried an expiration_value is the bug this function exists to prevent -
    see MAX_REFERENCE_AGE_DAYS above for what it cost.

    None when nothing has settled, or when the newest settlement is too old to
    be "the previous close" - a stale reference is worse than no reference,
    because every derived probability is quietly measured against the wrong
    day while still looking authoritative.
    """
    best_value, best_close = None, None
    for m in markets:
        value = _f(m.get("expiration_value"))
        if value is None:
            continue
        try:
            close = datetime.fromisoformat(str(m.get("close_time", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        # A close_time still ahead of us has not actually settled, whatever the
        # status field says; it cannot be the previous close.
        if close > now:
            continue
        if best_close is None or close > best_close:
            best_value, best_close = value, close
    if best_close is None:
        return None
    if (now - best_close).total_seconds() > max_age_days * 86400:
        return None
    return best_value


async def fetch_series(client: httpx.AsyncClient, series: str, now: datetime) -> dict | None:
    open_markets = (await _get(client, "/markets", series_ticker=series, status="open", limit=200)).get("markets", [])
    event = nearest_event(open_markets, now)
    if not event:
        return None
    reference = None
    try:
        # limit=200, not 40: each session lists ~30 buckets, so a 40-row page
        # holds barely one day and a bit. On 2026-08-31 Friday's settled event
        # was not in the first 40 rows at all, so picking by close_time would
        # still have missed it. The page has to be wide enough to contain the
        # most recent settlement before choosing within it means anything.
        settled = (await _get(client, "/markets", series_ticker=series, status="settled", limit=200)).get("markets", [])
        reference = latest_settlement(settled, now)
    except (httpx.HTTPError, ValueError):
        reference = None
    summary = summarize_range_event(event, reference)
    if summary:
        summary["series"] = series
    return summary


def unusable_reason(summary: dict, config: dict | None = None) -> str | None:
    """Why this prior should not be shown to the model, or None if it is fine.

    Kept separate from fetching so it is testable without a network, and
    recorded rather than applied silently: a suppressed prior still reaches the
    journal with this reason, so "the model got no second opinion today" is an
    answerable question rather than an absence."""
    config = config or {}
    min_volume = config.get("predictions_min_volume", MIN_VOLUME)
    max_flatness = config.get("predictions_max_flatness", MAX_FLATNESS)
    volume = summary.get("volume") or 0.0
    flatness = summary.get("flatness")
    if volume < min_volume:
        return f"thin: volume {volume} < {min_volume}"
    if flatness is not None and flatness > max_flatness:
        return f"flat: entropy {flatness} > {max_flatness} (near-uniform, no information)"
    return None


async def fetch_predictions(config: dict, now: datetime | None = None, cache_file: Path = CACHE_FILE) -> dict:
    """{underlying: summary} for the configured series, cached for
    CACHE_TTL_SEC. Empty dict on any failure."""
    now = now or datetime.now(timezone.utc)
    series_map = config.get("prediction_series") or DEFAULT_SERIES
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if time.time() - float(cached.get("fetched_at", 0)) < CACHE_TTL_SEC and cached.get("series_map") == series_map:
                return cached.get("data", {})
    except (OSError, ValueError):
        pass

    data = {}
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
            for underlying, series in series_map.items():
                try:
                    summary = await fetch_series(client, series, now)
                except (httpx.HTTPError, ValueError, KeyError):
                    summary = None
                if summary:
                    summary["suppressed"] = unusable_reason(summary, config)
                    data[underlying] = summary
    except httpx.HTTPError:
        return {}
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "series_map": series_map, "data": data}))
    except OSError:
        pass
    return data


# --- the chain's own odds (#140) ---------------------------------------------
# Idea credit: greatfriend#8857 (Discord, hackathon server) - "every option
# price is a probability in disguise". P(S > K) = -dC/dK read straight off
# call prices: the chain is itself a prediction market, and it is one we are
# already fetching every cycle. Rendered beside the Kalshi line in the same
# shape so the model can compare like with like - disagreement between two
# independent crowds is the signal; agreement is just the base rate.

CHAIN_MIN_STRIKES = 6
# The survival curve must be non-increasing, but raw quote mids wiggle by a
# couple of cents, so raw -dC/dK violates monotonicity CONSTANTLY on a $1
# ladder - measured live, 60 of 68 increments on SPY. Counting violations is
# therefore useless as a noise gate. Instead the curve is smoothed to the
# closest non-increasing sequence (pool-adjacent-violators) and the gate is
# how far the smoothing had to MOVE it: a mean absolute adjustment above
# this is a curve that was noise wearing a distribution, the same failure
# flatness catches for Kalshi.
CHAIN_MAX_MEAN_ADJUSTMENT = 0.05


def _isotonic_decreasing(values: list[float]) -> list[float]:
    """Closest (least-squares) non-increasing sequence: pool adjacent
    violators. Textbook PAVA on the negated sequence, unweighted - the
    strike spacing is uniform inside the fetched band."""
    blocks = [[v, 1] for v in values]  # [mean, count]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0]:  # violation of non-increasing
            total = blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]
            count = blocks[i][1] + blocks[i + 1][1]
            blocks[i] = [total / count, count]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for mean, count in blocks:
        out.extend([mean] * count)
    return out


def _call_mids(contracts: dict) -> tuple[str | None, list[tuple[float, float]]]:
    """(expiry_iso, [(strike, mid)...]) for the NEAREST expiry's two-sided
    calls. Nearest, because that is the closest analogue to Kalshi's
    same-day close market the chain offers (min_days_to_expiration keeps us
    off literal 0DTE)."""
    from bot.occ import parse_occ_symbol

    by_expiry: dict = {}
    for symbol, raw in (contracts or {}).items():
        try:
            occ = parse_occ_symbol(symbol)
        except ValueError:
            continue
        if occ.option_type != "call":
            continue
        quote = raw.get("latestQuote") or {}
        bid, ask = quote.get("bp"), quote.get("ap")
        if not (bid and ask and bid > 0 and ask > 0):
            continue
        by_expiry.setdefault(occ.expiration, []).append((occ.strike, (bid + ask) / 2, ask - bid))
    if not by_expiry:
        return None, []
    expiry = min(by_expiry)
    return expiry.isoformat(), sorted(by_expiry[expiry])


def chain_summary(contracts: dict, reference: float | None) -> dict | None:
    """The option chain's own implied distribution, in the Kalshi summary's
    shape. None when there are no usable calls at all; a dict with
    `suppressed` set when there are calls but the curve is not fit to show."""
    expiry, mids = _call_mids(contracts)
    if not mids:
        return None
    out = {"source": "chain", "expiry": expiry, "strikes": len(mids)}
    if len(mids) < CHAIN_MIN_STRIKES:
        out["suppressed"] = f"thin: only {len(mids)} usable call quotes"
        return out

    # P(S > K) at the midpoint of each adjacent strike pair = -dC/dK -
    # but only where the quotes are precise enough to differentiate. Deep
    # ITM calls carry $1-3 spreads against $1 strike gaps (measured live),
    # so their mid noise is LARGER than the derivative step and one such
    # pair poisons the whole curve. An increment is used only when both
    # quotes' spreads are smaller than the gap they span; the discarded
    # deep-ITM side saturates toward P=1 anyway, and the reference sits in
    # the liquid zone near spot.
    ks, raw = [], []
    for (k1, c1, sp1), (k2, c2, sp2) in pairwise(mids):
        gap = k2 - k1
        if max(sp1, sp2) >= gap:
            continue
        ks.append((k1 + k2) / 2)
        raw.append(min(max((c1 - c2) / gap, 0.0), 1.0))
    out["strikes"] = len(ks) + 1
    if len(ks) < CHAIN_MIN_STRIKES - 1:
        out["suppressed"] = f"thin: only {len(ks) + 1} tight-quoted call strikes"
        return out
    smoothed = _isotonic_decreasing(raw)
    mean_adjustment = sum(abs(a - b) for a, b in zip(raw, smoothed)) / len(raw)
    if mean_adjustment > CHAIN_MAX_MEAN_ADJUSTMENT:
        out["suppressed"] = f"noisy: isotonic fit moved probabilities {mean_adjustment:.3f} on average"
        return out
    points = list(zip(ks, smoothed))

    def survival(x: float) -> float:
        if x <= points[0][0]:
            return points[0][1]
        if x >= points[-1][0]:
            return points[-1][1]
        for (x1, p1), (x2, p2) in pairwise(points):
            if x1 <= x <= x2:
                return p1 + (p2 - p1) * (x - x1) / (x2 - x1)
        return points[-1][1]

    # Median: where the survival curve crosses 0.5 (linear interp).
    median = None
    for (x1, p1), (x2, p2) in pairwise(points):
        if p1 >= 0.5 >= p2:
            median = x1 if p1 == p2 else x1 + (x2 - x1) * (p1 - 0.5) / (p1 - p2)
            break
    if median is not None:
        out["implied_median"] = round(median, 2)
    if reference:
        out["reference_close"] = reference
        out["p_above_reference"] = round(survival(reference), 3)
        out["p_up_over_1pct"] = round(survival(reference * 1.01), 3)
        out["p_down_over_1pct"] = round(1 - survival(reference * 0.99), 3)
        if median is not None:
            out["implied_move_pct"] = round((median / reference - 1) * 100, 2)
    out["suppressed"] = None
    return out


def journal_fields(predictions: dict) -> dict:
    """The prior, compact enough to sit in one journal record.

    The prompt itself is not journaled - it carries the whole option chain and
    would dwarf every other record - so without this there is no way to answer
    "what second opinion did the model have when it made that trade?" after the
    fact. Same numbers prompt_block() renders, as data rather than prose, so a
    decision can be lined up against the prior it was given.

    Empty dict when there is nothing, so the caller can skip the record
    entirely and absence means "no prior this cycle"."""
    out = {}
    for underlying, s in (predictions or {}).items():
        out[underlying] = {
            "series": s.get("series"),
            "implied_median": s.get("implied_median"),
            "implied_move_pct": s.get("implied_move_pct"),
            "reference_close": s.get("reference_close"),
            "p_above_reference": s.get("p_above_reference"),
            "p_up_over_1pct": s.get("p_up_over_1pct"),
            "p_down_over_1pct": s.get("p_down_over_1pct"),
            "volume": s.get("volume"),
            "flatness": s.get("flatness"),
            # None when the model was shown this prior; a reason when it was
            # fetched but withheld.
            "suppressed": s.get("suppressed"),
        }
        if s.get("chain"):
            out[underlying]["chain"] = s["chain"]
    return out


def prompt_block(predictions: dict) -> str:
    """The lines the model sees. Empty string when there is nothing usable.

    Two crowds per underlying when both are fit to show: Kalshi's event
    market and the option chain's own prices (#140). The header says what to
    do with the pair, because the pair is the point."""
    if not predictions:
        return ""
    header = (
        "PREDICTION MARKETS (crowd-implied, read-only - PRIORS to weigh, not signals to copy): "
        "Kalshi's event market on the index close, and the option chain's own implied odds "
        "(P(S>K) = -dC/dK from call prices). They are independent crowds measuring nearly the "
        "same thing - DISAGREEMENT between them is information; agreement is just the base rate. "
        "Compare both to today's price action. When you cite one of these numbers in a reason, quote "
        "it exactly as printed or refer to it by name (e.g. 'Kalshi P(down>1%) supports this') - never "
        "round or restate it; quoted figures are audited against this block:"
    )
    lines = []
    for underlying, s in predictions.items():
        if s.get("series") and not s.get("suppressed"):
            ref = s.get("reference_close")
            bits = [f"{underlying} via {s.get('series')} (index close {str(s.get('close_time'))[:16]}Z)"]
            if ref:
                bits.append(f"prior close {ref:,.0f}, implied median {s['implied_median']:,.0f} ({s.get('implied_move_pct'):+.2f}%)")
                bits.append(f"P(above prior close) {s.get('p_above_reference')}, P(up>1%) {s.get('p_up_over_1pct')}, "
                            f"P(down>1%) {s.get('p_down_over_1pct')}")
            else:
                bits.append(f"implied median {s['implied_median']:,.0f}")
            bits.append(f"volume {s.get('volume')}")
            lines.append("- " + "; ".join(bits))
        chain = s.get("chain")
        if chain and not chain.get("suppressed"):
            ref = chain.get("reference_close")
            bits = [f"{underlying} via option chain (calls exp {chain.get('expiry')})"]
            if ref:
                if chain.get("implied_median") is not None:
                    bits.append(f"prior close {ref:,.2f}, implied median {chain['implied_median']:,.2f} "
                                f"({chain.get('implied_move_pct'):+.2f}%)")
                bits.append(f"P(above prior close) {chain.get('p_above_reference')}, "
                            f"P(up>1%) {chain.get('p_up_over_1pct')}, P(down>1%) {chain.get('p_down_over_1pct')}")
            elif chain.get("implied_median") is not None:
                bits.append(f"implied median {chain['implied_median']:,.2f}")
            lines.append("- " + "; ".join(bits))
    if not lines:
        return ""
    return "\n".join([header, *lines]) + "\n\n"
