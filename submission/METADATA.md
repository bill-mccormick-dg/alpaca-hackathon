# Submission metadata — lablab.ai form fields

Deadline **Thu Sep 4, 2026, 10:00 AM CDT**. Every field below is pasted into
the form as-is; edit here, not in the text box.

## Pre-flight (do in this order on Thu morning)

- [ ] Repo flipped to **public** (`gh repo edit bill-mccormick-dg/alpaca-hackathon --visibility public`) and `LICENSE` present
- [ ] Video uploaded (MP4, ≤ 5:00, target 3–5 min), link opens **logged out**
- [ ] Slides exported (PDF or Google Slides link, view access for anyone with the link)
- [ ] Cover image uploaded (real screenshot, 1280×720 or larger)
- [ ] Account ID below matches the official account exactly
- [ ] Final `eod_review` numbers pasted into WRITEUP.md results section
- [ ] Social post links collected (up to 5)

## Fields

**Project title** (≤ 50 chars)
```
AI Day Trader - Long Premium, Short Leash
```

**Short description** (≤ 255 chars)
```
An autonomous options agent on Alpaca's MCP server. An open-source model (Featherless) researches live bars, chains and news, then proposes defined-risk premium trades; deterministic code sizes, stops, and closes every one before expiry.
```

**Long description**
```
AI Day Trader - Long Premium, Short Leash is an autonomous options-trading agent built for the Alpaca AI Trading Agents Hackathon. Its one-line thesis: buy defined-risk, short-dated options premium on the five most liquid names when an open-source model sees a concrete reason; deterministic code sizes every trade, stops it, and closes it before expiry - the model never touches an order.

How a cycle works (every 10 minutes during market hours, from cron):
1. Deterministic exits run first: any contract on its expiry day is closed, and any position past its stop-loss or take-profit is closed. Code decides when a trade is done, not the model.
2. A snapshot is built through Alpaca's official MCP server: account, positions, the clock, and for each whitelisted underlying the ~12 nearest-the-money contracts inside a 1-45 day expiration window. Alpaca's free indicative options feed carries no Greeks, so implied volatility, delta, gamma, theta and vega are derived on the fly from each contract's market price via Black-Scholes.
3. The model (Kimi-K2 / Qwen3.8 on Featherless.ai) may call a small set of read-only research tools - recent bars, a stock snapshot, specific option contracts, news - up to six times, then must answer with a JSON array of proposals. Every tool call is journaled.
4. Every proposal passes through one risk gate that never negotiates: symbol whitelist, per-position notional cap, max positions, contracts-per-order cap, expiration window, entry cutoff time, and a daily-loss cutoff that flattens and halts. Rejections are journaled with the rule that refused them.
5. Only our own code calls Alpaca's order tools. There is no path from the model to an order.

Operations: a self-hosted CI runner deploys every merge to the trading host; a JSONL journal records every decision, order, rejection, exit, tool call and the exact config (hash + active overrides) each cycle ran with; an end-of-day review reconstructs round trips from Alpaca's fills, groups rejections by rule, appends an equity curve, and has the model write a one-change recommendation for tomorrow. Strategy knobs live in config with runtime overrides that expire at the close, so a day's lesson becomes tomorrow's config in minutes. A second paper account runs a challenger config against the same live market for A/B evidence.

Everything is MIT licensed and original to the event; the hosting, deploy pipeline and secrets plumbing were set up before kickoff and are disclosed in the README.
```

**Technology tags**: Alpaca, Alpaca MCP Server, Featherless AI, Kimi K2, Qwen3, Python, Model Context Protocol, GitHub Actions, Ansible, Proxmox, Docker

**Category tags**: Trading agents, Options, Autonomous agents, Fintech, Open-source models

**Alpaca paper trading account ID**: `PA3VS39Y5LE2`

**Public GitHub repository**: https://github.com/bill-mccormick-dg/alpaca-hackathon

**Demo application URL**: N/A — autonomous agent, no UI required (per FAQ)

**Video presentation**: (YouTube unlisted link — fill Thu)

**Slide presentation**: (link — fill Thu)

**Cover image**: `submission/cover.png` (fill Thu — a real `status.py` / equity-curve screenshot)

**Social posts** (up to 5): see `social_posts.md`
