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
from datetime import date, datetime

from bot import execute, journal, overrides
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import config_provenance, load_config
from bot.credentials import load_credentials, validate_account
from bot.decide import decide
from bot.exits import check_exits
from bot.featherless import DEFAULT_MODEL, FeatherlessClient
from bot.flatten import flatten_all
from bot.models import AccountState, Position
from bot.orders import INCOMPLETE
from bot.risk import EASTERN, RiskManager
from bot.snapshot import build_snapshot, price_for_proposal

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
    return AccountState(
        equity=acct["equity"],
        start_of_day_equity=acct["start_of_day_equity"],
        cash=acct["cash"],
        positions=positions,
    )


async def run(args: argparse.Namespace) -> int:
    validate_account(args.account)
    journal.use_account(args.account)
    overrides.use_account(args.account)
    config = load_config(args.config)
    risk = RiskManager(config, account=args.account)
    now = datetime.now(EASTERN)

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

        acct = account_from_snapshot(snap)
        day_pnl = acct.equity - acct.start_of_day_equity
        print(
            f"equity {acct.equity:.2f}  cash {acct.cash:.2f}  day P&L {day_pnl:+.2f}  "
            f"positions {acct.open_position_count}  account={args.account}"
        )
        journal.log(
            "cycle_start",
            account=args.account,
            equity=acct.equity,
            day_pnl=day_pnl,
            positions=acct.open_position_count,
            dry_run=args.dry_run,
        )
        # What this cycle actually ran with (git config + active overrides),
        # so a P&L change can be attributed to the config change behind it.
        journal.log("config", account=args.account, **config_provenance(config))

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
            journal.log("cycle_end", actions=len(exit_proposals), exits_only=True)
            return 0

        # The final day of the event: the score is fixed at the prior close,
        # so there is nothing to gain from a new position - only exits run.
        final_day = config.get("final_flatten_date")
        if final_day and now.date() >= date.fromisoformat(str(final_day)):
            print(f"final day ({final_day}) - no new entries")
            journal.log("cycle_end", actions=0, skipped="final day")
            return 0

        # --force skips this too: a rehearsal should exercise the model
        # call; risk.py still rejects any resulting buy as out-of-window.
        if not args.force and not risk.entries_allowed(now) and not acct.positions:
            print(f"flat and past the {risk.last_entry} ET entry cutoff - nothing to decide")
            return 0

        try:
            decision = await decide(snap, config, featherless, today=now.date(), mcp=client)
        except Exception as exc:  # noqa: BLE001 - one bad model call must not crash the cycle
            detail = describe_error(exc)
            journal.log("error", where="decide", detail=detail, model=featherless.model)
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
            journal.log("cycle_end", actions=0)
            return 0

        for p in proposals:
            price = price_for_proposal(snap, p)
            r = await execute.place_proposal(
                client, risk, acct, price or 0.0, p, dry_run=args.dry_run, now=now
            )
            label = f"{p.side} {p.qty} {p.symbol}"
            fields = {
                "side": p.side, "qty": p.qty, "symbol": p.symbol, "instrument": p.instrument,
                "price": price, "reason": p.reason,
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

        journal.log("cycle_end", actions=len(proposals))

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
