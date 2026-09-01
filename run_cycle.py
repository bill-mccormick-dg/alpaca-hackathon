#!/usr/bin/env python3
"""One full trading cycle: gates -> snapshot -> decide -> risk-check -> execute.

Mirrors alpaca-trader's run_cycle.py. Every order goes through
bot/execute.py's place_proposal(), so bot/risk.py's gates bind here exactly
as they would anywhere else - there is no other order path.

Usage:
  run_cycle.py                        normal cycle on the TEST account
  run_cycle.py --dry-run              full cycle, orders printed not sent
  run_cycle.py --force                skip market-open / trading-window gates
  run_cycle.py --account official     the judging account (see README "Account")
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta, timezone

from bot import (
    execute,
    holdings,
    journal,
    learning,
    mqtt,
    overrides,
    predictions,
    stale_orders,
)
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import config_provenance, load_config
from bot.credentials import load_credentials, validate_account
from bot.decide import decide
from bot.exits import check_exits
from bot.featherless import DEFAULT_MODEL, FeatherlessClient
from bot.flatten import flatten_all
from bot.identity import check_account_identity
from bot.models import AccountState, OpenOrder, Position
from bot.orders import INCOMPLETE
from bot.report import trades_payload
from bot.retry import RetryBudget, call_with_retry, summarize
from bot.risk import EASTERN, RiskManager, early_exit_block
from bot.snapshot import (
    _data,
    build_snapshot,
    chain_coverage,
    price_for_proposal,
    quote_option_mid,
)
from bot.trades import pair_round_trips

# Hackathon rule (docs/alpaca-official-guidelines.md): the judging account
# must not trade before the official window opens. Hardcoded on purpose - a
# config value could be edited by mistake; this can't be missed in review.
OFFICIAL_TRADING_STARTS = datetime(2026, 8, 31, 9, 30, tzinfo=EASTERN)


def official_account_may_trade(now: datetime | None = None) -> bool:
    now = now or datetime.now(EASTERN)
    return now >= OFFICIAL_TRADING_STARTS


def describe_error(exc: BaseException) -> str:
    """str(exc) alone is empty for some exceptions (httpx timeouts, for
    one) - the first live Featherless timeout journaled as detail="".
    Always include the type so the record says *what* failed."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


async def learning_block(client: AlpacaMCPClient, config: dict, snap: dict, now: datetime) -> str:
    """RECENT OUTCOMES block for the prompt (#31): closed round trips over
    the last learning_days from Alpaca's fills + the journal's exit
    reasons, open positions vs entry, today's rejections by rule. Any
    failure -> "" (the cycle must not depend on it)."""
    if not config.get("learning_enabled"):
        return ""
    try:
        from trade_report import (  # entrypoint module, no cycle back here
            fills_from_orders,
            journaled_sells,
        )

        days = int(config.get("learning_days", 2))
        after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        data = _data(await client.call_tool("get_orders", {"status": "all", "after": after, "nested": True, "limit": 500}))
        orders = data.get("result", data) if isinstance(data, dict) else data
        trips, _ = pair_round_trips(fills_from_orders(orders if isinstance(orders, list) else [], journaled_sells()))
        records_today = journal.read_events(now.date().isoformat())
        positions = (snap.get("account") or {}).get("positions") or []
        return learning.build_learning_block(trips, positions, records_today, max_trades=int(config.get("learning_trades", 15)))
    except Exception as exc:  # noqa: BLE001 - advisory context only
        journal.log("error", where="learning", detail=describe_error(exc))
        return ""


