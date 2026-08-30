# Deck images

Screenshots referenced by `../video/slides.html`. Each is a **drop-in**: replace
the file at the same path, re-export the PDF, and nothing else changes.

| File | Slide | What it should show |
|---|---|---|
| `ha-dashboard.png` | 13, Home Assistant | the operational dashboard with all three accounts populated |
| `equity-curve.png` | 14, Results | the equity graph Mon open → Thu close |

`ha-dashboard.png` is a **real capture** (2026-08-30, pre-open): all three
accounts, their state rows, and the controls with each account's own model in
the dropdown. Flat P&L and "hold" everywhere, because nothing has traded yet —
so it is still worth recapturing after Thursday's close.

`equity-curve.png` is still a **placeholder**. `tests/test_dashboard.py` fails
if a slide references a file that is missing, but nothing can tell a
placeholder from a real capture — that part is on you.

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
