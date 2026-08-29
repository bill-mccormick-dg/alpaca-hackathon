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
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from bot.risk import LOGS_DIR

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
CACHE_FILE = LOGS_DIR / "predictions-cache.json"
CACHE_TTL_SEC = 300
DEFAULT_SERIES = {"SPY": "KXINX", "QQQ": "KXNASDAQ100"}


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
                    data[underlying] = summary
    except httpx.HTTPError:
        return {}
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "series_map": series_map, "data": data}))
    except OSError:
        pass
    return data


def prompt_block(predictions: dict) -> str:
    """The lines the model sees. Empty string when there is nothing usable."""
    if not predictions:
        return ""
    header = (
        "PREDICTION MARKETS (Kalshi, crowd-implied, read-only - a PRIOR to weigh, not a signal to copy; "
        "compare to what the option chain implies and to today's price action):"
    )
    lines = [header]
    for underlying, s in predictions.items():
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
