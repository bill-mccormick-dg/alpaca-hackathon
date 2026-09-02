#!/usr/bin/env python3
"""End-of-day review: one command, everything needed to decide what to
change tomorrow.

  1. equity open/close/day P&L from the journal (+ appended to logs/equity.jsonl)
  2. trade_report's round trips and cuts for the day
  3. decision audit: holds vs proposals, rejections grouped by RULE (a rule
     rejecting the same idea all day is a prompt/config bug), errors,
     models/tokens/latency/cost, distinct configs seen
  4. every model decision of the day, for skimming
  5. an ADVISORY read of the day + ONE recommended change, written by a
     model that did NOT trade the day (bot/config.py::resolve_review_model;
     plain prose, no JSON contract). A human edits config; this never does.

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

from bot import journal, mqtt, overrides, prior_scores, review
from bot.config import (
    load_config,
    model_params_for,
    resolve_review_model,
    review_choice,
)
from bot.credentials import load_credentials, validate_account
from bot.featherless import DEFAULT_MODEL, FeatherlessClient
from bot.report import eod_payload
from bot.risk import EASTERN, LOGS_DIR
from bot.trades import pair_round_trips, summarize
from trade_report import fetch_orders, fills_from_orders, journaled_sells

EQUITY_LOG = LOGS_DIR / "equity.jsonl"

REVIEW_PROMPT = """You are the independent reviewer of one trading day of an autonomous PAPER-trading \
options agent. The decisions below were made by a different model, {trading_model}; you did not \
trade this day, and your job is to challenge its reasoning, not to explain it. Judging is on total \
account equity at the end of the week; the strategy thesis is: {thesis}

Below is the day's digest as JSON: equity, completed round trips with how each ended \
(stop-loss / take-profit / expiry rule / the model's own sell / end-of-day flatten), a decision \
audit (holds vs proposals, rejections grouped by the guardrail rule that refused them, errors), \
the configs in effect, and the trading model's raw output each cycle. Each rejection rule key is \
a short grouping label; "rejection_examples" gives the verbatim detail behind each key - reason \
from the example, not the label. An empty array "[]" is a deliberate HOLD - \
counted under "holds", never an error; "errors" are separate events (timeouts, malformed \
output, broker failures) and are listed with their text. A "prior_scores" block, when \
present, grades the prediction-market priors themselves (Brier score, lower is better; \
the 0.5 coin flip scores 0.25) - use it to judge whether the priors deserved the weight \
the model gave them, and which crowd to trust when Kalshi and the chain disagree. Its \
"withheld" rows shadow-grade priors the usability gate kept from the model: they say whether \
the gate discarded good information, not what the model leaned on. The audit's \
"citations_unsupported" counts numbers the model quoted in its reasons that appear nowhere in \
the prior it was actually given, and "citations_misattributed" counts another underlying's figure \
quoted as this one's ("citation_examples" shows each, with the real value): \
the reason strings are rhetoric, not evidence - judge every decision on the journalled prior, \
never on the figure the model quoted, and say so when a trade rested on an invented number. \
"exit_claim_examples" lists sell reasons whose stated facts the account contradicts: a \
"fabricated_urgency" flag is an exit justified by a forced close or backstop that was days \
away, and a "wrong_direction" flag claims the underlying was above/below its prior close when \
the tape says the opposite - treat any flagged exit as unjustified unless the journalled \
numbers independently support it.

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


def traded_models(digest: dict) -> set[str]:
    """Every model the journal says decided, erred or retried today - the
    digest's per-model rows (#231), never config["model"]: overrides expire
    at 16:00 ET and this runs at 16:05, so the config is the one thing that
    may not describe the day (#218)."""
    audit = digest.get("audit") or {}
    rows = audit.get("by_model") or audit.get("models") or {}
    return {m for m in rows if m and m != review.NO_MODEL}