def holdings_block(snap: dict, prior: dict | None) -> str:
    """POSITIONS YOU HOLD / RESTING ORDERS for the prompt (bot/holdings.py;
    #170, #173): the held symbols with the reason and prior they were opened
    on, and the prior now. Always rendered - a flat account gets the one-line
    "none" so a sell is unmistakably a naked short. The journal walk is what
    can fail; then the positions still print, just without their history."""
    account = snap.get("account") or {}
    positions = account.get("positions") or []
    open_orders = account.get("open_orders", [])
    context: dict = {}
    if positions:
        try:
            history = journal.read_events("all", events=("order_submitted", "predictions", "flatten", "daily_loss_flatten"))
            context = holdings.entry_context(positions, history)
        except Exception as exc:  # noqa: BLE001 - context is advisory; the list of holdings is not
            journal.log("error", where="holdings", detail=describe_error(exc))
    return holdings.render_positions_block(positions, open_orders, context, prior)


def account_from_snapshot(snap: dict) -> AccountState:
    acct = snap["account"]
    positions = {
        p["symbol"]: Position(
            symbol=p["symbol"],
            instrument=p["instrument"],
            qty=p["qty"],
            market_value=p["market_value"],
            underlying=p.get("underlying"),
            avg_entry_price=p.get("avg_entry_price"),
            current_price=p.get("current_price"),
        )
        for p in acct["positions"]
    }
    raw_orders = acct.get("open_orders")
    return AccountState(
        equity=acct["equity"],
        start_of_day_equity=acct["start_of_day_equity"],
        cash=acct["cash"],
        positions=positions,
        account_number=acct.get("account_number"),
        open_orders=[OpenOrder(**o) for o in raw_orders] if raw_orders is not None else None,
    )


