# Slide deck outline — AI Day Trader: Long Premium, Short Leash

**The deck itself is [`video/slides.html`](video/slides.html)** — open it in a browser and
export to PDF (or paste into Google Slides, link-viewable). This file is the outline of
that deck and should be regenerated from it rather than edited independently: an earlier
version of this file drifted three slides behind the real thing.

Fourteen slides, one idea each. Real screenshots over diagrams wherever a screenshot exists.

1. **Title** — "AI Day Trader — Long Premium, Short Leash" · autonomous options agent on
   Alpaca's MCP server · team · account `PA3VS39Y5LE2`
2. **The thesis in one sentence** — buy defined-risk, short-dated premium when an open
   model sees a reason; code sizes, stops, closes; the model never touches an order. Why
   long premium: known worst case → simple absolute guardrails.
3. **One cycle, every 10 minutes** — exits → snapshot (MCP) → research loop (MCP read
   tools) → proposals → risk gate → execute (MCP). The two "code only" boxes highlighted.
4. **Derived Greeks** — Alpaca's free feed has none; Black-Scholes IV solve +
   delta/theta/vega per contract, live.
5. **A second opinion from a prediction market** — Kalshi daily index-close markets as a
   snapshot prior. Read-only; never traded.
6. **The agent investigates before it acts** — the `--verbose` run: research tool calls,
   then a proposal with a stated reason.
7. **The leash** — risk gate table (caps, DTE window, entry cutoff), exits (expiry / stop /
   take-profit), daily-loss flatten+halt, kill switch. A REJECTED line beside its rule.
8. **Everything is journaled** — decision, order, rejection, exit, tool call, config hash +
   overrides per cycle.
9. **It runs on our own hardware, not a laptop** — Proxmox LXC (CT 108), Ansible-provisioned,
   cron-driven. The judging account's keys are root-only files on that box and the loader
   refuses to read them from anywhere else. A self-hosted Actions runner on the container
   deploys a merge in ~a minute; a freeze window refuses to sync trading code 08:20–15:15 CT
   so live behaviour cannot change mid-session. The account-identity guard keys on the
   broker's own account number rather than a CLI string.
10. **The daily loop** — eod_review → override (expires at close) or config PR → CI →
    self-hosted runner deploys before the open. 70 PRs, 491 tests, every merge deployed.
11. **The experiment farm — real A/B, not a backtest** — four days is not a backtest and the
    free feed has no historical options data, so a docker-compose farm runs one container per
    variant, each with its own config and its own paper account, against the *same live
    market*. A winner is promoted into the official config by pull request.
12. **Home Assistant, over MQTT — fully decoupled** — `journal.log()` → fire-and-forget MQTT
    publish → HA auto-discovery. Three audiences off one feed: an operator dashboard with the
    kill switch; a second, read-only dashboard for the team over Tailscale (no switch, button
    or service call — HA has no per-entity permissions, so the separation must be the
    dashboard itself); and phone push for problems only. Fills deliberately do not push — a
    channel that fires on routine activity gets muted and takes the halt alert with it. What
    does: halts, account-identity refusals, and a stall detector that notices no cycle has run
    for 25 minutes in market hours, the failure a dashboard cannot show because stale values
    look exactly like a quiet market. An hourly email carries trades + CSVs for anyone not
    watching a screen.
13. **Results** — equity curve Mon–Thu (from `logs/equity.jsonl`), round trips, exit mix,
    official vs challenger. An honest sentence about what didn't work.
14. **What's next, and thanks** — promote what the challenger proved; multi-leg spreads once
    the gates cover assignment risk. Repo + MIT license.

Speaker notes: keep each slide ≤ 40 seconds in the video's pitch section; slides 3, 7, 9 and
10 double as the video's diagram frames.

## Counts to refresh before export

`70 PRs, 491 tests` appears on slide 10 of `video/slides.html`. Re-check both before
exporting — they moved substantially in the final days:

```
gh pr list --state merged --limit 200 --json number -q 'length'
python -m unittest discover -s tests 2>&1 | tail -3
```
