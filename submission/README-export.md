# Exporting the deck

The deck is [`video/slides.html`](video/slides.html). `slides.pdf` in this
directory is the export the submission form wants — regenerate it after any
slide edit.

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PWD/submission/slides.pdf" \
  "file://$PWD/submission/video/slides.html"
```

Or just open the HTML and print to PDF — the `@media print` block does the
work either way.

## Two things that block a correct export

**Every slide must be printed, not just the visible one.** On screen the deck
shows one `section` at a time (`section { display:none }`); without the print
stylesheet the export is a single page. The `@media print` block force-shows
every section, gives each its own page, and keeps the dark background, which
browsers otherwise drop.

**A slide taller than its page loses content, and nothing warns you.** How it
loses it depends on one declaration: with `overflow:hidden` the excess is
destroyed and the PDF just quietly ends a slide early; with `overflow:visible`
it spills onto the next page and paints on top of the following slide. Both
happened here. The committed `slides.pdf` was clipping eight of fifteen pages,
including most of the Results slide, and it looked fine in the browser.

It looked fine in the browser because **the browser is not the page box**. On
screen a `vh` unit resolves against the viewport; in print it resolves against
the 1280x720 `@page`. Any measurement taken on screen at some other window size
is therefore measuring a different layout than the one being exported. The check
that used to live here did exactly that - it hand-rolled an approximation of the
print rules, dropped their padding, and ran in a temp directory where the deck's
relative image paths resolved to nothing. It reported "0 overflowing" every time.

Use the fitter instead. It applies the real `@media print` block in a 1280x720
window, from the deck's own directory so images load, and scales any slide that
does not fit rather than trimming it:

```sh
python scripts/fit_slides.py --check   # gate: exits 1 if any slide overflows
python scripts/fit_slides.py           # measure, scale to fit, write the block
```

The scales land in a generated `<!-- fit:auto -->` block in the deck; do not
hand-edit it. A slide reported at the 72% floor is one the script could not fit
without making the type too small to read - shrink it further and you have a
slide nobody can read, so that one needs an editorial cut instead.

`--check` needs Chrome, so it is a pre-export gate rather than a CI test. After
exporting, verify the artifact itself rather than the source - `pdftotext`
(`brew install poppler`) will show whether each slide's text actually landed on
its own page:

```sh
pdftotext -layout submission/slides.pdf - | grep -c $'\f'   # 14 form feeds = 15 pages
```

## Diagrams

The deck's two architecture diagrams are **pre-rendered SVG**, injected from the
mermaid fences in `docs/architecture.md`. Runtime mermaid is deliberately not used
here: the export is a headless-Chrome print, and a JS render pass it can race
produces a slide that looks right in a browser and exports blank.

After editing a fence:

```sh
python scripts/render_diagrams.py          # re-render + inject + sync the README
python scripts/render_diagrams.py --check  # just report staleness
```

`tests/test_dashboard.py` fails if the deck's SVG falls behind its source, so this
cannot be silently forgotten.

## Before the final export

Refresh the counts on slides 14-16 — the test count appears twice (in the infra
diagram, so via `scripts/render_diagrams.py`, and in the table on 14), and the PR
and deploy counts on 15. They moved a lot in the last days:

```sh
gh pr list --state merged --limit 300 --json number -q 'length'
python -m unittest discover -s tests 2>&1 | tail -3
```
