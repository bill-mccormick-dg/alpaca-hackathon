# baseline-2026-09-09

Written **2026-09-09T00:34:34+00:00**, before the run produced anything.
Commitment: *rules fixed before results*.

## The question

What does the current system do over a sustained period with nothing changed underneath it, and how much of any difference between two identically-configured accounts is just noise?

## The hypothesis, stated in advance

base_a and base_b start from identical equity ($100,000.00 each, verified against Alpaca on 2026-09-08) and load the same config file, so they will differ only by noise - and that difference IS the noise floor every later A/B must clear. base_mixed will not separate from the pair by more than that noise. We do not expect the fleet to beat the judged week's +5.10%, and we are not trying to. Compare the pair on dollar P&L and decision agreement, not percentage alone.

## What it started from

- git `7ad44f630353` — feat: pre-registration records opening equity, and warns when it cannot (#279)
- accounts: base_a, base_b, base_mixed
- opening equity: base_a $100,000.00, base_b $100,000.00, base_mixed $100,000.00
- 37 input files fingerprinted (sha256 in `data_fingerprint.json`)

Re-check at any time, and before analysing anything:

```sh
python scripts/fingerprint_run.py --run baseline-2026-09-09 --check
```

## Warnings at creation

None. Every input was committed and the accounts were configured as intended.

---

If the result contradicts the hypothesis above, that is the finding, and it gets
published as it stands. Deciding what we expected after seeing the number is the
failure this file exists to prevent.
