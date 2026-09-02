---
sidebar_position: 4
title: Strategy
---

# Strategy

What this bot is trying to do, who decides what, and how the day ends. Read
[Operations](operations.md) for how to run it; this page is the *why*.

If you build software but do not trade options, the section below is everything
you need to read the rest of this page. Nothing here is advice — it is a
glossary for the terms these docs use.

<details>
<summary><b>If you don't trade options</b> — the ten terms this page assumes</summary>

- **Option** — a contract to buy (**call**) or sell (**put**) 100 shares of a
  stock at a fixed **strike** price, until it **expires**. It is a bet on
  direction with a deadline.
- **Premium** — the price you pay for that contract. **Going long premium** means
  *buying* options. The alternative, *selling* them, collects premium up front
  but exposes you to open-ended loss — which is why this bot never does it.
- **Defined risk** — because we only ever buy, the worst case is known before
  entry: the premium paid, and not a cent more. That single fact is what lets
  every guardrail here be a simple absolute number.
- **DTE** — days to expiration. A 5-DTE contract expires in five days.
- **Option chain** — the full menu of contracts listed on one stock: every
  strike, at every expiration, calls and puts, each with its own live bid/ask.
  For SPY that is hundreds of contracts per expiry. "The chain" is the market's
  whole opinion about a stock laid out on a grid, and almost everything this
  bot does starts by fetching a slice of it — the strikes within ±8% of the
  current price, in the tradeable expiration window — and showing the model
  12 of them: at and slightly out of the money, call and put, across three
  expiries. Read one row as: "the market will sell you the right to buy SPY
  at 765 until Wednesday for about $2.40."
- **ATM / OTM** — at-the-money (strike ≈ current price) / out-of-the-money
  (strike beyond it). OTM contracts cost less and need a bigger move.
- **The Greeks** — the collective name for four sensitivities of an option's
  price, each written as a Greek letter, and each answering "if *one* thing
  changes, how much does this contract move?" Alpaca's snapshot supplies them
  (plus rho) for most contracts; `bot/greeks.py` derives them only for the
  ones it could not price. They are the next four entries.
- **Delta** — how much the option moves per $1 move in the stock. Runs 0 to +1
  for a call and −1 to 0 for a put, which is why the tactics say `|delta|`. It
  doubles as a rough "chance this finishes in the money", so |delta| ≈ 0.4 is a
  contract that needs a real move but not a miracle.
- **Gamma** — how fast delta itself changes as the stock moves. High gamma means
  your directional exposure shifts under you rather than staying put. It is
  largest for near-the-money contracts close to expiry, which is precisely the
  zone this bot trades — and it is why an overnight gap can do more damage than
  the same move during the session, when a cycle could react to it.
- **Theta** — the daily cost of time passing (the snapshot carries it *per day*).
  Long premium pays theta every day; that is the rent for defined risk, and it
  accelerates as expiry approaches.
