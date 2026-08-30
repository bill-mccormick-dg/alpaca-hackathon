---
sidebar_position: 5
title: Operations
---

# Operations

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

`official` reads `credentials.env` on CT 108 and *only* there. Any other name
reads `credentials-<name>.env` on CT 108, else a local `.env.<name>`, else
`.env`. Each account has its own journal (`logs/journal-<name>.jsonl`; the
official account keeps `logs/journal.jsonl`), its own overrides file and its
own daily-loss halt file - a challenger breaching its cutoff never halts the
official account.

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
`logs/overrides-<account>.yaml` wins for an allowlisted set of knobs -
`model`, `temperature`, `max_tokens`, `strategy_notes`,
`research_contracts_per_underlying`, `option_strike_band_pct`,
`stop_loss_pct`, `take_profit_pct`, `eod_close_dte`. Hard risk caps are
git-only on purpose. Overrides **expire at 16:00 ET** unless `--until` is
given, so intraday tweaks never outlive the day and tomorrow starts from git.
Every cycle journals a `config` event with the effective values, a config
hash and the active overrides.

## Journal

`logs/journal[-<account>].jsonl`, one JSON record per event, Eastern `ts`:
`cycle_start`, `config`, `decision` (raw output, model, usage, latency,
finish reason, reasoning head, tool calls), `tool_call`, `order_submitted` /
`order_rejected` (with the rule) / `order_error` / `dry_run`,
`daily_loss_halt`, `daily_loss_flatten`, `flatten`, `manual_halt`,
`override_set` / `override_cleared`, `error` (where + detail), `eod_review`,
`cycle_end`. `logs/` is git-ignored and excluded from the deploy rsync, so
state on CT 108 survives redeploys.

## Home Assistant over MQTT

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
its own **test** paper account (`.env.<name>`) and its own journal. `farm.py`
does inside the container what cron does on CT 108 - a cycle every 10 minutes
in market hours, the expiring-only flatten at 15:50 ET, `eod_review` at
16:05 ET - by running the same entrypoints as subprocesses.

```
docker compose --profile farm up -d                                   # all variants, detached
docker compose --profile farm up bot-kimi26                           # one, foreground
docker compose run --rm bot farm.py --account kimi26 --config config-variants/kimi26.yaml --once   # smoke test (dry-run)
```

Add a variant: a `config-variants/<name>.yaml` (copy an existing one),
a `.env.<name>` with that account's keys, and a `bot-<name>` service in
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
