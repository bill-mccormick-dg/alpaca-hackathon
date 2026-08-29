# Alpaca AI Trading Agents Hackathon

lablab.ai x Alpaca — Aug 28–Sep 4, 2026. Submission deadline Sep 4, 10:00 AM CDT.
See [docs/alpaca-official-guidelines.md](docs/alpaca-official-guidelines.md)
for Alpaca's full official rules/FAQ (copied verbatim) — this README
summarizes how our setup maps to them.

## Challenge: Options Alpha Agents

Build an autonomous AI trading agent that generates P&L using Alpaca's trading
platform, with a testable strategy.

**Core requirements**
- Autonomous agent using Alpaca's Trading API
- Must use Alpaca's MCP server or CLI
- Strategy must incorporate options trading
- Paper trading only, starting balance $100,000
- Judging on total account equity (not cash) plus workflow creativity/
  autonomy/robustness — P&L alone doesn't decide it

## Account

Two separate $100k Alpaca paper accounts, per Alpaca's rules (a testing
account can't be used for the official measurement):

- **`PA3VS39Y5LE2`** (created 2026-08-28) — the **official/judging**
  account. **No orders get placed on this account before Monday, Aug 31,
  9:30 AM ET.** Only equity from Mon 9:30 AM ET → Thu Sep 3 EOD counts
  toward scoring (snapshot Fri Sep 4, 9:30 AM ET). Read-only queries
  (account/positions/option chains) are fine any time.
- **`hackathon_test`** — safe to place real orders on for all development
  between now and Monday.

`bot/credentials.py:load_credentials()` defaults to `account="test"` for
exactly this reason — using the official account requires explicitly
passing `account="official"`, so an accidental order can't land on the
judging account. `scripts/verify_connection.py` mirrors this: defaults to
`--account test`, needs `--account official` to check the other one.

## Pre-event infrastructure (disclosure)

Per the official FAQ, infrastructure/boilerplate set up before kickoff is
allowed but must be disclosed: this repo's CT 108 hosting (Proxmox LXC),
Ansible deployment role, secrets pipeline (Alpaca + Featherless credentials),
and CI workflow were set up around/before the Aug 28 9:30 AM ET kickoff, in
the `homenetwork` infrastructure repo (private, separate from this
submission repo). No agent trading logic existed before kickoff.

## Architecture

Modeled on [`~/alpaca-trader`](https://github.com/bill-mccormick-dg/alpaca-trader)
(a working Claude-driven paper day-trading bot): deterministic snapshot → LLM
judgment → deterministic risk/execute, where risk checks never negotiate and
every order-placing path funnels through one guardrail function. Two things
differ here, both hackathon requirements:

- **[Alpaca's official MCP server](https://github.com/alpacahq/alpaca-mcp-server)**
  instead of raw SDK calls — the model gets broad *read* access (account,
  positions, option chains/Greeks, bars) to research with, but never calls
  the order-placing tools directly; a risk-check module (not yet built)
  validates proposals before our own code submits anything.
- **[Featherless.ai](https://featherless.ai)** (OpenAI-compatible, tool-calling
  confirmed on `moonshotai/Kimi-K2-Instruct` and the Qwen 3 family) instead of
  the Claude CLI.

Paper-only throughout — no live-trading code path exists.

## Setup

1. ~~Create an Alpaca paper trading account, $100,000 balance~~ — done
   (`PA3VS39Y5LE2`, see [Account](#account) above)
2. Featherless AI credits ($25/participant) optionally available for open-source
   model inference — see event page for claiming instructions
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and fill in `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
   (from the **`hackathon_test`** account — never the official one locally)
   and `FEATHERLESS_API_KEY`
5. Test: `python -m unittest discover -s tests` (credential-free unit tests),
   then `python scripts/verify_connection.py` (live check against the test
   account by default; `--account official` for the judging account, which
   should only ever get read-only calls before Monday — see [Account](#account))
6. Run a cycle: `python run_cycle.py --dry-run --force` (full snapshot →
   decide → risk-check on the test account, orders printed not sent, market
   gates skipped). Drop `--dry-run` to submit paper orders on the test account.
   `--account official` is refused outright before Mon Aug 31 9:30 AM ET
   unless `--dry-run` — hardcoded in `run_cycle.py`, not configurable.

## Submission checklist

- [ ] Project title + short/long description
- [ ] Technology & category tags
- [ ] Cover image
- [ ] Video presentation
- [ ] Slide presentation
- [ ] Public GitHub repository — currently **private** (fine during the
      build phase per Alpaca's own FAQ), but lablab.ai's submission
      checklist separately requires "Public GitHub repository" as a
      submission item. **Flip back to public before the Sep 4, 10:00 AM
      CDT deadline.**
- [ ] Demo application URL — likely N/A, UI not required (FAQ); only needed
      if we ship a demo app judges must open
- [ ] Alpaca paper trading account ID (required for judging) — `PA3VS39Y5LE2`
- [ ] One-page write-up: AI logic, risk gates, Alpaca infra
- [ ] Up to 5 social posts (X/LinkedIn, tag @lablabai and @AlpacaHQ)
- [ ] **No orders placed on the account before Mon Aug 31, 9:30 AM ET**
- [ ] Agent trading live by Mon Aug 31, 9:30 AM ET
