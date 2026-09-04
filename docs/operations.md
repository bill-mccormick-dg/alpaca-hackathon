---
sidebar_position: 5
title: Operations
---

# Operations

The runbook: how to run the bot, stop it, see what it did, and change it safely
while it is live. Read [Strategy](strategy.md) for *why* it trades the way it
does; this page is the *how*.

Three names used throughout, defined once here:

- **CT 108** — the Proxmox LXC (a Linux container on our own hardware) that runs
  the bot around the clock. Cron drives it; nobody has to be awake. See
  [Architecture](architecture.md) for the full picture.
- **MCP** — Model Context Protocol. Alpaca ships an official MCP server, and all
  market data and orders go through it rather than through raw SDK calls.
- **Featherless** — the inference provider hosting the open-source model that
  makes the trading decision.

Everything is **paper trading**: simulated money on a real market feed, with
`ALPACA_PAPER_TRADE=true` hardcoded and no live-trading code path in the repo.

Everything runs from the repo root with the venv's Python (`./.venv/bin/python`
on CT 108 at `/opt/alpaca-hackathon`; `docker compose run --rm bot ...`
locally). Every entrypoint takes `--account <name>` (default **test**) and
`--config <file>` (default `config.yaml`).

## Commands

| Command | What it does |
|---|---|
| `run_cycle.py [--dry-run] [--force] [--verbose]` | One cycle: gates -> snapshot -> exits -> decide -> risk-check -> execute. `--dry-run` prints orders instead of sending; `--force` skips the market-open / trading-window / entry-cutoff gates; `--verbose` prints model output, research calls, usage and latency |
| `flatten.py [--expiring-only] [--halt]` | Close positions, verified against the broker. `--expiring-only` (the cron backstop) closes only contracts expiring within `eod_close_dte` days; on/after `final_flatten_date` it closes everything. `--halt` also trips the kill switch |
| `status.py [--json]` | Halt state, runtime overrides, account, positions, today's journal summary. Read-only |
| `override.py show \| set <key> <value> [--until] \| clear <key>\|--all` | Intraday config tweaks without a deploy (see Runtime overrides) |
| `trade_report.py [--days N] [--json]` | Round trips reconstructed from Alpaca's fills, exits classified, cuts by underlying / instrument / DTE / hour |
| `eod_review.py [--date] [--no-model] [--json]` | The end-of-day digest (see The daily loop) |
| `python -m unittest discover -s tests` | Credential-free tests |
| `scripts/verify_*.py` | Manual live checks (Alpaca connectivity, Featherless, snapshot, option-chain coverage) |
| `scripts/audit_citations.py --account <name> --day YYYY-MM-DD` | Offline: which prior figures the model quoted that day that its prompt never contained (#172). Journal only, no credentials |

## Named accounts

Every entrypoint takes `--account <name>`. Two names matter:

- **`official`** — the judged account (`PA3VS39Y5LE2`). Its equity is the score.
- **`test`** (and any other name) — a **challenger**: a separate paper account
  running a variant config against the same live market, so a change can be
  tried without touching the judged one.

### Where an account's credentials come from

Resolved in this order, first hit wins:

1. environment variables
2. `credentials.env` (official) or `credentials-<name>.env` on CT 108
3. *(non-official only)* the `accounts.<name>` block in a local `secrets.yaml`
4. *(non-official only)* the legacy `.env.<name>`, then `.env`

The judged account can therefore only ever resolve from step 1 or 2 — never
from a file on someone's laptop.

### Three guards on the judged account

| Guard | What it checks | What it does |
|---|---|---|
| Resolution order | is the account named `official`? | skips every local file — steps 3 and 4 do not apply |
| `secrets.yaml` contents | does the file contain an `official` entry *anywhere*? | rejects the whole file, for **every** account — so pasting the judged keys there fails loudly instead of trading it under another name |
| `bot/identity.py` | does the broker's own account number match the name asked for? | refuses a challenger that resolves to the judged account, or whose number cannot be read at all |

The third is the only one keyed on the *credentials* rather than on a
command-line string. Its policy is deliberately asymmetric: a challenger fails
closed, but the official account **warns and proceeds** when the number is
unreadable — a parsing regression must not be able to halt the judged account
mid-session.

### What each account keeps separate

Its own journal (`logs/journal-<name>.jsonl`; the official account keeps
`logs/journal.jsonl`), its own overrides file, and its own daily-loss halt file
— so a challenger breaching its cutoff never halts the official account.

## Halt files (under `logs/`, checked at the top of every cycle)

- `HALT` - **global** break-glass halt: stops *every* account. Written only by
  `flatten.py --halt --all-accounts`; deliberately unreachable from MQTT/Home
  Assistant. Nothing trades until you delete the file.
- `HALT_manual` (official) / `HALT_manual_<name>` (others) - that one account's
  kill switch, from `flatten.py --halt` or the Home Assistant button. Only that
  account stops.
- `HALT_<YYYY-MM-DD>` (official) / `HALT_<name>_<date>` (others) - daily-loss
  halt, written after a breach of `daily_loss_cutoff_pct` flattens the
  account. Expires with the day; delete it to resume early.

## The journal in a browser

The zero-setup way to watch the bot think, and the first thing to give a new
teammate or a judge: **https://bot.wpmccormick.pw**. Enter an email address,
paste the one-time PIN Cloudflare sends, and the journal starts streaming.
Sessions last six hours. The Access policy deliberately accepts any working
email rather than a list of addresses - a judge cannot be pre-registered.

What it shows, live: every cycle as it happens, the model's full reasoning, the
orders and the rule behind each rejection. Controls are checkboxes for the three
accounts, a toggle for tool-call and config chatter, and a date picker that
replays an earlier day. There is nothing else - no halt, no override, no
button that reaches the trading path at all.

`journal_viewer.py` tails `logs/journal*.jsonl` and pushes them over
server-sent events, bound to the LAN on :8300; `cloudflared` publishes that
one port. It holds no credentials.

### When it is broken

| Symptom | Where to look |
|---|---|
| 502 after a successful login | the unit, not the tunnel: `systemctl status alpaca-hackathon-journal-viewer` |
| The page loads but never updates | the unit is up but the source changed under it - see the restart note below |
| Login loop, or "restricted to members" | Cloudflare Access dashboard state, not this repo |
| Nothing loads at all | check Cloudflare's status page before debugging CT 108 |

The login-methods trap is worth naming because it cost an evening: if the
Access application is pinned to a single identity provider, the email
one-time-PIN option disappears and *everyone* is locked out, including the
person who owns the account. The fix is in the Cloudflare dashboard - turn
"Accept all available identity providers" back on.

**The viewer does not restart itself on deploy.** Unlike `mqtt_bridge.py` it has
no source watcher, and `deploy.yml` has no restart step for it, so a change to
`journal_viewer.py` sits on disk while the old code keeps serving. After any
deploy that touches it:

```sh
systemctl restart alpaca-hackathon-journal-viewer
```

Deploy, tunnel and Access mechanics live in homenetwork's `alpaca-hackathon`
and `cloudflared` roles; the Access login rules are dashboard state on purpose.
The unit is gated on `journal_viewer.py` existing in the checkout, so an older
deploy does not leave it flapping under `Restart=always`.

## Runtime overrides

Two config layers with explicit precedence: `config.yaml` (git) is the base;
the account's overrides file wins for an allowlisted set of knobs. That file is
`logs/overrides.yaml` for **official** and `logs/overrides-<account>.yaml` for
the others — the same naming as the journals, so a challenger's intraday tweak
can never leak into the judged account.

| Knob | Changes |
|---|---|
| `model` | which model makes the decision |
| `temperature`, `max_tokens` | sampling and answer length |
| `strategy_notes` | the tactics paragraph appended to the prompt — the main dial |
| `research_contracts_per_underlying` | how many contracts the model is shown |
| `option_strike_band_pct` | how far from spot the shown strikes reach — and, on SPY/QQQ, how many pages the chain fetch takes |
| `stop_loss_pct`, `take_profit_pct` | when `bot/exits.py` closes a position |
| `eod_close_dte` | how near expiry the end-of-day backstop closes |
| `min_hold_minutes`, `early_exit_drawdown_pct` | the churn guard: how long a position must be held before the model may sell it, and the drawdown that waives the wait |
| `review_model` | which model critiques the day in `eod_review.py` — computed from `review_model_preference` unless pinned |
| `predictions_enabled` | whether the Kalshi prior is fetched and shown to the model — a switch in HA |

### Two ways to set one, one way in

| Path | How | Recorded as |
|---|---|---|
| CLI | `override.py --account <name> set <key> <value> [--until]` / `clear <key>` / `--all` | `set_by: cli` |
| Home Assistant | the `select` and `number` entities on the dashboard | `set_by: mqtt` |

The dashboard is not a second implementation. Each HA entity is wired to
`<prefix>/config/set` with a `command_template` carrying
`{"account": ..., "key": ..., "value": ...}`, and `mqtt_bridge.py` hands that
straight to the same `overrides.set_override()` the CLI calls — same allowlist,
same range checks, same file, same expiry. A value HA cannot propose is a value
the CLI would have rejected. Validation failures are published to
`<prefix>/config/error` rather than applied silently.

Both paths journal `override_set` / `override_cleared` with `set_by`, so
"who turned this knob" is answerable afterwards:

```sh
grep '"override_set"' logs/journal.jsonl | tail -5
```

**And the dashboard shows what is actually running, not what was requested.**
After every cycle the bot publishes its effective config — the same payload as
the `config` journal event — retained on
`<prefix>/<account>/config/effective`, and the HA entities read their state
from that topic. So a knob that was rejected, expired, or overridden by
something else shows its real value on the next cycle rather than the one you
typed. The bridge also primes that topic at startup so the controls are never
blank. See [Home Assistant over MQTT](#home-assistant-over-mqtt) for the
transport.

### `review_model` is computed, not set

The one knob whose normal state is *unset*. `eod_review.py` asks a model that
did **not** trade the day for its advisory read, and
`bot/config.py::resolve_review_model()` picks it: the first entry of
`review_model_preference` that is not this account's own `model` and did not
appear in the day's journal (the digest's per-model rows), recomputed on every
call. `official` reviews on `moonshotai/Kimi-K2.6` (computed); `test` and
`mixed` pin `Qwen/Qwen3.8-Flash-Next`.

Recomputing rather than storing is what makes the property survive a dashboard
model swap — switch an account onto its reviewer and the reviewer moves, instead
of the account silently grading its own homework.

To see which model will actually run it, read the resolved value rather than the
config file — an unset key tells you nothing:

```sh
# on the trading host
grep '"event": "config"' logs/journal.jsonl | tail -1 | python3 -m json.tool | grep review_model
```

Every `config` journal event now carries `review_model`, and the same value is
published retained on `<prefix>/<account>/config/effective`, which is where the
dashboard's **Review model** selector reads its state. It is deliberately **not**
part of `config_hash`: it cannot affect trading, so it must not invalidate the
hash that attributes a P&L change to a config change.

To pin one instead of computing it:

```sh
override.py --account official set review_model moonshotai/Kimi-K2-Instruct
```

or pick it from the dashboard selector. Both expire at 16:00 ET like any other
override; clear it and the computed default returns.

**A pin is refused when it names a model that traded** (#218) — the account's
current `model`, or any model in the day's journal. The review falls through
to the preference list, the digest prints `review_model pin … ignored` above
the critique, and the `eod_review` journal event carries `review_pin_ignored`.
So changing an account's model is a one-key change even when its reviewer is
pinned: the pin cannot turn into a self-review. The review runs at 16:05 ET,
after overrides expire, which is why the comparison is against the journal
and not against the config at that moment.

**Hard risk caps are git-only on purpose** — position size, position count, the
DTE window, the whitelist and the daily-loss cutoff cannot be raised at runtime
by anything, including the Home Assistant dashboard. Overrides **expire at
16:00 ET** (the market close) unless `--until` is given, so intraday tweaks
never outlive the day and tomorrow always starts from git.
Every cycle journals a `config` event with the effective values, a config
hash and the active overrides.

## Journal

`logs/journal[-<account>].jsonl` — one JSON record per event, with an Eastern
`ts`. It is the single "something happened" record: every report, the
dashboard and the hourly email are all built from it, so if it is not
journaled it did not happen.

| Event | Written when |
|---|---|
| `cycle_start`, `cycle_end` | each cycle opens / closes, with equity and position count; `cycle_start` also carries `chain_coverage` — per underlying, how many contracts and pages the option chain fetch took, the furthest DTE it reached, and whether it hit the page cap (`truncated`) |
| `config` | the effective config for that cycle: values, a hash, any active overrides, and the resolved `review_model` |
| `decision` | the model answered — raw output, model, token usage, latency, finish reason, reasoning head, tool calls, the size of the learning and positions blocks it was shown (`learning_chars`, `positions_block_chars`), and `citations`: how many prior figures its reasons quoted, which appear nowhere in the prior it was given (`unsupported`) and which belong to a different underlying (`misattributed`) (#172; `scripts/audit_citations.py` rebuilds it for older days) |
| `tool_call` | the model used one of its four read-only research tools |
| `order_submitted` / `order_rejected` / `order_error` / `dry_run` | an order's outcome — rejections carry **the rule that rejected them**; a sell that code resolved from a neighbouring strike onto the held contract carries `resolved_from` with the symbol the model wrote (#170) |
| `order_canceled` | one of the bot's own entry buys was still resting from an earlier cycle and the new cycle cancelled it (`ok`, the broker's text when it could not) — #171 |
| `decide_retry` | a transient model failure was retried inside the cycle |
| `identity_refused` / `identity_unverified` | the broker's account number did not match the account asked for |
| `daily_loss_halt`, `daily_loss_flatten`, `flatten`, `manual_halt` | trading stopped, and why. A flatten's `closed[]` lists the contracts it closed; the email, its trades CSV and the HA trade card expand that into one `sell` row per contract (#221) |
| `override_set` / `override_cleared` | a runtime knob changed |
| `error` | anything else, with where and the detail — `where: open_orders` means the resting-order lookup failed and that cycle sized on holdings alone |
| `predictions` | the Kalshi prior the model was handed that cycle — the numbers **and** whether they were withheld |
| `eod_review` | the end-of-day digest ran, and `review_model` says which model wrote the critique — check it is not the account's own `model` (#177) |

`logs/` is git-ignored and excluded from the deploy rsync, so state on CT 108
survives redeploys.

### Asking the journal what actually happened

The file names differ by account: `journal.jsonl` is the **official** account,
`journal-test.jsonl` and `journal-mixed.jsonl` the other two.

Did the model get a second opinion on the last cycle, and if not, why not:

```sh
ssh root@<ct108> "grep '\"predictions\"' /opt/alpaca-hackathon/logs/journal.jsonl | tail -1"
```

`"suppressed": null` means the prior was in the prompt. A string
(`"thin: volume 91.9 < 250.0"`) means it was fetched, journalled and withheld,
and names the gate that withheld it. **No record at all** means no prior was
available that cycle — the feed failed, or `predictions_enabled` is off.

This is a different question from the one
[`scripts/verify_predictions.py`](strategy.md#a-worked-example) answers. The
script tells you what the prior looks like *right now*; the journal tells you
what the model was handed *at the moment it decided*. Only the second one
explains a trade.

Two more worth knowing:

```sh
# why was an order refused - the record carries the rule that refused it
grep '"order_rejected"' logs/journal.jsonl | tail -5

# what the model actually said, most recent first
grep '"decision"' logs/journal.jsonl | tail -1

# did the option menu actually reach the configured DTE window this cycle
grep '"cycle_start"' logs/journal.jsonl | tail -1 | jq .chain_coverage
```

Read `chain_coverage` per underlying: `max_dte` should sit near
`max_days_to_expiration` (45); far below it with `truncated: true` means the
fetch hit `CHAIN_MAX_PAGES` (`bot/snapshot.py`) — raise the cap or narrow
`option_strike_band_pct`. Far below it with `truncated: false` just means no
listed expiry that far out inside the band. Before #158 this was invisible:
SPY/QQQ silently stopped at 3 DTE, and an in-window proposal the model found
through its research tools was refused as unpriceable.

## Home Assistant over MQTT

Three terms used below: a **retained** MQTT message is one the broker keeps and
replays to anyone who subscribes later, so a dashboard opened at 3pm shows the
day so far rather than an empty card. **MQTT discovery** is Home Assistant's
convention for a device announcing its own entities, so nothing has to be
configured by hand on the HA side. **Lovelace** is Home Assistant's dashboard
format.

Publish-only from the bot, fully decoupled: `bot/mqtt.py` hangs off the
journal's `log()`, so every journaled event is also published to
`<prefix>/<account>/event/<event>`, and a few **retained** state topics feed
Home Assistant sensors via MQTT discovery (`equity`, `day_pnl`, `positions`,
`halt`, `last_decision`), plus `<prefix>/<account>/config/effective` after
every cycle. No broker configured (`MQTT_HOST` unset) or broker down -> no-op;
a publish can never delay or fail a cycle. `config.yaml` -> `mqtt:` block;
broker host/credentials from `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`,
`MQTT_PASSWORD` in the same env files that carry the API keys.

**Dashboard**: [`ansible/`](../ansible/) (host-agnostic - no hardcoded network,
path or container name) deploys a Lovelace dashboard - each account's State,
then Day P&L history, then a Controls row (kill switch + tunable knobs) -
driven entirely by the MQTT topics above and below. `cd ansible && cp
inventory.example.ini inventory.ini` (fill in your HA host) `&&
ansible-playbook site.yml`. See `ansible/README.md`.

**Remote access, on request.** The dashboard lives on the home LAN and is not
public. It can be published the same way the viewer is - through the
`cloudflared` connector on CT 108 as **https://ha.wpmccormick.pw**, with a
Cloudflare Access policy in front - but with a different policy from the
viewer's: an explicit allow-list of email addresses (added on request), signed
in through Google rather than a one-time PIN. Home Assistant's own login still
applies behind Access, and Access has no way to reach the kill switch or the
knobs on its own. The hostname, the route and the policy are Zero Trust
dashboard state; the Home Assistant side (trusting the connector as a reverse
proxy) is in the `homenetwork` repo's `homeassistant-setup` role. Ask, and an
address is added; it can be removed the same way.

Inbound, both handled by `mqtt_bridge.py` (long-running):

- `<prefix>/config/set` - applies `{"account","key","value","until"?}` through
  the same `set_override()` the CLI uses - allowlisted keys, validated,
  expiring at the close - then republishes the effective config. Errors go to
  `<prefix>/<account>/config/error`. The dashboard's Controls row is this:
  one HA `number`/`text` entity per knob, per account, each publishing here
  via a `command_template` - see `mqtt_bridge.py::discovery_payloads()`.
- `<prefix>/<account>/command/halt` - the kill switch (payload must be
  exactly `HALT`). Reuses `flatten.py`'s own `run()` to flatten **that
  account's** positions and halt **that account only**
  (`bot/risk.py::RiskManager.manual_halt_file`). The global "halt everything"
  is deliberately not reachable from here - it is CLI-only
  (`flatten.py --halt --all-accounts`), so no dashboard tap can stop the
  judging account during the scoring window. Resuming (deleting the halt
  file) likewise stays CLI-only, never exposed to HA.

HA automation ideas still open: a light that goes green on `order_submitted`
and red on `daily_loss_halt` / `manual_halt`.

## Deploy safety during the scoring week

`.github/workflows/deploy.yml` has two independent guards so writing up the
project can never disturb the trading host:

1. **`paths-ignore`** - a push touching only `docs/`, `docs-site/`,
   `submission/`, `ansible/`, `tests/`, any `*.md`, `LICENSE` or
   `.gitignore` does not start a deploy at all. (The ansible role is applied
   from a workstation, never by CI.)
2. **A market-hours freeze** - a push that changes **trading code** is
   refused Mon-Fri 08:20-15:15 CT, covering every scheduled cron run with a
   margin, so live behaviour cannot change mid-session and no cycle can
   start against a half-synced tree. Outside those hours it deploys
   normally, which is what the daily loop needs (EOD review -> config change
   -> PR -> deploy before the next open).

Trading code is `run_cycle.py`, `flatten.py`, `eod_review.py`,
`mqtt_bridge.py`, anything under `bot/`, `config.yaml`, `config-test.yaml`,
anything under `config-variants/` and `requirements.txt`. `mqtt_bridge.py` is on that list deliberately: it
cannot change strategy, but it holds the kill switch and calls
`flatten.run()`, so a bad bridge deploy can cancel orders and close
positions.

The freeze is a hard job failure, not a skip - a red X on `main` is the
signal that `main` and the trading host have diverged. Re-run the job after
the close to apply it. If the changed-file list cannot be determined, it
fails closed rather than assuming the push is docs-only.

The bridge restart is also skipped while any halt file exists, so CI cannot
kill a kill-switch flatten mid-flight; `mqtt_bridge.py`'s own source watcher
restarts it once idle.

### Finding out what the host is actually running

Read `/opt/alpaca-hackathon/DEPLOYED` - sha, subject line and timestamp,
written by the deploy job itself.

**Do not use `git log` in that directory.** It answers a different question.
Two channels write `/opt/alpaca-hackathon`: Ansible clones it and owns the
`.git`, while CI rsyncs files in with `--exclude='.git'`. The checkout's HEAD
therefore stays pinned to whatever Ansible last checked out while the files
move on, so `git log` reports a stale commit and `git status` shows a pile of
phantom modifications. Both are working as designed and neither tells you
what is running.

The same split is why a change under `ansible/` never reaches anything on its
own: `paths-ignore` excludes it from CI on purpose, so those roles only take
effect when someone runs the playbook. That is easy to forget - the dashboard
sat pointing at a `text.` entity that had already been replaced by a
`select.`, so every controls card read "Entity not found" while the template
in git was correct the whole time. After changing anything under `ansible/`:

```sh
cd ansible && ansible-playbook -i inventory.ini site.yml --check --diff   # confirm
cd ansible && ansible-playbook -i inventory.ini site.yml                  # apply
```

## CT 108 cron (Central time)

```
*/10 8-14 * * 1-5   run_cycle.py --account official
*/10 8-14 * * 1-5   run_cycle.py --account test --config config-test.yaml
50 14 * * 1-5       flatten.py --expiring-only --account <each>
5 15 * * 1-5        eod_review.py --account <each>
```

Managed by Ansible in the `homenetwork` repo; logs in `logs/cron-<account>.log`.

## The experiment farm (local, `docker compose --profile farm`)

Current variants:

| Variant | Changes vs `config.yaml` | Question it answers |
|---|---|---|
| `kimi26` | newer model, research tools + learning, no Kalshi prior | is the newer model worth it? |
| `mixed` | **only** `strategy_notes` — stock and options as peers rather than options-first | which instrument should the agent reach for? |

`mixed` is deliberately a single-variable change: same model, same caps, same
research settings. Anything else differing from `config.yaml` is a bug.


The bot is stateless per cycle, so scaling *experiments* is trivial: one
container per variant, each with its own config (`config-variants/<name>.yaml`),
its own **test** paper account (an `accounts.<name>` block in `secrets.yaml`)
and its own journal. `farm.py`
does inside the container what cron does on CT 108 - a cycle every 10 minutes
in market hours, the expiring-only flatten at 15:50 ET, `eod_review` at
16:05 ET - by running the same entrypoints as subprocesses.

```
docker compose --profile farm up -d                                   # all variants, detached
docker compose --profile farm up bot-kimi26                           # one, foreground
docker compose run --rm bot farm.py --account kimi26 --config config-variants/kimi26.yaml --once   # smoke test (dry-run)
```

Add a variant: a `config-variants/<name>.yaml` (copy an existing one),
an `accounts.<name>` block in `secrets.yaml`, and a `bot-<name>` service in
`docker-compose.yml`. Compare variants with `eod_review.py --account <name>`
or `trade_report.py --account <name>`; promote a winner by copying its values
into `config.yaml` via a PR. Nothing here can touch the official account.

## Official-account safety

`run_cycle.py` and `flatten.py` refuse `--account official` before Mon Aug 31
9:30 AM ET unless `--dry-run` - hardcoded, not configurable. Paper-only
throughout: `ALPACA_PAPER_TRADE=true` is hardcoded in `bot/alpaca_mcp.py` and
no live-trading code path exists. The model never gets an order-placing tool;
every order funnels through `bot/execute.py::place_proposal()` ->
`bot/risk.py::check_order()`.
