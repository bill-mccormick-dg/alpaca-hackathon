# baseline-2026-09-08

Written **2026-09-07T20:44:00+00:00**, before the run produced anything.
Commitment: *rules fixed before results*.

## The question

What does the current system do over a sustained period with nothing changed underneath it, and how much of any difference between two identically-configured accounts is just noise?

## The hypothesis, stated in advance

The three accounts are drawn from the same distribution. official and test will differ only by noise, and that difference is the noise floor every later A/B must clear. mixed will not separate from the pair by more than that noise. We do not expect the fleet to beat its judged-week return (+5.10% over four sessions), and we are not trying to.

## What it started from

- git `9c308c3c9528` — fix: a squash-merge orphans the SHA a pre-registration recorded (#272) (re-pointed from 15fb32b27f50, orphaned by a squash-merge; every input was verified byte-identical first)
- accounts: official, test, mixed
- 39 input files fingerprinted (sha256 in `data_fingerprint.json`)

Re-check at any time, and before analysing anything:

```sh
python scripts/fingerprint_run.py --run baseline-2026-09-08 --check
```

## Warnings at creation

None. Every input was committed and the accounts were configured as intended.

---

If the result contradicts the hypothesis above, that is the finding, and it gets
published as it stands. Deciding what we expected after seeing the number is the
failure this file exists to prevent.
