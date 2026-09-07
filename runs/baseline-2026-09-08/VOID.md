# VOID — this registration does not govern any run

Kept in place, not deleted. A pre-registration you can make disappear is not a
pre-registration, and "check git history" is a weaker claim than "look in the
directory".

Superseded by **`runs/baseline-2026-09-09/`**. Two things were wrong with it,
and only the first was foreseeable:

1. **It fingerprinted the wrong accounts.** `official`, `test` and `mixed` are
   parked for the duration of judging — `submission/METADATA.md` tells judges
   to pull `PA3VS39Y5LE2` and see $105,095.51, and trading it again during
   judging week makes that statement false. The baseline runs on three new
   accounts instead.

2. **Its hypothesis was false when written.** It claimed `official` and `test`
   would "differ only by noise". They held $105,095.51 and $92,938.91 against
   *absolute* position caps (`max_position_usd: 5000`,
   `max_contracts_per_order: 10`), so `test` ran **13% more levered** in
   percentage terms, and the 2% daily halt would have fired at $2,102 on one
   and $1,859 on the other. That is a systematic difference, not noise, and no
   analysis afterwards separates the two.

The lesson worth keeping: the fingerprint checked that the *configs* matched
and reported no warnings, which was true and not sufficient. Equal config with
unequal equity is not a replicate when sizing is absolute. `warnings.json` had
nothing to say about it because nobody had thought to ask.

Its own history is intact: created at `15fb32b`, re-pointed to `9c308c3` when
a squash-merge orphaned that SHA (#272/#273), last at `c0c80cb`.
