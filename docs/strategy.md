---
sidebar_position: 4
title: Strategy
---

# Strategy

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

## Division of labour

| Decision | Who | Where |
|---|---|---|
| Which underlyings, what limits | human, in config | `config.yaml` |
| Whether there is a reason to trade, which direction, which contract | model | `bot/decide.py` prompt → JSON proposals |
| Whether a proposal is allowed at all | code, never negotiated | `bot/risk.py::check_order()` |
| Sizing cap, position count, DTE window, entry cutoff | code | `bot/risk.py` |
| Stop-loss / take-profit on premium, forced close on expiry day | code, every cycle | `bot/exits.py` (issue #32) |
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

## What the code enforces regardless of the notes

`config.yaml` is the source of truth; at the time of writing: whitelist
`SPY QQQ AAPL MSFT NVDA`; max $5,000 notional per position; max 4 concurrent
positions; max 10 contracts per order; 1–45 DTE; entries 09:45–15:15 ET, sells
until 15:45 ET; 2% daily-loss cutoff → flatten + halt for the day.

## Holding period and end of day

Judging is on total equity, not daily P&L, and the DTE window allows
multi-day holds. So the end-of-day rule is *not* "flatten everything":
`flatten.py --expiring-only` (the cron backstop) closes contracts expiring
within `eod_close_dte` days; everything else is held overnight under the
per-position stop/take-profit (`bot/exits.py`, checked every cycle before the
model is consulted), and any contract reaching `expiry_close_dte` is
force-closed that day regardless of P&L.

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

A tiny sample, so diagnostics rather than verdicts (`trade_report.py`, #29):

- The model traded on stated reasons, and the journal shows them.
- Rejections are rare and *sensible* (a cap, not a malformed proposal) — a
  guardrail rejecting the same idea all day is a prompt bug, not a win.
- Exits were mostly stops/take-profits doing their job, not expiry rescues.
- Equity above $100,000 helps; a clean, explainable curve with the guardrails
  visibly working is the write-up either way.

## Daily loop

After each close: `eod_review.py` (#30) → read the digest → edit
`strategy_notes` / exits / knobs → PR → CI → deploy → the next morning runs
the new version. Promote the test-account challenger config (#34) when it
wins convincingly.

## Runtime overrides (intraday, no deploy)

`config.yaml` in git is the base. `logs/overrides.yaml` on the CT — written
only through `bot/overrides.py` (the `override.py` CLI today, the MQTT bridge
in #14) — wins for these keys: `model`, `temperature`, `max_tokens`,
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

**MQTT contract** (implemented by #14, defined here so both sides agree):

- Subscribe `alpaca-hackathon/config/set`, JSON `{"key": ..., "value": ...,
  "until": "<ISO, optional>"}` → `set_override(key, value, until,
  set_by="mqtt")`. A `null` value clears the key. Validation errors are
  published to `alpaca-hackathon/config/error`.
- After every cycle the bot publishes (retained)
  `alpaca-hackathon/config/effective` — the same payload as the `config`
  journal event. Home Assistant always sees what the bot is actually
  running, which is the whole "no fight" guarantee.

**Kill switch + dashboard knobs** (mqtt_bridge.py, #14's "two-way control"
stretch goal): the bridge also subscribes to
`alpaca-hackathon/<account>/command/halt` — payload must be exactly `HALT`
(matches the HA button's `payload_press`) — and reuses `flatten.py`'s own
`run()` to flatten **only that account's** positions. The HALT file it
writes (`bot/risk.py::RiskManager.manual_halt_file`) is intentionally
shared across accounts on purpose, so pressing either account's kill
switch halts trading everywhere, not just that account — resuming
(`rm logs/HALT`) stays a deliberate CLI-only step, never exposed to HA.
On startup the bridge also publishes (retained) MQTT discovery for that
button and for `number`/`text` entities covering every overridable knob
except `strategy_notes` (prose — stays `override.py set strategy_notes
@file`/PR-only), one set per account, each wired to `config/set` via a
`command_template` so `mqtt_bridge.py`'s existing validation path is
untouched.
