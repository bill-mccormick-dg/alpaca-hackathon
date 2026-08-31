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
- **ATM / OTM** — at-the-money (strike ≈ current price) / out-of-the-money
  (strike beyond it). OTM contracts cost less and need a bigger move.
- **The Greeks** — the collective name for four sensitivities of an option's
  price, each written as a Greek letter, and each answering "if *one* thing
  changes, how much does this contract move?" `bot/greeks.py` derives all four
  per contract. They are the next four entries.
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
  more than once ("prior close 7,731 … a PRIOR to weigh"), so it is worth
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

### The Greeks are indicative, and the prompt says so

`bot/greeks.py` solves each contract's implied volatility from *its own* market
price and derives delta/gamma/theta/vega from that — independently, per
contract. They will therefore not be internally consistent: put and call deltas
at one strike need not sum to −1, and some quotes are plainly stale.

This is stated in the prompt rather than hidden, along with an instruction not
to spend effort auditing or reconciling the numbers: skip anything that looks
broken and decide from what is plausible. A contract whose price was too thin to
solve arrives with no Greeks at all and is to be judged on price, strike and DTE
alone. Presenting derived figures as if they were an exchange feed would invite
exactly the wrong kind of confidence.

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
| Stop-loss / take-profit on premium, forced close on expiry day | code, every cycle | `bot/exits.py` (#32, deterministic exits) |
| Daily-loss halt, kill switch, end-of-day handling | code | `run_cycle.py`, `flatten.py` |
| The only order path | code | `bot/execute.py::place_proposal()` |

The model is given: account state, per-underlying spot price, the ~12
nearest-the-money contracts within the tradeable expiration window with
bid/ask/last and **derived** Greeks (Alpaca's free feed carries none —
`bot/greeks.py` solves implied volatility from each contract's market price
and computes delta/gamma/theta/vega from it), the hard limits, and the
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

> PREDICTION MARKETS (Kalshi, crowd-implied, read-only - a PRIOR to weigh, not
> a signal to copy; compare to what the option chain implies and to today's
> price action)

So it can only influence a trade by persuading the model, and anything it
inspires still passes the same `check_order()` funnel as every other proposal.
Read-only, no API key, never traded, cached for five minutes, and silently
omitted on any failure.

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

Real output, taken the evening before the 2026-08-31 session opened:

```
gates               volume >= 250.0, flatness <= 0.97

--- SPY via KXINX ---
  event            KXINX-26AUG31H1600  (index close 2026-08-31T20:00Z)
  reference close  7730.99
  implied median   7712.5  (-0.24%)
  P(above prior)   0.453
  P(up >1%)        0.32
  P(down >1%)      0.409
  buckets/volume   30 buckets, volume 70.0
  flatness         0.952   (1.0 = uniform = no information)
                   <-7375.0  0.058
           7675.0-7699.9999  0.048
           7700.0-7724.9999  0.048
  VERDICT          thin: volume 70.0 < 250.0

=== what the model is handed ===
(nothing - every prior was withheld by the gates above)
```

That is the gates earning their place. Read the numbers: a 4.6% **down** move
(`<-7375.0`) is the single most likely bucket, and P(up>1%) + P(down>1%) = 0.73
implies a roughly three-in-four chance of a >1% session - against a real base
rate nearer one in five. On 70 contracts, those are not beliefs, they are wide
spreads. Handing that to the model as "what the crowd thinks" would actively
mislead it toward buying more premium than the day warrants.

During market hours, with volume behind the buckets, the block the model
receives looks like this:

```
PREDICTION MARKETS (Kalshi, crowd-implied, read-only - a PRIOR to weigh, not a
signal to copy; compare to what the option chain implies and to today's price
action):
- SPY via KXINX (index close 2026-08-31T20:00Z); prior close 7,731, implied
  median 7,712 (-0.24%); P(above prior close) 0.453, P(up>1%) 0.32,
  P(down>1%) 0.409; volume 70.0
```

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
| Expiration window | 1–45 DTE | "guards against 0-DTE gamma risk on one side, multi-month decay drag on the other" |
| Entries | 09:45–15:15 ET | skips the opening auction's noise; stops opening new risk near the close |
| Sells | until 15:45 ET | exits stay legal after entries stop |
| Daily loss | 2% of start-of-day equity | breaching it flattens and halts for the day |

**These are the hard caps, not the tactics.** The prompt asks for 2–14 DTE (see
above); the *code* permits 1–45. The narrower band is a preference the model is
told to favour, the wider one is the limit it cannot cross — so a sensible
contract slightly outside the tactic is allowed, and a wild one is not.

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

One is the continuous rule; the other is the end-of-day safety net behind it.

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

A tiny sample, so diagnostics rather than verdicts (`trade_report.py` — #29, round-trip attribution):

- The model traded on stated reasons, and the journal shows them.
- Rejections are rare and *sensible* (a cap, not a malformed proposal) — a
  guardrail rejecting the same idea all day is a prompt bug, not a win.
- Exits were mostly stops/take-profits doing their job, not expiry rescues.
- Equity above $100,000 helps; a clean, explainable curve with the guardrails
  visibly working is the write-up either way.

## Daily loop

After each close: `eod_review.py` (#30, the end-of-day digest) → read the digest → edit
`strategy_notes` / exits / knobs → PR → CI → deploy → the next morning runs
the new version. Promote the test-account challenger config (#34, the two-account A/B) when it
wins convincingly.

## Runtime overrides (intraday, no deploy)

`config.yaml` in git is the base. `logs/overrides.yaml` on the CT — written
only through `bot/overrides.py` (the `override.py` CLI today, the MQTT bridge
in #14, the Home Assistant integration) — wins for these keys: `model`, `temperature`, `max_tokens`,
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

**MQTT contract** (implemented by #14, the Home Assistant integration; defined here so both sides agree):

- Subscribe `alpaca-hackathon/config/set`, JSON `{"key": ..., "value": ...,
  "until": "<ISO, optional>"}` → `set_override(key, value, until,
  set_by="mqtt")`. A `null` value clears the key. Validation errors are
  published to `alpaca-hackathon/config/error`.
- After every cycle the bot publishes (retained)
  `alpaca-hackathon/config/effective` — the same payload as the `config`
  journal event. Home Assistant always sees what the bot is actually
  running, which is the whole "no fight" guarantee.

**Kill switch + dashboard knobs** (mqtt_bridge.py — #14's "two-way control"
stretch goal): the bridge also subscribes to
`alpaca-hackathon/<account>/command/halt` — payload must be exactly `HALT`
(matches the HA button's `payload_press`) — and reuses `flatten.py`'s own
`run()` to flatten **only that account's** positions and halt **only that
account** (`bot/risk.py::RiskManager.manual_halt_file`). The break-glass
"halt every account" (`logs/HALT`) is deliberately unreachable from HA —
it is CLI-only (`flatten.py --halt --all-accounts`), so a stray dashboard
tap can never stop the judged account during the scoring window. Resuming
(deleting the halt file) likewise stays a CLI-only step, never exposed to HA.
On startup the bridge also publishes (retained) MQTT discovery for that
button and for `number`/`text` entities covering every overridable knob
except `strategy_notes` (prose — stays `override.py set strategy_notes
@file`/PR-only), one set per account, each wired to `config/set` via a
`command_template` so `mqtt_bridge.py`'s existing validation path is
untouched.
