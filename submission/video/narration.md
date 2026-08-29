# Narration — first cut (target 4:45, hard cap 5:00)

Read at a natural pace; each block is timed for the footage it sits over.
Slides: `slides.html` (arrow keys). CT 108 footage: `demo.sh`. Local footage
(farm, MQTT): `demo_local.sh` + `mqtt_watch.py` — **director's notes** mark
where to actually cut to a live screen recording versus stay on a slide.

---

**[0:00 — slide 1, title — 8s]**
AI Day Trader: Long Premium, Short Leash. An autonomous options agent on
Alpaca's MCP server, built this week for the Alpaca AI Trading Agents
Hackathon.

**[0:08 — slide 2, thesis — 20s]**
The idea in one sentence: buy defined-risk, short-dated options premium on the
most liquid names when an open-source model can name a reason — and let
deterministic code size it, stop it, and close it before expiry. The model
never touches an order. Open model proposes; code disposes.

**[0:28 — slide 3, one cycle — 25s]**
Every ten minutes: exits run first, then a snapshot through Alpaca's MCP
server — derived Greeks, since the free feed has none, plus a Kalshi
prediction-market prior on where the index might close today. The model may
investigate with read-only tools before it answers. Every proposal goes
through one risk gate. Our own code places the order.

**[0:53 — terminal, shot 1 — 55s]** *(director: `demo.sh` on CT 108)*
Here's a live cycle. The equity line is the real paper account. Watch the
research lines — bars, snapshots, news, six calls, each journaled — then the
answer: a JSON array, or hold. And here's the leash: this proposal passes the
risk gate; this one would be refused, and the journal records exactly which
rule said no.

**[1:48 — terminal, shot 2+3 — 20s]** *(director: `demo.sh`)*
Status shows what's held and whether anything is halted. The journal: every
decision, order, rejection, exit and tool call, with the config each cycle
actually ran with.

**[2:08 — terminal, shot 4 — 30s]** *(director: `demo.sh`)*
The kill switch. One command closes everything — cancelled, settled, closed,
verified against the broker — and writes a HALT file. The next cycle refuses
to run until a human deletes it.

**[2:38 — terminal, shot 5 — 30s]** *(director: `demo.sh`)*
At the close, one command reconstructs every round trip from Alpaca's fills,
classifies how each ended, groups rejections by rule, appends the equity
curve, and the model writes its own read of the day with one recommended
change.

**[3:08 — terminal, shot 6 — 15s]** *(director: `demo.sh`)*
We apply that change with an override that expires at the close, or a config
PR that deploys before the open. Tomorrow always starts from git.

**[3:23 — slide "the farm" + terminal — 35s]** *(director: `demo_local.sh` part 1,
local machine — `docker compose --profile farm config`, then the diff against
the challenger config, then `trade_report.py` for both accounts side by side)*
Four days isn't enough for a real backtest, so we built the next best thing: a
docker-compose farm, one container per variant, each its own config and its
own paper account, all trading the same live market. Different model,
different thesis, different risk parameters — same conditions. Whichever one
wins gets promoted into the official config with a pull request.

**[3:58 — slide "MQTT/HA" + screen recording — 30s]** *(director: `demo_local.sh`
part 2 — split screen or cut between the two terminals: `mqtt_watch.py`
subscribed locally, a cycle triggered on CT 108, messages arriving live; then
cut to the Home Assistant dashboard if deployed)*
Every decision also publishes over MQTT — fully decoupled, so a broker being
offline never touches a trading cycle. Home Assistant picks the sensors up
automatically: equity, day P&L, halt state, one dashboard per account plus
the comparison chart. And it's two-way — a config change can be published
from Home Assistant and applied the same way the CLI does, expiring at the
close.

**[4:28 — slide 9, results — 25s]**
(Fill after Thursday's close.) Equity from Monday's open to Thursday's close:
___. ___ round trips, exits split ___. The challenger did ___. What didn't
work: ___.

**[4:53 — slide 10 — 10s]**
Everything is MIT-licensed and in the repo. Thanks to Alpaca, lablab.ai and
Featherless.

*(~4:58 with results filled in briefly; trim slide 3 or shot 1 by a few
seconds if it runs over 5:00 once real footage is timed)*
