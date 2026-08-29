---
sidebar_position: 7
title: Official guidelines (Alpaca)
---

# Alpaca Official Guidelines — Alpaca AI Trading Agents Hackathon

Verbatim from Alpaca (via lablab.ai event page, "Build with Alpaca" section),
copied here 2026-08-28 for a durable, version-controlled reference. See
[README.md](https://github.com/bill-mccormick-dg/alpaca-hackathon/blob/main/README.md) for how this project's setup maps to these rules.

## 📢 Official guidelines from Alpaca

Answers to frequently asked questions about market data, judging,
submissions, and the trading timeline.

### REQUIRED for submission

For your official submission, it is required to create a new paper account
with a starting balance of $100,000. You can use the same email to create a
new paper account if you already have an account with Alpaca. Your agent
should begin trading from this account on **Monday, August 31 at 9:30 a.m.
ET**. Please do not use your testing account for the official P&L
measurement.

### Technical Resources

- Alpaca Trading API Docs: https://docs.alpaca.markets/us/docs/trading-api
- Alpaca Skills: https://github.com/alpacahq/alpaca-skills
- Market Data API: https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data
- Alpaca JS SDK: https://github.com/alpacahq/alpaca-trade-api-js
- Trading API: https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
- Alpaca MCP Server (setup/usage docs): https://github.com/alpacahq/alpaca-mcp-server

### Market Data

Participants may use either:
- Alpaca's free market data subscription, which provides the indicative
  options feed
- Algo Trader Plus, which includes the OPRA options feed

Both free and paid market data tiers are permitted. Participants will not
automatically receive Algo Trader Plus or OPRA access during the event.

### Judging Criteria

Submissions will be evaluated based on a combination of:
- Trading performance and P&L during the official scoring window.
  Performance will be judged using **total account equity**, not cash
  balance.
- The creativity, autonomy, and robustness of the agent trading workflow

P&L will be an important factor, but winners will not be selected based on
P&L alone.

### Timeline

- **Hackathon window:** Friday, August 28 at 9:30 a.m. ET to Friday,
  September 4 at 9:30 a.m. ET
- **Official P&L measurement:** Monday, August 31 at 9:30 a.m. ET to Friday,
  September 4 at 9:30 a.m. ET. Alpaca will look at the portfolio's total
  equity as of **EOD Thursday Sep 3rd**. Any option exercises/assignments
  for options expiring on Sep 3rd will be reflected in the EOD value.
- You may create a paper account now to develop and test your agent over
  the weekend.

### UI Requirements

A user interface is **not required**. Judging is primarily on the
autonomous agent workflow and its trading performance.

### Links

(more available on the lablab.ai event page under "Build with Alpaca")

- Getting Started: https://docs.alpaca.markets/us/docs/getting-started
- Alpaca Skills: https://github.com/alpacahq/alpaca-skills
- Market Data API: https://docs.alpaca.markets/us/docs/getting-started-with-alpaca-market-data
- Alpaca JS SDK: https://github.com/alpacahq/alpaca-trade-api-js
- Trading API: https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
- Alpaca MCP Server: https://github.com/alpacahq/alpaca-mcp-server

## Alpaca AI Trading Agents Hackathon — FAQ

**How will submissions be judged? Is the competition based only on P&L?**
Submissions will be evaluated based on a combination of trading performance
(measured by total account equity) and the creativity, autonomy, and
robustness of the agent trading workflow. Winners will not be selected based
on P&L alone.

**Will judges consider risk-adjusted metrics such as Sharpe ratio, Sortino
ratio, or maximum drawdown?**
Performance will be judged using total account equity at the official
hackathon close.

**How will Alpaca track account performance?**
Alpaca will track total account equity.

**Will there be an ongoing scoreboard, as there was in the previous Alpaca
competition?**
No scoreboard for this competition, but they will try to incorporate this
for the next challenge.

**What is the official P&L measurement window?**
Monday, August 31 at 9:30 a.m. ET through Friday, September 4 at 9:30 a.m.
ET. They will look at the portfolio's total equity as of EOD Thursday Sep
3rd. Any option exercises and assignments for options expiring on Sep 3rd
will be reflected in the EOD value.

**When should my agent begin trading for the competition?**
Your agent should begin trading from the official competition paper account
on Monday, August 31 at 9:30 a.m. ET. Trades made in a testing account
before then will not count toward the official measurement.

**Do I need a new paper account for the official submission?**
Yes. You must use a new paper account with a starting balance of $100,000
for the official measurement period. An account used for testing should not
be used for the official measurement.

**Can I use my existing email address to create the new paper account?**
Yes. You may use the same email address if you already have an Alpaca
account.

**Can I test before the official measurement window begins?**
Yes. You may prototype and trade using a testing account before the
official window. For the competition, use a new $100,000 paper account
beginning Monday, August 31 at 9:30 a.m. ET.

**Does trading after Friday, September 4 at 9:30 a.m. ET count toward the
score?**
No. The measurement window ends at 9:30 a.m. ET on Friday, September 4,
when a snapshot of total account equity will be taken.

**Will agents trade on live market data or be evaluated through simulations
or backtests?**
Agents will trade using live market data during the official measurement
window. Final performance will be based on the dedicated Alpaca paper
trading account, not a historical backtest.

**Can I include historical backtests or simulated market shocks if live
market conditions are flat?**
Yes. You may include backtests and simulated shocks in the project
write-up and repository as additional evidence of the agent's guardrails.
Official P&L will still be based on the live paper account at the Friday
snapshot, and judges will evaluate the workflow alongside the equity
result.

**Must the GitHub repository be public during the hackathon?**
No. It may remain private during the hackathon.

**Can I use a repository created before kickoff if it contains only a
README, LICENSE, and .gitignore?**
Yes, although creating a fresh repository is recommended.

**May I reuse or depend on my own pre-existing library or application?**
Yes. Judging is scoped to the agent submitted during the event, including
its options workflow using the Trading API with MCP or CLI,
competition-paper-account performance, and the creativity, autonomy, and
robustness of the workflow.

**May I set up infrastructure, boilerplate, or other supporting components
before kickoff?**
Yes.

**If pre-event work is permitted, must it be disclosed in the README or
final submission?**
Yes.

**Is Alpaca MCP required, or is the Trading API or CLI sufficient?**
You can use Alpaca's MCP or CLI and use your preferred language. If for
whatever reason you want to use an SDK to implement your bot, explain
clearly your reasons and prioritize the official SDKs.

**What is the recommended way to use Alpaca MCP with an AI agent?**
See the Alpaca MCP server documentation for setup instructions for your
selected AI agent.

**Does the Alpaca MCP server support options data and trading?**
Yes. The official Alpaca MCP server can fetch contracts and option chains,
retrieve quotes and Greeks, and place single-leg and multi-leg option
orders. Using alpaca-py is optional if you want to manage the trading loop
in your own code.

**What options market data is available in paper trading?**
The latest option quotes and chains available through the API are
real-time. The free Basic plan includes Alpaca's Indicative options feed,
while Algo Trader Plus provides full OPRA data. On Basic, the 15-minute
restriction applies to historical bars and trades, **not** the latest
quote. Dashboard charts may lag, so agents should rely on API data.

**Will participants automatically receive OPRA or Algo Trader Plus
access?**
No. Participants will not automatically receive Algo Trader Plus or OPRA
access. Both free and paid market data tiers are permitted.

**Which options order types are supported in paper trading through MCP?**
MCP option orders support market, limit, stop, and stop-limit orders.
Trailing-stop orders are for stocks. A risk agent can monitor an options
position and submit a market or limit order to close it.

**Are there any restrictions on options trading strategies?**
No.

**Does an autonomous agent need to be hosted?**
No. If the agent runs autonomously and only places orders, a GitHub
repository is sufficient. A hosted link is needed only if the submission
includes a demo app that judges must open.

**Can I deploy a Blazor WebAssembly app on Vercel?**
Yes. A Blazor WebAssembly app published as static HTML, JavaScript, and
WebAssembly files and hosted on Vercel is compliant.

**Are there restrictions on model providers or hosting infrastructure?**
No. There are no restrictions on the model provider or hosting
infrastructure.
