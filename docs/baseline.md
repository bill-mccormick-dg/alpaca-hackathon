---
sidebar_position: 7
title: The baseline run
---

# The baseline run

Starting **Tue Sep 8, 2026**. The competition is over; this is the run that
establishes what the current system does when nobody is steering it, so that
the backlog can be implemented against a number instead of an argument.

Read this before changing anything on a live account between Sep 8 and the end
of the run. The rule that matters is in [What may ship during the
run](#what-may-ship-during-the-run), and it has exactly one line: **if it
changes what the bot would trade, it waits.**

The ranked backlog itself lives in a local, deliberately un-committed survey
(it names and critiques other teams' submissions, which does not belong in a
public repo). Item numbers below refer to it.

## Why a baseline at all

We finished the judged week at +5.10% over four sessions and 15 round trips.
That is not enough to distinguish edge from exposure, and we have no backtest,
so at this moment **every guard in `bot/risk.py` is there because someone
argued for it, and not one has ever been retired for failing a test.** The
backlog's top items are all guards. Adding more guards to a system that cannot
measure guards is how you end up with a very careful bot and no idea which part
of the care is doing anything.

So: a reference period first, then changes against it.

## The configuration, as of Sep 8

Verified 2026-09-07 by loading all three configs and diffing the *effective*
values, not by reading them:

| Pair | Differing keys |
|---|---|
| `official` vs `test` | **0** — including `strategy_notes` |
| `official` vs `mixed` | **0** numeric; prose only (`strategy_notes`, `instrument_note`) |

Shared by all three: `Qwen/Qwen3.8-Flash-Next`, `min_hold_minutes: 0`,
`early_exit_drawdown_pct: 25`, `research_tools_enabled: true`,
`daily_loss_cutoff_pct: 2.0`, `stop_loss_pct: 40`, `take_profit_pct: 60`,
`expiry_close_dte: 0`.

### `official` and `test` are a replicate pair, on purpose

This is the most useful property of the run and the easiest to accidentally
destroy. Two accounts, same model, same config, same prompt, same market —
whatever they *differ* by at the end of the period is the **noise floor**:
run-to-run variance with no variable changed.

That number is the thing every future A/B has to clear before it is allowed to
claim anything. Without it, the first config change that produces a 3%
divergence will get read as a result, and it may well be a coin flip. We have
never had this number.

`mixed` is the single-variable arm: identical knobs, different prompt prose.
Whatever it does differently is attributable to the prompt or to noise, and the
replicate pair is what tells you which.

**If you change a value on `test`, the replicate is over.** Say so in the
commit message, because a later reader will otherwise read the divergence as
signal.

## What this baseline is not

It is not a continuation of the judged week. Three variables moved at once
between Sep 4 and Sep 8:

1. one model across all three accounts (was: K3 on `test`),
2. research tools on everywhere (was: `official` without them),
3. the exit leash off everywhere — `min_hold_minutes: 0` (was: effectively
   30–40 minutes on `official` and `mixed`).

Any of those could move the numbers, and because they moved together **no
difference against the judged week can be attributed to any one of them.**
That is an acceptable price for a clean starting point, but it means judged-week
figures are not a control. Don't quote them as one.

One consequence worth naming: `research_tools_enabled: true` on every account
means `bot/citations.py::audit` is skipped fleet-wide, because it does not run
when research tools ran. The baseline's citation data will be thinner than the
judged week's, and that is a known hole (backlog item 30), not a surprise.

## What may ship during the run

The test is not "is it safe" — it is **"would it change what the bot trades?"**

| | Ships during the run | Waits |
|---|---|---|
| Journaling and measurement | ✅ recording more about a decision does not change it | |
| Assertions and startup checks | ✅ they either pass silently or refuse to start | |
| Docs, tests, `scripts/`, reports | ✅ not deployed to the trading path anyway | |
| Guards, gates, exit rules, sizing | | ❌ items 2, 3, 4, 15 |
| Config values on any live account | | ❌ ends the replicate pair |
| Prompt / `strategy_notes` | | ❌ the prompt *is* the strategy here |

Item **1** (fail-closed calibration manifest) is on the left-hand side, and it
is the only top-ten item that is: it changes no trading behaviour, so it cannot
contaminate the run. Ship it **report-only first** — log what is missing, exit
0 — and promote it to fail-closed once it has run clean for a few days. A
`SystemExit` from a false positive on day one would halt an account in the run
it was meant to protect.

Note that a deploy is still gated by the freeze (Mon–Fri 08:20–15:15 CT,
[market holidays excepted](operations#deploy)).

## Pre-registration

Backlog item **24** is the reproducibility bundle, and the cheap half of it
costs twenty minutes and should be done before the open:

- a stated **neutral hypothesis**, written down before the run rather than
  after it;
- a **fingerprint** of the three configs as they stand tonight (sha256 + byte
  count), so "was anything changed mid-run?" is answerable in one command;
- `notes.md` recording what we expected, so the write-up at the end is a
  comparison and not a narrative.

The neutral hypothesis for this run, stated in advance:

> The three accounts are drawn from the same distribution. `official` and
> `test` will differ only by noise. `mixed` will not separate from the pair by
> more than that noise. We do not expect the fleet to beat its judged-week
> return, and we are not trying to.

Writing that down is the whole discipline. If the run beats it, we learn
something; if we only decide what we expected after seeing the result, we
learn nothing and will not be able to tell the difference.

## What to collect while it runs

Cheap now, expensive to reconstruct later.

- **Fill quality** (items 12, 13): journal `order_type` and `limit_price` on
  `order_submitted`, plus the NBBO at send time. Today the journal cannot
  answer "did we rest a limit or pay the touch?" — reconstructing the judged
  week meant re-parsing `decision.raw` and pairing proposals to submissions by
  symbol and side. Four fields on one dict.
- **The candidate menu** — see the volatility section below.
- **Daily equity per account**, which `equity.jsonl` already gives us.

## Volatility: the one capability gap, and what it needs from this run

This is the largest thing the system does not see, and it is cheaper to close
than it looks — but only if the baseline records the right thing *now*.

**What we already have.** `bot/decide.py` computes implied volatility and the
full Greek set per candidate contract (Alpaca's own where available,
Black-Scholes-derived otherwise) and puts them in the prompt. So the claim "we
compute no volatility signal" needs a correction: we compute per-contract IV
every cycle and hand it to the model.

**What we do not have** is any *reference* for that number. IV of 34% is not
information; IV of 34% against a 20th-percentile 52-week range, or against
realised vol over the last 20 sessions, is. We have no IV rank, no IV-vs-RV
comparison, no term structure. So the model is told what the option's volatility
is and has nothing to judge it against — and we buy long premium, where paying
too much for volatility is the main way a directionally correct trade still
loses.

**The problem for this run.** The `decision` event stores `raw` (the model's
proposals) and call metadata. It does **not** store the candidate menu. So the
IV we computed at decision time is discarded every cycle, and the baseline will
accumulate *no* record of what volatility we were being offered.

That means a volatility signal could not be evaluated against this baseline
afterwards — we would have to run a whole new period to test it.

**So: journal the menu.** Record the per-contract IV and Greeks that went into
the prompt, or at minimum ATM IV per underlying per cycle. It gates nothing and
changes no decision — an *observer*, in the sense of backlog item 26 — so it is
on the ships-during-the-run side of the table above. It costs one journal call
and it is the difference between being able to ask "would an IV-rank filter
have helped?" in October and having to spend another month to find out.

This is the general shape of the gap the backlog note calls out: **the system
produces evidence and does not consume it.** IV is the clearest case — we
compute the number, show it to the model, and throw it away.

## Priorities after the run — the short version

The full ranked list is in the survey; this is the shape of it.

1. **Item 1 — fail-closed calibration manifest.** An afternoon. We have hit
   the failure it prevents twice in one week (`expiry_close_dte` defaulting
   silently, `early_exit_drawdown_pct` inert). It gates the guards below,
   because each of those adds a config key to a system that has already
   swallowed two. Report-only during the run, fail-closed after.
2. **Items 2, 3, 4 — the churn guards, as one PR.** Entry cooldown scoped to
   the prior exit reason, one direction per underlying per session, and an
   unconditional already-holding guard. They answer a real incident (Sep 3:
   sold QQQ at −4.8% and NVDA at −3.3%, bought both back within twenty
   minutes) and they let us **delete `min_hold_minutes`** — the blunt guard
   that blocks good exits — rather than stack a third layer on it. These are
   behavioural: they land after the baseline, not during it.
3. **Items 12–15 — execution.** Worth doing, not worth prioritising: measured
   spread cost for the judged week was about **$35 against $5,095 of P&L** on
   a strategy whose take-profit is +60%. Item 12 is an audit hole rather than
   a cost, so fold it into whichever PR touches the order path next.
4. **The volatility signal**, per the section above — the first capability
   addition rather than another guard, and the one that needs a decision made
   *before* the baseline ends.
5. **Items 24, 25, 26, 39 — the evaluation loop.** The reproducibility bundle,
   publishing negative results, components that earn their authority by
   surviving a study, and eventually a point-in-time backtest harness (~2
   weeks). These rank last by the backlog's own sort rule — which orders by
   "does it fix a failure we have evidence of hitting" — and that rule is
   structurally blind here, because an absent measurement never files an
   incident report. On the substance this is the biggest gap we have. This
   baseline is the first payment against it.
