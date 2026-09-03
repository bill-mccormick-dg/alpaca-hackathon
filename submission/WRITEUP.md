# Autobelay — one-page write-up

*long premium, short leash*

**Team:** RazorsEdge — William P. McCormick / William C. McCormick · **Account:** `PA3VS39Y5LE2` ($100,000 paper) ·
**Repo:** github.com/bill-mccormick-dg/alpaca-hackathon (MIT) · **Stack:** Alpaca
MCP server, Featherless.ai (Qwen3.8-Flash-Next / Kimi-K3), Python

## Thesis

Buy defined-risk, short-dated options premium on the five most liquid names
(SPY, QQQ, AAPL, MSFT, NVDA) when an open-source model sees a concrete reason;
deterministic code sizes every trade, stops it, and closes it before expiry —
**the model never touches an order.** Long premium's worst case is known before
entry, which keeps every guardrail simple and absolute, and it matches what a
language model is actually good at: forming a directional view from evidence it
can name. It is bad at managing a position minute to minute, so it doesn't.

The trading is autonomous — no human approves an order. The risk *envelope* is
not: the whitelist, the position caps and the 2 % daily-loss cutoff live in git,
and every knob that is reachable at runtime expires at the close.

## AI logic

**We audit the model's rhetoric, not just its trades.** The reason strings are
load-bearing — the digest, the reviewer, the email and the viewer all quote them
— so two audits check them against facts the model cannot move.
`bot/citations.py::audit` matches every percentage in a prior-shaped clause
against the prior the model was actually handed that cycle: `unsupported` when
it matches nothing either crowd gave it, `misattributed` when it matches another
underlying's prior. Its first run cleared the model — on Sep 1, 22 figures
quoted and 22 exact — and caught one real error: QQQ's chain prior had been
withheld as untradeable, and the model quoted SPY's as "the options market" for
QQQ. `audit_exit_claims` grades exit *reasons* against the account. The same
session, the judged account sold a 7-DTE SPY put at −12 % across four attempts, every reason
citing an expiry pressure that does not exist — "forced expiry sale", "backstop
forces exit", when with `expiry_close_dte: 0` no code path touches a 7-DTE
contract for six more days — and the filled exit claimed the market "held above
prior close" while SPY sat 0.73 % *below* it: the strike, 760, read as the prior
close, 766.87. Both audits are journaled per cycle and surfaced in the digest,
and both are **reporting only** — prose is not an order parameter, and
`check_order` does not grade rhetoric. The honest limit: the citations audit is
skipped whenever research tools ran, because a quoted figure may legitimately
have come from a tool result, so the more agentic the configuration the less of
its arithmetic we can mechanically verify. The digest counts the skips.

**No account grades its own homework.** `bot/config.py::review_choice()` picks
the first model in the preference list that is neither the trading model nor any
model the journal says traded that day; a `review_model` pinned to one of those
is refused, and the refusal is journaled as `review_pin_ignored`. We state this
as a caught failure rather than a design claim, because for two sessions it was
false: `resolve_review_model()` existed, was documented and was journaled every
cycle, and nothing called it — so on Aug 31 and Sep 1 the judged account's
critique was written by Kimi-K2.6, the model whose decisions it was grading,
under a prompt opening "the decisions below were yours". The journal is what
proved it, and the `eod_review` event is what makes the fix checkable rather
than asserted. Resolution then moved off the config and onto the journal, so a
mid-session model change on the dashboard cannot quietly restore a self-review.

**The inputs are graded, not just the model.** Two independent crowds answer the
same question every cycle — Kalshi's index-close market and the option chain's
own implied distribution — and both are usability-gated: a barely-traded market
is withheld rather than shown, and the prompt says what to do with the pair
(disagreement is information; agreement is the base rate). Every night the exact
probabilities the model was handed are Brier-scored against what the market did.
Tue Sep 1, judged account: Kalshi **0.004**, the chain **0.008**, against 0.25
for a coin flip. Priors the gate withheld are shadow-scored (0.006), so we can
see whether the gate is discarding anything good. Each open position is also
shown with the prior *at the time it was opened* beside the prior now, so "has
my thesis changed?" is a comparison against recorded numbers rather than against
the model's memory of them.

**Three live accounts, one deliberate variable each.** `official` is judged.
`test` is the challenger: a different model, with the research tools and the
learning loop on. `mixed` differs from `official` in one thing — it may choose
stock or options per idea — which makes instrument choice a decision with a
control rather than an assumption. Same box, same 10-minute cadence, same
market; what wins is promoted into the official config by pull request. Four
days is no backtest, and the free feed carries no historical options data, so
the experiment runs live instead.

**The cycle.** Every 10 minutes in market hours the agent builds a snapshot
through Alpaca's MCP server — account, positions, open orders, clock, and per
underlying a 12-contract menu paginated across the whole 2–45 DTE window, each
contract carrying Alpaca's own IV and Greeks (`bot/greeks.py` solves only the
contracts Alpaca does not price, and marks those as derived, so the model knows
which numbers are rough). The model then runs a **bounded research loop**: up to
six read-only tool calls — recent bars, a stock snapshot, specific contracts,
news — each journaled, after which it must answer with a JSON array of proposals
or `[]`. Holding is the default. The thesis and tactics are in the prompt as
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
exposed a real bug: the funnel permitted what the prompt forbade. The same
gate then failed in production on a model we had deployed: running Kimi-K3 as
a challenger trial, six of its first seven live cycles were forfeited because
it *described* the research it meant to do instead of calling a tool, while
Qwen3.8 answered the same prompt in 15s after six tool calls. That cut both
ways and we fixed both — the model is not the one we score on, and the
research loop had been hanging up on a model still willing to work.

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
Actions runner** on that host deploys every merge (169 PRs, CI-gated, 795 unit
tests). A JSONL journal is the single source of truth: decisions, orders,
rejections, exits, tool calls, and the exact config hash each cycle ran with.
`trade_report.py` reconstructs round trips from Alpaca's fills and classifies
every exit (stop / take-profit / expiry / model / flatten); `eod_review.py`
turns that into a daily digest, an equity curve, and a one-change
recommendation written by a model that did not trade the day. Runtime overrides expire at the
close, so intraday tweaks never outlive the day and tomorrow starts from git.
The two challenger accounts above run the same code on the same host.

## Results (fill Thu Sep 3 after the close)

Equity Mon open → Thu close: **$___ → $___ (__ %)**. __ round trips; win rate
__ %; exits: __ stop / __ take-profit / __ expiry / __ model / __ flatten.
Rejections by rule: __. Challenger vs official: __. What we'd change next: __.

## Disclosure

Hosting (Proxmox LXC), Ansible role, secrets pipeline and the CI workflow were
set up around kickoff in a separate private infrastructure repo. No trading
logic existed before kickoff. All code in this repo is original to the event.
