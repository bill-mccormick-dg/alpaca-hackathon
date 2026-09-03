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

**[0:08 — slide 2, thesis — 24s]**
One sentence: buy defined-risk, short-dated options premium on the most liquid
names when an open-source model can name a reason — and let deterministic code
size it, stop it, and close it before expiry. Open model proposes; code
disposes. The trading is autonomous — nobody approves an order. The risk
*envelope* is not: the caps and the two-percent daily cutoff live in git, and
every knob you can reach at runtime expires at the close.

**[0:32 — slide 3, one cycle — 20s]**
Every ten minutes: gates, a snapshot through Alpaca's MCP server, and code's
own exit rules run *before* the model is asked anything. The model gets one
box. It returns JSON. Its proposals and code's own exit sells go through the
same `check_order`, which rejects and never negotiates — and everything either
of them does lands in the journal.

**[0:52 — shot 1, live cycle — 35s]** *(director: `demo.sh` on CT 108)*
Here is a real cycle on a live paper account. Equity and positions from the
broker. The model may investigate first — bars, a snapshot, news, read-only
Alpaca tools, six calls at most, every one journaled. Then it answers: a JSON
array, or an empty one, which is a hold and a perfectly good decision. What it
proposes goes to the gate.

**[1:27 — shot 2, `last_cycle.py` — 30s]** *(director: hold on this screen; it
is the densest thing in the video)*
This is one cycle from the judged account — the inputs, and what came of them.

Top: the option chain it was actually given — twenty-nine hundred SPY contracts
across three API pages, out to forty-five days. One page is five hundred
contracts, which on SPY is *three days*; now it paginates until the window is
covered, and journals the coverage so the question has an answer.

Middle: the second opinion. Kalshi's index-close market and the option chain's
own implied odds — two independent crowds on the same question. Note the
withheld line: that market had barely traded, so the model was told nothing
rather than something unearned.

Bottom: the decision, and the order — with the reason it gave, quoting those
same numbers back.

**[1:57 — slide 4, Greeks — 8s]**
About those contracts: Alpaca's snapshot carries Greeks on ninety-four percent
of the chain, our Black-Scholes solve is the *backstop* for the rest, and every
contract says which it got — so the model knows which numbers are rough.

**[2:05 — slide 7, grading the prior — 20s]**
A prior nobody scores is decoration, so every night we Brier-score the exact
probabilities the model was handed against what the market did. Yesterday:
Kalshi four thousandths, the chain eight — against twenty-five hundredths for a
coin flip. One day, and a day with a clear direction. The point is that we grade
the inputs, not just the model.

**[2:25 — slide 8, grading what it says — 20s]**
And we grade what it *says*. Every percentage the model quotes in a reason is
checked against the prior it was actually handed, and every exit reason against
the account. Tuesday: twenty-two figures quoted, twenty-two exact. The same
session, four attempts to sell a seven-day put — every one citing a forced
expiry close that no code path would produce for another six days — and the exit
that filled called the strike the prior close. All of it journaled, all of it in
the digest, and all of it *reporting only*: prose is not an order parameter.

**[2:45 — shot 4, kill switch — 22s]** *(director: `demo.sh`)*
The leash. One command closes everything — cancelled, settled, closed, verified
against the broker — and writes a halt file. The next cycle refuses to run until
a human deletes it.

**[3:07 — browser: the live viewer — 40s]** *(director: `bot.wpmccormick.pw`;
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

**[3:47 — browser: Home Assistant — 15s]** *(director: the operator dashboard)*
The same journal publishes over MQTT, fire-and-forget, so a broker being down
never touches a trading cycle. Home Assistant discovers the sensors on its own:
equity, day P&L, halt state, the last decision, per account. This dashboard has
the kill switch and the knobs; the team reads the viewer you just saw, which
has none. The phone gets problems only — never fills. What it does buzz about
is silence: no cycle for twenty-five minutes, the one failure a dashboard
cannot show.

**[4:02 — shot 5 then shot 7 — 25s]** *(director: `demo.sh`)*
At the close, one command rebuilds every round trip from Alpaca's fills, groups
the rejections by the rule that refused them, and scores the priors. A different
model — one that did not trade the day — writes the critique. Then it ships
itself: CI runs seven hundred and ninety-five tests on every pull request, a
runner on the container deploys the merge in about a minute, and a freeze window
hard-fails any trading-code merge while the market is open.

**[4:27 — slide 19, results — 15s]**
(Fill after Thursday's close.) From Monday's open to Thursday's close the judged
account went ___. ___ round trips. What didn't work: ___.

**[4:42 — slide 20, thanks — 8s]**
MIT licensed, in the repo. Thanks to Alpaca, lablab.ai and Featherless.

---

## If it runs long

Both of the cheap cuts are already spent: the Greeks slide is down to one
sentence and shot 2 has lost the pagination story, which is what paid for the
audit block at 2:25. What is left to cut, in order: the withheld-prior aside in
shot 2 (−8s), and the second half of the Home Assistant block, from "The phone
gets problems only" (−7s). Do not cut the four bugs — it is the only part of the
video that shows the *loop* working rather than the system running. Do not cut
the audit block either; it and the four bugs are the two things a repo skim
cannot find on its own (#202).