def reviewer_model(config: dict, traded=()) -> str:
    """The model that writes the critique. resolve_review_model() picks one
    that did not trade the day; only when it has nothing to pick (no
    preference list, or every entry traded) does the trading model review
    itself - a same-model review beats no review.

    This function is the whole fix for #177: resolve_review_model() had been
    journaled on every config event and shown on the dashboard since #127,
    but the review itself was still built on config["model"], so every
    account graded its own homework while the docs said otherwise. #218 is
    its second half: the comparison is against `traded`, what the journal
    says ran, not against the config at 16:05."""
    return resolve_review_model(config, traded) or config.get("model") or DEFAULT_MODEL


def describe_traded(digest: dict, config: dict) -> str:
    """'Qwen/Qwen3.8-Flash-Next (29 decisions), moonshotai/Kimi-K3 (6 errors)'
    - what the prompt calls the model under review. The config's `model`
    is the fallback for a digest with no per-model rows."""
    rows = ((digest.get("audit") or {}).get("by_model") or {})
    parts = []
    for model, m in rows.items():
        if model == review.NO_MODEL:
            continue
        bits = [f"{m['decisions']} decisions"] if m.get("decisions") else []
        if m.get("errors"):
            bits.append(f"{m['errors']} errors")
        parts.append(f"{model} ({', '.join(bits)})" if bits else model)
    return ", ".join(parts) or config.get("model") or DEFAULT_MODEL


async def model_recommendation(config: dict, creds: dict, digest: dict, model: str | None = None) -> tuple[str, str]:
    """(critique text, model that wrote it). `model` is the reviewer run()
    already resolved against the day's models; resolving here from config
    alone is the #218 hole."""
    model = model or reviewer_model(config, traded_models(digest))
    client = FeatherlessClient(
        creds["FEATHERLESS_API_KEY"], model=model,
        timeout=float(config.get("request_timeout_sec", 60)),
    )
    slim = {k: v for k, v in digest.items() if k not in ("trips",)}
    slim["trips"] = digest.get("trips", [])[:30]
    prompt = REVIEW_PROMPT.format(
        trading_model=describe_traded(digest, config),
        thesis=str(config.get("strategy_notes") or "").strip().splitlines()[0] if config.get("strategy_notes") else "(none)",
        digest=json.dumps(slim, default=str),
    )
    kwargs = {"max_tokens": 600, "temperature": 0.3}
    # Keyed on the REVIEWER, not the trading model - the two differ by
    # design, and a per-model param (#206) must follow the model called.
    kwargs.update(model_params_for(config, model))
    response = await client.chat([{"role": "user", "content": prompt}], **kwargs)
    return (response["choices"][0]["message"].get("content") or "").strip(), model


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

    if records:
        try:
            scores = await prior_scores.score_day(day, args.account, records)
            if scores:
                digest["prior_scores"] = scores
        except Exception as exc:  # noqa: BLE001 - grading the inputs must never take the digest down
            digest["prior_scores"] = {"error": f"{type(exc).__name__}: {exc}"}

    if not args.no_model and records:
        # Recorded before the call, so a failed review still says which model
        # was asked - "unavailable" from the wrong model is a different bug.
        traded = traded_models(digest)
        reviewer, refused = review_choice(config, traded)
        digest["review_model"] = reviewer or config.get("model") or DEFAULT_MODEL
        if refused:
            digest["review_pin_ignored"] = refused
        if digest["review_model"] in traded:
            # Say it, rather than letting the heading carry it alone (#218).
            digest["review_note"] = (f"same-model review: {digest['review_model']} traded today and every "
                                     "review_model_preference entry did too - read the critique as self-assessment")
        try:
            creds = load_credentials(args.account)
            digest["recommendation"], digest["review_model"] = await model_recommendation(config, creds, digest, digest["review_model"])
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
    try:
        mqtt.configure(config, args.account)
        mqtt.publish_report("eod_summary", eod_payload(md, day=day, account=args.account))
    except Exception as exc:  # noqa: BLE001 - the digest is already written; publishing is a bonus
        print(f"eod publish skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(md)
    print(f"(written to {out})")
    journal.log("eod_review", day=day, equity=digest["equity"], trades=trade_summary.get("trades") if trade_summary else None,
                review_model=digest.get("review_model"), review_pin_ignored=digest.get("review_pin_ignored"),
                traded_models=sorted(traded_models(digest)))
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
