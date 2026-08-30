#!/usr/bin/env python3
"""Render the architecture diagrams from docs/ into the slide deck.

The mermaid source lives in ```mermaid fences in docs/architecture.md, tagged
with an HTML comment naming the diagram:

    <!-- diagram:runtime -->
    ```mermaid
    flowchart LR
      ...
    ```

GitHub renders those fences natively and the Docusaurus site renders them with
@docusaurus/theme-mermaid, so the markdown needs no build step at all. The
slide deck is the exception: it is a single self-contained HTML file with no
external resources, exported to PDF by headless Chrome. Loading mermaid there
at runtime would make the export race a JS render pass, and a slide that looks
right in a browser but exports blank is the worst failure available - it shows
up only in the artifact that goes to judges.

So this script renders each fence to a static SVG and injects it into
submission/video/slides.html between marker comments, along with a hash of the
source it was rendered from. tests/test_dashboard.py compares that hash against
the fence, so editing a diagram and forgetting to re-render fails CI - without
CI needing Chrome or a network.

    python scripts/render_diagrams.py            # render + inject
    python scripts/render_diagrams.py --check    # report drift, change nothing
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DOC = ROOT / "docs/architecture.md"
DECK = ROOT / "submission/video/slides.html"
# The README shows the runtime diagram too. It is SYNCED from the same fence
# rather than copy-pasted, so there is exactly one source for it.
README = ROOT / "README.md"
README_DIAGRAM = "runtime"

# Pinned deliberately. cdnjs serves 11.15.0; a guessed version (11.4.1) 404s and
# mermaid then never defines itself, which yields an empty SVG rather than an error.
MERMAID_URL = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.15.0/mermaid.min.js"

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FENCE = re.compile(
    r"<!--\s*diagram:(?P<name>[a-z0-9_-]+)\s*-->\s*\n```mermaid\n(?P<src>.*?)\n```",
    re.DOTALL,
)


def diagrams(doc: Path = SOURCE_DOC) -> dict[str, str]:
    """{name: mermaid source} for every tagged fence in the markdown."""
    return {m.group("name"): m.group("src").strip() for m in FENCE.finditer(doc.read_text())}


def source_hash(src: str) -> str:
    """Hash of the mermaid source, ignoring trailing whitespace per line so a
    reflow that changes nothing visually does not trip the drift test."""
    normalized = "\n".join(line.rstrip() for line in src.strip().splitlines())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def render(src: str, name: str) -> str:
    """Mermaid source -> SVG markup, via headless Chrome.

    Uses the browser already required for the PDF export rather than pulling in
    mermaid-cli and its own bundled Chromium."""
    harness = f"""<!doctype html><html><body>
<div id="out"></div>
<script src="{MERMAID_URL}"></script>
<script>
  mermaid.initialize({{ startOnLoad: false, theme: 'dark',
    themeVariables: {{ background: '#0f1720', primaryColor: '#182230',
      primaryTextColor: '#e6edf3', primaryBorderColor: '#4fb39c',
      lineColor: '#9fb0c0', fontSize: '15px' }} }});
  mermaid.render('d', {json.dumps(src)}).then(r => {{
    document.getElementById('out').innerHTML = r.svg;
    document.title = 'RENDERED';
  }}).catch(e => {{ document.getElementById('out').textContent = 'ERROR ' + e; }});
</script></body></html>"""

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "h.html"
        page.write_text(harness)
        dom = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=15000",
             "--dump-dom", f"file://{page}"],
            capture_output=True, text=True, timeout=180, check=False,
        ).stdout

    if "ERROR" in dom and "<svg" not in dom:
        raise SystemExit(f"{name}: mermaid failed to render:\n{dom[:400]}")
    svg = re.search(r"<svg.*?</svg>", dom, re.DOTALL)
    if not svg:
        raise SystemExit(f"{name}: no SVG produced (mermaid may not have loaded from the CDN)")
    out = svg.group(0)
    # Let the slide size it; mermaid hardcodes a pixel width otherwise.
    out = re.sub(r'\s(width|height)="[^"]*"', "", out, count=2)
    out = out.replace("<svg ", '<svg style="max-width:100%;max-height:52vh;height:auto" ', 1)
    return out


def inject(deck_text: str, name: str, svg: str, digest: str) -> str:
    """Replace the marked block in the deck with freshly rendered SVG."""
    start, end = f"<!-- diagram:{name} -->", f"<!-- /diagram:{name} -->"
    if start not in deck_text or end not in deck_text:
        raise SystemExit(f"{DECK}: missing {start} ... {end} markers")
    head, rest = deck_text.split(start, 1)
    _, tail = rest.split(end, 1)
    block = f"{start}\n<!-- rendered from docs/architecture.md, source sha {digest} -->\n{svg}\n{end}"
    return head + block + tail


def deck_hashes(deck_text: str) -> dict[str, str]:
    """{name: hash} recorded in the deck at render time."""
    pattern = re.compile(
        r"<!--\s*diagram:([a-z0-9_-]+)\s*-->\s*\n<!--\s*rendered from [^,]+, source sha ([0-9a-f]+)\s*-->"
    )
    return {m.group(1): m.group(2) for m in pattern.finditer(deck_text)}


def sync_readme(name: str, src: str) -> bool:
    """Mirror one mermaid fence into the README between its markers.

    Returns True if the file changed. A second hand-maintained copy would drift
    the first time someone edited only one of them."""
    start, end = f"<!-- diagram:{name} -->", f"<!-- /diagram:{name} -->"
    text = README.read_text()
    if start not in text or end not in text:
        return False
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    block = f"{start}\n<!-- synced from docs/architecture.md by scripts/render_diagrams.py -->\n```mermaid\n{src}\n```\n{end}"
    updated = head + block + tail
    if updated != text:
        README.write_text(updated)
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report drift without rendering or writing")
    args = ap.parse_args()

    sources = diagrams()
    if not sources:
        print(f"no tagged mermaid fences in {SOURCE_DOC}", file=sys.stderr)
        return 1

    deck_text = DECK.read_text()
    recorded = deck_hashes(deck_text)

    if args.check:
        drift = [n for n, s in sources.items() if recorded.get(n) != source_hash(s)]
        for name in sources:
            state = "STALE" if name in drift else "ok"
            print(f"  {state:6} {name}")
        if drift:
            print(f"\nrun: python scripts/render_diagrams.py   ({', '.join(drift)} changed)", file=sys.stderr)
            return 1
        return 0

    for name, src in sources.items():
        digest = source_hash(src)
        print(f"rendering {name} ({digest}) ...", flush=True)
        deck_text = inject(deck_text, name, render(src, name), digest)
    DECK.write_text(deck_text)
    print(f"injected {len(sources)} diagram(s) into {DECK.relative_to(ROOT)}")
    if README_DIAGRAM in sources and sync_readme(README_DIAGRAM, sources[README_DIAGRAM]):
        print(f"synced {README_DIAGRAM} into {README.relative_to(ROOT)}")
    print("now re-export the PDF - see submission/README-export.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
