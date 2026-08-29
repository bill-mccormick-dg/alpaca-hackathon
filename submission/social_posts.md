# Build-in-public posts (optional social prize — up to 5 links)

Tag **@lablabai** and **@AlpacaHQ** on X; **lablab.ai** and **Alpaca** on
LinkedIn. One real screenshot per post beats any graphic. Paste the links into
METADATA.md as they go up.

1. **Sat — the thesis**
   > Building for the @AlpacaHQ x @lablabai AI trading agents hackathon:
   > "AI Day Trader - Long Premium, Short Leash." An open-source model (via Featherless)
   > proposes defined-risk options trades; deterministic code sizes, stops and
   > closes every one. The model never touches an order. 280 tests so far.
   + screenshot: README thesis block or the risk gate table.

2. **Sun — derived Greeks**
   > Alpaca's free options feed has no Greeks. So the agent solves implied vol
   > from each contract's price and derives delta/theta/vega on the fly —
   > Black-Scholes, pure Python, unit-tested against textbook values. Here's
   > what the model sees per contract.
   + screenshot: a prompt contract block with iv/delta/theta.

3. **Mon — first live day**
   > First live session on the competition account. Every decision, order,
   > rejection and research call is journaled; here's the end-of-day digest
   > the agent writes about itself, including its own one-change
   > recommendation for tomorrow.
   + screenshot: eod_review markdown (redact nothing — it's paper).

4. **Tue/Wed — the agent investigates**
   > Before deciding, the model calls read-only Alpaca MCP tools — bars, a
   > snapshot, news — up to six times, then must answer. Six calls, one
   > proposal with a stated reason, accepted by the risk gate. Setback that
   > made this work: thinking-mode models returned nothing until we disabled
   > thinking via one request flag.
   + screenshot: the `research:` lines + model output.

5. **Thu — results, honestly**
   > Four days on the competition account: equity curve, exit mix, official vs
   > challenger config. What worked, what didn't, what we'd change. Repo is
   > public (MIT). Thanks @AlpacaHQ @lablabai @FeatherlessAI.
   + screenshot: equity curve from logs/equity.jsonl.

Links:
1.
2.
3.
4.
5.
