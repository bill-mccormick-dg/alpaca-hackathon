# Slide deck outline — Long Premium, Short Leash

Ten slides, one idea each. Export to PDF (or Google Slides, link-viewable).
Real screenshots over diagrams wherever a screenshot exists.

1. **Title** — "Long Premium, Short Leash" · autonomous options agent on Alpaca's
   MCP server · team · account `PA3VS39Y5LE2`
2. **The thesis in one sentence** — buy defined-risk, short-dated premium when an
   open model sees a reason; code sizes, stops, closes; the model never touches
   an order. Why long premium: known worst case → simple absolute guardrails.
3. **One cycle** — exits → snapshot (MCP) → research loop (MCP read tools) →
   proposals → risk gate → execute (MCP). Arrow diagram with the two "code only"
   boxes highlighted.
4. **Derived Greeks** — Alpaca's free feed has no Greeks; Black-Scholes IV solve
   + delta/theta/vega per contract, live. Screenshot of a prompt contract block.
5. **The agent investigates** — the `--verbose` run: six research calls, then a
   proposal with a stated reason. Screenshot of the tool_call journal lines.
6. **The leash** — risk gate table (caps, DTE window, entry cutoff), exits
   (expiry / stop / take-profit), daily-loss flatten+halt, kill switch. Screenshot
   of a REJECTED line with its rule.
7. **Everything is journaled** — decision, order, rejection, exit, tool call,
   config hash + overrides per cycle. Screenshot of `eod_review` markdown.
8. **The daily loop** — eod_review → override (same day, expires at close) or
   config PR → CI → self-hosted runner deploys → next morning. Challenger
   account A/B. 54 PRs, 280 tests.
9. **Results** — equity curve Mon–Thu (from `logs/equity.jsonl`), round trips,
   exit mix, official vs challenger. Honest sentence about what didn't work.
10. **What we'd do next** — Kalshi prediction-market prior, learning loop,
    multi-leg spreads once gates cover assignment risk. Repo + license.

Speaker notes: keep each slide ≤ 40 seconds when used in the video's pitch
section; slides 3, 6 and 8 double as the video's diagram frames.
