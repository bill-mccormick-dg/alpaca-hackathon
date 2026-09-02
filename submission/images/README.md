# Deck images

Screenshots referenced by `../video/slides.html`. Each is a **drop-in**: replace
the file at the same path, re-export the PDF, and nothing else changes.

| File | Slide | What it should show |
|---|---|---|
| `ha-dashboard.png` | Home Assistant | the operational dashboard with all three accounts populated |
| `viewer.png` | Watching it think | a band of the live journal viewer: an order with the model's full stated reason, then the priors and cycle lines under it |
| `equity-curve.svg` | Results | **generated** - `python scripts/equity_curve.py` |

`ha-dashboard.png` is a **real capture** (2026-08-30, pre-open): all three
accounts, their state rows, and the controls with each account's own model in
the dropdown. Flat P&L and "hold" everywhere, because nothing has traded yet —
so it is still worth recapturing after Thursday's close.

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
# macOS: cmd-shift-4, then drag over the dashboard panel
# then resize so the PDF stays small:
sips -Z 1400 ~/Desktop/Screenshot*.png --out submission/images/ha-dashboard.png
```

To fit the whole dashboard in one frame rather than stitching crops together,
collapse the sidebar and zoom the page out (the 2026-08-30 capture used
`document.body.style.zoom = '0.62'`, which fits the state row, the graphs and
the controls at once). Zooming is honest — stitching separate screenshots into
one image is not.

Target roughly 1400px wide. A 4K capture will add megabytes to the exported PDF
for no visible gain at slide size.

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
