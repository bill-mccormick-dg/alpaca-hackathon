---
sidebar_position: 7
title: The baseline run
---

# The baseline run

Starting **Wed Sep 9, 2026**, on three **new** paper accounts. The competition
is over; this is the run that establishes what the current system does when
nobody is steering it, so the backlog can be implemented against a number
instead of an argument.

Read this before changing anything on a live account between Sep 9 and the end
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

## The lineup

| Account | Config | Role |
|---|---|---|
| `base_a` | `config.yaml` | replicate pair |
| `base_b` | `config.yaml` | replicate pair |
| `base_mixed` | `config-variants/mixed.yaml` | single-variable arm (prose only) |

All three are **new paper accounts opened with identical starting equity**.
The judged three - `official`, `test`, `mixed` - are parked for the duration of
judging and do not open new positions.

### How the judged three are parked

Two independent mechanisms, deliberately:

| | Where | Effect |
|---|---|---|
| `alpaca_hackathon_judged_accounts_enabled: false` | homenetwork Ansible role | cron never schedules them |
| `parked_accounts: [official, test, mixed]` | all three config files | `run_cycle` refuses even if a cron line survives |

The second exists because the first only takes effect when someone runs the
playbook, and the change was needed the same evening. It is also the more
durable of the two: it is in git, reviewed and tested, rather than in the state
of one host.

**Parking stops new entries, not exits.** The gate sits after the exit block
and before the model call, and `tests/test_parked_accounts.py` asserts that
placement rather than trusting the comment. A park that skipped exits too would
leave a contract to run into expiration with `expiry_close_dte` unable to fire,
and an assignment moves the judged equity far more than trading would. Parking
means *take no new risk*, not *go inert*. Running before `decide()` also saves
the model call.

It is keyed on the **account name**, not on config, because `base_a`/`base_b`
share `config.yaml` with `official` and `base_mixed` shares
`config-variants/mixed.yaml` with `mixed`. A config-level switch would park the
new accounts along with the old ones. `parked_accounts` is in `TRACKED_KEYS`,
so a flat day is attributable to the park in the cycle's own `config` event
rather than being misread as the strategy declining to trade.

To bring them back after judging: empty `parked_accounts` **and** flip
`alpaca_hackathon_judged_accounts_enabled`. Both, or they stay parked.

### Why new accounts rather than the judged ones

Two reasons, and the second was nearly missed.

**The judged accounts had drifted apart in equity, and position sizing is
absolute.** `max_position_usd: 5000` and `max_contracts_per_order: 10` do not
scale with account size. At the Sep 3 settled closes a $5,000 position was
4.76% of `official` ($105,095.51) but 5.38% of `test` ($92,938.91) - so `test`
ran **13% more levered** in percentage terms. Two accounts taking identical
trades would have posted systematically different returns, and the 2% daily
halt would have fired at $2,102 on one and $1,859 on the other, which is a
behavioural fork no analysis can undo. A "replicate pair" with that in it is
not a replicate pair.

**`submission/METADATA.md` tells judges to pull `PA3VS39Y5LE2` and see
$105,095.51.** Resuming trading on the judged account during judging week
makes that statement false. Scoring itself is locked - the organisers
snapshotted at 09:30 ET on Fri Sep 4 - so this was never a scoring risk, but
it is a credibility one, and it costs nothing to avoid.

New accounts solve both: equal equity makes the pair genuinely identical, and
the judged three sit frozen at exactly what the submission describes.

### The pair shares one config file