- **Vega** — how much the option moves per **one point** of change in implied
  volatility (the snapshot's vega is per 1 vol point, not per whole unit).
  Buying premium is being long vega: if IV falls after you buy, the contract
  loses value even with the stock exactly where you predicted. That is what the
  tactic means by IV looking "cheap or rich".
- **Implied volatility (IV)** — how much movement the option's price implies the
  market expects. High IV means options are expensive.
- **Mark** — the broker's current mid-price for a position, used to value it
  without selling.
- **Notional** — what the position costs to open. For an option that is
  `contracts x 100 x contract price`, which *is* the premium paid, because the
  price fed to the cap is the contract's own bid/ask mid
  (`bot/snapshot.py::price_for_proposal`). It is **not** the underlying
  exposure: ten NVDA calls at $4.00 are $4,000 of premium against roughly
  $180,000 of stock. For shares it is simply `qty x price`. The `$5,000` cap
  below is this number.
- **Assignment** — being forced to buy or sell the underlying stock because a
  contract you *sold* was exercised. Only affects sellers, which is another
  reason this bot only buys.
- **Prior** — a starting belief you hold *before* weighing today's evidence, and
  then revise. Borrowed from Bayesian inference, where a prior is combined with
  new evidence to produce a posterior. Calling the Kalshi distribution a prior
  rather than a signal is the whole point: it is one input the model weighs
  against the option chain and the day's price action, not an instruction to
  follow. Nothing in the code acts on it.
- **"Prior" vs "prior close"** — unrelated, unfortunately. *Prior close* is
  simply the **previous session's closing price**, the reference level the
  Kalshi numbers are measured against. The two senses sit in the same sentence
  more than once ("prior close 7,712 … a PRIOR to weigh"), so it is worth
  reading slowly.

</details>

> **Thesis:** Buy defined-risk, short-dated options premium on the five most
> liquid names when an open-source model sees a concrete reason; deterministic
> code sizes every trade, stops it, and closes it before expiry — the model
> never touches an order.

The sentence above is also the first thing the model reads each cycle
(`strategy_notes` in `config.yaml`, appended verbatim to the prompt by
`bot/decide.py`). Changing the thesis or its tactics is a config edit, not a
code change — that is deliberate, so the end-of-day loop can move fast.

## Why options, and why *long* premium

The hackathon requires options. Buying premium (long calls / long puts) is the
one options posture whose worst case is known before entry — the premium paid —
which makes every trade sizable by a fixed dollar risk and lets the guardrails
stay simple and absolute. Selling premium or multi-leg spreads would need
margin-aware, assignment-aware risk logic we cannot prove correct in four days.

Long premium also matches what the model is actually good at: forming a
directional view from evidence it can name. It is bad at managing a position
minute to minute, so it does not.

## The risk shape is an invariant, not a policy

"This bot only buys premium" is the kind of claim that is usually a convention —
something written in a prompt and mostly respected. Here it is a property of the
code, and the difference matters if you are deciding whether to trust it.

**Selling is capped at what is already held.** `bot/risk.py::check_order()` is
the only path to an order, and its sell branch reads:

```python
if p.side == "sell":
    if p.qty > held_qty:
        return False, f"cannot sell {p.qty}, only {held_qty} held"
```

So `sell` can only ever mean *close something we own*. Opening a short option —
the posture with unbounded loss — is not forbidden by instruction, it is
unreachable: there is no sequence of model outputs that produces one, because a
sell of something not held is rejected before it becomes an order.
`tests/test_risk.py` pins this from three directions (exceeding held quantity,
the exact boundary, and a symbol not held at all).

**Assignment cannot happen either**, and for two independent reasons. Assignment
only affects sellers, and we never sell. Even if that were not true,
`expiry_close_dte` force-closes any contract on its expiry day regardless of
profit or loss, so nothing is ever held into exercise. Two unrelated mechanisms
would both have to fail.

Between them, the worst case for any single position is the premium paid,
bounded by the $5,000 notional cap, with at most four open at once. That is what
makes the guardrails able to be simple absolute numbers rather than
margin-aware, assignment-aware risk logic — the sort we could not prove correct
in four days.

### Where the Greeks come from, and the prompt says so

Alpaca's option snapshot carries `impliedVolatility` and a `greeks` block
(delta, gamma, theta, vega, rho) on most contracts — computed on one surface
with rates and dividends, so put and call deltas at a strike agree. Those are
what the prompt shows, tagged `greeks_source: alpaca`. (Until 2026-08-31 the
bot believed the free feed carried none and re-derived everything; on NVDA the
home-grown numbers were ~5 vol points and ~5 delta points off, with the
call/put skew the wrong way round — see #160.)

The contracts Alpaca does not price — far out of the money, or a one-sided
quote — fall back to `bot/greeks.py`, which solves implied volatility from the
contract's *own* market price and derives the rest, independently per contract.
Those arrive tagged `greeks_source: derived` and the prompt tells the model to
treat them as rough guides, along with an instruction not to spend effort
auditing or reconciling the numbers: skip anything that looks broken and decide
from what is plausible. A contract whose price was too thin even for that
arrives with no Greeks at all and is to be judged on price, strike and DTE
alone.

## When the bot buys or sells stock

Nothing in the code decides between shares and contracts. There is no
instrument-selection function, no heuristic, no threshold. The model chooses,
and the only thing steering it is `strategy_notes` — which is exactly why that
choice is the single key the `mixed` variant changes.

**On the official account, stock is a bit part.** The notes end with one line:
*"Stock is allowed only as a small directional complement, never the main
idea."* The thesis above it is entirely about buying short-dated premium, so the
model has no criteria for reaching for shares and, in practice, rarely should.

**On `mixed`, instrument choice is the decision being tested.** The variant
replaces those notes with explicit criteria and forbids a default:

- **Options** when the view has a horizon — a catalyst, a trend expected to
  resolve within days, or implied volatility that looks cheap for the move
  expected. The capped loss is what lets the position be sized for its risk.
- **Stock** when the view is directional but open-ended, when implied volatility
  is expensive so premium is a poor way to buy the same exposure, or when the
  expected move is a slow grind that time decay would eat. Shares have no expiry
  and no theta.
- *"Neither is the default and neither is a fallback. State the instrument and
  the reason it beats the other one for this specific idea."*

#### "Directional but open-ended", unpacked

That phrase carries the whole distinction, and it is the deliberate opposite of
*"a directional view with a horizon"* in the bullet above it.

**Directional** means you have a view on *which way*. **Open-ended** means you
have no view on *when* — nothing in your reasoning says the move should arrive
by Thursday rather than three weeks out.

That decides the instrument, because an option is a bet on two things at once:
direction **and** timing. It expires. Buy a 7-day call and the move you
predicted arrives on day nine, and you lose the whole premium — being right and
late is indistinguishable from being wrong. The deadline is not a side effect,
it is most of what you paid for; a longer-dated contract costs more precisely
because it carries more time.

Shares are a bet on direction alone. No expiry, no theta, so being early costs
opportunity and nothing else. So "directional but open-ended" means **an option
would charge you for a deadline your thesis does not have** — you would be
paying for precision you cannot supply.

| The view | Has a clock? | Instrument |
|---|---|---|
| "NVDA reports Thursday and I think they beat" | yes — a dated event resolves it | option |
| "NVDA's uptrend has further to run" | no — real view, no deadline | stock |

Read together, the three stock criteria are one idea from three angles, all
saying the option's *pricing* works against this particular thesis rather than
that the thesis is weak: you are paying for a deadline you do not need
(open-ended), overpaying for the same exposure (expensive IV), or watching theta
eat the move before it arrives (a slow grind).

That is the whole experiment: two accounts, same market, same cadence, one
difference — whether instrument choice is a deliberate decision or an
afterthought — and the P&L answers it instead of us. Because the notes force the
model to *name* the instrument and say why it beats the other one, the journal
carries that reasoning per trade, which is what lets `eod_review` attribute P&L
to instrument choice rather than to luck.

### What the code does differently for shares

Almost nothing, which is the point. `check_order()` has exactly two
instrument-aware branches:

1. the contracts-per-order cap and the DTE window, which only apply to options;
2. the notional calculation — `qty x price x 100` for a contract, `qty x price`
   for shares.

Everything else is identical: the five-name whitelist, the $5,000 cap, four
concurrent positions, the 15:15 ET entry cutoff, the 2% daily-loss halt. A stock
proposal is checked, rejected or executed by the same funnel, and is journalled
the same way.

**Shorting is impossible here too.** The sell-capped-at-held-quantity rule above
is not option-specific, so the bot can only ever sell shares it already owns.
There is no path to a short equity position any more than to a naked short call.

### How a stock position ends — and one thing to watch

`bot/exits.py` computes `days_to_expiration` as `None` for anything that is not
an option, so the expiry rule simply does not apply. Stop-loss and take-profit
do, on the position's own price, exactly as for a contract.

**That is worth thinking about, because the thresholds were chosen for
premium.** A 40% stop and a 60% take-profit are ordinary moves for a short-dated
option, which routinely halves or doubles in a session. For shares of SPY they
are enormous — a 40% drawdown on an index ETF is a market crisis, not a stop.
So on a stock position the code-driven exits are effectively dormant, and the
position is managed by the model proposing an exit, or by the daily-loss halt,
or not at all. If `mixed` starts holding real stock positions, that asymmetry is
the first thing to revisit.

The end-of-day backstop treats them differently too: `flatten.py --expiring-only`
skips anything that is not an expiring option (`bot/flatten.py`, "stock or
unparseable"), so shares are held overnight by design. Only the
`final_flatten_date` run closes everything.

## Division of labour

| Decision | Who | Where |
|---|---|---|
| Which underlyings, what limits | human, in config | `config.yaml` |
| Whether there is a reason to trade, which direction, which contract | model | `bot/decide.py` prompt → JSON proposals |
| Whether a proposal is allowed at all | code, never negotiated | `bot/risk.py::check_order()` |
| Sizing cap, position count, DTE window, entry cutoff | code | `bot/risk.py` |
| Stop-loss / take-profit on premium, forced close on expiry day | code, every cycle | `bot/exits.py` (the deterministic exits) |
| Daily-loss halt, kill switch, end-of-day handling | code | `run_cycle.py`, `flatten.py` |
| The only order path | code | `bot/execute.py::place_proposal()` |

The model is given: account state, per-underlying spot price, a menu of ~12
contracts per name (the chain is fetched across the *whole* window — paginated,
since one API page covers only 1–3 DTE on SPY/QQQ — and `bot/menu.py` spends
the 12 slots on three expiries across the tactics' 2–14 DTE band, and per
expiry and side the at-the-money strike plus the out-of-the-money strike whose
delta is nearest 0.40; see "What the model sees" below for why) with
bid/ask/last and Greeks (Alpaca's own IV and delta/gamma/theta/vega/rho where
the feed supplies them, which is most contracts; `bot/greeks.py` derives them
from the contract's market price for the rest, tagged as such), the hard
limits, and the
strategy notes. It returns a JSON array of proposals or `[]`. Holding is the
default and is treated as a good decision.

## Tactics the notes currently encode

- Buy calls or puts with 2–14 DTE, at or slightly OTM (|delta| ≈ 0.35–0.55).
- Evidence-based direction only: price vs prior close, a clear intraday trend,
  IV cheap or rich relative to the contract's peers. No reason → no trade.
- One idea per underlying at a time.
- Exits belong to code (stop / take-profit / expiry close); the model may still
  propose an early exit on a thesis change and must say why.
- Stock only as a small directional complement.

## The second opinion: a prediction-market prior

The option chain tells you what *options traders* are pricing. Kalshi's daily
index-close markets tell you what a different crowd, betting on the same close,
believes. Where those disagree is worth the model's attention, so the agent is
handed both.

Kalshi lists one YES contract per price bucket for the S&P 500 (`KXINX`) and
Nasdaq-100 (`KXNASDAQ100`) close. The set of YES prices across ~30 buckets *is*
a crowd-implied probability distribution of today's close. `bot/predictions.py`
normalises it (raw YES prices sum above 1 - that overround is the bookmaker's
edge, divided out) and reduces it to a few facts the chain cannot give:

- **implied median close**, and the move that implies from yesterday
- **P(close above yesterday's close)**
- **P(up > 1%)** and **P(down > 1%)**
- the **volume** behind all of it

Yesterday's settled market carries `expiration_value`, the actual index close,
which supplies the reference level.

### It is a prior, not a signal

Nothing in the code acts on it. `risk.py`, `execute.py` and `exits.py` never
see it; it appears in exactly one place, the prompt, labelled:

> PREDICTION MARKETS (crowd-implied, read-only - PRIORS to weigh, not signals
> to copy): Kalshi's event market on the index close, and the option chain's
> own implied odds … DISAGREEMENT between them is information; agreement is
> just the base rate. Compare both to today's price action. When you cite one
> of these numbers in a reason, quote it exactly as printed or refer to it by
> name - never round or restate it; quoted figures are audited against this
> block:

So it can only influence a trade by persuading the model, and anything it
inspires still passes the same `check_order()` funnel as every other proposal.
Read-only, no API key, never traded, cached for five minutes, and silently
omitted on any failure.

**And the model's citations of it are audited (#172).** Built on a suspicion
that turned out to be the operator's error, and kept because the check is
cheap and the risk is real. On 2026-09-01 the judged account's model wrote
"68.7% chance of down>1% close", "only 7.6% chance of finishing above prior
close" and "extreme bearishness (81.9% down>1%)". Read against the prompt an
hour earlier those looked invented and skewed toward the trade; read against
the prior journaled in the *same* cycle — 13:00 and 13:20 Eastern, where the
email and the viewer had shown 12:00 and 12:20 Central — all three are exact.
The first run of the audit over that day found 22 quoted figures, 22 supported.
What it did surface was subtler: at 13:20 the QQQ chain prior had been withheld
as noisy, and the model quoted SPY's chain figure (27.3%) as "the options
market" for QQQ — a real number under the wrong name.

So `bot/citations.py` checks every percentage the model writes in a
prior-shaped clause against every number the prior block carried (both crowds,
both underlyings, and complements for "P(below)" phrasing), and reports two
kinds of miss: *unsupported*, a figure that matches nothing within half a point,
and *misattributed*, a figure that matches only another underlying's prior
while this one had a prior on show. Both ride on the `decision` journal event
as `citations`; the digest prints the counts and each miss beside the real
value; the reviewer prompt says to judge on the journalled prior rather than
the quote; the feed marks the cycle. Deliberately reporting only:
`check_order()` bounds what can be traded and does not grade rhetoric. The
prompt's last sentence above — quote or name, never restate — is the cheap
half; the audit is how we know whether the model's prose can be trusted on a
given day, which after day 2 it could.

### When it is withheld

A range market that has barely traded still quotes every bucket, and the
midpoint of thirty wide spreads is noise. Normalising noise does not make it a
belief - it makes a *flat* distribution that looks authoritative. That is worse
than showing the model nothing, so two gates decide whether the prior is fit to
show:

| Gate | Default | Catches |
|---|---|---|
| `predictions_min_volume` | 250 | nobody has traded it, so there is no crowd to imply anything |
| `predictions_max_flatness` | 0.93 | quotes too close to uniform to carry information |

**Flatness** is Shannon entropy over `log(n)`: `1.0` is perfectly uniform,
lower is more peaked. Normalising by bucket count is what makes it comparable -
Kalshi splits some days into 6 buckets and some into 30, and a raw measure
(modal bucket as a multiple of uniform, say) rates a genuinely peaked 6-bucket
market the same as a flat 30-bucket one.

A withheld prior is still **journalled, with the reason**, so "the model got no
second opinion today" is an answerable question rather than an absence.

### A worked example

Run it against the live feed any time:

```sh
python scripts/verify_predictions.py            # official config
python scripts/verify_predictions.py --no-cache
```

Real output, taken in the early hours of 2026-08-31, before that session
opened. One run, both outcomes:

```
gates               volume >= 250.0, flatness <= 0.93

--- SPY via KXINX ---
  event            KXINX-26AUG31H1600  (index close 2026-08-31T20:00Z)
  reference close  7711.76
  implied median   7687.5  (-0.31%)
  P(above prior)   0.293
  P(up >1%)        0.049
  P(down >1%)      0.15
  buckets/volume   30 buckets, volume 756.3
  flatness         0.689   (1.0 = uniform = no information)
           7675.0-7699.9999  0.268
           7650.0-7674.9999  0.187
           7700.0-7724.9999  0.159
           7625.0-7649.9999  0.102
  VERDICT          usable - shown to the model

--- QQQ via KXNASDAQ100 ---
  event            KXNASDAQ100-26AUG31H1600  (index close 2026-08-31T20:00Z)
  reference close  29433.43
  implied median   29450.0  (+0.06%)
  P(above prior)   0.514
  buckets/volume   30 buckets, volume 45.5
  flatness         0.801   (1.0 = uniform = no information)
  VERDICT          thin: volume 45.5 < 250.0
```

SPY clears both gates on 756 contracts and a distribution with a clear peak, so
it is shown. QQQ quotes all thirty buckets just as confidently on **45**, so it
is withheld. Nothing about the QQQ numbers looks broken - that is the point.
A thin market does not announce itself; it produces a plausible-looking
distribution that nobody has put money behind. Volume is the gate that catches
it, and the model is told nothing rather than something unearned.

The block the model actually receives is then just the surviving line:

```
PREDICTION MARKETS (Kalshi, crowd-implied, read-only - a PRIOR to weigh, not a
signal to copy; compare to what the option chain implies and to today's price
action):
- SPY via KXINX (index close 2026-08-31T20:00Z); prior close 7,712, implied
  median 7,688 (-0.31%); P(above prior close) 0.293, P(up>1%) 0.049,
  P(down>1%) 0.15; volume 756.3
```

### The reference close has to be the right day

Every figure above except the median is measured **against the previous
session's close**, so that one number is a yardstick the whole block depends on.
Getting it wrong does not produce an obvious error - it produces the same
confident output, silently shifted.

That is not hypothetical. Until the early hours of 2026-08-31 the code asked
Kalshi for settled markets and took the first one the response happened to
contain. That page is not ordered by date: it returned **Thursday** Aug 27's
close (7730.99) while **Friday** Aug 28's (7711.76) sat further down - past the
40-row window the code was even asking for. A 0.25% error in the yardstick moved
the numbers handed to the model like this:

| | SPY, Thu ref -> Fri ref | QQQ, Thu ref -> Fri ref |
|---|---|---|
| implied move | -0.56% -> **-0.31%** | -0.98% -> **-0.28%** |
| P(above prior close) | 0.153 -> **0.297** | 0.323 -> **0.490** |
| P(down > 1%) | 0.356 -> **0.287** | 0.445 -> **0.323** |

QQQ went from a clearly bearish prior to a coin flip. Both gates passed
throughout - the distribution was real and the volume was what it was. Only the
yardstick was wrong, and every error it caused pointed the same way.

`latest_settlement()` now picks by `close_time` rather than by position, over a
page wide enough to contain the most recent settlement, and **withholds the
reference entirely** if the newest one is more than five days old rather than
measuring against a stale close. Five days covers the longest ordinary gap, a
Thursday close before a Friday holiday.

The general lesson is the one this codebase keeps relearning: a gate can only
catch the failure it measures. Volume and flatness both ask *is this
distribution worth believing*. Neither asks *is it being compared to the right
day*.

### The chain's own odds

Kalshi is not the only crowd in the room. Every call price is a probability
in disguise: **P(S > K) = −dC/dK**, read straight off adjacent strikes. The
bot already fetches the ladder every cycle, so it computes the chain's own
implied distribution — survival probabilities from call-mid differences,
smoothed to the closest non-increasing curve, measured against the ETF's own
prior close — and shows it beside the Kalshi line in the same shape:

```
- SPY via KXINX (index close ...); P(above prior close) 0.27, ...; volume 976
- SPY via option chain (calls exp 2026-09-01); prior close 769.28,
  implied median 766.31 (-0.39%); P(above prior close) 0.253, ...
```

Two independent crowds measuring nearly the same thing. The prompt tells the
model what the pair means: **disagreement between them is information;
agreement is just the base rate.**

The chain prior has its own gates, mirroring Kalshi's in spirit: increments
are used only where both quotes' spreads are tighter than the strike gap
they span (deep-ITM calls carry $1–3 spreads against $1 gaps — their noise
is larger than the derivative step), and a curve the isotonic smoothing had
to move by more than 0.05 probability on average is withheld as noise
wearing a distribution. Its quiet advantage is that SPY options have no
thin-volume failure mode: on the first live run, QQQ's Kalshi market was
withheld at 46 contracts while the chain still produced a usable prior —
which is precisely the flatness gate's blind spot (#111) being covered from
the other side.

Idea credit: fellow contestant **greatfriend#8857** (Discord), whose framing
— "every option price is a probability in disguise" — named the half of
their design that fits this bot's thesis. (The other half, trading spreads,
deliberately does not.)

### The flatness gate's blind spot

Flatness cannot distinguish *these quotes carry no information* from *the crowd
genuinely expects a wide day*. Modelled against well-priced 30-bucket
distributions:

| Session | Flatness | At 0.93 |
|---|---|---|
| calm, daily sigma 0.5% | 0.602 | shown |
| normal, 0.8% | 0.740 | shown |
| active, 1.2% | 0.858 | shown |
| volatile, 1.8% | 0.948 | **suppressed** |
| very volatile, 2.5% | 0.983 | **suppressed** |

So a correctly-priced high-volatility session is withheld precisely when a
second opinion is worth the most. **Volume is the load-bearing gate**; treat
flatness as a backstop against flat quotes. If it starts suppressing liquid
days, raise it rather than concluding the market is broken - it is a config
key, so that is an override rather than a deploy.

The real fix is to measure quote *width* instead of distribution shape: an
unpriced market is one where every bucket carries a wide bid/ask, and that
stays true however volatile the day is.

## What the code enforces regardless of the notes

`config.yaml` is the source of truth, and every value there is a hard cap in
`bot/risk.py` — the model proposes, the config decides. At the time of writing,
with the reasoning each carries in `config.yaml`:

| Limit | Value | Why |
|---|---|---|
| Whitelist | `SPY QQQ AAPL MSFT NVDA` | the most liquid names, so a fill is always available and the spread is narrow |
| Notional per position | $5,000 | what the position costs to open: `contracts x 100 x contract price` for options — the premium paid, *not* the underlying exposure — and `qty x price` for shares |
| Concurrent positions | 4 | keeps total exposure comprehensible at a glance |
| Contracts per order | 10 | "a backstop against a fat-fingered or hallucinated large size, independent of the dollar cap" |
| Expiration window (entries) | 2–45 DTE | "guards against 0-DTE gamma risk on one side, multi-month decay drag on the other" — and, since #166, the floor sits above `eod_close_dte` so the funnel refuses what the 14:50 backstop would sell the same day. Sells are exempt: a position must stay closable to expiry |
| Entries | 09:45–15:15 ET | skips the opening auction's noise; stops opening new risk near the close |
| Sells | until 15:45 ET | exits stay legal after entries stop |
| Daily loss | 2% of start-of-day equity | breaching it flattens and halts for the day |
| Resting orders | counted | a buy still working at the broker counts as held: a second buy for the same symbol is refused, and its symbol counts toward the position cap — the caps bound *committed* exposure, not just what has filled (#171) |
| Stale entries | cancelled | any of the bot's own buys still open at the top of a cycle was sent by an earlier cycle; it is cancelled (journaled as `order_canceled`) before the model or the funnel look at the account. Sells are never cancelled this way |

**These are the hard caps, not the tactics.** The prompt asks for 2–14 DTE (see
above); the *code* permits 2–45. The narrower band is a preference the model is
told to favour, the wider one is the limit it cannot cross — so a sensible
contract slightly outside the tactic is allowed, and a wild one is not.

### Why the whitelist is five names

**Alpaca does not impose it.** The broker will trade any optionable symbol it
supports; `SPY QQQ AAPL MSFT NVDA` is our choice, living in `config.yaml` and
enforced in `bot/risk.py`. Nothing about the platform caps the list.

Worth separating from a number it is easy to confuse with:
`research_contracts_per_underlying: 12` is **12 option contracts per name**, not
an allowance of twelve symbols. The two are different axes, and together they
set the size of the menu: 5 underlyings × 12 contracts = **60 contracts in
every prompt**, each with strike, DTE, bid/ask/last and Greeks.

Three reasons the list stays short:

**Liquidity, which is the stated one.** These carry the tightest option spreads
available. That matters more than it sounds: every position pays the spread
twice, entering and exiting, and against a 40% stop and 60% take-profit the
spread is a real fraction of the move being traded for. On a thinner name it can
consume the edge outright.

**Breadth is capped elsewhere anyway.** `max_positions` is 4. Adding names
widens the menu without widening what can be held, so the binding constraint is
position count, not candidate count — and with four trading days there is no
time for a wider net to pay off.

**The prompt is not free.** Those 60 contracts already dominate it. Doubling the
names doubles what the model reads before answering inside an 800-token budget,
and lengthens every cycle (one more snapshot fetch per name) against a
ten-minute cadence.

**Changing it** is a one-line config edit — the whitelist is data, not code. Two
caveats: `config.yaml` is trading code under the [deploy
freeze](operations.md#deploy-safety-during-the-scoring-week), so it cannot land
Mon–Fri 08:20–15:15 CT; and know what raising
`research_contracts_per_underlying` actually surfaces: the pool is the whole
strike band across the whole expiration window, and the first twelve slots go
to the at-the-money and slightly-out-of-the-money strike per side across three
expiries (`bot/menu.py`, #159); slots beyond that fill nearest-the-money first,
so a higher count mostly adds neighbouring strikes on SPY/QQQ and more expiries
of the same strikes on NVDA/AAPL/MSFT.

One mechanical detail, in case you ever debug a rejection: an option proposal is
checked against its **underlying**, not its OCC symbol. A contract on a
whitelisted name passes regardless of how its symbol is spelled.

## Holding period and end of day

Judging is on total equity, not daily P&L, and the DTE window allows
multi-day holds. So the end-of-day rule is *not* "flatten everything":
`flatten.py --expiring-only` (the cron backstop) closes contracts expiring
within `eod_close_dte` days; everything else is held overnight under the
per-position stop/take-profit (`bot/exits.py`, checked every cycle before the
model is consulted), and any contract reaching `expiry_close_dte` is
force-closed that day regardless of profit or loss.

The two keys are easy to confuse and do different jobs:

| Key | Default | Who acts on it | When |
|---|---|---|---|
| `expiry_close_dte` | 0 | `bot/exits.py`, **every cycle** | closes a contract once it has this many days left — 0 means "expiring today" |
| `eod_close_dte` | 1 | `flatten.py --expiring-only`, **once at 15:50 ET** | the cron backstop, in case a cycle did not run |
| `min_days_to_expiration` | 2 | `bot/risk.py::check_order()`, **on every buy** | the entry floor — must sit *above* `eod_close_dte`, or the funnel admits contracts the backstop is guaranteed to sell that afternoon |

One is the continuous rule; the other is the end-of-day safety net behind it;
the third keeps entries out of the zone the second one clears.

**The floor gates entries only.** Until #166 the expiration window ran on every
option order, sells included, which had two bad consequences. Raising the floor
to keep 1-DTE entries out would have made every 1-DTE *holding* unsellable
through the funnel — by the model, and by `exits.py`, whose stop, take-profit
and expiry-day sells pass through the same `check_order()`. And at the old
floor of 1, a contract reaching its expiry day was already refused
("0 days to expiration outside [1, 45]"), so the expiry-day rule could not fire
at all; only the previous afternoon's backstop stood between a position and
exercise. Sells now skip the window entirely — a sell only ever closes something
held, and the one thing it checks is that the contract has not already expired.
`RiskManager` logs a warning at startup whenever the floor does not exceed
`eod_close_dte`, because those two keys only agree by hand.

**Both are now stated in the prompt.** The model used to be told the expiration
*window* (1–45 DTE) but nothing about the rules that *end* a position, so it
could propose a short-dated contract in good faith that the 15:50 backstop would
sell hours later — which reads as model error in the journal when it is policy
the model never saw. The prompt now names both values, drawn from config rather
than written into the text, so raising either cannot silently desynchronise the
instructions from the code (`tests/test_decide.py` pins that they track config
and that the defaults match what `exits.py` and `flatten.py` actually do).

At `eod_close_dte: 1` this is close to a no-op — the model rarely proposes 1-DTE
contracts and the whole point of the setting is that the bot never carries a
contract into its final overnight. It matters if the value is ever raised: at 4,
with the tactics asking for 2–14 DTE, every entry at 2–4 DTE would be bought and
sold the same afternoon, and on Thursday the 15:50 sweep would liquidate most of
the book minutes before the mark that decides the score.

**The score is fixed at Thursday's close.** Per Alpaca's FAQ, the Fri Sep 4
09:30 ET snapshot *"will look at the portfolio's total equity as of EOD
Thursday Sep 3rd"*, with exercises/assignments of Sep 3 expiries reflected.
Consequences:

- Contracts expiring Thu Sep 3 are closed that day by the expiry rule — never
  left to exercise into a surprise stock position.
- Positions held through Thursday's close count at their **mark**; selling
  them Thursday afternoon would only pay the bid/ask spread. So Thursday's
  backstop is the normal expiring-only one, not a flatten-all.
- **Fri Sep 4** (`final_flatten_date`): no new entries all day, and the
  end-of-day backstop flattens *everything* so nothing is carried over the
  weekend. That is cleanup after the snapshot, not part of the score.

## What "working" looks like by Thursday

A tiny sample, so diagnostics rather than verdicts (`trade_report.py`, round-trip attribution):

- The model traded on stated reasons, and the journal shows them — and, since
  #172, says how often those reasons quoted numbers the prompt never contained
  (day 2: none of 22).
- Rejections are rare and *sensible* (a cap, not a malformed proposal) — a
  guardrail rejecting the same idea all day is a prompt bug, not a win.
- Exits were mostly stops/take-profits doing their job, not expiry rescues.
- Equity above $100,000 helps; a clean, explainable curve with the guardrails
  visibly working is the write-up either way.

## Why these models

Four seats take a Featherless model: the judged account, each of the two
challenger accounts, and the end-of-day reviewer, which is not a fourth model
but whichever of the others did not trade that account. This section says how
they got there, what would change them, and what has been ruled out.

### The honest history: nothing was selected

The current model is a **compatibility-driven default, not the winner of a
comparison**, and it is worth saying so plainly before the criteria below,
because the criteria read like a rationale and were not one.

`moonshotai/Kimi-K2-Instruct` entered the repo in `36c8f7a` (Fri Aug 28, step
2 of the build) as `FeatherlessClient`'s default, on one stated ground:
"Featherless's docs confirm native tool-calling support on it, which the
decision loop will need down the line." It was promoted to a config value in
`d5912db` — a commit about making the model *configurable*, which argues about
cost attribution and not at all about which model. No benchmark was run, no
alternatives were compared, and no issue records a choice. It worked, so it
stayed.

Two later moves were equally undecided:

- The judged account was pinned to `moonshotai/Kimi-K2.6` in
  [#167](https://github.com/bill-mccormick-dg/alpaca-hackathon/issues/167) —
  explicitly *not* a claim that K2.6 is better. A runtime override had put 32
  of day 1's 33 decisions on K2.6; the pin makes the config match what the
  account actually traded, rather than letting an expiry silently revert it to
  a model with one live cycle behind it.
- The `test` account's `Qwen/Qwen3.8-Flash-Next` arrived with the
  aggressive-challenger config, alongside changes to research tools, the
  learning loop and several knobs.

**The two-account A/B has therefore never isolated the model.** `official` and
`test` differ in model *and* research tools *and* the learning loop *and*
prior settings, so the equity difference between them measures a config
bundle. `farm.py` was built to run N single-variable challengers in parallel
([#13](https://github.com/bill-mccormick-dg/alpaca-hackathon/issues/13)) and
has only ever run the three production accounts. Presenting an invented
rationale would be worse than admitting the default.

So what follows is **selection policy, not post-hoc justification**: the gates
a candidate must clear, each tied to something the code already enforces or
the pipeline already depends on, and what would actually make us switch.

### The gates, and the code behind each

| # | Gate | Why — the thing in this repo that makes it binding |
|---|---|---|
| 1 | **Open weights, on Featherless** | Hackathon requirement. It is why the Claude CLI that `alpaca-trader` used is not here at all. |
| 2 | **Native tool calling** | `bot/research.py::TOOLS` offers four read-only tools; without `features.tool_use` the challenger config cannot run. |
| 3 | **Context for the prompt, with headroom** | One decision prompt is ~5k tokens with 60 contracts (5,246 measured). The bounded research loop accumulates far more: 95,328 prompt tokens on one test-account decision across six tool calls. 4k-context models cannot be handed the prompt at all. |
| 4 | **Follows the instructions in the prompt** | Not just valid JSON — the right *content*. `bot/risk.py` rejects what breaks a cap, but a model that proposes five actions against `max_positions: 4`, or 1-DTE contracts the 14:50 backstop will close the same afternoon, wastes the cycle inside the rules. |
| 5 | **Answers reliably, warm** | A 10-minute cron with `request_timeout_sec: 60`. A model that has to cold-start, or that returns `capacity_exhausted`, forfeits the cycle — the next attempt is the next cron slot. |
| 6 | **Thinking mode can be turned off** | Every config carries `model_params.chat_template_kwargs.enable_thinking: false`, and it is load-bearing rather than insurance: without it a thinking model spends the whole `max_tokens` budget on hidden reasoning and returns **empty content** with `finish_reason=length`. A model that ignores the flag is unusable here. |
| 7 | **Costed, and cheap enough to run all day** | `model_prices` in config feeds both the dashboard's model dropdown and `bot/review.py::estimate_cost_usd`, so a model that is not priced is a model whose spend is not reported. |
| 8 | **Not its own reviewer** | `bot/config.py::resolve_review_model()` needs at least one entry in `review_model_preference` that is not the account's own model — see [the critique comes from a model that did not trade](#the-critique-comes-from-a-model-that-did-not-trade). |

Gates 1–3 and 5–7 are mechanical and checkable from the catalog. **Gate 4 is
the one that discriminates**, and it is the one no catalog field reports.

### The lineup, and the evidence for it

Live from Featherless's catalog. Everything in this table is printed by
`python scripts/verify_models.py --all-configs --rejected`, so it can be
re-checked rather than believed:

| Seat | Model | Tools | Context | $ in/out per M |
|---|---|---|---|---|
| `official` trades (`config.yaml`) | `moonshotai/Kimi-K2.6` | yes | 262,144 | 0.77 / 3.50 |
| `test` trades (`config-test.yaml`) | `Qwen/Qwen3.8-Flash-Next` | yes | 32,768 | 0.15 / 0.50 |
| `mixed` trades (`config-variants/mixed.yaml`) | `moonshotai/Kimi-K2-Instruct` | yes | 32,768 | 0.60 / 2.50 |
| Reviewer | computed per account from `review_model_preference` | — | — | — |

Two things that table does not say, which the script does:

- **Availability is per-model and it moves.** On 2026-09-02 the other two
  were `warm` but `Kimi-K2-Instruct` was `unregistered`: not loaded, so the
  next call to it pays a cold start. K2-Instruct is the judged account's
  *reviewer*, one call a day after the close, which is the seat where a cold
  start costs least; in a trading seat that same tier is gate 5 failing. The
  script prints the tier on every run, because it changes underneath you.
- **Advertised context is a lower bound, not a ceiling.** The 95,328-token
  research cycle ran on a model the catalog lists at 32,768, and Featherless
  answered it (`finish_reason=stop`). So context is a disqualifier at the
  bottom end and not a ranking anywhere else.

Runtime overrides can move any account onto any model in `model_prices` from
the dashboard, and they **expire at 16:00 ET**. The table above is git — what
an account is running *right now* is in its `config` journal event and on the
dashboard.

### Rejected alternatives

**The finance-domain fine-tunes.** The obvious move for a trading bot is a
model trained on finance. Featherless's "Finance LLMs" category has nine, and
the five most on-the-nose for options — `vfaix-vpa-options-trader`,
`vfai-x-3.5-9b-options`, `atlas-r2-qwen3-14b`, `NEXUS-Finance`,
`atlas-finanzas-deepseek` — **are not in the API catalog at all** (confirmed
again 2026-09-02). They are visible in the web UI, marked COLD, and cannot be
called. Of the ones that are callable, `AdaptLLM/finance-chat` and
`hyokwan/familidata` are 4k-context with no tool use: they fail gates 2 and 3
outright and the script prints both failures.

**The one that clears every mechanical gate — `mukaj/Llama-3.1-Hawkish-8B`.**
This is the candidate worth documenting, because nothing about the catalog
rules it out. Tool use, 32k context, on our plan, and **$0.14 / $0.26 per M —
cheaper than anything we run.** Tested 2026-08-31 against the live
`config-test.yaml` prompt (60 contracts, ~4.9k tokens, research tools off,
three runs each):

| | Hawkish-8B | Kimi-K2-Instruct |
|---|---|---|
| Latency, single call | 7.4s | 1.8s |
| Valid JSON array | 3/3 | 3/3 |
| Proposals | 1, 5, 5 | 0, 0, 0 (hold) |
| Contracts at 1 DTE | **3/3 runs** | none |

Every Hawkish run proposed at least one 1-DTE contract, which the prompt says
plainly "will be sold the same afternoon regardless of how it is doing", and
`strategy_notes` asks for 2–14 DTE. One run proposed **five actions against
`max_positions: 4`**, so the funnel would have refused one outright; another
proposed a SPY call and a QQQ put simultaneously on the single reason that
"implied volatility is low relative to the underlying's price action".

The JSON was well-formed every time. It is the *content* that ignores the
constraints — which is gate 4, and the distinction the rubric turns on:
**syntactic compliance is necessary and not sufficient, and a domain
fine-tune buys financial vocabulary rather than constraint-following.**

Stated honestly: three samples, one off-hours prompt, market closed, research
disabled. Kimi holding after hours is also the easy answer. This is evidence
about instruction-following, not a verdict on live intraday P&L — the real
test is a challenger account, never the judged one.

**It also found a bug, which is the better argument for testing candidates at
all.** `min_days_to_expiration` was 1 while `eod_close_dte` is 1, so the funnel
admitted exactly the entries the 14:50 backstop was guaranteed to close the
same afternoon — the guardrail permitted what the prompt forbade, and only the
incumbent model's goodwill was closing the gap. Every config now floors at 2
([#166](https://github.com/bill-mccormick-dg/alpaca-hackathon/issues/166)).
Gate 4 was doing a job the code should have been doing, and now is.

**`GLM-5.3-Flash`, on gate 6 alone.** It ignores
`chat_template_kwargs.enable_thinking` entirely — the flag is accepted and has
no effect — so there is no way to stop it spending the token budget on hidden
reasoning. That is a whole-config failure and not a tuning problem, and it is
noted in `config-test.yaml` beside the setting so nobody tries it twice.

**A snapshot that already rotted, which is the argument for the script.**
`KBTG-Labs/THaLLE-0.2-ThaiLLM-8B-fa` returned `capacity_exhausted` on a live
call on 2026-08-31. Re-tested 2026-09-02 it answers fine — though only with
`enable_thinking: false`; without it, it burns the whole budget on hidden
reasoning and returns empty content, gate 6 exactly. A model rejected on
availability is rejected *as of a date*, which is why the evidence lives in
`scripts/verify_models.py` and not only in this paragraph.

### What would make us change

In order of what would actually move us, not in order of how easy it is to
measure:

1. **Constraint adherence on the real prompt, measured.** Replay a fixed set
   of journaled prompts through a candidate and score DTE compliance, proposal
   count against `max_positions`, sell-symbol correctness, and whether cited
   numbers appear in the prompt (`scripts/audit_citations.py` already scores
   that last one). This is the artifact that is missing, and it is cheap.
2. **A single-variable live A/B.** `farm.py --account <name> --config ...` on
   its own paper account, differing from `config.yaml` in `model` and nothing
   else — the thing the current `official`/`test` pair cannot give us.
3. **A gate failing on the incumbent.** A model going `unregistered` in a
   trading seat, ignoring `enable_thinking`, or repeatedly returning
   `finish_reason=length` (the digest counts `truncated_outputs` daily) is a
   reason to move without any comparison at all.

Cost breaks ties and does not decide: the whole day's inference is dollars, and
one avoidable bad fill costs more than a week of it.

**During the scoring week, the bar for switching the judged account is
higher than any of the above.** Continuity beats an unvalidated improvement:
#167 pinned K2.6 for exactly that reason.

## Daily loop

After each close: `eod_review.py` (the end-of-day digest) → read the digest → edit
`strategy_notes` / exits / knobs → PR → CI → deploy → the next morning runs
the new version. Promote the test-account challenger config (the two-account A/B) when it
wins convincingly.


### The critique comes from a model that did not trade

The digest ends with an advisory read of the day and **one** recommended change.
That paragraph is what the daily loop turns on, so where it comes from matters:
a model reviewing its own reasoning tends to explain rather than challenge it.

So `eod_review.py` asks a *different* model. The choice is computed, not
configured — `bot/config.py::resolve_review_model()` walks
`review_model_preference` in order and takes the first entry that is not this
account's own `model`:

| Account | Trades on | Reviewed by |
|---|---|---|
| `official` | Kimi-K2.6 | **Kimi-K2-Instruct** |
| `test` | Qwen3.8-Flash-Next | **Kimi-K2.6** |
| `mixed` | Kimi-K2-Instruct | **Kimi-K2.6** |

No account is ever its own reviewer, because the rule is computed rather than
configured: when `official` was pinned to K2.6 its reviewer moved to
K2-Instruct on its own, with no second edit. That is the whole point of
resolving it at review time. It costs one call a day.

**It is recomputed every time, deliberately.** The trading model is changeable
at runtime from the dashboard. Had the reviewer been resolved once and stored,
switching an account onto K2.6 would quietly leave it grading its own homework —
the failure would be invisible, because the digest would still arrive and still
read plausibly. Recomputing means the property holds without anyone maintaining
it: switch `official` to K2.6 and the reviewer drops to K2-Instruct on the next
run. `tests/test_config.py` pins exactly that transition.

Set `review_model` — in config, or as a runtime override, or from the
dashboard's **Review model** selector — to pin one instead of computing it.
Unset is the normal case.

**The claim is checkable, because for two days it was false.** From #127 the
resolved reviewer was journaled on every `config` event and shown on the
dashboard, but `eod_review.py` itself still built its client on `model` — so
on 2026-08-31 and 2026-09-01 every account graded its own homework while this
section said otherwise (#177). Since then the digest's last heading names the
model that actually wrote it (`## Model's read of the day (advisory, by …)`),
and the `eod_review` journal event carries `review_model`. If the heading ever
names the account's trading model, the preference list is exhausted or
misconfigured — not a feature.

#### What it actually produces

From a verification run on Kimi-K2.6 (against a *synthetic* digest — no real
trading had happened yet):

> "…one explicitly rejected by the guardrails because the requested quantity of
> 14 contracts exceeded the configured maximum of 10 per order. **This rejection
> indicates a mismatch between the agent's sizing logic and the current risk
> limits, rather than a market failure.**"

That distinction — a guardrail firing because the *prompt* is wrong, not because
the market was — is the read worth having, and it is the one a self-review is
least likely to volunteer.

**It is advisory and nothing more.** No code acts on it. `eod_review.py` places
no orders, the recommendation is prose in a markdown file, and a human decides
whether it becomes an override or a config PR. `--no-model` skips it entirely
and still produces the numbers.

## Runtime overrides (intraday, no deploy)

`config.yaml` in git is the base. `logs/overrides.yaml` on the CT — written
only through `bot/overrides.py` (the `override.py` CLI today, the MQTT bridge
in the Home Assistant integration) — wins for these keys: `model`, `temperature`, `max_tokens`,
`strategy_notes`, `research_contracts_per_underlying`,
`option_strike_band_pct`, `stop_loss_pct`, `take_profit_pct`,
`eod_close_dte`. Hard risk caps are deliberately not on the list.

Why the two layers never fight:

- They never edit each other; `load_config()` merges them at read time,
  every cycle.
- Overrides **expire at 16:00 ET** by default (an evening tweak lasts through
  the next day's close). Durable changes are PRs; tomorrow starts from git.
- Every cycle journals a `config` event: effective values, a `config_hash`,
  `strategy_notes` hash + first line, and the active overrides with their
  expiry and origin. `status.py` shows the same.

**MQTT contract** (implemented by the Home Assistant integration; defined here so both sides agree):

- Subscribe `alpaca-hackathon/config/set`, JSON `{"account": ..., "key": ...,
  "value": ..., "until": "<ISO, optional>"}` → `set_override(key, value, until,
  set_by="mqtt")`. `account` defaults to the *test* account, never `official`
  by accident. A `null` value clears the key. Validation errors are published
  to `alpaca-hackathon/config/error`.
- After every cycle the bot publishes (retained)
  `alpaca-hackathon/config/effective` — the same payload as the `config`
  journal event. Home Assistant always sees what the bot is actually
  running, which is the whole "no fight" guarantee.

### Kill switch and dashboard knobs

`mqtt_bridge.py` publishes retained MQTT discovery on startup, so Home
Assistant builds one set of controls per account with no configuration on the
HA side. What it creates:

| Control | Domain | Does |
|---|---|---|
| Kill switch | `switch` | flattens and halts **that one account** |
| Model | `select` | swaps the model, from a fixed list of costed options |
| Numeric knobs | `number` | temperature, max tokens, research contracts, strike band, stop-loss, take-profit, EOD close DTE |
| Kalshi prior | `switch` | `predictions_enabled` — whether the crowd prior is fetched and shown to the model |
| Review model | `select` | which model critiques the day in `eod_review.py` |

A `switch` rather than a button, because a button is stateless and could never
show whether the account is *currently* halted; its state comes from the
retained halt topic. A `select` rather than free text, because `model` is the
one knob accepted as any non-empty string, so a thumb-typo on a phone was
writable and would have failed every cycle until it expired.

`strategy_notes` is deliberately absent: it is prose, and stays
`override.py set strategy_notes @file` or a PR.

#### The knobs

Every one is safe to move mid-session, and none of them can breach a risk limit
— see "What is *not* a knob" below.

| Knob | Range | Default | Changes |
|---|---|---|---|
| `temperature` | 0–2, step 0.1 | 0.2 | how much the model varies between cycles |
| `max_tokens` | 50–8000, step 50 | 800 | the answer budget per decision |
| `research_contracts_per_underlying` | 1–60 | 12 | how many contracts per name go into the prompt |
| `option_strike_band_pct` | 0.01–0.5 | 0.08 | how far from spot the chain is fetched (±8%) — on SPY/QQQ that is ~245 contracts per expiration, so the band also sets how many pages the fetch takes (capped at `CHAIN_MAX_PAGES`; a cap hit is journaled as `truncated`) |
| `stop_loss_pct` | 1–100 | 40 | close at this much loss on entry premium |
| `take_profit_pct` | 1–1000 | 60 | close at this much gain |
| `eod_close_dte` | 0–45 | 1 | the end-of-day sweep closes contracts with this many days left |
| `min_hold_minutes` | 0–390 | 30 | a model-proposed sell of a position younger than this is refused (the churn guard, #132) |
| `early_exit_drawdown_pct` | 1–100 | 25 | …unless the position is already down more than this on its premium |
| `review_model` | the costed model list | *computed* | which model writes the end-of-day critique |
| `predictions_enabled` | on / off | on | whether the Kalshi prior is fetched and shown to the model |

**Model behaviour — `temperature`, `max_tokens`.** Raise temperature if the
agent is proposing the same idea every cycle and you want more variety; lower it
toward 0 if it is being erratic. `max_tokens` is the budget for the *answer*,
and 800 is sized for a JSON array of a few proposals rather than prose. Raising
it is the fix if you see `finish_reason=length` in the journal — the model ran
out of room mid-answer, which forfeits the cycle. Lowering it saves nothing
worth having.

**What the model sees — `research_contracts_per_underlying`,
`option_strike_band_pct`.** These decide the size and shape of the menu.
Widening the strike band reaches further out of the money (and fetches more
pages on the index names); raising the contract count shows more of what was
fetched — the whole band across the whole 2–45 DTE window is the pool. Both
make the prompt longer and the cycle slower. Reach for these if the agent
complains it cannot find a suitable contract, not to give it more to read.

*Which* 12 is `bot/menu.py`'s call, and it changed on 2026-09-01 (#159). The
menu used to be the 12 nearest the money by |strike − spot|, and on names with
coarse strikes that collapsed to one strike: NVDA's 12 on day 1 were all K=220
— call and put across six expiries — MSFT's all 510, AAPL 10 of 12 at 317.5.
The model could pick side and expiry from the menu and nothing else; "half a
delta out" was not on offer, and its NVDA flip-flopping (call, put, call, all
at 220) was exactly the degenerate choice space it had been given. SPY and QQQ
escaped only by the accident of $1 strikes. The tactics ask for "at or slightly
out of the money (|delta| roughly 0.35–0.55)" with 2–14 DTE, so the menu now
spends its slots on that: three expiry buckets, the nearest to 2, 7 and 14 DTE;
per expiry and side the at-the-money strike, then the out-of-the-money strike
whose Alpaca delta is nearest 0.40 (the next strike out when a contract has no
delta). ATM picks for every bucket come before any OTM pick, so a small count
still spans expiries; leftover slots fall back to nearest-the-money; the result
is ordered by expiry then strike so the prompt reads as a grid. On NVDA that is
three strikes × three expiries × two sides instead of one strike × six. The
targets and the delta are constants, not knobs: the slot count is already a
knob, and the selection rule is code the config hash does not need to track.

**What the model knows about its own positions.** Every cycle is a fresh
process, and until #173 the model saw that a position existed and how it was
doing — one row in the snapshot JSON — and nothing else: not why it had opened
it, not what the prior said at the time, not even a plain list of what it was
allowed to sell. Two live failures on the judged account on 2026-09-01 came
straight from that. It tried to close a +32% SPY put three cycles running and
named a neighbouring strike each time (763, 763, 762 for a held 764) — the held
symbol was one row among thirty, and since #158 the menu lists its same-expiry
neighbours right beside it (#170). And it proposed exiting a ten-minute-old QQQ
put on a "weakening thesis" while every number it had cited at entry had moved
in its favour, because it re-derived the view from scratch (#173).

`bot/holdings.py` now renders a prose block after the prior:

```
POSITIONS YOU HOLD (the ONLY symbols a sell may name - copy them exactly):
- QQQ260903P00708000 x4 @ 3.715, +0.4% vs entry; opened 13:10 ET
    stated at entry: "QQQ down 1.2% intraday with both Kalshi and option chain showing heavy downside odds; ..."
    prior at entry: Kalshi P(above) 0.068, P(up>1%) 0.044, P(down>1%) 0.779 | chain P(above) 0.075, P(up>1%) 0.013, P(down>1%) 0.575
    prior now:      Kalshi P(above) 0.068, P(up>1%) 0.044, P(down>1%) 0.819 | chain withheld (noisy: isotonic fit moved probabilities 0.051 on average)
RESTING ORDERS (sent, unfilled - they already count against your caps; ...):
- buy 4 QQQ260903P00709000 @ limit 3.56, submitted 12:00 ET
```

The entry reason comes from the `order_submitted` event that opened the
position (the first buy since the symbol was last flat, so a name traded,
closed and re-bought gets today's reason, not last week's); the prior at entry
is the `predictions` record journaled in that same cycle. **The prior line is
what makes replaying the reason safe**: per #172 the model fabricates
statistics inside its reasons, so a reason alone would let it "verify" the
position against its own confabulation. Paired with the journaled prior, "has
my thesis weakened?" becomes a comparison against numbers we control — and in
the QQQ example above the honest answer was that it had strengthened
(P(down>1%) 0.779 → 0.819 in the ten minutes between entry and the proposed
exit). A
position with no journaled opener says *no recorded thesis* rather than
inventing one. A flat account gets one line — *none — any sell would be a naked
short* — so the long-only rule is stated in terms of the list rather than left
to the model's memory of the preamble. The strategy notes changed to match:
*an open position is presumed valid until a stop, a take-profit, or a NAMED
change in the evidence cited at entry*, replacing the old "you may still
propose a sell on a thesis change — say why", which invited a thesis-change
hunt every ten minutes that a capable model always won.

For the one operation where the symbol is already known, code also closes the
transcription gap: a sell naming an option that is not held, when exactly one
held contract shares its underlying, type and expiration, is resolved to that
contract before any guard runs (`holdings.resolve_sell`; the order event
carries `resolved_from` with what the model wrote). It can only ever reduce
exposure, and the quantity still passes `check_order`. Anything ambiguous —
two puts held at the same expiry — is left to the funnel, whose rejection now
names what *is* held on that underlying so the next cycle's learning block
carries the right symbol.

The counter-argument is anchoring: a model that holds a loser because "my
thesis said so". The asymmetry favours showing the thesis anyway — code owns
stop-loss, take-profit, expiry and the end-of-day backstop without consulting
the model, so stubbornness is capped at `stop_loss_pct`, while churn had no
ceiling beyond `min_hold_minutes`. Watch the EOD digest for a rise in stop-loss
exits after this landed.

**The churn guard — `min_hold_minutes`, `early_exit_drawdown_pct`.** Before the
positions block existed, the treatment for ten-minute exits was a leash
(#132): a model-proposed sell of a position opened fewer than `min_hold_minutes`
ago is refused unless the position is already down more than
`early_exit_drawdown_pct` of its premium — the stop and take-profit own the
marks; the model's early exit has to wait out the hold. Code exits never pass
through it. Both are runtime knobs, and both stay: the block above removes the
reason to churn, the leash bounds it if the model churns anyway.

**How positions end — `stop_loss_pct`, `take_profit_pct`, `eod_close_dte`.**
The first two are checked at the top of every cycle *before* the model is
consulted, as percentages of the entry premium: 40 means "close if it has lost
40% of what was paid". Tighten the stop after a bad gap; widen the take-profit
if you keep getting stopped out of trades that then run. `eod_close_dte` is the
overnight-hold policy — see [Holding period and end of day](#holding-period-and-end-of-day)
before touching it, because raising it interacts badly with short-dated entries.

**The second opinion, `predictions_enabled`.** A switch, because the failure
it exists for is binary: on 2026-08-31 the prior spent a morning measured
against the wrong day's close (see "The reference close has to be the right
day"), and the only ways to pull it were a deploy the market-hours freeze
refuses or nothing. Off, the cycle runs without the prediction-markets block
entirely; the journal's `predictions` line disappears rather than reporting a
suppressed prior, and flipping it shows up in `config_hash` — turning the
model's inputs on and off is exactly the kind of change P&L attribution has
to see.

**The reviewer, `review_model`.** Unset, it is *computed*: the first entry of
`review_model_preference` that is not this account's own `model`, recomputed at
review time. A model grading its own reasoning is the weakest form of review,
and recomputing rather than storing means switching the trading model from the
dashboard cannot silently make the reviewer the same model that traded. Set it
to pin one instead. It touches nothing but the end-of-day digest.

**Overrides expire.** A change made through the dashboard or `override.py`
lasts until **16:00 ET today** unless you pass `--until`; an evening tweak
carries through the next session. So a knob turned in anger during a bad hour
does not silently become the configuration. Making it durable means editing
`config.yaml` and opening a PR.

**Both paths validate identically.** The dashboard's slider bounds are the same
numbers `bot/overrides.py` enforces, so Home Assistant cannot propose a value
the CLI would reject — the entities are wired to `config/set`, which runs the
same validator.

#### What is *not* a knob

Deliberately absent from the dashboard, and from `override.py` entirely:
`max_position_usd`, `max_positions`, `max_contracts_per_order`, `underlyings`,
`min_days_to_expiration` / `max_days_to_expiration` (entries only — sells are
always legal down to expiry), `daily_loss_cutoff_pct`, `last_entry` /
`trade_end`, `expiry_close_dte` and `final_flatten_date`.

Those are the risk limits, and changing one takes a config edit, a pull request
and a deploy — which during market hours the freeze refuses outright. The knobs
tune how the agent thinks and how positions are managed; **nothing reachable at
runtime can widen how much it is allowed to lose.**

Every knob is wired to `config/set` — the selects and numbers through a
`command_template`, the prior's switch through its two fixed payloads — so a
dashboard change takes exactly the same validation path as the CLI. There is
no second way in.

**The halt is per-account, on purpose.** The switch publishes to
`alpaca-hackathon/<account>/command/halt` with a payload of exactly `HALT`,
and the bridge reuses `flatten.py`'s own `run()` to flatten and halt **only
that account** (`RiskManager.manual_halt_file`).

The break-glass *halt everything* (`logs/HALT`) is deliberately **unreachable
from Home Assistant** — it is CLI-only, `flatten.py --halt --all-accounts`. So
no dashboard tap, however stray, can stop the judged account during the scoring
window. Resuming is CLI-only for the same reason: getting out of a halt should
require more intent than getting into one.
