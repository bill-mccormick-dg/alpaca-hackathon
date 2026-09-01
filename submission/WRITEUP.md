# AI Day Trader - Long Premium, Short Leash — one-page write-up

**Team:** Bill McCormick (+1) · **Account:** `PA3VS39Y5LE2` ($100,000 paper) ·
**Repo:** github.com/bill-mccormick-dg/alpaca-hackathon (MIT) · **Stack:** Alpaca
MCP server, Featherless.ai (Kimi-K2 / Qwen3.8), Python

## Thesis

Buy defined-risk, short-dated options premium on the five most liquid names
(SPY, QQQ, AAPL, MSFT, NVDA) when an open-source model sees a concrete reason;
deterministic code sizes every trade, stops it, and closes it before expiry —
**the model never touches an order.** Long premium's worst case is known before
entry, which keeps every guardrail simple and absolute, and it matches what a
language model is actually good at: forming a directional view from evidence it
can name. It is bad at managing a position minute to minute, so it doesn't.

## AI logic

Every 10 minutes in market hours the agent builds a snapshot through Alpaca's
MCP server — account, positions, clock, and per underlying the ~12
nearest-the-money contracts in a 2–45 DTE window, each with Alpaca's own IV and
Greeks (Black-Scholes in `bot/greeks.py` fills in only the contracts Alpaca
does not price, and marks them as derived). The model then
runs a **bounded research loop**: up to six read-only tool calls (recent bars, a
stock snapshot, specific contracts, news) through Alpaca's MCP server, each
journaled, after which it must answer with a JSON array of proposals or `[]`.
Holding is the default. The thesis and tactics are in the prompt as
`strategy_notes` — config, not code — and the model's reasoning, tool calls,
token usage and latency are journaled every cycle. Thinking-mode models are run
with thinking disabled; that single setting turned empty answers into decisions.

**The guardrails do not trust the model's arithmetic, and we can show where it
went wrong.** Each cycle the model is handed a prediction-market prior (Kalshi's
index-close market and the option chain's own implied odds). On day 2 it quoted
that prior three times in its stated reasons — "68.7% chance of down>1%",
"7.6% chance of finishing above", "81.9% down>1%" — and all three were invented:
the prompt said 33.8%, 12.6% and 59.5%, every error skewed toward the trade it
was proposing. Since then every percentage the model cites in a reason is
checked against the numbers it was actually shown, the count rides on the
`decision` journal event, the end-of-day digest lists each unsupported quote
with the nearest real value, and the reviewer model is told to judge decisions
on the journalled prior rather than on the figure quoted. The funnel still does
not act on prose — it bounds what can be traded, not what can be said — but a
fabricated statistic no longer propagates unlabelled into the artifacts a
reader sees. Each open position is also shown with the prior *at the time it
was opened* beside the prior now, so "has my thesis changed?" is a comparison
against recorded numbers, not against the model's memory of them.

## Risk gates (deterministic, never negotiated)

`bot/risk.py::check_order()` is the only gate and `bot/execute.py::place_proposal()`
the only order path. Per proposal: whitelist, side/qty sanity, ≤ $5,000 notional
per position (contracts × 100 × price), ≤ 4 positions, ≤ 10 contracts per
order, 2–45 DTE on entries (sells stay legal to expiry), entries only 09:45–15:15 ET, sells until 15:45. Per cycle,
**before** the model is consulted: close any contract on its expiry day, and
any position past −40 % / +60 % of entry premium (`bot/exits.py`). Per day:
a 2 % loss cutoff flattens everything and halts; a manual `HALT` file is a
global kill switch. End of day: contracts expiring within a day are closed;
the rest may be held under the stops — judging is on Thursday's closing equity,
so selling healthy positions early only pays the spread. Friday: no entries,
flatten all. Rejections are journaled with the rule that refused them.

## Alpaca infrastructure

All market data and every order go through **Alpaca's official MCP server**
(`alpaca-mcp-server`, stdio) from a thin async client; the model only ever sees
a curated read-only subset of its tools. Paper-only is hardcoded. A dedicated
LXC on a home Proxmox host runs the bot from cron; a **self-hosted GitHub
Actions runner** on that host deploys every merge (54 PRs, CI-gated, 280 unit
tests). A JSONL journal is the single source of truth: decisions, orders,
rejections, exits, tool calls, and the exact config hash each cycle ran with.
`trade_report.py` reconstructs round trips from Alpaca's fills and classifies
every exit (stop / take-profit / expiry / model / flatten); `eod_review.py`
turns that into a daily digest, an equity curve, and a one-change
recommendation written by the model itself. Runtime overrides expire at the
close, so intraday tweaks never outlive the day and tomorrow starts from git.
A second paper account runs a challenger config (different model, research
tools on) against the same live market — the only backtest available in four
days.

## Results (fill Thu Sep 3 after the close)

Equity Mon open → Thu close: **$___ → $___ (__ %)**. __ round trips; win rate
__ %; exits: __ stop / __ take-profit / __ expiry / __ model / __ flatten.
Rejections by rule: __. Challenger vs official: __. What we'd change next: __.

## Disclosure

Hosting (Proxmox LXC), Ansible role, secrets pipeline and the CI workflow were
set up around kickoff in a separate private infrastructure repo. No trading
logic existed before kickoff. All code in this repo is original to the event.
