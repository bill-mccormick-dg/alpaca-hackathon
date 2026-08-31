# Slide deck outline — AI Day Trader: Long Premium, Short Leash

**The deck itself is [`video/slides.html`](video/slides.html)** — open it in a browser and
export to PDF (or paste into Google Slides, link-viewable). This file is the outline of
that deck and should be regenerated from it rather than edited independently: an earlier
version of this file drifted three slides behind the real thing.

Fifteen slides, one idea each. Real screenshots over diagrams wherever a screenshot exists.

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
10. **Built to be changed while it is running** — the development story: 506 tests in 1.3s
    that need no API keys and no network; the whole thing runnable with nothing installed but
    Docker; `--dry-run`/`--force` making a full cycle testable at 2am; runtime overrides that
    change strategy without a deploy. Six of those tests exist because something broke.
11. **The daily loop** — eod_review → override (expires at close) or config PR → CI →
    self-hosted runner deploys before the open. 82 PRs, 506 tests, every merge deployed.
12. **The experiment farm — real A/B, not a backtest** — four days is not a backtest and the
    free feed has no historical options data, so the A/B runs *live*: three paper accounts
    cron'd on the trading host itself, same ten-minute cadence, same market, different configs.
    `test` swaps the model and enables research tools and learning; `mixed` differs from the
    official config by exactly one key, giving stock and options equal footing. A winner is
    promoted into the official config by pull request. Separately, `docker compose` brings the
    bot, the variant farm and the docs site up self-contained on a laptop — that is how the
    project is developed, and it would serve just as well to run it. (The farm is *not* a
    compose-only thing; saying so undersells that it is running against three live accounts.)
13. **Home Assistant, over MQTT — fully decoupled** — `journal.log()` → fire-and-forget MQTT
    publish → HA auto-discovery. Three audiences off one feed: an operator dashboard with the
    kill switch; a second, read-only dashboard for the team over Tailscale (no switch, button
    or service call — HA has no per-entity permissions, so the separation must be the
    dashboard itself); and phone push for problems only. A filled order sends no phone alert,
    deliberately — a channel that buzzes on routine trades gets muted and takes the halt alert
    with it. What
    does: halts, account-identity refusals, and a stall detector that notices no cycle has run
    for 25 minutes in market hours, the failure a dashboard cannot show because stale values
    look exactly like a quiet market. An hourly email carries trades + CSVs for anyone not
    watching a screen.
14. **Results** — equity curve Mon–Thu (from `logs/equity.jsonl`), round trips, exit mix,
    official vs challenger vs the mixed-instrument variant. An honest sentence about what didn't work.
15. **What's next, and thanks** — promote what the challenger proved; multi-leg spreads once
    the gates cover assignment risk. Repo + MIT license.

Speaker notes: keep each slide ≤ 40 seconds in the video's pitch section; slides 3, 7, 9 and
11 double as the video's diagram frames.

## Counts to refresh before export

`82 PRs, 506 tests` appears on slide 11 of `video/slides.html`. Re-check both before
exporting — they moved substantially in the final days:

```
gh pr list --state merged --limit 200 --json number -q 'length'
python -m unittest discover -s tests 2>&1 | tail -3
```
