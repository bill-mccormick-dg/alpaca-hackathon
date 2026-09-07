# baseline-2026-09-09 — NOT YET REGISTERED

This bundle is **empty on purpose**. Do not read the absence of
`strategy_spec.json` as a run that was never pre-registered — read it as one
that has not started.

The 2026-09-07 registration is **void** - see `runs/baseline-2026-09-08/VOID.md`,
which is kept on disk rather than deleted. It fingerprinted
`official`, `test` and `mixed`, and the baseline will not run on those
accounts. Its hypothesis was also wrong: it claimed the pair would "differ
only by noise" while the two accounts held $105,095.51 and $92,938.91 against
fixed-dollar position caps, which is a systematic 13% leverage difference, not
noise.

Register this run once the three new accounts exist and their starting equity
is known:

```sh
python scripts/fingerprint_run.py --run baseline-2026-09-09 \
    --accounts base_a,base_b,base_mixed \
    --question "..." --hypothesis "..."
```

Do it BEFORE the first cycle on Wednesday. After that the timestamp proves
nothing, which is the only thing the bundle is for.
