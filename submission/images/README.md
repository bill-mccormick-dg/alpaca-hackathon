# Deck images

Screenshots referenced by `../video/slides.html`. Each is a **drop-in**: replace
the file at the same path, re-export the PDF, and nothing else changes.

| File | Slide | What it should show |
|---|---|---|
| `ha-dashboard.png` | Home Assistant, over MQTT | the operational dashboard, whole and uncropped — sidebar, Official and Test columns, state, day-P&L graph, controls |
| `viewer.png` | Watching it think | a band of the live journal viewer: an order with the model's full stated reason, then the priors and cycle lines under it |
| `equity-curve.svg` | Results | **generated** - `python scripts/equity_curve.py` |

`ha-dashboard.png` is a **real capture** (2026-09-02, mid-session, zoom 1.0,
1143x1068) used exactly as it was taken: equity 99,552.71, a day P&L of -655.10
that the graph under it actually traces, two open positions and `hold` as the
last decision. Home Assistant's own sidebar is in frame; it lists panel names
only, and the operator's call was to keep the screenshot whole rather than trim
it into something that no longer looks like the app it is.

It supersedes a 2026-08-30 pre-open capture that showed all three accounts and
nothing happening: flat graphs, zero positions, `hold` everywhere. One account
mid-session is better evidence than three accounts doing nothing, and the
three-account story is already carried by the variants and results slides.

## How the slide uses it (#230)

The old capture was unreadable, and two scalings compounded to make it so. It
was taken at `zoom = 0.62` so that the state rows, the graphs and the controls
would fit one frame; the slide then caps images by *height*, because the page box
is only 720px, and rendered its 1400x784 at 550x308 — another 39%. Dashboard body
text landed near 5px. It read as a texture that says "there is a dashboard"
rather than as a dashboard.

So the screenshot is no longer a figure on the slide — it **is** the slide.
`section.shot > img.bleed` positions it absolutely at full page height, bleeding
past the section padding, and the feature list floats over it on the right. Three
consequences worth knowing before editing that slide:

- The image contributes nothing to `scrollHeight`, so `fit_slides.py` measures
  the text column alone. The picture can never push the slide over the page.
- `.scrim` fades the image into the deck's background from 42% of the page
  across, which is exactly where the Official column ends. Move that stop left
  and it starts dimming the numbers that are the point of the screenshot.
- The fade stops are `rgba(6,18,31,0)`, not `transparent`. `transparent` is
  `rgba(0,0,0,0)`, and interpolating to it greys the middle of the gradient.

A recapture is a drop-in as long as the Official column still ends near 70% of
the image's width. If it does not, move the scrim's stops with it and read
page 12 back at full size.

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

Open the dashboard in a browser and screenshot Home Assistant, not the whole
desktop. The sidebar may stay: it carries panel names only. What must not is
anything below it that names a device, an address or a person — check the frame
before committing, because this repo goes public.

```sh
# macOS: cmd-shift-4, then drag over the dashboard. Only the colour space is
# touched on the way in - no scaling, no crop:
ffmpeg -y -i ~/Desktop/Screenshot*.png -vf format=rgb24 \
  submission/images/ha-dashboard.png
```

Capture at **zoom 1.0 and do not downscale**. The temptation is to zoom out so
that every account, graph and control fits one frame, and then to shrink the
result "so the PDF stays small" — that is exactly what produced the unreadable
slide in #230, twice over. A screenshot that will be shown near 1:1 has to be
captured at 1:1; the slide crops by *framing*, not by resampling.

Crop, never composite. Cutting one rectangle out of one capture is honest;
stitching separate screenshots into a dashboard that was never on screen at once
is not.

A Retina capture is 2x, and for an image the slide shows near 1:1 that extra
resolution is worth keeping — it is not what makes the PDF big. A full-screen 4K
capture is; frame it on the dashboard before committing.

## After replacing

```sh
python -m unittest discover -s tests      # image references still resolve
python scripts/fit_slides.py              # re-fit: an image changes slide height
# then re-export - see ../README-export.md
```

**Re-fit before exporting.** An image changes its slide's height, and a slide
that no longer fits the 720px page box loses content in the PDF without any
warning — see ../README-export.md for how that went unnoticed across eight
pages. `fit_slides.py` scales the slide (and its image) to fit rather than
trimming; `--check` is the gate.
