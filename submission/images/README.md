# Deck images

Screenshots referenced by `../video/slides.html`. Each is a **drop-in**: replace
the file at the same path, re-export the PDF, and nothing else changes.

| File | Slide | What it should show |
|---|---|---|
| `ha-dashboard.png` | *(source, not on a slide)* | the Official column of the operational dashboard: state, day-P&L graph, controls |
| `ha-dashboard-state.png` | One feed, three audiences | **derived** - the state card of that capture, cropped (see below) |
| `viewer.png` | Watching it think | a band of the live journal viewer: an order with the model's full stated reason, then the priors and cycle lines under it |
| `equity-curve.svg` | Results | **generated** - `python scripts/equity_curve.py` |

`ha-dashboard.png` is a **real capture** (2026-09-02, mid-session, zoom 1.0):
the Official column, with equity 99,552.71, a day P&L of -655.10 that the graph
under it actually traces, two open positions and `hold` as the last decision.
The sidebar is cropped off, not hidden after the fact — Home Assistant's sidebar
lists the rest of the home network, which does not belong in a public repo.

It supersedes a 2026-08-30 pre-open capture that showed all three accounts and
nothing happening: flat graphs, zero positions, `hold` everywhere. One account
mid-session is better evidence than three accounts doing nothing, and the
three-account story is already carried by the variants and results slides.

## Why the slide shows one card and not the whole dashboard (#230)

The old capture was unreadable on the slide, and two scalings compounded to make
it so. It was taken at `zoom = 0.62` so that the state rows, the graphs and the
controls would fit in one frame; the slide then caps images by *height*, because
the page box is only 720px, and rendered its 1400x784 at 550x308 — another 39%.
Dashboard body text landed near 5px. It read as a texture that says "there is a
dashboard" rather than as a dashboard. Half the slide width sat empty beside it,
because a height-limited image gains nothing from width.

So the deck now shows the state card at **1:1**, beside the cards rather than
above them:

```sh
ffmpeg -y -i submission/images/ha-dashboard.png -vf "crop=502:424:43:64" \
  submission/images/ha-dashboard-state.png
```

The crop keeps the `Official` column heading, which is the only thing naming the
account, and stops at the bottom of the state card. Its 502x424 is pinned in an
inline `max-height` on the `<img>`, which beats both the print block's 360px cap
and anything `fit_slides.py` generates — shrinking this image is the bug, so the
fitter is left only the type to scale. Re-run the crop after replacing the source
capture, and re-check the box if the recapture is at a different zoom.

`equity-curve.svg` is **generated from `logs/equity.jsonl`**, so it is never a
placeholder and never hand-drawn:

```sh
scp root@<the-bot-host>:/opt/alpaca-hackathon/logs/equity.jsonl logs/
python scripts/equity_curve.py
```

It plots percent change from each account's open on the first scored session
rather than raw dollars, because the three accounts did not start from the same
equity and a shared dollar axis would show `mixed`'s head start as performance.
The closing dollar figures are in the key. Re-run it after Thursday's close;
the script says so itself while the week is still partial.

Generated too, and none of it hand-drawn:

| What | Command |
|---|---|
| `../cover.png` + `hero.svg` | `python scripts/make_cover.py --png` |
| `spot-*.svg` (7 slide illustrations) | `python scripts/make_spots.py` |
| `equity-curve.svg` | `python scripts/equity_curve.py` |

The spots are not decoration: each carries the fact its slide is about. The
payoff diagram *is* the defined-risk argument, the strike grid *is* what
changed about the menu, the Brier scale *is* the claim about the prior. Slides
that already have an architecture diagram or a real chart get no spot.

They are referenced with `<img>`, never inlined. An SVG's internal `<style>`
is not scoped, so inlining one leaks its class names into the host document —
which is exactly how the architecture diagrams once restyled the deck's own
subtitles in monospace.

`viewer.png` is a **real capture** of https://bot.wpmccormick.pw, cropped to a
250px band. Two things about the crop are deliberate. The top 50px of the
original hold a screen-recorder badge sitting over the viewer's own header, so
they are gone. And the band's top and bottom edges are *faded* rather than cut:
a feed is mid-scroll wherever you crop it, and a hard edge through half a line
reads as a broken screenshot where a fade reads as a stream. Recapture it the
same way with the recorder overlay turned off if you want the account filters
and the replay picker in frame too.

That leaves `ha-dashboard.png` and `viewer.png` as the only hand-captured images, and
`tests/test_dashboard.py` can only tell you the file exists - whether it shows
a day with real trades in it is on you.

## Capture them Thursday, not before

A screenshot taken before the accounts have traded shows flat equity, zero
positions and "hold" everywhere. That undersells the work: an empty dashboard
reads as "built but unproven". After Thursday's close it shows real curves,
real trades with the model's reasoning, and three variants diverging.

## Capturing

Open the dashboard in a browser and screenshot the **panel, not the window** —
Home Assistant's sidebar lists the rest of the home network, which does not
belong in a submission or in a repo that goes public.

```sh
# macOS: cmd-shift-4, then drag over the dashboard panel; then crop the
# sidebar and the neighbouring columns off, keeping the Official column:
ffmpeg -y -i ~/Desktop/Screenshot*.png -vf "crop=545:1016:251:0,format=rgb24" \
  submission/images/ha-dashboard.png
```

Capture at **zoom 1.0 and do not downscale**. The temptation is to zoom out so
that every account, graph and control fits one frame, and then to shrink the
result "so the PDF stays small" — that is exactly what produced the unreadable
slide in #230, twice over. A screenshot that will be shown at 1:1 has to be
captured at 1:1. Crop to the part the slide needs instead of scaling the whole.

Crop, never composite. Cutting one rectangle out of one capture is honest;
stitching separate screenshots into a dashboard that was never on screen at once
is not.

A Retina capture is 2x, so a 2x screenshot of a 500px card is 1000px of source
for a 500px slot — that is worth keeping, and it is not what makes the PDF big.
A full-screen 4K capture is; crop it down before committing.

## After replacing

```sh
ffmpeg -y -i submission/images/ha-dashboard.png \
  -vf "crop=502:424:43:64" submission/images/ha-dashboard-state.png
python -m unittest discover -s tests      # image references still resolve
python scripts/fit_slides.py              # re-fit: an image changes slide height
# then re-export - see ../README-export.md
```

**Re-fit before exporting.** An image changes its slide's height, and a slide
that no longer fits the 720px page box loses content in the PDF without any
warning — see ../README-export.md for how that went unnoticed across eight
pages. `fit_slides.py` scales the slide (and its image) to fit rather than
trimming; `--check` is the gate.
