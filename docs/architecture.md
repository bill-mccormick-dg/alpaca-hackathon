---
sidebar_position: 3
title: Architecture
---

# Architecture

Modelled on a working Claude-driven paper day-trading bot (`alpaca-trader`):
**deterministic snapshot -> LLM judgment -> deterministic risk/execute**, where
risk checks never negotiate and every order-placing path funnels through one
guardrail function. Two things differ, both hackathon requirements: all market
data and orders go through **Alpaca's official MCP server**, and the model is
an open-source one on **Featherless.ai** instead of the Claude CLI.

## One cycle

`run_cycle.py`, from cron every 10 minutes in market hours:

```
gates          halt files? official window? market open? trading window?
snapshot       account, positions, clock, option chains -> derived Greeks, (Kalshi prior)
exits          expiry / stop-loss / take-profit  -> sell proposals -> execute; cycle ends if any fired
final day?     no new entries on/after final_flatten_date
learning       RECENT OUTCOMES block from fills + journal (challenger)
decide         prompt -> model (optionally: bounded research tool loop) -> JSON proposals
risk gate      every proposal through check_order(); rejected ones journaled with the rule
execute        our code calls place_stock_order / place_option_order via MCP
journal        cycle_start, config, decision, tool_call, order_*, cycle_end
```

## Modules

| Module | Role | Notes |
|---|---|---|
| `bot/alpaca_mcp.py` | Thin async client around `alpaca-mcp-server` (stdio) | `ALPACA_PAPER_TRADE=true` hardcoded; retries transient connect errors only |
| `bot/credentials.py` | Named accounts -> credential files | `official` never reads a local `.env` |
| `bot/config.py` + `bot/overrides.py` | `config.yaml` + runtime overrides (allowlisted, expire 16:00 ET) | `config_provenance()` is what the journal records each cycle |
| `bot/snapshot.py` | Account/positions, option chains in a strike band around spot, clock | `feed=indicative` always (no OPRA) |
| `bot/greeks.py` | Black-Scholes IV solve (bisection) + delta/gamma/theta/vega | Alpaca's free feed has none |
| `bot/predictions.py` | Kalshi daily index-close markets -> implied median, P(above prior close), P(\|move\|>1%) | prior only, never traded |
| `bot/learning.py` | Facts-only block: recent round trips, open positions vs entry, today's rejections by rule | windowed, capped |
| `bot/research.py` | Four read-only tools the model may call (bars, stock snapshot, option contracts, news) | maps to MCP with fixed safe args; nothing that orders |
| `bot/decide.py` | Prompt assembly, bounded tool loop, lenient proposal parsing, `Decision` (usage, latency, reasoning, tool calls) | thinking models need `model_params` |
| `bot/exits.py` | expiry / stop_loss / take_profit rules, checked before the model | code decides when a trade is done |
| `bot/risk.py` | `RiskManager.check_order()` - the one gate; halt files; trading window | never clamps, only rejects with a reason |
| `bot/execute.py` | `place_proposal()` - the one order path | numeric fields stringified for MCP |
| `bot/flatten.py` + `bot/orders.py` | Flatten all / expiring-only, verified against the broker | cancel -> wait -> close -> poll until flat |
| `bot/journal.py` | JSONL journal per account; `daily_summary()` | the single "something happened" chokepoint |
| `bot/trades.py` | FIFO round-trip pairing from fills, x100 multiplier, exit classification, cuts | pure |
| `bot/review.py` | End-of-day digest facts + markdown | pure |

Entrypoints: `run_cycle.py`, `flatten.py`, `status.py`, `override.py`,
`trade_report.py`, `eod_review.py` - all take `--account <name>` and
`--config <file>`.

## Who decides what

| Decision | Who |
|---|---|
| Which underlyings, all caps and windows | humans, in `config.yaml` (git) |
| Whether there is a reason to trade, direction, which contract | the model |
| Whether a proposal is allowed at all; sizing caps; DTE; entry cutoff | code (`risk.py`) |
| When a position is done (stop / take-profit / expiry) | code (`exits.py`) |
| Daily-loss halt, kill switch, end-of-day, final day | code |
| The only order path | code (`execute.py`) |

## Infrastructure

- **CT 108** (Proxmox LXC, Debian 12): venv + cron, credentials only readable
  by root, `logs/` owned by the runner user. Provisioned by Terraform +
  Ansible in a separate private repo (`homenetwork`), disclosed in the README.
- **CI/CD**: GitHub Actions lint+test on every PR; a **self-hosted runner on
  CT 108** rsyncs `main` into `/opt/alpaca-hackathon` on every merge (a
  sanity check refuses to sync an incomplete checkout).
- **Local**: `docker compose` - the bot image (tests, dry runs, your own paper
  account) and this docs site.
