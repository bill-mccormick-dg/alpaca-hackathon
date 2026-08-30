---
sidebar_position: 1
slug: /
title: Start here
---

# AI Day Trader - Long Premium, Short Leash

An autonomous options agent on Alpaca's MCP server, built for the lablab.ai x
Alpaca AI Trading Agents Hackathon (Aug 28 - Sep 4, 2026). An open-source
model (Featherless) researches live bars, chains and news, then proposes
defined-risk premium trades; deterministic code sizes, stops, and closes every
one before expiry. **The model never touches an order.**

## Read in this order

1. **[Onboarding](onboarding)** - get the stack running on your machine in
   ten minutes, and how we ship changes.
2. **[Architecture](architecture)** - what one cycle does, module by module,
   and which decisions belong to code vs. the model.
3. **[Strategy](strategy)** - the thesis, the tactics in the prompt, the
   holding-period / end-of-day policy, runtime overrides.
4. **[Operations](operations)** - every command, the halt files, the journal,
   the official-account safety rules.
5. **[The daily loop](daily-loop)** - what happens each trading day and what
   we do at the close.
6. **[Official guidelines](alpaca-official-guidelines)** - Alpaca's rules and
   FAQ, verbatim.

## The two accounts

| | Official `PA3VS39Y5LE2` | `hackathon_test` |
|---|---|---|
| Purpose | Judged. Equity as of EOD Thu Sep 3 is the score | Ours. Development and the A/B challenger |
| Config | `config.yaml` - Kimi-K2-Instruct, Kalshi prior **on**, research tools/learning **off** | `config-test.yaml` - Qwen3.8 with research tools, Kalshi prior, learning loop **on** |
| Keys | Only on CT 108 (`/root/.config/alpaca-hackathon/credentials.env`) | Your own `secrets.yaml` locally; CT 108 has its copy |
| Rule | No orders before Mon Aug 31 9:30 ET (enforced in code) | Trade freely |

## Where things run

- **CT 108** (a Proxmox LXC on our own hardware): cron runs both accounts every
  10 minutes in market hours, the end-of-day flatten at 14:50 CT and the
  review at 15:05 CT. A self-hosted GitHub Actions runner on the same host
  deploys every merge to `main` within a minute or two.
- **Your machine**: `docker compose` (see Onboarding) for tests, dry-run cycles
  against your test account, and this docs site.

Open work is on the [issue tracker](https://github.com/bill-mccormick-dg/alpaca-hackathon/issues),
labelled `P1-monday` to `P4-later`.
