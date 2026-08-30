# Deck images

Screenshots referenced by `../video/slides.html`. Each is a **drop-in**: replace
the file at the same path, re-export the PDF, and nothing else changes.

| File | Slide | What it should show |
|---|---|---|
| `ha-dashboard.png` | 13, Home Assistant | the operational dashboard with all three accounts populated |
| `equity-curve.png` | 14, Results | the equity graph Mon open → Thu close |

Both currently hold **placeholders**, deliberately obvious ones. `tests/test_dashboard.py`
fails if a slide references a file that is missing, but nothing can tell a
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
# then resize so the PDF stays small (it is ~390 KB of clean vector today):
sips -Z 1400 ~/Desktop/Screenshot*.png --out submission/images/ha-dashboard.png
```

Target roughly 1400px wide. A 4K capture will add megabytes to the exported PDF
for no visible gain at slide size.

## After replacing

```sh
python -m unittest discover -s tests      # image references still resolve
# re-measure and re-export - see ../README-export.md
```

An image changes a slide's height, so **re-measure before exporting**: an
over-tall slide is silently clipped by the print stylesheet's `overflow:hidden`.
