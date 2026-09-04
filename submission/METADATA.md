# Submission metadata — lablab.ai form fields

Deadline **Fri Sep 4, 2026, 10:00 AM CDT** - seven days to the hour from the
Fri Aug 28 10:00 CDT kickoff. Every field below is pasted into
the form as-is; edit here, not in the text box.

Form: <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/razorsedge/submission>
(team **RazorsEdge**; reachable as "Update Project" on the event page).
It is a **3-step wizard**, and a filled draft is *not* a submission — the
team page says "Submission draft in progress" until the last step is
completed.

**Status checked 2026-09-03 21:30 CT: Step 1 of 3, 80% complete** (was 36% on
Aug 30). Title 37/50, short description 237/255, long description 1979/2000,
categories Finance + Coding, track Options Alpha Agents, technologies Alpaca /
Featherless / GitHub Copilot / Anthropic Claude — all present. **All five
social-post links are still empty**, and they are the only thing feeding the
Social Engagement prize. The wizard is client-side: `?step=2` does not work,
only *Next* advances it, so steps 2 and 3 cannot be inspected without
advancing. The event's live page counts us in "drafts in progress" (37), not
"submissions" (19). There
is **no P&L leaderboard** for this event (the FAQ says so outright, and
`/live` is momentum stats + a social feed) — nothing to appear on, so the
submission itself is the only thing that puts the project in front of
judges.

## Pre-flight (do in this order Thu evening or Fri morning)

- [ ] Repo flipped to **public** (`gh repo edit bill-mccormick-dg/alpaca-hackathon --visibility public`) and `LICENSE` present
- [ ] Video uploaded — **lablab's step 2 takes the MP4 file directly**, so
      there is no YouTube/Drive step and no link to test. Limits: ≤ 5:00 and
      **≤ 300 MB** (lablab's stated cap; 5 min of 1080p H.264 screen content
      fits easily, a high-bitrate or ProRes export does not). The finished cut
      is `submission/video/build/autobelay.mp4`, **4:53 / 30.6 MB** — build it
      with `python3 submission/video/assemble.py`, which reports both limits
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
- [x] **Step 1 — P&L is LIVE on the form** (2026-09-03 22:23 CT, saved by
      advancing to step 2; team page confirms it). Short description ends
      "+5.1% over the judged week" (245/255); long description **opens** with
      "Judged account, Aug 31 - Sep 3: $100,000 to $105,096, +5.1%…" (1998/2000).
      Of 142 rival projects only 23 published a number at all.
- **The judged figure is $105,095.51 (+5.10%), not the $105,667.53 in
      `logs/equity.jsonl`.** Corrected 2026-09-04 09:10 CT. `eod_review` snapshots
      at 15:05 CT = 16:05 ET, and the option marks on the two positions carried
      overnight settled $572.02 lower afterward. The authoritative number is
      Alpaca's own `last_equity` on account PA3VS39Y5LE2 — the prior trading
      day's settled close, which is what `docs/alpaca-official-guidelines.md`
      lines 101/146 say is measured ("total equity as of EOD Thursday Sep 3rd").
      Read three times the morning of Sep 4, identical each time. Same correction
      on the challenger: test settled at $92,956.74 = **-7.0%**, not -7.1%.
      Note the guidelines also say (line 171) the window "ends at 9:30 a.m. ET on
      Friday, September 4, when a snapshot of total account equity will be taken";
      at 09:50 ET official read $106,855.51 (+6.9%). We publish the lower,
      verifiable figure — a judge pulling the account sees $105,095.51.
- **The long description renders as ONE unbroken block — no HTML, no markdown,
      and paragraph breaks are dropped.** Verified: lablab puts the string into a
      single `<p>` as one text node (React-escaped), `white-space: normal`, so the
      stored `\n\n` collapses; 102 description chunks across 12 rival pages contain
      zero markdown and zero tags. **That is why the result sentence is first** —
      anything buried mid-text is lost in the wall. Don't spend time on formatting.
- [ ] **Step 1 — social post links are still empty.** Five fields, and the
      LinkedIn post already exists; pasting it costs nothing and is the only
      thing feeding the Social Engagement prize ($500 to each of 2 teams,
      plus a month of Algo Trader Plus per member). Likely the reason the
      wizard still reads 80% with every required field filled.
- [x] **Step 2 — DONE**: cover image, video presentation and slide PDF are all
      uploaded (verified on the live form, 2026-09-03 22:4x CT).
- [x] **Step 3 — DONE**: GitHub repo, demo platform (Other), demo URL,
      **Alpaca account ID `PA3VS39Y5LE2`**, and Additional Information.
