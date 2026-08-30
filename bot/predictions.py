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


async def fetch_series(client: httpx.AsyncClient, series: str, now: datetime) -> dict | None:
    open_markets = (await _get(client, "/markets", series_ticker=series, status="open", limit=200)).get("markets", [])
    event = nearest_event(open_markets, now)
    if not event:
        return None
    reference = None
    try:
        settled = (await _get(client, "/markets", series_ticker=series, status="settled", limit=40)).get("markets", [])
        values = [_f(m.get("expiration_value")) for m in settled if _f(m.get("expiration_value"))]
        reference = values[0] if values else None
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
    return out


def prompt_block(predictions: dict) -> str:
    """The lines the model sees. Empty string when there is nothing usable."""
    if not predictions:
        return ""
    header = (
        "PREDICTION MARKETS (Kalshi, crowd-implied, read-only - a PRIOR to weigh, not a signal to copy; "
        "compare to what the option chain implies and to today's price action):"
    )
    usable = {u: s for u, s in predictions.items() if not s.get("suppressed")}
    if not usable:
        return ""
    lines = [header]
    for underlying, s in usable.items():
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
    return "\n".join(lines) + "\n\n"
