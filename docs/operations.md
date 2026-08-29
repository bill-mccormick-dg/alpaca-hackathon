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

- `HALT` - manual kill switch, created by `flatten.py --halt`, **global**.
  Nothing trades until you delete the file.
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

## CT 108 cron (Central time)

```
*/10 8-14 * * 1-5   run_cycle.py --account official
*/10 8-14 * * 1-5   run_cycle.py --account test --config config-test.yaml
50 14 * * 1-5       flatten.py --expiring-only --account <each>
5 15 * * 1-5        eod_review.py --account <each>
```

Managed by Ansible in the `homenetwork` repo; logs in `logs/cron-<account>.log`.

## Official-account safety

`run_cycle.py` and `flatten.py` refuse `--account official` before Mon Aug 31
9:30 AM ET unless `--dry-run` - hardcoded, not configurable. Paper-only
throughout: `ALPACA_PAPER_TRADE=true` is hardcoded in `bot/alpaca_mcp.py` and
no live-trading code path exists. The model never gets an order-placing tool;
every order funnels through `bot/execute.py::place_proposal()` ->
`bot/risk.py::check_order()`.
