# Narration — first cut (target 4:00, hard cap 5:00)

Read at a natural pace; each block is timed for the footage it sits over.
Slides are `slides.html` (arrow keys); terminal footage is `demo.sh` on CT 108.

---

**[0:00 — slide 1, title — 8s]**
AI Day Trader: Long Premium, Short Leash. An autonomous options agent on
Alpaca's MCP server, built this week for the Alpaca AI Trading Agents
Hackathon.

**[0:08 — slide 2, thesis — 25s]**
The idea in one sentence: buy defined-risk, short-dated options premium on the
most liquid names when an open-source model can name a reason — and let
deterministic code size it, stop it, and close it before expiry. The model
never touches an order. Open model proposes; code disposes. We chose long
premium because its worst case is known before entry, which keeps every
guardrail simple and absolute.

**[0:33 — slide 3, one cycle — 20s]**
Every ten minutes in market hours: exits run first, then a snapshot through
Alpaca's MCP server, then the model — which may investigate with read-only
tools before it answers — then every proposal through one risk gate, then our
own code places the order. Same path for every order, no exceptions.

**[0:53 — terminal, shot 1 — 60s]**
Here's a live cycle on our test account. The equity line is the real paper
account through the MCP server. Alpaca's free options feed has no Greeks, so
the agent derives implied vol and delta for every contract from its price.
Watch the research lines: bars for SPY and QQQ, snapshots, news — six
read-only calls, each journaled. Then the answer: a JSON array, or hold. And
here's the leash — this proposal passes the risk gate; this one would be
refused, and the journal records exactly which rule said no.

**[1:53 — terminal, shot 2+3 — 25s]**
Status shows what's held, whether anything is halted, and today's summary.
And the journal: every decision, order, rejection, exit and tool call, with the
config hash each cycle actually ran with.

**[2:18 — terminal, shot 4 — 35s]**
The kill switch. One command closes everything — cancel, wait for the cancels
to settle, close, then poll the broker until it's really flat — and writes a
HALT file. The next cycle refuses to run until a human deletes it.

**[2:53 — terminal, shot 5 — 35s]**
At the close, one command reconstructs every round trip from Alpaca's fills
and classifies how each ended — stop, take-profit, expiry, the model's own
sell, or the end-of-day flatten. It groups rejections by rule, appends the
equity curve, and the model writes its own read of the day with one
recommended change.

**[3:28 — terminal, shot 6 — 20s]**
We apply that change with an override that expires at the close — or a config
PR that CI deploys to the trading host before the open. Intraday tweaks never
outlive the day; tomorrow always starts from git.

**[3:48 — slide 9, results — 30s]**
(Fill after Thursday's close.) Equity from Monday's open to Thursday's close:
___. ___ round trips, exits split ___. The challenger account, running a
different model with research tools and a prediction-market prior, did ___.
What didn't work: ___.

**[4:18 — slide 10 — 12s]**
Everything is MIT-licensed and in the repo: 300 tests, 60 pull requests, every
decision journaled. Thanks to Alpaca, lablab.ai and Featherless.

*(~4:30 total)*
