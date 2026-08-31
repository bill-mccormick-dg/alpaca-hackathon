"""Brier-score the journalled prediction-market priors (issue #142).

The bot journals the Kalshi prior (and the chain-implied prior, #140)
every cycle and, until #142, never graded it. The reviewer model
critiques the decisions; nothing critiqued the inputs — after the
reference-close bug (#129) shipped wrong probabilities for three days
undetected, "is this prior actually predictive?" should be a number,
not a feeling. Once both crowds are scored, the same number says which
to trust when they disagree.

Scoring: each day, for every underlying with a journalled, non-suppressed
prior, take each source's LAST forecast of the day (the honest score is
time-weighted; this is the deliberate simplification), resolve whether
the underlying closed above its previous close, and compute the Brier
score (p − outcome)². Lower is better; the 0.5 coin flip scores 0.25 on
every day, so that constant is the baseline to beat.

Simplifications, on purpose:
- The Kalshi market settles on the INDEX close while the outcome here is
  resolved from the tracked ETF's daily bar (iex feed) — direction almost
  always agrees; divergence (dividends, settlement-print quirks) is noise
  at this sample size.
- Suppressed priors are not scored: the question is whether what the
  model LEANED ON was predictive, not whether the withheld ones were.

Read-only against the market, after hours, out of band from trading —
same safety class as the rest of eod_review.
"""

import json

from bot.risk import LOGS_DIR

PRIOR_SCORES_LOG = LOGS_DIR / "prior_scores.jsonl"
COIN_FLIP_BRIER = 0.25  # (0.5 - outcome)^2 regardless of outcome


def last_forecasts(records: list[dict]) -> dict[str, dict[str, float]]:
    """{underlying: {source: p_above_reference}} from one day's journal
    records, keeping each source's last forecast (records arrive in
    journal order, so later cycles simply overwrite earlier ones)."""
    out: dict[str, dict[str, float]] = {}
    for r in records:
        if r.get("event") != "predictions":
            continue
        for underlying, s in r.items():
            if underlying in ("ts", "event", "account") or not isinstance(s, dict):
                continue
            p = s.get("p_above_reference")
            if p is not None and not s.get("suppressed"):
                out.setdefault(underlying, {})["kalshi"] = float(p)
            chain = s.get("chain") or {}
            cp = chain.get("p_above_reference")
            if cp is not None and not chain.get("suppressed"):
                out.setdefault(underlying, {})["chain"] = float(cp)
    return out


def resolve_outcome(bars: list[dict], day: str) -> int | None:
    """1 if the underlying closed above the previous session's close on
    `day`, 0 if not; None when the feed has no final bar for the day yet
    (eod_review run before the close, or a holiday) or no prior session
    to compare against. `bars` are daily bars, any order."""
    rows = sorted((b for b in bars or [] if b.get("t") and b.get("c") is not None),
                  key=lambda b: str(b["t"]))
    for i, bar in enumerate(rows):
        if str(bar["t"])[:10] == day:
            if i == 0:
                return None
            return int(float(bar["c"]) > float(rows[i - 1]["c"]))
    return None


def score(forecasts: dict[str, dict[str, float]], outcomes: dict[str, int]) -> list[dict]:
    """One row per (underlying, source) where both a forecast and a
    resolved outcome exist."""
    rows = []
    for underlying, by_source in sorted(forecasts.items()):
        outcome = outcomes.get(underlying)
        if outcome is None:
            continue
        for source, p in sorted(by_source.items()):
            rows.append({
                "underlying": underlying,
                "source": source,
                "p": p,
                "outcome": outcome,
                "brier": round((p - outcome) ** 2, 4),
            })
    return rows


def _day_means(rows: list[dict]) -> dict[str, float]:
    by_source: dict[str, list[float]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r["brier"])
    return {s: round(sum(v) / len(v), 4) for s, v in sorted(by_source.items())}


def append_scores_log(day: str, account: str, rows: list[dict]) -> None:
    """One line per (date, account), rewritten if the day is re-run —
    the same pattern as eod_review's equity.jsonl."""
    record = {"date": day, "account": account,
              "forecasts": rows, "day_mean": _day_means(rows)}
    PRIOR_SCORES_LOG.parent.mkdir(exist_ok=True)
    lines = []
    if PRIOR_SCORES_LOG.exists():
        for line in PRIOR_SCORES_LOG.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (r.get("date") == day and r.get("account") == account):
                lines.append(line)
    lines.append(json.dumps(record))
    PRIOR_SCORES_LOG.write_text("\n".join(lines) + "\n")


def running_means(account: str) -> dict[str, dict]:
    """{source: {mean, days}} over every day in the scores log for this
    account (including the line just appended for today)."""
    if not PRIOR_SCORES_LOG.exists():
        return {}
    per_source: dict[str, list[float]] = {}
    for line in PRIOR_SCORES_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("account") != account:
            continue
        for source, mean in (r.get("day_mean") or {}).items():
            per_source.setdefault(source, []).append(float(mean))
    return {s: {"mean": round(sum(v) / len(v), 4), "days": len(v)}
            for s, v in sorted(per_source.items())}


async def fetch_daily_bars(account: str, underlyings: list[str]) -> dict[str, list[dict]]:
    """{underlying: [daily bars]} from Alpaca, ~2 weeks back — enough to
    always contain the session before `day` across weekends and holidays."""
    from bot.alpaca_mcp import AlpacaMCPClient
    from bot.credentials import load_credentials
    from bot.snapshot import _data

    creds = load_credentials(account)
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        result = await client.call_tool("get_stock_bars", {
            "symbols": ",".join(underlyings), "timeframe": "1Day",
            "days": 14, "limit": 14 * len(underlyings), "feed": "iex", "sort": "asc",
        })
    bars = _data(result).get("bars") or {}
    return {u: bars.get(u) or [] for u in underlyings}


async def score_day(day: str, account: str, records: list[dict]) -> dict | None:
    """The digest block: today's per-forecast scores, per-source day
    means, and the running means over the log. None when the day
    journalled no usable prior; {"skipped": reason} when outcomes cannot
    be resolved yet. Appends to the scores log as a side effect."""
    forecasts = last_forecasts(records)
    if not forecasts:
        return None
    bars = await fetch_daily_bars(account, sorted(forecasts))
    outcomes = {u: resolve_outcome(bars[u], day) for u in forecasts}
    rows = score(forecasts, {u: o for u, o in outcomes.items() if o is not None})
    if not rows:
        return {"skipped": f"no final daily bar for {day} yet - run after the close"}
    append_scores_log(day, account, rows)
    return {
        "baseline": COIN_FLIP_BRIER,
        "today": _day_means(rows),
        "running": running_means(account),
        "forecasts": rows,
    }
