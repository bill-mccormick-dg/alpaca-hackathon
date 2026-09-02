# CLAUDE.md — what a session should know before it acts

Read by Claude Code at the start of every session in this repo. Everything here
was learned the hard way during the hackathon week (Aug 28 – Sep 4, 2026); each
item names the mistake it prevents. Docs for humans are in `docs/`; this file is
for the agent.

## Verify against the authoritative source, not the last thing you read

Four wrong claims in one session, each from reading a stale or second-hand
source and asserting it. The fix in every case was one command.

| Question | Wrong source | Right source |
|---|---|---|
| What model is an account running *now*? | a `config` journal event from hours ago; an issue comment | `logs/overrides-<acct>.yaml` on CT 108, or `override.py --account X --config ... show` |
| Is cron installed? | `crontab -l` ("no crontab for root") | `/etc/cron.d/alpaca-hackathon` — the Ansible role installs there |
| Did an account trade or hold? | token count of the decision (`out=2` ≈ `[]`) | `order_submitted` / `order_rejected` events — those are the real names; there is no `order`, `exit`, or `rejected` event |
| What lineup do the docs describe? | the branch you wrote them on | `main` — rebase before merging docs that describe config; `tests/test_docs_lineup.py` now fails on drift |
| Which model produced today's errors? | the digest's `models` count (decisions only) | the `model` field on each `error` / `decide_retry` event; the digest's per-model rows since #231 |

When a live number matters (prior close, a price the model cited), get it from
Alpaca (`get_stock_snapshot` → `prevDailyBar.c`), not from the model's reason.

## Operational facts that bite

- **Deploy freeze**: Mon–Fri 08:20–15:15 CT for anything matching
  `^(run_cycle|flatten|eod_review|mqtt_bridge)\.py$|^bot/|^config(-test)?\.yaml$|^config-variants/|^requirements\.txt$`.
  Docs, tests, `scripts/`, `mail_report.py`, `bot/report.py` deploy any time.
  Merging frozen code inside the window fails the deploy job and leaves `main` undeployed — open the PR, merge after 15:15.
- **Runtime overrides expire 16:00 ET; the EOD review runs 15:05 CT = 16:05 ET.**
  A `review_model` override needs `--until 17:00` to be seen by the review; the
  self-review check itself no longer depends on it (#218).
- **`request_timeout_sec` is not overridable** (`bot/overrides.py::OVERRIDABLE_KEYS`).
  A dashboard model swap cannot carry its timeout. K3 needs ~140s; `config.yaml` has 60.
- **The reviewer is resolved against the journal's models, not the config** (#218):
  `review_choice(config, traded)` refuses a pin that names any model that traded and
  falls through to the preference list. The 16:00 ET override expiry no longer matters
  to the 16:05 review. `review_pin_ignored` on the `eod_review` event says when it fired.
- **`min_hold_minutes` is effectively 30–40**: cycles run on a 10-minute cron grid,
  the hold counts from the fill. Don't widen it (#222).
- **Every account's cron log is `logs/cron-<acct>.log`**; a cycle that exits at a
  gate ("outside trading window", `trade_start` 09:45 ET) writes there and nothing
  to the journal. An empty journal at 09:40 ET is not a fault.
- **The daily-loss cutoff (2%) flattens and halts; it is not overridable.**
  `test` hit it on Sep 1 and came within 0.4% on Sep 2.
- **Submission deadline: Fri Sep 4 2026 10:00 CDT** (seven days from the Fri Aug 28
  10:00 kickoff). Judged equity is EOD Thu Sep 3. Don't conflate them.

## What the checks do and do not see

- `scripts/verify_models.py --all-configs --rejected` — live Featherless catalog:
  tool_use, context, plan, price, **tier** (`/v1/models/{id}` → `availability.tier`;
  `unregistered` = cold start). Exits non-zero only for configured models.
- `bot/citations.py::audit` is **skipped whenever research tools ran** — so it never
  runs on `test`. `audit_exit_claims` always runs, but `spot_ref` is built only from
  the chain prior's `reference_close`, which `snapshot.py` fetches **only for SPY/QQQ**;
  NVDA/AAPL/MSFT can never get a direction check. And `DIRECTION_CLAIM` needs a verb —
  "227.55, below the prior close" doesn't match. Both gaps are on #226.
- The emailed trades CSV and body list flatten closes as `sell` rows with the flatten's
  name as `event` and no price (`closed[]` has none) — `trade_report.py` reads fills
  from Alpaca and is authoritative. Rejections are a separate `rejected-<day>.csv`.
- `scripts/fit_slides.py` needs Chrome; a re-cut PDF of an unchanged deck differs by
  exactly 8 bytes (CreationDate/ModDate) — don't commit that.

## How the operator wants to work

- State a concern once, with evidence, then execute the request in full. Do not
  re-ask. Read-only investigation needs no permission; live account changes do.
- Fold small fixes into the open issue rather than opening a new PR per finding.
- Guards should not block a well-reasoned exit or re-entry. The funnel sees
  early-vs-late, not good-vs-bad; put the model's own prior statements in the prompt
  (#225) and score the reasoning (#226) instead of lengthening holds.
- When a reviewer's suggestion was pushed back on with reasons, use the pushed-back
  version.
- Report outcomes plainly, including your own errors, in one sentence each.

## Open threads (Sep 2, 2026)

- #216 Part 2 — replay journaled prompts, score constraint adherence (209 decisions exist); reopened Sep 2, scheduled Thu Sep 3 during the freeze
- #222 — model exits: 11 attempts / 6 blocked / 5 executed on Sep 2; two sound, three not
- #226 — reasoning-scoring levers, in order
- PRs behind the freeze: #217 (K3 nudge), #225 (CLOSED TODAY block)

## Where things are

- CT 108: `root@192.168.212.10`, repo at `/opt/alpaca-hackathon`, journals in `logs/`.
- Local Python is `.venv/bin/python`; system `python3` lacks `yaml`/`httpx`.
- The other Claude session may be working the same repo — check `git log main` before
  branching, and never branch from a local `main` carrying unpushed commits.