- [x] **SUBMITTED.** The public project page carries `"isDraft": false` —
      this is not a draft. Verified logged-out at
      <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/razorsedge/autobelay-long-premium-short-leash>

**Do not trust this checklist over the live form.** It was wrong twice on the
night of Sep 3 (claimed steps 2 and 3 unstarted when both were complete) and
sent a session down the wrong path. Read the form.

**Demo URL gotcha**: it was briefly `bot.wpmccormick.pm` — a typo with **no DNS
record at all**, so the link was dead. Corrected to `.pw`, which resolves but
returns **302 into Cloudflare Access**: a judge gets an auth wall, not the
viewer. That is acceptable because the FAQ says a UI is not required and
judging is "primarily on the autonomous agent workflow"; remote access is
offered on request (deck slide 13, `ansible/README.md` §3).

## Fields

**Project title** (≤ 50 chars)
```
Autobelay - long premium, short leash
```

**Short description** (≤ 255 chars)
```
An autonomous options agent on Alpaca's MCP server. An open-source model (Featherless) researches bars, chains and news, then proposes defined-risk premium trades; deterministic code sizes, stops and closes every one. +5.1% over the judged week.
```

**Long description** (<= 2000 chars; this is 1998 - leads with the result)
```
Judged account, Aug 31 - Sep 3: $100,000 to $105,096, +5.1% over 15 round trips, nothing held past expiry. The Kimi-K3 challenger ran the same code and the same gates on a different model and finished -7.0%, halted by the 2% cutoff. That gap is the result we defend.

Autobelay is an autonomous options-trading agent - the brake is deterministic code. Thesis: buy defined-risk, short-dated options premium on the five most liquid names when an open-source model sees a concrete reason; code sizes every trade, stops it, and closes it before expiry. The model never touches an order.

Every 10 minutes: (1) deterministic exits run first: stop-loss, take-profit, expiry-day close. (2) A snapshot is built through Alpaca's MCP server: account, positions, and per underlying a 12-contract menu (ATM and ~0.40-delta per side, three expiries) with IV and Greeks. (3) The model may call read-only research tools, then must answer with JSON proposals. Featherless models, one per account: Qwen3.8-Flash-Next judged, Kimi-K3/K2.6 challenging. (4) One risk gate that never negotiates: whitelist, notional cap, position count, expiration window, entry cutoff, daily-loss halt. Only our own code calls Alpaca's order tools.

The accounting is the point. Every decision, order, rejection and exit is logged with the model and config behind it. The model's prose is audited against its own inputs: quoted figures against the prior it was shown, exit reasons against the account. The priors it is handed are Brier-scored nightly, so the inputs are graded too. The end-of-day critique comes from a model that did not trade that day: no account grades its own homework.

The trading is autonomous; the risk envelope is human: runtime knobs expire at the close, hard caps need a pull request, and nothing at runtime can widen what the bot may lose. It self-hosts; halting an account or swapping its model is a phone control. MIT licensed and original to the event; pre-kickoff infrastructure disclosed in the README.
```

**Technology tags**: Alpaca, Alpaca MCP Server, Featherless AI, Qwen3, Kimi K3, Kimi K2.6, Python, Model Context Protocol, GitHub Actions, Ansible, Proxmox, Docker

**Category tags**: Trading agents, Options, Autonomous agents, Fintech, Open-source models

**Alpaca paper trading account ID**: `PA3VS39Y5LE2`

**Public GitHub repository**: https://github.com/bill-mccormick-dg/alpaca-hackathon

**Demo application platform**: self-hosted (Proxmox LXC); the journal viewer is
published through a Cloudflare tunnel

**Demo application URL**: N/A — autonomous agent, no UI required (per FAQ).
The live journal viewer at <https://bot.wpmccormick.pw> is the closest thing,
but it is behind an email one-time PIN, so it is not a link a judge can open.

**Additional information** (free-text field on step 3): scaling beyond the
event — the risk funnel and the journal are broker-agnostic; what is
Alpaca-specific is the MCP client and the option-chain shape.

**Video presentation**: upload `submission/video/build/autobelay.mp4` (4:53,
30.6 MB) with the uploader on step 2 — a file, not a link.

**Slide presentation**: (link — fill after Thu's close)

**Cover image**: `submission/cover.png` — generated, 1600x900. `python scripts/make_cover.py --png`. A price series that runs up, rolls over, and stops dead on a line held by a rope; the dashed ghost below is where it was going. Drawn rather than screenshotted: a terminal is an unreadable grey rectangle at thumbnail size.

**Social posts** (up to 5): see `social_posts.md`
