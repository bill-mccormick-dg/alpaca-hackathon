# Video script - Autobelay

**Word-for-word narration is [`video/narration.md`](video/narration.md)**; the
terminal half is [`video/demo.sh`](video/demo.sh), which prints a caption before
each command and pauses for you. This file is the shot list and the checklist.

**Format:** MP4, 1080p, <= 5:00 (this cut is timed to ~4:40). Screen recording
plus voice. Terminal font >= 16 pt, dark theme, window ~1600x900.
**Every shot is the real system running** - no generated imagery, no mockups.

## One command drives the whole take

```sh
bash submission/video/record.sh --check    # nothing recorded: is everything ready?
bash submission/video/record.sh            # the take
```

`record.sh` runs on your Mac and alternates the two halves in narration order:
it `ssh`es to CT 108 for each terminal shot, and drives Chrome for each slide
and each web page. Three scripts, so each does one thing:

| Script | Runs on | Does |
|---|---|---|
| `record.sh` | Mac | the running order, and the pacing |
| `demo.sh` | CT 108 | the seven terminal shots (`demo.sh 2` runs just one) |
| `browser.sh` | Mac | one Chrome window, five tabs, and the slide keystrokes |

**How the pacing works**, because it is the only unusual part. A terminal step
waits for Enter in your terminal. A browser step puts Chrome in front and then
waits for you to switch *back*. So you talk for exactly as long as you want
over either one and nothing advances until you move; the switch itself is the
cut. Fluffed a line? `record.sh --from 9` picks up at any step, and
`--list` prints the order.

`--check` verifies the host, the journal, Accessibility permission, Chrome's
AppleScript JavaScript switch, and - the one that actually bites - whether the
viewer's Cloudflare session has expired. Its email one-time PIN is slow on
camera and the session only lasts six hours, so find out before you record, not
during. It also opens the Chrome window: **drag that onto the display you are
recording and leave it there.** Chrome opens new windows on whichever display
it last used, and no amount of AppleScript reliably moves them.

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
| - | browser | Home Assistant: the dashboard (desktop, then a phone frame), what pushes and what does not |
| 5, 7 | `demo.sh` | the end-of-day digest, then `DEPLOYED` and the runner: it ships itself |
| - | slides | results, thanks |

Shots 3 and 6 (`status.py`, `override.py`) are in `demo.sh` and are good filler
if a block runs short; neither is in the narration's timing.

**Slide 15, "Choosing the model, honestly", is deliberately deck-only.** The
narration's last block already ends at 4:58 against a 5:00 cap, so a new beat
does not fit; the model-selection argument is made in the deck and the
write-up instead, and `narration.md` names what to cut first if it runs long. If a
block does run short, it goes between the kill switch and the viewer, in one
sentence: *"Featherless lists twenty-one thousand models; the gate that
discriminates is not a benchmark score, it is whether the model does what the
prompt says - and we have one rejection that clears every other gate and fails
that one."*

## Capture checklist

- [ ] `record.sh --check` clean, and the Chrome window on the recording display
- [ ] Rehearse the command sequence (`PAUSE=0 SKIP_HALT=1`)
- [ ] Log into the viewer *before* recording - the email OTP is slow on camera
- [ ] Collapse the Home Assistant sidebar (it lists the rest of the home network)
- [ ] Chrome: View > Developer > Allow JavaScript from Apple Events, so the
      Home Assistant shots fit the whole dashboard in frame by themselves
- [ ] Don't click inside the deck while narrating - it advances on any click
- [ ] QuickTime/OBS 1080p; mic level check; notifications off
- [ ] Export H.264/AAC MP4; check length <= 5:00
- [ ] Upload YouTube (unlisted); test the link logged out; paste into METADATA.md
