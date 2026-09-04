# Post-competition backlog

Findings that are **not** worth changing before the Fri Sep 4 10:00 CDT deadline,
recorded so they are not lost. Everything here touches `run_cycle.py` or `bot/`
— trading code — and none of it is a live risk, so none of it justifies an edit
on the last night.

The first three came out of reading 140 rival repos (`scripts/rival_repos.py`)
and then checking our own code for the same mistakes. Two of the three we do not
have. The one we do have fails safe.

## 1. An unpriceable exit is refused, and the reason misdescribes itself

`run_cycle.py:302`:

```python
price = price_for_proposal(snap, p) or acct.positions[p.symbol].current_price or 0.0
```

The `or 0.0` is the pattern that is dangerous in several rival repos, where it
becomes a submitted limit price. **It cannot here.** This `price` is passed to
`execute.place_proposal` → `risk.check_order`, and the submitted limit comes from
`p.limit_price`, a different value. `bot/risk.py:221` then has:

```python
if price <= 0:
    return False, "price must be positive"
```

So a missing quote **fails closed**: the order is refused, never sent at zero.

Two things are still wrong with it:

- **It is the source of the 17 `price must be positive` rejections on `test`**,
  which the mid-week audit wrote off as an unexplained data bug. They are all
  this path: no quote → `0.0` → refused, and the journal rows even carry
  `"price": null`. The message describes the symptom, not the cause; it should
  say the quote was missing. All 17 are on `test` (official and mixed have
  none), and they stop dead after Aug 31 — 5 on Aug 29, 4 on Aug 30, 8 on
  Aug 31, then nothing for three trading days. **Why they stopped has not been
  established**; it is worth knowing before changing the code, because the
  answer may be that the underlying quote gap went away on its own rather than
  that anything fixed it.
- **It applies to exits as well as entries**, and the two are not symmetric. An
  entry that cannot be priced is a non-event. An exit that cannot be priced
  leaves the position open and logs it as a validation rejection, which is the
  one direction where silence is expensive — and it is the same "late exits are
  the real exposure" theme as #222 and #226.

Fix: distinguish "no quote available" from "price invalid" in `check_order`, and
on the exit path retry or escalate rather than dropping the attempt.

## 2. Daily-loss halt across a restart — no defect

Checked because several rival repos re-baseline their drawdown on restart and so
can never halt. Ours cannot:

- `start_of_day_equity` comes from Alpaca's `last_equity`
  (`bot/snapshot.py:92`), not from equity observed at our first cycle of the day,
  so restarting mid-drawdown does not move the baseline.
- The halt also writes a dated file — `bot/risk.py:159`,
  `HALT{suffix}_{day.isoformat()}` — that outlives the process. This is what kept
  `test` down for the rest of Thu Sep 3 after it tripped the 2% cutoff at
  12:10 ET; `logs/HALT_test_2026-09-03` is still on CT 108.

## 3. Clamp-then-gate — not present in the trading path

The pattern (clamp a value, then test the clamped value against a limit, so the
test can never fail) appears twice, both in `bot/research.py:97,117`, clamping
tool arguments — bars lookback and news limit — before calling Alpaca. Nothing is
gated on the clamped result, which is the safe use of it.

## Also open

See the rival survey (`submission/rival-survey.md`, gitignored — it is a scratch
record of other people's projects) for ~60 adoption candidates, and re-run
`scripts/rival_repos.py recheck` after the deadline: 140 of 142 rival repos were
already public, and the rest should open when the private-during-competition
window closes.
