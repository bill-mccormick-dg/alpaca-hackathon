---
sidebar_position: 6
title: The daily loop
---

# The daily loop

What happens on a trading day, and what we do at the close. All times Eastern.

## During the day (automatic)

| When | What |
|---|---|
| 09:30 | Cron cycles start (`*/10` from 09:00, but the bot gates itself until the window opens) |
| 09:45 - 15:15 | New entries allowed. Every 10 min per account: exits first, then snapshot -> (research) -> decide -> risk -> execute |
| any cycle | A position at its expiry day, or past -40% / +60% of entry premium, is closed by code before the model is consulted |
| any cycle | Daily loss >= 2% of start-of-day equity -> flatten everything, halt for the day |
| 15:15 - 15:45 | Sells only |
| 15:50 | `flatten.py --expiring-only`: close contracts expiring within 1 day; hold the rest overnight under the stops |
| 16:05 | `eod_review.py` per account -> `logs/eod/<date>-<account>.md`, `logs/equity.jsonl`, and an advisory read from a model that did **not** trade the day |

Everything in that table is **cron on CT 108**, installed by the
`alpaca-hackathon` Ansible role - nothing waits on a person being awake. Each
account gets its own entry and its own log, so the three stay separable:

```
*/10 8-14 * * 1-5   run_cycle.py    --account <name>   # cycles
50 14 * * 1-5       flatten.py      --expiring-only    # the backstop
5 15 * * 1-5        eod_review.py   --account <name>   # the digest
0 9-14 * * 1-5      mail_report.py  --account <name>   # hourly email (quiet hours suppressed)
0 15 * * 1-5        mail_report.py  --account <name> --force  # the wrap-up always sends
```

(Cron runs in **Central** time - the container's timezone - so 15:05 CT is the
16:05 ET row above. Output goes to `logs/cron-<account>.log`.)

To *watch* this loop happen rather than read about it, open the live journal
at [bot.wpmccormick.pw](https://bot.wpmccormick.pw) - every cycle, prior,
order and flatten below streams there as it lands
([Operations](operations.md#the-journal-in-a-browser)).

`eod_review.py` is deliberately last: market close is 16:00 ET and the
expiring-only backstop runs at 15:50, so by 16:05 the session is finished, the
sweep has happened and the equity figure is final. It places no orders - there
is no order-submitting call in it at all - so it is safe to run by hand at any
time, as often as you like:

```sh
ssh root@<ct108> 'cd /opt/alpaca-hackathon && \
  .venv/bin/python eod_review.py --account official'
```

Add `--date YYYY-MM-DD` to re-run an earlier day, and `--no-model` to skip the
advisory read (faster, and it works with no Featherless key - useful when you
only want the numbers).

Judging is on **equity as of EOD Thursday Sep 3**. Thursday gets the normal
expiring-only backstop (selling healthy positions would only pay the spread).
**Friday Sep 4** (`final_flatten_date`): no new entries; the backstop closes
everything.

**Alpaca says this two ways, and they are not the same.** The timeline says
equity "as of EOD Thursday Sep 3rd"; the FAQ says the window "ends at 9:30
a.m. ET on Friday, September 4, when a snapshot of total account equity will
be taken" (both quoted in
[alpaca-official-guidelines.md](alpaca-official-guidelines.md)). Under the
first, Thursday's close is the score and overnight positions are irrelevant;
under the second, whatever is held overnight is marked at Friday's open, gap
included. Going into Fri Sep 4 the judged account held SPY 771C and NVDA 230C
— about 8% of equity — so the difference was real, not theoretical. Nothing in
the rules requires stopping the agent, and `trade_start` (09:45 ET) is after
the 9:30 snapshot either way, so the bot cannot trade into it; trading after
09:30 ET Friday explicitly does not count. If a future event repeats this
wording, decide *before* Thursday's close whether to hold overnight.

## At the close (human, ~15 minutes)

1. Read all three digests: `logs/eod/<date>-official.md`, `<date>-test.md` and
   `<date>-mixed.md` (on CT 108, or `eod_review.py --account X --date <date>
   --no-model` anywhere with the journal). Look at, in order:
   - equity and the round trips: **how did trades end?** stops vs take-profits
     vs expiry vs model sells vs flatten;
   - **rejections by rule** - a rule refusing the same idea all day is a
     prompt or config problem, not bad luck;
   - errors, truncated outputs, latency;
   - the one recommended change (advisory). It is written by a model that did
     **not** trade the day (see
     [the critique](strategy.md#the-critique-comes-from-a-model-that-did-not-trade)),
     so treat it as a second opinion rather than the agent explaining itself —
     it is most useful when it disagrees with what the day's decisions implied.
     It is advice, not an instruction: you decide whether it becomes an override
     or a config PR, and often the answer is neither.
2. Compare the three. `test` is the challenger - different model, research
   tools and the learning loop; `mixed` differs from official by exactly one
   key, stock and options as peers rather than options-first. If a variant's
   feature clearly helped, promote it.
3. Decide **one** change. Two ways to apply it:
   - same-day / experimental: `override.py --account <name> set <key> <value>`
     (expires 16:00 ET; `--until` to extend);
   - durable: edit `config.yaml` / `config-test.yaml` / `strategy_notes`, open
     a PR, merge when green - it deploys to CT 108 before the next open.
4. Post a build-in-public update if there is something worth saying
   (`submission/social_posts.md`).

## Emergency controls

- `flatten.py --account official --halt` - close everything, verified, and stop
  the official account until `logs/HALT_manual` is deleted. Add `--all-accounts`
  (writes `logs/HALT`) to stop every account instead.
- `status.py --account official` - what is held, what is halted, what today
  looked like.
- `override.py --account official clear --all` - back to git config.

## Thursday close / Friday morning

- Thu 16:05: the official digest is the results section of the write-up
  (`submission/WRITEUP.md`) - fill it.
- Fri 09:30: snapshot taken by Alpaca. Friday's cron makes no new entries and
  flattens everything at 15:50. Flip the repo public, submit before 10:00 CDT
  (`submission/METADATA.md` pre-flight list).
