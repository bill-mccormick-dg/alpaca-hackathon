#!/usr/bin/env python3
"""End-of-day review: one command, everything needed to decide what to
change tomorrow.

  1. equity open/close/day P&L from the journal (+ appended to logs/equity.jsonl)
  2. trade_report's round trips and cuts for the day
  3. decision audit: holds vs proposals, rejections grouped by RULE (a rule
     rejecting the same idea all day is a prompt/config bug), errors,
     models/tokens/latency/cost, distinct configs seen
  4. every model decision of the day, for skimming
  5. an ADVISORY read of the day + ONE recommended change, written by the
     same model the bot uses (plain prose, no JSON contract). A human edits
     config; this never does.

Writes logs/eod/<date>-<account>.md and prints it. Read-only against the
account - safe on the official account any time.

Usage:
  eod_review.py [--account test] [--config config.yaml] [--date YYYY-MM-DD]
                [--no-model] [--json]
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime

from bot import journal, mqtt, overrides, review
from bot.config import load_config
from bot.credentials import load_credentials, validate_account
from bot.featherless import DEFAULT_MODEL, FeatherlessClient
from bot.risk import EASTERN, LOGS_DIR
from bot.trades import pair_round_trips, summarize
from trade_report import fetch_orders, fills_from_orders, journaled_sells

EQUITY_LOG = LOGS_DIR / "equity.jsonl"

REVIEW_PROMPT = """You are reviewing one trading day of an autonomous PAPER-trading options agent \
that you yourself drive (the decisions below were yours). Judging is on total account equity at \
the end of the week; the strategy thesis is: {thesis}

Below is the day's digest as JSON: equity, completed round trips with how each ended \
(stop-loss / take-profit / expiry rule / your own sell / end-of-day flatten), a decision audit \
(holds vs proposals, rejections grouped by the guardrail rule that refused them, errors), the \
configs in effect, and your raw output each cycle. An empty array "[]" is a deliberate HOLD - \
counted under "holds", never an error; "errors" are separate events (timeouts, malformed \
output, broker failures) and are listed with their text.

Write plain text for the operator - no markdown headings, no JSON:
1. 3-6 sentences on what actually happened and why, in plain language. Be specific: name the \
trade or the pattern. If the guardrails rejected the same idea repeatedly, say so - that is a \
prompt or config problem, not bad luck.
2. ONE concrete recommended change for tomorrow, chosen from what the operator can change \
without code: the strategy notes (thesis/tactics text), stop_loss_pct / take_profit_pct, \
eod_close_dte, research_contracts_per_underlying, option_strike_band_pct, temperature, or the \
model. State the change and the evidence for it in two sentences. If the honest answer is \
"change nothing, the sample is too small", say that.

DIGEST:
{digest}
"""


def _today() -> str:
    return datetime.now(EASTERN).date().isoformat()


def _exit_day(trip: dict) -> str | None:
    when = trip.get("exit_time")
    if isinstance(when, datetime):
        return (when.astimezone(EASTERN) if when.tzinfo else when).date().isoformat()
    return str(when)[:10] if when else None


def append_equity_log(day: str, account: str, equity: dict) -> None:
    if equity.get("equity_close") is None:
        return
    EQUITY_LOG.parent.mkdir(exist_ok=True)
    record = {"date": day, "account": account, **{k: equity[k] for k in ("equity_open", "equity_close", "day_pnl", "day_pnl_pct")}}
    # one line per (date, account): rewrite if today's already there
    lines = []
    if EQUITY_LOG.exists():
        for line in EQUITY_LOG.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (r.get("date") == day and r.get("account") == account):
                lines.append(line)
    lines.append(json.dumps(record))
    EQUITY_LOG.write_text("\n".join(lines) + "\n")


async def model_recommendation(config: dict, creds: dict, digest: dict) -> str:
    client = FeatherlessClient(
        creds["FEATHERLESS_API_KEY"], model=config.get("model") or DEFAULT_MODEL,
        timeout=float(config.get("request_timeout_sec", 60)),
    )
    slim = {k: v for k, v in digest.items() if k not in ("trips",)}
    slim["trips"] = digest.get("trips", [])[:30]
    prompt = REVIEW_PROMPT.format(
        thesis=str(config.get("strategy_notes") or "").strip().splitlines()[0] if config.get("strategy_notes") else "(none)",
        digest=json.dumps(slim, default=str),
    )
    kwargs = {"max_tokens": 600, "temperature": 0.3}
    if isinstance(config.get("model_params"), dict):
        kwargs.update(config["model_params"])
    response = await client.chat([{"role": "user", "content": prompt}], **kwargs)
    return (response["choices"][0]["message"].get("content") or "").strip()


async def run(args: argparse.Namespace) -> int:
    validate_account(args.account)
    journal.use_account(args.account)
    overrides.use_account(args.account)
    config = load_config(args.config)
    mqtt.configure(config, args.account)
    day = args.date or _today()

    records = journal.read_events(day)
    trade_summary, trips = None, []
    try:
        orders = await fetch_orders(args.account, days=max(1, args.days))
        fills = fills_from_orders(orders, journaled_sells())
        all_trips, _ = pair_round_trips(fills)
        trips = [t for t in all_trips if _exit_day(t) == day]
        trade_summary = summarize(trips)
    except Exception as exc:  # noqa: BLE001 - the digest must still print from the journal alone
        trade_summary = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}

    digest = review.build_digest(day, args.account, records, trade_summary, trips, price_table=config.get("model_prices"))
    append_equity_log(day, args.account, digest["equity"])

    if not args.no_model and records:
        try:
            creds = load_credentials(args.account)
            digest["recommendation"] = await model_recommendation(config, creds, digest)
        except Exception as exc:  # noqa: BLE001 - advisory only
            digest["recommendation"] = f"(model review unavailable: {type(exc).__name__}: {exc})"

    if args.json:
        print(json.dumps(digest, indent=2, default=str))
        return 0

    md = review.render_markdown(digest)
    out_dir = LOGS_DIR / "eod"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{day}-{args.account}.md"
    out.write_text(md)
    print(md)
    print(f"(written to {out})")
    journal.log("eod_review", day=day, equity=digest["equity"], trades=trade_summary.get("trades") if trade_summary else None)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="test")
    ap.add_argument("--config", default=None)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default today, Eastern)")
    ap.add_argument("--days", type=int, default=2, help="how far back to fetch orders (default 2, to catch overnight holds)")
    ap.add_argument("--no-model", action="store_true", help="skip the advisory model read")
    ap.add_argument("--json", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
