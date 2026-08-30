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
| `scripts/verify_*.py` | Manual live checks (Alpaca connectivity, Featherless, snapshot) |

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

## Runtime overrides

Two config layers with explicit precedence: `config.yaml` (git) is the base;
`logs/overrides-<account>.yaml` wins for an allowlisted set of knobs:

| Knob | Changes |
|---|---|
| `model` | which model makes the decision |
| `temperature`, `max_tokens` | sampling and answer length |
| `strategy_notes` | the tactics paragraph appended to the prompt — the main dial |
| `research_contracts_per_underlying` | how many contracts the model is shown |
| `option_strike_band_pct` | how far from spot the shown strikes reach |
| `stop_loss_pct`, `take_profit_pct` | when `bot/exits.py` closes a position |
| `eod_close_dte` | how near expiry the end-of-day backstop closes |

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
| `cycle_start`, `cycle_end` | each cycle opens / closes, with equity and position count |
| `config` | the effective config for that cycle: values, a hash, and any active overrides |
| `decision` | the model answered — raw output, model, token usage, latency, finish reason, reasoning head, tool calls |
| `tool_call` | the model used one of its four read-only research tools |
| `order_submitted` / `order_rejected` / `order_error` / `dry_run` | an order's outcome — rejections carry **the rule that rejected them** |
| `decide_retry` | a transient model failure was retried inside the cycle |
| `identity_refused` / `identity_unverified` | the broker's account number did not match the account asked for |
| `daily_loss_halt`, `daily_loss_flatten`, `flatten`, `manual_halt` | trading stopped, and why |
| `override_set` / `override_cleared` | a runtime knob changed |
| `error` | anything else, with where and the detail |
| `eod_review` | the end-of-day digest ran |

`logs/` is git-ignored and excluded from the deploy rsync, so state on CT 108
survives redeploys.

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
`mqtt_bridge.py`, anything under `bot/`, `config.yaml`, `config-test.yaml`
and `requirements.txt`. `mqtt_bridge.py` is on that list deliberately: it
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

## CT 108 cron (Central time)

```
*/10 8-14 * * 1-5   run_cycle.py --account official
*/10 8-14 * * 1-5   run_cycle.py --account test --config config-test.yaml
50 14 * * 1-5       flatten.py --expiring-only --account <each>
5 15 * * 1-5        eod_review.py --account <each>
```

Managed by Ansible in the `homenetwork` repo; logs in `logs/cron-<account>.log`.

## The experiment farm (local, `docker compose --profile farm`)

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
