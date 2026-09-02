# Video script - AI Day Trader: Long Premium, Short Leash

**Word-for-word narration is [`video/narration.md`](video/narration.md)**; the
terminal half is [`video/demo.sh`](video/demo.sh), which prints a caption before
each command and pauses for you. This file is the shot list and the checklist.

**Format:** MP4, 1080p, <= 5:00 (this cut is timed to ~4:40). Screen recording
plus voice. Terminal font >= 16 pt, dark theme, window ~1600x900.
**Every shot is the real system running** - no generated imagery, no mockups.

## Before you press record

Have these open, each on its own desktop or window:

| Window | What |
|---|---|
| Deck | `video/slides.html` in a browser, full screen, arrow keys |
| Terminal | `ssh` to CT 108, `cd /opt/alpaca-hackathon`, 16pt+ |
| Viewer | https://bot.wpmccormick.pw - log in first, the OTP takes a minute |
| Home Assistant | the operator dashboard, and the read-only team dashboard |
| GitHub | the repo's Actions tab, deploy workflow |

Then rehearse once, without touching the test account's positions:

```sh
PAUSE=0 SKIP_HALT=1 bash submission/video/demo.sh
```

`SKIP_HALT=1` skips the one destructive shot. Drop it for the take - the kill
switch closing real positions is the point of that shot. `FORCE=--force` lets
the whole thing rehearse outside market hours.

**Record during market hours**, Wednesday or Thursday, so shot 1 is a real
cycle and shot 2 has a fresh one to show.

## Shot list

| # | Source | Shows |
|---|---|---|
| - | slides 1-3 | thesis, then the runtime diagram: the model in one box, one order path |
| 1 | `demo.sh` | a live cycle: gates, snapshot, research tools, JSON, the gate |
| 2 | `demo.sh` | `last_cycle.py` - the chain it was given (pagination), the prior (including a withheld one), the decision, the order and its stated reason |
| - | slides | Greeks: Alpaca's, with Black-Scholes as the backstop; then Brier-scoring the priors |
| 4 | `demo.sh` | the kill switch, verified against the broker, and the next cycle refusing |
| - | browser | the live viewer, and the four bugs it caught in two days |
| - | browser | Home Assistant: operator dashboard, team dashboard, what pushes and what does not |
| 5, 7 | `demo.sh` | the end-of-day digest, then `DEPLOYED` and the runner: it ships itself |
| - | slides | results, thanks |

Shots 3 and 6 (`status.py`, `override.py`) are in `demo.sh` and are good filler
if a block runs short; neither is in the narration's timing.

## Capture checklist

- [ ] Rehearse the command sequence (`PAUSE=0 SKIP_HALT=1`)
- [ ] Log into the viewer *before* recording - the email OTP is slow on camera
- [ ] Collapse the Home Assistant sidebar (it lists the rest of the home network)
- [ ] QuickTime/OBS 1080p; mic level check; notifications off
- [ ] Export H.264/AAC MP4; check length <= 5:00
- [ ] Upload YouTube (unlisted); test the link logged out; paste into METADATA.md
