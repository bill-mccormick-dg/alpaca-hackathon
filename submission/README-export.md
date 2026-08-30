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

**A slide taller than its page is silently clipped**, because the print rules
set `overflow:hidden`. Nothing warns you — the PDF just quietly loses the
bottom of a slide. Both slides added on Aug 30 overflowed (738px and 797px
against a 720px page) and had to be trimmed.

Check before exporting:

```sh
python3 - <<'PY'
import pathlib, subprocess, tempfile, re, html
src = pathlib.Path("submission/video/slides.html").read_text()
src = src.replace("</style>", """
  section, section.active { display:flex !important; flex-direction:column;
    height:720px !important; width:1280px; box-sizing:border-box; overflow:visible; }
</style>""", 1)
src = src.replace("</body>", """
<script>window.addEventListener('load',()=>{document.body.innerHTML='<pre id=out>'+
[...document.querySelectorAll('section')].map((s,i)=>`${i+1} ${s.scrollHeight>720?'OVERFLOW':'ok'} ${s.scrollHeight}px`).join('\\n')+'</pre>'})</script>
</body>""", 1)
p = pathlib.Path(tempfile.mkdtemp())/"m.html"; p.write_text(src)
out = subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "--headless=new","--disable-gpu","--virtual-time-budget=4000","--dump-dom",f"file://{p}"],
    capture_output=True, text=True).stdout
m = re.search(r'<pre id="?out"?>(.*?)</pre>', out, re.S)
print(html.unescape(m.group(1)) if m else "could not measure")
PY
```

## Before the final export

Refresh the counts on slide 10 — they moved a lot in the last days:

```sh
gh pr list --state merged --limit 300 --json number -q 'length'
python -m unittest discover -s tests 2>&1 | tail -3
```
