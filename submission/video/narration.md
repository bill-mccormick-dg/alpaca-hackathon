# Narration — second cut (target 4:40, hard cap 5:00)

Read at a natural pace. Each block names the footage it sits over: **slide N**
is the deck, **shot N** is the caption `demo.sh` prints, and **browser** is a
web page.

You do not have to arrange any of it. `bash submission/video/record.sh` walks
this order for you - ssh for the terminal shots, Chrome for the slides and the
pages - and waits on each one until you switch away, so the pauses are however
long your narration is. Run `record.sh --check` first.

---

**[0:00 — slide 1, title — 8s]**
Autobelay: long premium, short leash. An autonomous options agent on Alpaca's
MCP server, built this week for the Alpaca AI Trading Agents Hackathon. An auto
belay is the device in a climbing gym that catches you with nobody holding the
other end. Here the brake is deterministic code, and the model never touches an
order.

**[0:08 — slide 2, thesis — 17s]**
One sentence: buy defined-risk, short-dated options premium on the most liquid
names when an open-source model can name a reason — and let deterministic code
size it, stop it, and close it before expiry. The model never touches an order.
Open model proposes; code disposes.

**[0:25 — slide 3, one cycle — 20s]**
Every ten minutes: gates, a snapshot through Alpaca's MCP server, and code's
own exit rules run *before* the model is asked anything. The model gets one
box. It returns JSON. Its proposals and code's own exit sells go through the
same `check_order`, which rejects and never negotiates — and everything either
of them does lands in the journal.

**[0:45 — shot 1, live cycle — 35s]** *(director: `demo.sh` on CT 108)*
Here is a real cycle on a live paper account. Equity and positions from the
broker. The model may investigate first — bars, a snapshot, news, read-only
Alpaca tools, six calls at most, every one journaled. Then it answers: a JSON
array, or an empty one, which is a hold and a perfectly good decision. What it
proposes goes to the gate.

**[1:20 — shot 2, `last_cycle.py` — 45s]** *(director: hold on this screen; it
is the densest thing in the video)*
This is one cycle from the judged account — the inputs, and what came of them.

Top: the option chain it was actually given. Twenty-nine hundred SPY contracts
across three API pages, out to forty-five days. That matters because one page
is five hundred contracts, and on SPY, at dollar strikes with daily expiries,
five hundred contracts is *three days*. For two days our config said forty-five
and the model was quietly seeing three, and reaching off-menu for contracts it
could not price. Now it paginates until the window is covered, and journals the
coverage so the question has an answer.

Middle: the second opinion. Kalshi's index-close market and the option chain's
own implied odds — two independent crowds on the same question. Note the
withheld line: that market had barely traded, so the model was told nothing
rather than something unearned.

Bottom: the decision, and the order — with the reason it gave, quoting those
same numbers back.

**[2:05 — slide, Greeks — 18s]**
About those contracts. We spent three days believing Alpaca's free feed carried
no Greeks and solving implied volatility ourselves. That was wrong: the
snapshot carries them on ninety-four percent of the chain. So we use Alpaca's,
and our Black-Scholes solve is the *backstop* for the rest — every contract
says which it got, so the model knows which numbers are rough.

**[2:23 — slide, grading the prior — 20s]**
A prior nobody scores is decoration, so every night we Brier-score the exact
probabilities the model was handed against what the market did. Yesterday:
Kalshi four thousandths, the chain eight — against twenty-five hundredths for a
coin flip. One day, and a day with a clear direction. The point is that we grade
the inputs, not just the model.

**[2:43 — shot 4, kill switch — 22s]** *(director: `demo.sh`)*
The leash. One command closes everything — cancelled, settled, closed, verified
against the broker — and writes a halt file. The next cycle refuses to run until
a human deletes it.

**[3:05 — browser: the live viewer — 40s]** *(director: `bot.wpmccormick.pw`;
show a cycle arriving, then a blocked line, then the date picker)*
Everything you just saw is also a web page. The journal streams to a browser
over server-sent events, published through a Cloudflare tunnel with an email
one-time PIN — no VPN, nothing to install. It is read-only; it cannot halt or
trade.

It has earned its keep. In two days of watching this scroll past, we found four
bugs — none of them a failing test, because in every case the code did exactly
what we had told it to. The feed cut the model's reasoning mid-word. Trying to
close a thirteen-hundred-dollar winner, the model named a neighbouring strike,
three cycles running. An unfilled limit order sat invisible, and ten minutes
later the same idea was bought again next door. And a ten-minute-old position
was proposed for exit on a weakening thesis, when every number it had cited had
moved in its favour. All four are fixed and deployed.

**[3:45 — browser: Home Assistant — 25s]** *(director: operator dashboard, then
the read-only team one)*
The same journal publishes over MQTT, fire-and-forget, so a broker being down
never touches a trading cycle. Home Assistant discovers the sensors on its own:
equity, day P&L, halt state, the last decision, per account. This dashboard has
the kill switch and the knobs; the team gets a second one with no controls at
all, because Home Assistant has no per-entity permissions, so the separation has
to *be* the dashboard. The phone gets problems only — never fills. What it does
buzz about is silence: no cycle for twenty-five minutes, the one failure a
dashboard cannot show.

**[4:10 — shot 5 then shot 7 — 25s]** *(director: `demo.sh`)*
At the close, one command rebuilds every round trip from Alpaca's fills, groups
the rejections by the rule that refused them, and scores the priors. A different
model — one that did not trade the day — writes the critique. Then it ships
itself: CI runs seven hundred and sixty-five tests on every pull request, a
runner on the container deploys the merge in about a minute, and a freeze window
hard-fails any trading-code merge while the market is open.

**[4:35 — slide, results — 15s]**
(Fill after Thursday's close.) From Monday's open to Thursday's close the judged
account went ___. ___ round trips. What didn't work: ___.

**[4:50 — slide, thanks — 8s]**
MIT licensed, in the repo. Thanks to Alpaca, lablab.ai and Featherless.

---

## If it runs long

In order, the first things to cut: the Greeks slide to one sentence (−10s), the
second half of the Home Assistant block (−12s), the pagination detail in shot 2
down to "one page is three days on SPY; now it paginates" (−15s). Do not cut the
four bugs — it is the only part of the video that shows the *loop* working
rather than the system running.
