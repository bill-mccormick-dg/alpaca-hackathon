# Alpaca AI Trading Agents Hackathon

lablab.ai x Alpaca — Aug 28–Sep 4, 2026. Submission deadline Sep 4, 10:00 AM CDT.

## Challenge: Options Alpha Agents

Build an autonomous AI trading agent that generates P&L using Alpaca's trading
platform, with a testable strategy.

**Core requirements**
- Autonomous agent using Alpaca's Trading API
- Must use Alpaca's MCP server or CLI
- Strategy must incorporate options trading
- Paper trading only, starting balance $100,000
- Judging requires a **brand-new** paper account created for this hackathon
  (not reused from other projects)

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

1. Create an Alpaca paper trading account (new one, dedicated to this hackathon)
2. Set starting balance to $100,000
3. Featherless AI credits ($25/participant) optionally available for open-source
   model inference — see event page for claiming instructions
4. `pip install -r requirements.txt`
5. `cp .env.example .env` and fill in `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
   (from the dedicated paper account above) and `FEATHERLESS_API_KEY`
6. Test: `python -m unittest discover -s tests` (credential-free unit tests),
   then `python scripts/verify_connection.py` (live connectivity check
   against the real paper account via Alpaca's MCP server)

## Submission checklist

- [ ] Project title + short/long description
- [ ] Technology & category tags
- [ ] Cover image
- [ ] Video presentation
- [ ] Slide presentation
- [ ] Public GitHub repository
- [ ] Demo application URL
- [ ] Alpaca paper trading account ID (required for judging)
- [ ] One-page write-up: AI logic, risk gates, Alpaca infra
- [ ] Up to 5 social posts (X/LinkedIn, tag @lablabai and @AlpacaHQ)
