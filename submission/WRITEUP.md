# Autobelay — one-page write-up

*long premium, short leash*

**Team:** RazorsEdge — William P. McCormick / William C. McCormick · **Account:** `PA3VS39Y5LE2` ($100,000 paper) ·
**Repo:** github.com/bill-mccormick-dg/alpaca-hackathon (MIT) · **Stack:** Alpaca
MCP server, Featherless.ai (Kimi-K2.6 / Qwen3.8-Flash-Next), Python

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
MCP server — account, positions, open orders, clock, and per underlying a
12-contract menu from a chain fetched across the whole 2–45 DTE window: the
at-the-money and a slightly-out-of-the-money strike per side across three
expiries in the tactics' band, each with Alpaca's own IV and
Greeks (Black-Scholes in `bot/greeks.py` fills in only the contracts Alpaca
does not price, and marks them as derived). The model then
runs a **bounded research loop**: up to six read-only tool calls (recent bars, a
stock snapshot, specific contracts, news) through Alpaca's MCP server, each
journaled, after which it must answer with a JSON array of proposals or `[]`.
Holding is the default. The thesis and tactics are in the prompt as
`strategy_notes` — config, not code — and the model's reasoning, tool calls,
token usage and latency are journaled every cycle. Thinking-mode models are run
with thinking disabled; that single setting turned empty answers into decisions.

**Choosing the model, honestly.** Featherless lists 21,912 models, 15,575 of
them with tool calling on our plan, so "open weights" narrows nothing by
itself. Our gates are written down and re-checkable: tool calling for the
research loop, context for a ~5k prompt that reaches 95k tokens mid-research,
a thinking-mode toggle without which these models return empty answers, a warm
endpoint for a 10-minute cron, and a price in the table that reports the day's
spend. `scripts/verify_models.py` checks every one against the live catalog.
What we do not claim is a bake-off: Kimi was the first model confirmed to
support tool calling and it stayed, and our official/test A/B varies the model
*and* the research tools *and* the learning loop, so it compares config
bundles rather than models. The gate that discriminates is instruction
adherence, and one rejection shows why. `Llama-3.1-Hawkish-8B`, a finance
fine-tune, is cheaper than everything we run and clears every mechanical gate.
On the live prompt it proposed a 1-DTE contract in 3 of 3 runs, which the
prompt forbids and the 14:50 backstop would close the same afternoon, and five
actions against a four-position cap. Well-formed JSON, wrong content. It also
exposed a real bug: the funnel permitted what the prompt forbade.

**The guardrails do not trust the model's arithmetic, and we check it.** Each
cycle the model is handed a prediction-market prior (Kalshi's index-close
market and the option chain's own implied odds). Every percentage it then cites
in a stated reason is checked against the numbers it was actually shown, the
count rides on the `decision` journal event, and the end-of-day digest lists
any quote that matches nothing (or that belongs to a different underlying) with
the real value beside it; the reviewer model is told to judge decisions on the
journalled prior rather than on the figure quoted. The audit was built on a
suspicion — three day-2 quotes looked invented when read against the wrong
hour's prompt — and its first run cleared the model: 22 figures quoted on day
2, 22 exact, one attributed to the wrong underlying. That is the honest
robustness story: the model's prose is checked, and the check said it was
telling the truth. Each open position is also shown with the prior *at the
time it was opened* beside the prior now, so "has my thesis changed?" is a
comparison against recorded numbers, not against the model's memory of them.

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
