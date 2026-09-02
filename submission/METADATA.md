# Submission metadata — lablab.ai form fields

Deadline **Thu Sep 4, 2026, 10:00 AM CDT**. Every field below is pasted into
the form as-is; edit here, not in the text box.

Form: <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/razorsedge/submission>
(team **RazorsEdge**; reachable as "Update Project" on the event page).
It is a **3-step wizard**, and a filled draft is *not* a submission — the
team page says "Submission draft in progress" until the last step is
completed.

**Status checked 2026-08-30: Step 1 of 3, 36% complete.** The event's live
page counts us in "drafts in progress" (37), not "submissions" (19). There
is **no P&L leaderboard** for this event (the FAQ says so outright, and
`/live` is momentum stats + a social feed) — nothing to appear on, so the
submission itself is the only thing that puts the project in front of
judges.

## Pre-flight (do in this order on Thu morning)

- [ ] Repo flipped to **public** (`gh repo edit bill-mccormick-dg/alpaca-hackathon --visibility public`) and `LICENSE` present
- [ ] Video uploaded (MP4, ≤ 5:00, target 3–5 min), link opens **logged out**
- [ ] Slides exported (PDF or Google Slides link, view access for anyone with the link)
- [ ] Cover image uploaded (`submission/cover.png`, 1600×900)
- [ ] Account ID below matches the official account exactly
- [ ] Final `eod_review` numbers pasted into WRITEUP.md results section
- [ ] Social post links collected (up to 5)

## Form state (what is actually left)

- [x] **Step 1 — Basic Information**: title (41/50), short description
      (237/255), long description (893/2000), categories (Finance, Coding),
      track (Options Alpha Agents), technologies (Alpaca, Featherless,
      GitHub Copilot, Anthropic Claude) — all filled.
- [ ] **Step 1 — social post links are still empty.** Five fields, and the
      LinkedIn post already exists; pasting it costs nothing and is the only
      thing feeding the Social Engagement prize ($500 to each of 2 teams,
      plus a month of Algo Trader Plus per member).
- [ ] **Step 2** — not started (cover image / video / slides).
- [ ] **Step 3** — not started (GitHub URL, demo URL, **Alpaca account ID**,
      which the rules call out as required for judging).
- [ ] **Actually submit.** Completing the last step is what moves the team
      out of "draft in progress". Do not leave this to 09:59 on Thu Sep 4.

## Fields

**Project title** (≤ 50 chars)
```
Autobelay - long premium, short leash
```

**Short description** (≤ 255 chars)
```
An autonomous options agent on Alpaca's MCP server. An open-source model (Featherless) researches live bars, chains and news, then proposes defined-risk premium trades; deterministic code sizes, stops, and closes every one before expiry.
```

**Long description**
```
Autobelay is an autonomous options-trading agent built for the Alpaca AI Trading Agents Hackathon. An auto belay is the climbing-gym device that catches a falling climber with nobody holding the other end; here the brake is deterministic code. Its one-line thesis: buy defined-risk, short-dated options premium on the five most liquid names when an open-source model sees a concrete reason; deterministic code sizes every trade, stops it, and closes it before expiry - the model never touches an order.

How a cycle works (every 10 minutes during market hours, from cron):
1. Deterministic exits run first: any contract on its expiry day is closed, and any position past its stop-loss or take-profit is closed. Code decides when a trade is done, not the model.
2. A snapshot is built through Alpaca's official MCP server: account, positions, the clock, and for each whitelisted underlying a 12-contract menu drawn from a chain paginated across the whole 2-45 day expiration window - the at-the-money and a roughly 0.40-delta strike per side across three expiries, so the model can choose strike distance and not just direction. Alpaca's own implied volatility and Greeks are used where the feed supplies them, which is about 94% of contracts; Black-Scholes on our side is the backstop for the rest, and each contract records which it got.
3. The model (Kimi-K2 / Qwen3.8 on Featherless.ai) may call a small set of read-only research tools - recent bars, a stock snapshot, specific option contracts, news - up to six times, then must answer with a JSON array of proposals. Every tool call is journaled.
4. Every proposal passes through one risk gate that never negotiates: symbol whitelist, per-position notional cap, max positions, contracts-per-order cap, expiration window (entries only - a held contract stays sellable to expiry), open orders counted as committed exposure, entry cutoff time, and a daily-loss cutoff that flattens and halts. Rejections are journaled with the rule that refused them.
5. Only our own code calls Alpaca's order tools. There is no path from the model to an order.

Operations: a self-hosted CI runner deploys every merge to the trading host; a JSONL journal records every decision, order, rejection, exit, tool call and the exact config (hash + active overrides) each cycle ran with; an end-of-day review reconstructs round trips from Alpaca's fills, groups rejections by rule, appends an equity curve, and has the model write a one-change recommendation for tomorrow. Strategy knobs live in config with runtime overrides that expire at the close, so a day's lesson becomes tomorrow's config in minutes. Two more paper accounts run variant configs against the same live market for A/B evidence, and the prediction-market priors the model is handed are Brier-scored nightly against what the market actually did - the inputs are graded, not just the model.

Everything is MIT licensed and original to the event; the hosting, deploy pipeline and secrets plumbing were set up before kickoff and are disclosed in the README.
```

**Technology tags**: Alpaca, Alpaca MCP Server, Featherless AI, Kimi K2, Qwen3, Python, Model Context Protocol, GitHub Actions, Ansible, Proxmox, Docker

**Category tags**: Trading agents, Options, Autonomous agents, Fintech, Open-source models

**Alpaca paper trading account ID**: `PA3VS39Y5LE2`

**Public GitHub repository**: https://github.com/bill-mccormick-dg/alpaca-hackathon

**Demo application URL**: N/A — autonomous agent, no UI required (per FAQ)

**Video presentation**: (YouTube unlisted link — fill Thu)

**Slide presentation**: (link — fill Thu)

**Cover image**: `submission/cover.png` — generated, 1600x900. `python scripts/make_cover.py --png`. A price series that runs up, rolls over, and stops dead on a line held by a rope; the dashed ghost below is where it was going. Drawn rather than screenshotted: a terminal is an unreadable grey rectangle at thumbnail size.

**Social posts** (up to 5): see `social_posts.md`
