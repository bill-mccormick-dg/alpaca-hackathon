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

Judging is on **total equity at Fri Sep 4 09:30 ET**, not on daily P&L, and
the DTE window allows multi-day holds. So the end-of-day rule is *not*
"flatten everything": contracts expiring the next trading day are closed at
the end-of-day backstop; everything else may be held overnight under the
per-position stop/take-profit, and is force-closed on its expiry day
(issue #32 implements this; until it lands the backstop flattens all).
Everything must be flat, or deliberately held, by Fri 09:30 ET.

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
