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

Alpaca requires the official measurement to run on a **fresh** $100k paper
account — untouched by test trades. Account `PA3VS39Y5LE2` (created
2026-08-28) serves as both: it's had read-only queries only (Step 1's
`verify_connection.py` — `get_account_info`/`get_clock`), zero trades, zero
positions, so it still qualifies as fresh. It **doubles as the official
account** — no second account needed, as long as this rule holds:

**No orders get placed on this account before Monday, Aug 31, 9:30 AM ET.**
Read-only development (querying account/positions/option chains, dry-run
proposals that never call a placing tool) is fine any time. Only equity from
Mon 9:30 AM ET → Thu Sep 3 EOD counts toward scoring (snapshot Fri Sep 4,
9:30 AM ET).

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
   (from the account above) and `FEATHERLESS_API_KEY`
5. Test: `python -m unittest discover -s tests` (credential-free unit tests),
   then `python scripts/verify_connection.py` (live connectivity check —
   **read-only tools only until Monday 9:30 AM ET**, see [Account](#account))

## Submission checklist

- [ ] Project title + short/long description
- [ ] Technology & category tags
- [ ] Cover image
- [ ] Video presentation
- [ ] Slide presentation
- [x] Public GitHub repository
- [ ] Demo application URL — likely N/A, UI not required (FAQ); only needed
      if we ship a demo app judges must open
- [ ] Alpaca paper trading account ID (required for judging) — `PA3VS39Y5LE2`
- [ ] One-page write-up: AI logic, risk gates, Alpaca infra
- [ ] Up to 5 social posts (X/LinkedIn, tag @lablabai and @AlpacaHQ)
- [ ] **No orders placed on the account before Mon Aug 31, 9:30 AM ET**
- [ ] Agent trading live by Mon Aug 31, 9:30 AM ET
