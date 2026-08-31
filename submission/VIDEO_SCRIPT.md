# Video script — AI Day Trader - Long Premium, Short Leash

**First cut lives in `submission/video/`**: `demo.sh` (the CT 108 terminal
footage - live cycle, kill switch, eod_review, override - captioned,
`PAUSE=0` to rehearse), `demo_local.sh` + `mqtt_watch.py` (the local half:
the docker-compose farm/A-B comparison, and a genuine live MQTT capture -
subscribe locally, trigger a cycle on CT 108, watch the messages arrive),
`narration.md` (word-for-word voice-over with director's notes on which
footage each block sits over, timed to ~4:58 under the 5:00 cap),
`slides.html` (twelve slides, arrow keys, real journal/tool-call/MQTT
excerpts). The outline below is the original plan the first cut follows.

**Format:** MP4, 1080p, ≤ 5:00 (target 3:30–4:30). Screen recording + voice.
Terminal font ≥ 16 pt, dark theme, window ~1600×900. Record Wed or Thu after a
day with real trades. Dry-run the whole command sequence once first.

Every shot is the real system running — no generated imagery.

## 0:00–0:35 · Pitch (slide 2, then slide 3)

> "This is Long Premium, Short Leash — an autonomous options agent built on
> Alpaca's MCP server. The idea in one sentence: buy defined-risk, short-dated
> premium when an open-source model can name a reason, and let deterministic
> code size it, stop it, and close it before expiry. The model never touches
> an order. Open model proposes; code disposes."

## 0:35–2:15 · The agent in action (terminal on CT 108, during market hours)

Command: `./.venv/bin/python run_cycle.py --account test --config config-test.yaml --verbose`

Narrate as it scrolls:
- equity/positions line — "live paper account through Alpaca's MCP server"
- (pause on the prompt contract block if shown) — "Alpaca's free feed has no
  Greeks, so we derive IV and delta from each contract's price on the fly"
- `research:` lines — "before deciding, the model investigates: bars, a
  snapshot, news — read-only Alpaca MCP tools, six calls max, every one
  journaled"
- `model output:` — "then it must answer: a JSON array, or hold"
- `SUBMITTED ...` / `REJECTED ...: ...` — "and here's the leash: this proposal
  passes; this one is refused by the position cap, and the journal records
  which rule said no"

Then `./.venv/bin/python status.py` — positions, halt state, today's summary.

## 2:15–3:00 · The leash (kill switch)

Command: `./.venv/bin/python flatten.py --halt --account test`
> "One command closes everything, verified against the broker, and trips the
> kill switch." Then run a cycle: `halted: manual halt`. Show the `HALT` file,
> delete it. (If the Home Assistant light exists: cut to it going red.)

## 3:00–3:45 · Measure, then change (eod_review)

Command: `./.venv/bin/python eod_review.py --account official --date <yesterday>`
> "At the close, one command reconstructs every round trip from Alpaca's fills,
> groups rejections by rule, appends the equity curve, and then a *different*
> model - one that did not trade today - writes its read of the day with one
> recommended change. Featherless is twenty thousand models behind one API, so
> the reviewer costs one call and is not grading its own homework. We apply it with an
> override that expires at the close, or a config PR that CI deploys to the
> box before the open." Show `override.py show` and the PR list briefly.

## 3:45–4:20 · Results and honesty (slide 9)

Equity curve Mon→Thu, round trips, exit mix, official vs challenger. One
sentence on what didn't work and what we'd change. Then slide 10 for two
seconds: repo, MIT, next steps.

## Capture checklist

- [ ] Rehearse the command sequence; keep output short (`| head` where needed)
- [ ] QuickTime/OBS 1080p; mic level check; no notifications
- [ ] Export H.264/AAC MP4; check length ≤ 5:00
- [ ] Upload YouTube (unlisted); test the link logged out; paste into METADATA.md