`base_a` and `base_b` both load `config.yaml`. Not two identical files - one
file. That makes "the pair is identical" true by construction rather than by
someone keeping two files in step, which is precisely how `official` and
`test` drifted (#269). `mqtt_bridge.py::ACCOUNT_CONFIG_PATH` now maps every
account explicitly, with no silent fallback, and a test asserts the pair
shares a file while the variant does not.

### How P&L is compared

Per-account percentages are recorded as before (`equity.jsonl` carries
`day_pnl_pct` against each account's own open, and `equity_curve.py` plots
percent change from each account's own baseline). With equal starting equity
those are directly comparable again.

Compare the pair on **dollar P&L and decision agreement**, not percentage
alone. Dollar P&L is exactly comparable because position caps are absolute;
decision agreement - did they propose the same contracts? - is independent of
equity entirely, and is the cleaner noise measure, since returns are dominated
by market direction rather than by the model.

## What this baseline is not

It is not a continuation of the judged week. It runs on different accounts,
and three variables moved at once between Sep 4 and Sep 9:

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
| `parked_accounts` | ✅ for a judged account | ❌ never add a baseline account mid-run |

Item **1** (fail-closed calibration manifest) is on the left-hand side, and it
is the only top-ten item that is: it changes no trading behaviour, so it cannot
contaminate the run. Ship it **report-only first** — log what is missing, exit
0 — and promote it to fail-closed once it has run clean for a few days. A
`SystemExit` from a false positive on day one would halt an account in the run
it was meant to protect.

Note that a deploy is still gated by the freeze (Mon–Fri 08:20–15:15 CT,
[market holidays excepted](operations#deploy)).

## Before the first cycle on Wednesday

In order. Nothing below can be done afterwards and still mean anything.

| | Done by | State |
|---|---|---|
| Three paper accounts, **identical starting equity** | operator | ⬜ |
| Six vault variables (`vault_alpaca_hackathon_{base_a,base_b,base_mixed}_{api,secret}_key`) | operator | ⬜ |
| `ansible-playbook site.yml -e @vault.yml --limit <ct108> --tags alpaca-hackathon` | operator | ⬜ |
| Confirm `/etc/cron.d/alpaca-hackathon` shows three `run_cycle` lines and no `--account official` | operator | ⬜ |
| Register `runs/baseline-2026-09-09/` with the real opening equity | either | ⬜ |
| Judged three cannot open new positions | — | ✅ `parked_accounts`, deployed 2026-09-07 |
| Candidate menu journaled | — | ✅ deployed 2026-09-07 |
| Holiday-aware deploy freeze | — | ✅ deployed 2026-09-07 |

The identical-equity item is the one with no recovery path. Everything else can
be fixed on Thursday; a lineup that started from three different balances
cannot, because the whole run is then measuring its own setup.

## Pre-registration

Backlog item **24**, the cheap half, done before the open:
`scripts/fingerprint_run.py` writes `runs/<name>/` and it is **committed to
git**, because the point of a pre-registration is that someone else can check
when it was written, and a gitignored file proves nothing about that.

```sh
python scripts/fingerprint_run.py --run baseline-2026-09-09 \
    --accounts base_a,base_b,base_mixed \
    --question "..." --hypothesis "..."     # once, before the run
python scripts/fingerprint_run.py --run baseline-2026-09-09 --check
                                            # any time after; exits 1 on drift
```

Four files, the shape the survey's `wheel` and `doa-parent` reads described:

| | |
|---|---|
| `strategy_spec.json` | the question, the accounts, and `"commitment": "rules fixed before results"` |
| `data_fingerprint.json` | sha256 + byte count of every config, every `bot/*.py`, the entrypoints and `requirements.txt`, plus the git SHA and dirty flag |
| `warnings.json` | what was already true and would weaken the run |
| `notes.md` | the same, for humans |

**Files are hashed as well as covered by the git SHA on purpose.** A hand-edit
on the trading host leaves the SHA untouched, which is exactly what an
undisclosed mid-run change looks like.

`--check` reports file changes and effective-config changes **separately**: a
`bot/*.py` edit and a config value change are different kinds of mid-run event
and deserve different sentences in a write-up. Run it before analysing
anything.

`warnings.json` records rather than fixes — a pre-registration that quietly
cleans up its own inputs is not recording the run that happened. It flags an
uncommitted tree, a **broken replicate pair** (any effective value differing
between `base_a` and `base_b`), a `base_mixed` arm differing by more than
prose, and a `final_flatten_date` already in the past.

**Not yet registered for this run.** The 2026-09-07 bundle is void: it
fingerprinted `official`/`test`/`mixed`, which will not run this baseline, and
its hypothesis claimed the pair would "differ only by noise" while the two
accounts carried a systematic 13% leverage difference. It stays on disk at
`runs/baseline-2026-09-08/`, marked `VOID.md`, rather than being deleted - a
pre-registration you can make disappear is not one, and "check git history" is
a weaker claim than "look in the directory".

`runs/baseline-2026-09-09/` therefore holds only a `notes.md` marker until the
accounts exist. Register it, with the real starting equity, **before the first
cycle on Wednesday**:

```sh
python scripts/fingerprint_run.py --run baseline-2026-09-09 \
    --accounts base_a,base_b,base_mixed \
    --question "..." --hypothesis "..."
```

The neutral hypothesis for this run, stated in advance:

> The three accounts are drawn from the same distribution. `base_a` and
> `base_b` start from identical equity, load the same config file, and will
> differ only by noise - that difference is the noise floor every later A/B
> must clear. `base_mixed` will not separate from the pair by more than that
> noise. We do not expect the fleet to beat the judged week's +5.10%, and we
> are not trying to.

This version is defensible in a way the 2026-09-07 one was not: with equal
starting equity and one shared config file, "differ only by noise" is a claim
about the model rather than an artefact of two accounts being sized
differently against fixed-dollar caps.

Writing that down is the whole discipline. If the run beats it, we learn
something; if we only decide what we expected after seeing the result, we
learn nothing and will not be able to tell the difference. If the result
contradicts it, that is the finding, and it gets published as it stands
(item 25).

## What to collect while it runs

Cheap now, expensive to reconstruct later.

- **Fill quality** (items 12, 13): journal `order_type` and `limit_price` on
  `order_submitted`, plus the NBBO at send time. Today the journal cannot
  answer "did we rest a limit or pay the touch?" — reconstructing the judged
  week meant re-parsing `decision.raw` and pairing proposals to submissions by
  symbol and side. Four fields on one dict.
- **The candidate menu** — ✅ **done**, shipping since 2026-09-07. See the
  volatility section below.
- **Daily equity per account**, which `equity.jsonl` already gives us. Record
  the three opening balances in the pre-registration too: with absolute
  position caps, the starting equity *is* the leverage, and the old bundle's
  silence on it is what let a 13% artefact through.

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

**Done, before the run started.** Every cycle now appends the menu it was shown
— each contract with bid/ask, spread, IV and Greeks — to
`logs/menu-<account>-<date>.jsonl`. It gates nothing and changes no decision:
an *observer*, in the sense of backlog item 26, which is why it was allowed to
land the night before the baseline rather than waiting for the end of it.

Three properties make it safe, and each is pinned by a test:

- **It is the menu the model actually saw.** `decide()` summarizes once, hands
  that dict to `build_prompt()`, and carries the same dict out on `Decision`.
  Recomputing it afterwards would look identical today and diverge the first
  time contract selection or the Greek fallback changes.
- **It stays out of the hot paths.** Not written through `journal.log()`: that
  republishes everything to the MQTT feed ahead of its allow-list, and
  `read_events("all")` parses the whole journal every cycle for the learning
  and holdings blocks. A record is ~16 KB against the journal's few hundred
  bytes.
- **It cannot cost a cycle.** A failed write returns `None` and is swallowed.

Volume is ~570 KB per account per day, ~1.7 MB/day across the three, so about
100 MB over a 60-session baseline. `dry_run` is on every row, so rehearsal
cycles can be dropped from a study. Read them with
`bot/journal.py::read_menus(account, day)`.

What this buys: in October, "would an IV-rank filter have helped?" is a query
against data we already have, instead of a proposal to spend another month
collecting it.

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
