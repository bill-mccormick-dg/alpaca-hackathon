# Submission metadata — lablab.ai form fields

Deadline **Fri Sep 4, 2026, 10:00 AM CDT** - seven days to the hour from the
Fri Aug 28 10:00 CDT kickoff. Every field below is pasted into
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

## Pre-flight (do in this order Thu evening or Fri morning)

- [ ] Repo flipped to **public** (`gh repo edit bill-mccormick-dg/alpaca-hackathon --visibility public`) and `LICENSE` present
- [ ] Video uploaded (MP4, ≤ 5:00, target 3–5 min), link opens **logged out**
- [ ] Slides exported (PDF or Google Slides link, view access for anyone with the link)
- [ ] Cover image uploaded (`submission/cover.png`, 1600×900)
- [ ] Account ID below matches the official account exactly
- [ ] Final `eod_review` numbers pasted into WRITEUP.md results section
- [ ] Social post links collected (up to 5)

## Form state (what is actually left)

- [x] **Step 1 — Basic Information**: title (41/50), short description
      (237/255), long description (1977/2000 — **REPLACE what is in the
      form**: as of 2026-09-02 the live form held `alpaca-trader`'s copy,
      a different project — 12 names, 15-min snapshot, flatten-before-
      close, a shadow benchmark that does not exist here, and no mention
      of options or MCP, both track requirements), categories (Finance, Coding),
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
      out of "draft in progress". Do not leave this to 09:59 on Fri Sep 4.

## Fields

**Project title** (≤ 50 chars)
```
Autobelay - long premium, short leash
```

**Short description** (≤ 255 chars)
```
An autonomous options agent on Alpaca's MCP server. An open-source model (Featherless) researches live bars, chains and news, then proposes defined-risk premium trades; deterministic code sizes, stops, and closes every one before expiry.
```

**Long description** (<= 2000 chars; this is 1977)
```
Autobelay is an autonomous options-trading agent. An auto belay catches a falling climber with nobody holding the other end; here the brake is deterministic code. Thesis: buy defined-risk, short-dated options premium on the five most liquid names when an open-source model sees a concrete reason - code sizes every trade, stops it, and closes it before expiry. The model never touches an order.

Every 10 minutes: (1) deterministic exits run first - stop-loss, take-profit, expiry-day close. (2) A snapshot is built through Alpaca's official MCP server: account, positions, and per underlying a 12-contract menu - at-the-money and roughly 0.40-delta per side across three expiries - with Alpaca's IV and Greeks. (3) The model may call read-only research tools, then must answer with JSON proposals. Open-source models on Featherless, one per account: Qwen3.8-Flash-Next on the judged account, Kimi-K3 and Kimi-K2.6 on the challengers. (4) One risk gate that never negotiates: whitelist, notional cap, position count, expiration window, entry cutoff, daily-loss halt. Only our own code calls Alpaca's order tools.

The interesting part is the accounting. Every decision, order, rejection and exit is journaled with the model that made it and the config it ran under. The model's prose is audited against its own inputs: quoted figures are checked against the prior it was shown, and exit reasons against the account - a sell citing a forced close days before any code exit could fire is flagged. The prediction-market priors it is handed are Brier-scored nightly, so the inputs are graded too. The end-of-day critique comes from a model that did not trade that day, so no account grades its own homework.

The trading is autonomous; the risk envelope is human: runtime knobs expire at the close, hard caps need a pull request, and nothing at runtime can widen what the bot may lose.

MIT licensed and original to the event; pre-kickoff infrastructure is disclosed in the README.
```

**Technology tags**: Alpaca, Alpaca MCP Server, Featherless AI, Qwen3, Kimi K3, Kimi K2.6, Python, Model Context Protocol, GitHub Actions, Ansible, Proxmox, Docker

**Category tags**: Trading agents, Options, Autonomous agents, Fintech, Open-source models

**Alpaca paper trading account ID**: `PA3VS39Y5LE2`

**Public GitHub repository**: https://github.com/bill-mccormick-dg/alpaca-hackathon

**Demo application URL**: N/A — autonomous agent, no UI required (per FAQ)

**Video presentation**: (YouTube unlisted link — fill after Thu's close)

**Slide presentation**: (link — fill after Thu's close)

**Cover image**: `submission/cover.png` — generated, 1600x900. `python scripts/make_cover.py --png`. A price series that runs up, rolls over, and stops dead on a line held by a rope; the dashed ghost below is where it was going. Drawn rather than screenshotted: a terminal is an unreadable grey rectangle at thumbnail size.

**Social posts** (up to 5): see `social_posts.md`