async def run(args: argparse.Namespace) -> int:
    validate_account(args.account)
    journal.use_account(args.account)
    overrides.use_account(args.account)
    config = load_config(args.config)
    mqtt.configure(config, args.account)
    risk = RiskManager(config, account=args.account)
    now = datetime.now(EASTERN)

    def end_cycle(**fields) -> None:
        """Journal cycle_end, and refresh the day's trades for the Home
        Assistant / team view first (#87).

        Every exit path goes through here on purpose. The first version of
        this hooked only the exits-only early return, so a NORMAL cycle - the
        overwhelming majority - never republished, and the dashboard's trade
        card sat at `unknown` all day. Read back from the journal rather than
        accumulated in memory: each cycle is its own process."""
        try:
            mqtt.publish_report("recent_trades", trades_payload(journal.read_events(), args.account))
        except Exception as exc:  # noqa: BLE001 - a reporting side channel must never fail a cycle
            print(f"trade report skipped: {describe_error(exc)}", file=sys.stderr)
        journal.log("cycle_end", **fields)

    if args.account == "official" and not args.dry_run and not official_account_may_trade(now):
        print(
            f"refusing: official account may not trade before "
            f"{OFFICIAL_TRADING_STARTS:%Y-%m-%d %H:%M %Z} (use --dry-run to rehearse)",
            file=sys.stderr,
        )
        return 2

    halt_reason = risk.halted(now)
    if halt_reason:
        print(f"halted: {halt_reason}")
        return 0

    creds = load_credentials(args.account)
    featherless = FeatherlessClient(
        creds["FEATHERLESS_API_KEY"],
        model=config.get("model") or DEFAULT_MODEL,
        timeout=float(config.get("request_timeout_sec", 60)),
    )

    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        snap = await build_snapshot(client, config, now=now)

        if not args.force:
            if not snap["market_open"]:
                print(f"market closed (next open {snap['next_open']})")
                return 0
            if not risk.in_trading_window(now):
                print("outside trading window")
                return 0

        # What second opinion the model was handed this cycle. Logged from the
        # snapshot rather than the prompt because the prompt carries the whole
        # option chain; no record at all means no prior was available (the
        # feed failed, or predictions_enabled is off), which is itself the
        # answer.
        prior = predictions.journal_fields(snap.get("predictions") or {})
        if prior:
            journal.log("predictions", account=args.account, **prior)

        acct = account_from_snapshot(snap)

        # Every guard above this line keys on the --account STRING. This one
        # keys on what the broker says the account actually is, and it runs
        # before a single proposal is generated.
        allowed, identity_message = check_account_identity(args.account, acct.account_number)
        if identity_message:
            print(identity_message, file=sys.stderr)
        if not allowed:
            journal.log(
                "identity_refused",
                account=args.account,
                account_number=acct.account_number,
                reason=identity_message,
            )
            return 2
        if identity_message:
            journal.log("identity_unverified", account=args.account, reason=identity_message)

        day_pnl = acct.equity - acct.start_of_day_equity
        print(
            f"equity {acct.equity:.2f}  cash {acct.cash:.2f}  day P&L {day_pnl:+.2f}  "
            f"positions {acct.open_position_count}  account={args.account}"
        )
        # Whether the option menu actually reached the configured DTE window
        # this cycle (#158) - an observation, so it rides on cycle_start
        # rather than config, and a truncated chain is loud, not silent.
        coverage = chain_coverage(snap.get("options"))
        for underlying, cov in coverage.items():
            if cov["truncated"]:
                print(
                    f"WARNING: {underlying} option chain truncated at {cov['pages']} page(s) "
                    f"(max DTE fetched {cov['max_dte']}) - raise CHAIN_MAX_PAGES or narrow the band",
                    file=sys.stderr,
                )
        journal.log(
            "cycle_start",
            account=args.account,
            equity=acct.equity,
            day_pnl=day_pnl,
            positions=acct.open_position_count,
            dry_run=args.dry_run,
            chain_coverage=coverage,
        )
        # What this cycle actually ran with (git config + active overrides),
        # so a P&L change can be attributed to the config change behind it.
        journal.log("config", account=args.account, **config_provenance(config))

        # Entry buys still resting from an EARLIER cycle are cancelled before
        # anything else looks at the account (#171): a thesis the model has
        # not re-examined must not fill hours later, and must not be counted
        # as either held or absent. Sells are never touched. The snapshot and
        # the account are edited to match, so the model and the funnel see
        # the post-cancel state, not the one the broker reported a moment ago.
        open_orders = (snap.get("account") or {}).get("open_orders")
        if open_orders is None:
            journal.log("error", where="open_orders", detail="open-order lookup failed; funnel is sizing on holdings alone")
            print("WARNING: could not read open orders - resting buys are invisible this cycle", file=sys.stderr)
        elif not args.dry_run:
            for cancel in await stale_orders.cancel_stale_entries(client, open_orders):
                journal.log("order_canceled", reason="unfilled entry from a previous cycle", **cancel)
                print(f"{'CANCELLED' if cancel['ok'] else 'cancel FAILED for'} stale buy {cancel['qty']} {cancel['symbol']}"
                      f"{' @ ' + str(cancel['limit_price']) if cancel.get('limit_price') else ''} (sent {cancel['submitted_at']}): {cancel['detail']}")
                if cancel["ok"]:
                    open_orders[:] = [o for o in open_orders if o["id"] != cancel["id"]]
            acct.open_orders = [OpenOrder(**o) for o in open_orders]

        if risk.daily_loss_breached(acct):
            # Flatten everything, then halt for the rest of the day.
            journal.log("daily_loss_halt", equity=acct.equity, start_of_day=acct.start_of_day_equity)
            if not args.dry_run:
                outcome = await flatten_all(client)
                journal.log("daily_loss_flatten", **vars(outcome))
                print(outcome.message)
                if outcome.state == INCOMPLETE:
                    print(f"WARNING: {outcome.message}", file=sys.stderr)
                halt = risk.daily_halt_file(now.date())
                halt.parent.mkdir(exist_ok=True)
                halt.write_text("daily loss cutoff breached\n")
            print("DAILY LOSS CUTOFF BREACHED - flattened and halted for today")
            return 0

        # Deterministic exits first - code decides when a position is done,
        # before the model is asked about anything new. If any fire, the
        # cycle ends there: the snapshot is stale once positions changed,
        # and the next cycle is minutes away.
        exit_proposals = check_exits(acct.positions, now.date(), config)
        for p in exit_proposals:
            price = price_for_proposal(snap, p) or acct.positions[p.symbol].current_price or 0.0
            r = await execute.place_proposal(client, risk, acct, price, p, dry_run=args.dry_run, now=now)
            fields = {"side": "sell", "qty": p.qty, "symbol": p.symbol, "instrument": p.instrument,
                      "price": price, "reason": p.reason}
            if r.status == execute.SUBMITTED:
                journal.log("order_submitted", order_id=r.order_id, exit=True, **fields)
                print(f"EXIT {p.symbol} x{p.qty} (order {r.order_id}): {p.reason}")
            elif r.status == execute.DRY_RUN:
                journal.log("dry_run", exit=True, **fields)
                print(f"DRY-RUN exit {p.symbol} x{p.qty}: {p.reason}")
            else:
                journal.log("order_error" if r.status == execute.ERROR else "order_rejected",
                            exit=True, detail=r.detail, **fields)
                print(f"EXIT FAILED {p.symbol}: {r.detail}", file=sys.stderr)
        if exit_proposals:
            end_cycle(actions=len(exit_proposals), exits_only=True)
            return 0

        # The final day of the event: the score is fixed at the prior close,
        # so there is nothing to gain from a new position - only exits run.
        final_day = config.get("final_flatten_date")
        if final_day and now.date() >= date.fromisoformat(str(final_day)):
            print(f"final day ({final_day}) - no new entries")
            end_cycle(actions=0, skipped="final day")
            return 0

        # --force skips this too: a rehearsal should exercise the model
        # call; risk.py still rejects any resulting buy as out-of-window.
        if not args.force and not risk.entries_allowed(now) and not acct.positions:
            print(f"flat and past the {risk.last_entry} ET entry cutoff - nothing to decide")
            return 0

        outcomes = await learning_block(client, config, snap, now)
        positions_block = holdings_block(snap, prior)

        # Measured 21% of cycles losing their decision to a transient model
        # failure (issue #85). Without a retry the slot is simply forfeited -
        # the next attempt is the next cron cycle, ten minutes later, which
        # during the scoring window is a lost chance to enter or adjust.
        budget = RetryBudget(
            max_attempts=int(config.get("decide_max_attempts", 3)),
            budget_sec=float(config.get("decide_retry_budget_sec", 120)),
        )

        def _on_retry(attempt, reason, delay, exc):
            journal.log(
                "decide_retry",
                attempt=attempt,
                reason=reason,
                delay_sec=delay,
                detail=summarize(exc),
                model=featherless.model,
            )
            print(f"decision attempt {attempt} failed ({reason}); retrying in {delay:.0f}s", file=sys.stderr)

        try:
            decision = await call_with_retry(
                lambda: decide(snap, config, featherless, today=now.date(), mcp=client, learning=outcomes,
                               positions_block=positions_block),
                budget=budget,
                on_retry=_on_retry,
            )
        except Exception as exc:  # noqa: BLE001 - one bad model call must not crash the cycle
            detail = describe_error(exc)
            journal.log(
                "error",
                where="decide",
                detail=detail,
                model=featherless.model,
                attempts=budget.max_attempts,
                elapsed_sec=round(budget.elapsed, 1),
            )
            print(f"decision step failed: {detail}", file=sys.stderr)
            return 1
        proposals, raw = decision.proposals, decision.raw
        journal.log(
            "decision",
            raw=raw,
            count=len(proposals),
            model=decision.model,
            usage=decision.usage,
            latency_sec=decision.latency_sec,
            finish_reason=decision.finish_reason,
            reasoning=decision.reasoning or None,
            tool_calls=decision.tool_calls or None,
            learning_chars=len(outcomes) or None,
            positions_block_chars=len(positions_block) or None,
        )

        if args.verbose:
            print(
                f"model {decision.model} in {decision.latency_sec}s, finish={decision.finish_reason}, "
                f"usage {decision.usage}"
            )
            for tc in decision.tool_calls:
                print(f"  research: {tc['name']}({tc['args']}) -> {tc['chars']} chars in {tc['sec']}s")
            if decision.reasoning:
                print(f"model reasoning (head): {decision.reasoning[:600]!r}")
            print(f"model output: {raw}")
        if not proposals:
            print("decision: hold (no actions)")
            end_cycle(actions=0)
            return 0

        # Entry times for the min-hold guard: the last submitted BUY per
        # symbol today, from the journal. Read once per cycle, not per
        # proposal - and only the model's sells are guarded; exits.py and
        # flatten.py never pass this way.
        entries_today = {}
        for rec in journal.read_events(events=("order_submitted",)):
            if rec.get("side") == "buy" and rec.get("symbol"):
                entries_today[rec["symbol"]] = rec.get("ts")

        for p in proposals:
            # #170: a sell naming a neighbouring strike of the one contract
            # held at that expiry means the held one. Resolved before every
            # guard so the min-hold check and the funnel judge the real
            # position; the journal keeps what the model actually wrote.
            as_written = p.symbol
            p, resolution = holdings.resolve_sell(p, acct.positions)
            resolved = {"resolved_from": as_written} if resolution else {}
            if resolution:
                print(f"RESOLVED {resolution}")
            block = early_exit_block(p, acct.positions.get(p.symbol), entries_today.get(p.symbol), config, now)
            if block:
                journal.log("order_rejected", detail=block, side=p.side, qty=p.qty,
                            symbol=p.symbol, instrument=p.instrument, price=None, reason=p.reason, **resolved)
                print(f"REJECTED {p.side} {p.qty} {p.symbol}: {block}")
                continue
            price = price_for_proposal(snap, p)
            price_source = "snapshot"
            if price is None and p.instrument == "option":
                # Researched contracts live outside the snapshot's page - see
                # bot/snapshot.py::quote_option_mid. One quote, then the
                # funnel judges it on the market's price like any other.
                price = await quote_option_mid(client, p.symbol)
                price_source = "quote" if price else "none"
            if price is None:
                # Say what is actually wrong. Passing 0.0 into the risk check
                # yields "price must be positive", which reads as a broken
                # quote when the truth is that nothing could price this symbol
                # - the confusion that made the 2026-08-31 rejections look
                # like market data failures.
                r = execute.ExecutionResult(
                    execute.REJECTED, p,
                    f"no price for {p.symbol} (not in snapshot, no live quote)",
                )
            else:
                r = await execute.place_proposal(
                    client, risk, acct, price, p, dry_run=args.dry_run, now=now
                )
            label = f"{p.side} {p.qty} {p.symbol}"
            fields = {
                "side": p.side, "qty": p.qty, "symbol": p.symbol, "instrument": p.instrument,
                "price": price, "price_source": price_source, "reason": p.reason, **resolved,
            }
            if r.status == execute.ZERO_QTY:
                continue
            if r.status == execute.REJECTED:
                journal.log("order_rejected", detail=r.detail, **fields)
                print(f"REJECTED {label}: {r.detail}")
            elif r.status == execute.DRY_RUN:
                journal.log("dry_run", **fields)
                print(f"DRY-RUN would {label} @ ~{price or 0:.2f}: {p.reason}")
            elif r.status == execute.SUBMITTED:
                journal.log("order_submitted", order_id=r.order_id, **fields)
                print(f"SUBMITTED {label} (order {r.order_id}): {p.reason}")
            elif r.status == execute.ERROR:
                journal.log("order_error", detail=r.detail, **fields)
                print(f"submit failed for {label}: {r.detail}", file=sys.stderr)

        end_cycle(actions=len(proposals))

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--account", default="test", help="named account: official, test, or any credentials-<name>.env")
    ap.add_argument("--config", default=None, help="config file (default config.yaml); e.g. config-<name>.yaml for a variant")
    ap.add_argument("--verbose", action="store_true", help="print the raw model output")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
