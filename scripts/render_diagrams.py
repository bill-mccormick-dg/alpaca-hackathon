#!/usr/bin/env python3
"""The architecture diagrams: one source, three destinations.

The diagrams used to be mermaid fences in docs/architecture.md, rendered to
SVG by headless Chrome against a CDN copy of mermaid. That worked, but the
auto-layout decided where things went, and at slide size the result was a
thicket - twenty small boxes and crossing edges, unreadable in a video frame.
Three diagrams that judges actually look at are worth composing by hand.

So the source is now this file: each diagram is a function that emits SVG
from an explicit grid. That buys a consistent design system (one palette, one
type scale, deliberate whitespace), and it drops the CDN, the Chrome render
pass and mermaid itself from the pipeline - `--check` now runs anywhere,
including CI, which could never verify the old rendering.

Destinations, all generated, never hand-edited:

  docs/diagrams/<name>.svg      referenced by docs/architecture.md and the
                                Docusaurus site as a plain <img>
  README.md                     the runtime diagram, between its markers
  submission/video/slides.html  inlined, because the deck is a single
                                self-contained file exported to PDF - an
                                external reference that fails to load exports
                                as a blank slide, and only in the artifact
                                that goes to judges

tests/test_dashboard.py compares a hash of the generated SVG against what the
deck records, so editing a diagram and forgetting to re-run this fails CI.

    python scripts/render_diagrams.py            # write + inject
    python scripts/render_diagrams.py --check    # report drift, change nothing
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "docs/diagrams"
DECK = ROOT / "submission/video/slides.html"
README = ROOT / "README.md"
README_DIAGRAM = "runtime"

# ---------------------------------------------------------------- design system
# Navy, to sit on the deck's gradient as a card rather than as a black hole
# punched through it. The semantic assignments below are what matter and are
# unchanged; only the surfaces moved.
BG, PANEL, BOX, BORDER = "#0b1d33", "#12283f", "#17324c", "#31567a"
FG, DIM = "#eaf3fd", "#a6c0dc"
ACCENT = "#4fd1b0"   # deterministic code, and anything that is a guarantee
MODEL = "#b191ff"    # the model, and the humans reading its output
GATE = "#f0b429"     # the one gate, and the freeze
STOP = "#ff6b6b"     # refusals
EXT = "#5aa9e6"      # somebody else's service
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
MARKERS = {"a": DIM, "ag": ACCENT, "am": MODEL, "ar": STOP, "ax": EXT, "ay": GATE}


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def head(w: int, h: int) -> str:
    defs = "".join(
        f'<marker id="{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for k, c in MARKERS.items())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">
<defs>{defs}</defs>
<style>
  /* Every class here is prefixed, because these diagrams are INLINED into the
     slide deck and an SVG's internal stylesheet is not scoped - it applies to
     the whole host document. Unprefixed `.sub` and `.t` silently restyled the
     deck's own subtitles in monospace, and only on the slides that happened to
     come after a diagram in document order. */
  .dg-t {{ font-family: {FONT}; fill: {FG}; }}
  .dg-m {{ font-family: {MONO}; }}
  .dg-band {{ font-family: {FONT}; fill: {DIM}; font-size: 15px; letter-spacing: .14em; font-weight: 700; }}
  .dg-lbl {{ font-family: {FONT}; fill: {DIM}; font-size: 15px; }}
  .dg-sub {{ font-family: {MONO}; fill: {DIM}; font-size: 14px; }}
  .dg-cap {{ font-family: {FONT}; fill: {DIM}; font-size: 17px; }}
</style>
<rect width="{w}" height="{h}" rx="22" fill="{BG}" stroke="{BORDER}" stroke-opacity=".55"/>
'''


def band(x, y, w, h, text, color=BORDER, dash=None, anchor="start") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    lx = x + 18 if anchor == "start" else x + w - 18
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{PANEL}" stroke="{color}"{d}/>'
            f'<text class="dg-band" x="{lx}" y="{y + 26}" fill="{color}" text-anchor="{anchor}">{esc(text)}</text>')


def box(x, y, w, h, lines, color=BORDER, fill=BOX, size=20, sub=None, mono=False, sw=1.5) -> str:
    out = [(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
            f'stroke="{color}" stroke-width="{sw}"/>')]
    lh = size + 6
    top = y + h / 2 - ((len(lines) + (1 if sub else 0)) * lh) / 2 + size - 2
    for i, ln in enumerate(lines):
        out.append(f'<text class="{"dg-t dg-m" if mono else "dg-t"}" x="{x + w/2}" y="{top + i*lh}" font-size="{size}" '
                   f'text-anchor="middle" font-weight="600">{esc(ln)}</text>')
    if sub:
        out.append(f'<text class="dg-sub" x="{x + w/2}" y="{top + len(lines)*lh}" text-anchor="middle">{esc(sub)}</text>')
    return "".join(out)


def path(pts, marker="a", color=DIM, dash=None, width=2) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#{marker})"' if marker else ""
    p = "M" + " L".join(f"{x},{y}" for x, y in pts)
    return f'<path d="{p}" stroke="{color}" stroke-width="{width}" fill="none"{m}{d} stroke-linejoin="round"/>'


def label(x, y, text, anchor="middle", color=None, size=None) -> str:
    c = f' fill="{color}"' if color else ""
    z = f' font-size="{size}"' if size else ""
    return f'<text class="dg-lbl" x="{x}" y="{y}" text-anchor="{anchor}"{c}{z}>{esc(text)}</text>'


def caption(w, y, text) -> str:
    return f'<text class="dg-cap" x="{w/2}" y="{y}" text-anchor="middle">{esc(text)}</text>'


# ---------------------------------------------------------------- runtime
def runtime() -> str:
    """One cycle: where the model sits, and the single path to an order."""
    W, H = 1280, 772
    bw, bh = 265, 76
    s = [head(W, H)]

    s.append(band(28, 36, 1224, 150, "DETERMINISTIC CODE", ACCENT))
    ra, xs = 88, [50, 355, 660, 965]
    s.append(box(xs[0], ra, bw, bh, ["cron"], ACCENT, sub="every 10 min, 08:00-14:50 CT"))
    s.append(box(xs[1], ra, bw, bh, ["gates"], BORDER, sub="halt - market - window - identity"))
    s.append(box(xs[2], ra, bw, bh, ["snapshot"], BORDER, sub="positions - chain - prior"))
    s.append(box(xs[3], ra, bw, bh, ["exits due?"], BORDER, sub="expiry - stop - take-profit"))
    for i in range(3):
        s.append(path([(xs[i] + bw, ra + bh / 2), (xs[i + 1] - 7, ra + bh / 2)], "ag", ACCENT))

    s.append(band(28, 246, 900, 150, "THE MODEL - PROPOSES ONLY", MODEL, dash="6 5", anchor="end"))
    rb = 298
    s.append(box(70, rb, 380, bh, ["bot/decide.py"], MODEL, "#231c35",
                 sub="prompt + 4 read-only tools", mono=True))
    s.append(box(540, rb, 330, bh, ["JSON proposals"], MODEL, "#231c35", sub="or [] - hold"))
    s.append(path([(450, rb + 38), (533, rb + 38)], "am", MODEL))
    s.append(box(985, rb, 240, bh, ["Featherless.ai"], EXT, sub="open-weight model"))
    s.append(path([(934, rb + 22), (978, rb + 22)], "ax", EXT, dash="5 4"))
    s.append(path([(978, rb + 54), (934, rb + 54)], "ax", EXT, dash="5 4"))

    s.append(path([(xs[3] + bw / 2, ra + bh), (xs[3] + bw / 2, 216), (200, 216), (200, rb - 7)], "ag", ACCENT))
    s.append(label(214, 208, "nothing due -> ask the model", "start"))

    s.append(band(28, 470, 1224, 150, "DETERMINISTIC CODE - THE ONLY ORDER PATH", ACCENT, anchor="end"))
    rc = 522
    s.append(box(70, rc, 380, bh, ["place_proposal()"], ACCENT,
                 sub="every write goes through here", mono=True))
    s.append(box(540, rc, 330, bh, ["check_order()"], GATE, sub="rejects, never clamps", mono=True, sw=2.5))
    s.append(box(960, rc, 265, bh, ["Alpaca MCP"], EXT, sub="paper account only"))
    s.append(path([(450, rc + 38), (533, rc + 38)], "ag", ACCENT))
    s.append(path([(870, rc + 38), (953, rc + 38)], "ag", ACCENT))
    s.append(label(911, rc + 26, "approved"))

    s.append(path([(705, rb + bh), (705, 424), (270, 424), (270, rc - 7)], "am", MODEL))
    s.append(path([(xs[3] + bw, ra + bh / 2), (1256, ra + bh / 2), (1256, 444), (350, 444), (350, rc - 7)],
                  "ag", ACCENT))
    s.append(label(1244, 436, "code's own exit sells - same gate", "end"))

    s.append(box(400, 672, 480, 62, ["journal.jsonl"], BORDER, sub="one record per event", mono=True))
    s.append(path([(705, rc + bh), (705, 665)], "ay", GATE))
    s.append(path([(1092, rc + bh), (1092, 703), (887, 703)], "a", DIM))
    s.append(path([(640, rc + bh), (640, 640), (330, 640), (330, 703), (393, 703)], "ar", STOP))
    s.append(label(324, 632, "rejected, with the rule that said no", "end", STOP))

    s.append(caption(W, 758, "Everything outside the dashed box is deterministic code - "
                             "there is no path from the model to an order."))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- journal
def journal() -> str:
    """One append-only file, and the four things that read it."""
    W, H = 1280, 620
    s = [head(W, H)]
    s.append(f'<rect x="330" y="34" width="620" height="96" rx="12" fill="{BOX}" '
             f'stroke="{ACCENT}" stroke-width="2.5"/>')
    s.append('<text class="dg-t dg-m" x="640" y="72" font-size="22" text-anchor="middle" '
             'font-weight="700">journal.jsonl</text>')
    s.append('<text class="dg-sub" x="640" y="98" text-anchor="middle">one per account, append-only</text>')
    s.append(label(640, 120, "every decision, order, rejection, exit, tool call and config hash", size=14))

    columns = [
        (50, ACCENT, "the browser", "Live journal viewer", "bot.wpmccormick.pw",
         ["journal_viewer.py, :8300 on the LAN", "-> cloudflared tunnel",
          "-> Cloudflare Access (email OTP)", "server-sent events, live follow",
          "read-only: no controls at all"]),
        (360, EXT, "the dashboard", "Home Assistant", "MQTT auto-discovery",
         ["journal.log() -> fire-and-forget", "equity, P&L, halt, last decision",
          "kill switch + knobs, per-account", "sections layout: phone-friendly",
          "phone: problems only, never fills"]),
        (670, GATE, "the inbox", "Hourly email", "mail_report.py",
         ["trades with the model's full reason", "CSVs attached, host local time",
          "silent when nothing traded", "one final send after the close"]),
        (980, MODEL, "the loop", "eod_review.py", "the daily change",
         ["round trips from Alpaca's fills", "rejections grouped by rule",
          "Brier score on the priors", "critique by a model that did not trade"]),
    ]
    cw, top = 250, 200
    for x, color, kicker, name, sub, bullets in columns:
        s.append(f'<rect x="{x}" y="{top}" width="{cw}" height="330" rx="12" fill="{PANEL}" stroke="{color}"/>')
        s.append(f'<text class="dg-band" x="{x + cw/2}" y="{top + 30}" fill="{color}" '
                 f'text-anchor="middle">{esc(kicker.upper())}</text>')
        s.append(f'<text class="dg-t" x="{x + cw/2}" y="{top + 66}" font-size="21" text-anchor="middle" '
                 f'font-weight="700">{esc(name)}</text>')
        s.append(f'<text class="dg-sub" x="{x + cw/2}" y="{top + 92}" text-anchor="middle" '
                 f'fill="{color}">{esc(sub)}</text>')
        for i, b in enumerate(bullets):
            s.append(f'<text class="dg-lbl" x="{x + 16}" y="{top + 128 + i*34}" font-size="14.5">{esc("- " + b)}</text>')
        s.append(path([(640, 130), (640, 168), (x + cw / 2, 168), (x + cw / 2, top - 7)], "a", color))

    s.append(caption(W, 590, "One file, four windows. Three are read-only views for humans; "
                             "the fourth decides what changes tomorrow."))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- infra
def infra() -> str:
    """Where it runs, how a merge reaches it, and who can see in."""
    W, H = 1280, 812
    s = [head(W, H)]

    s.append(box(28, 26, 352, 56, ["developer"], BORDER, sub="branch -> pull request"))
    s.append(band(28, 106, 352, 420, "GITHUB", EXT))
    s.append(path([(204, 82), (204, 99)], "a", DIM))
    s.append(box(50, 150, 308, 76, ["CI - ubuntu-latest"], EXT, sub="ruff + 732 tests, no keys"))
    s.append(box(50, 254, 308, 62, ["squash-merge to main"], BORDER))
    s.append(box(50, 344, 308, 96, ["freeze window"], GATE, sub="trading code, 08:20-15:15 CT", sw=2.5))
    s.append(label(204, 424, "hard fail - red X on main", color=STOP))
    s.append(box(50, 462, 308, 48, ["docs, tests, ansible: no deploy"], BORDER, size=15))
    s.append(path([(204, 226), (204, 247)], "a", DIM))
    s.append(path([(204, 316), (204, 337)], "a", DIM))
    s.append(path([(358, 285), (392, 285), (392, 486), (365, 486)], "a", DIM))

    s.append(band(430, 106, 470, 620, "CT 108 - PROXMOX LXC", ACCENT))
    s.append(box(452, 150, 426, 76, ["self-hosted runner"], ACCENT, sub="outbound only - no inbound port"))
    s.append(box(452, 260, 426, 82, ["/opt/alpaca-hackathon"], BORDER,
                 sub="rsync --delete, then restart the bridge", mono=True))
    s.append(path([(665, 226), (665, 253)], "ag", ACCENT))
    s.append(path([(358, 392), (410, 392), (410, 188), (445, 188)], "ag", ACCENT, width=2.5))
    s.append(label(393, 252, "deploy", "start", ACCENT, 13))

    s.append(box(452, 372, 205, 74, ["cron"], BORDER, sub="cycles - flatten", size=18))
    s.append(box(673, 372, 205, 74, ["credentials"], ACCENT, sub="0600, root only", size=18))
    s.append(box(452, 474, 205, 74, ["mqtt_bridge"], GATE, sub="the one inbound", size=18))
    s.append(box(673, 474, 205, 74, ["journal_viewer"], BORDER, sub=":8300, LAN only", size=18))
    s.append(box(452, 576, 426, 62, ["logs/journal*.jsonl"], BORDER,
                 sub="never leaves the box unencrypted", mono=True, size=18))
    for x in (554, 775):
        s.append(path([(x, 342), (x, 365)], "a", DIM))
    s.append(path([(554, 548), (554, 569)], "a", DIM))
    s.append(path([(775, 569), (775, 548)], "a", DIM))
    s.append(label(664, 672, "the judged account resolves only from this box", size=14))
    s.append(label(664, 700, "- never from a laptop, never from CI", size=14))

    s.append(band(946, 106, 306, 236, "MARKET + MODEL", EXT))
    s.append(box(966, 150, 266, 52, ["Alpaca MCP"], EXT, size=18))
    s.append(box(966, 214, 266, 52, ["Featherless.ai"], EXT, size=18))
    s.append(box(966, 278, 266, 52, ["Kalshi - prior only"], EXT, size=18))
    for y in (176, 240, 304):
        s.append(path([(886, 301), (920, 301), (920, y), (959, y)], "ax", EXT, dash="5 4"))

    s.append(band(946, 372, 306, 354, "THE PEOPLE WHO WATCH IT", MODEL))
    s.append(box(966, 416, 266, 76, ["Cloudflare Access"], MODEL, sub="tunnel + email OTP", size=18))
    s.append(box(966, 508, 266, 62, ["any browser"], MODEL, sub="team, judges", size=18))
    s.append(box(966, 586, 266, 62, ["Home Assistant"], MODEL, sub="dashboards, push", size=18))
    s.append(box(966, 664, 266, 52, ["email"], MODEL, sub="hourly + EOD", size=18))
    s.append(path([(886, 511), (930, 511), (930, 454), (959, 454)], "am", MODEL))
    s.append(path([(1099, 492), (1099, 501)], "am", MODEL))
    s.append(path([(886, 607), (930, 607), (930, 617), (959, 617)], "am", MODEL))
    s.append(path([(886, 607), (930, 607), (930, 690), (959, 690)], "am", MODEL))

    s.append(box(28, 560, 352, 88, ["Ansible"], ACCENT, sub="from a workstation, never CI"))
    s.append(box(28, 668, 352, 58, ["homenetwork ansible-vault"], ACCENT, size=17))
    s.append(path([(204, 668), (204, 652)], "ag", ACCENT))
    s.append(path([(380, 604), (423, 604)], "ag", ACCENT))

    s.append(caption(W, 794, "A merge deploys in about a minute. Nothing reaches the container "
                             "inbound except the tunnel and the broker."))
    s.append("</svg>")
    return "".join(s)


DIAGRAMS = {"runtime": runtime, "journal": journal, "infra": infra}


# ---------------------------------------------------------------- pipeline
def diagrams() -> dict[str, str]:
    """{name: svg} for every diagram, freshly generated."""
    return {name: fn() for name, fn in DIAGRAMS.items()}


def source_hash(svg: str) -> str:
    return hashlib.sha256(svg.strip().encode()).hexdigest()[:12]


def for_deck(svg: str) -> str:
    """The deck sizes diagrams itself.

    Drop the intrinsic width/height so the slide's CSS wins, and set
    flex:0 0 auto - a slide is a flex column, and without it the SVG is a
    shrinkable flex item that a text-heavy slide crushes to a hairline. No
    max-height inline, deliberately: the deck's stylesheet sets that so
    scripts/fit_slides.py can override it when a slide does not fit.
    """
    svg = re.sub(r'\s(width|height)="\d+"', "", svg, count=2)
    return svg.replace("<svg ", '<svg style="width:100%;height:auto;flex:0 0 auto" ', 1)


def inject(text: str, name: str, body: str, digest: str, where: Path) -> str:
    start, end = f"<!-- diagram:{name} -->", f"<!-- /diagram:{name} -->"
    if start not in text or end not in text:
        raise SystemExit(f"{where}: missing {start} ... {end} markers")
    head_, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return (f"{head_}{start}\n<!-- generated by scripts/render_diagrams.py, "
            f"source sha {digest} - do not hand-edit -->\n{body}\n{end}{tail}")


def deck_hashes(text: str) -> dict[str, str]:
    """{name: hash} as recorded in a destination at generation time."""
    pattern = re.compile(r"<!--\s*diagram:([a-z0-9_-]+)\s*-->\s*\n<!--[^>]*source sha ([0-9a-f]+)[^>]*-->")
    return {m.group(1): m.group(2) for m in pattern.finditer(text)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report drift without writing anything")
    args = ap.parse_args()

    built = diagrams()
    deck_text = DECK.read_text()
    recorded = deck_hashes(deck_text)

    if args.check:
        drift = []
        for name, svg in built.items():
            digest = source_hash(svg)
            on_disk = SVG_DIR / f"{name}.svg"
            stale = recorded.get(name) != digest or not on_disk.exists() or on_disk.read_text() != svg
            print(f"  {'STALE' if stale else 'ok':6} {name}")
            if stale:
                drift.append(name)
        if drift:
            print(f"\nrun: python scripts/render_diagrams.py   ({', '.join(drift)} changed)", file=sys.stderr)
            return 1
        return 0

    SVG_DIR.mkdir(parents=True, exist_ok=True)
    for name, svg in built.items():
        digest = source_hash(svg)
        (SVG_DIR / f"{name}.svg").write_text(svg)
        deck_text = inject(deck_text, name, for_deck(svg), digest, DECK)
        print(f"  {name} ({digest}) -> docs/diagrams/{name}.svg + the deck")
    DECK.write_text(deck_text)

    svg = built[README_DIAGRAM]
    img = (f'<p align="center">\n'
           f'  <img src="docs/diagrams/{README_DIAGRAM}.svg" width="900"\n'
           f'       alt="One cycle: deterministic gates and snapshot, the model proposing JSON, '
           f'then a single order path through check_order() to Alpaca MCP">\n'
           f'</p>')
    updated = inject(README.read_text(), README_DIAGRAM, img, source_hash(svg), README)
    if updated != README.read_text():
        README.write_text(updated)
        print(f"  synced {README_DIAGRAM} into README.md")

    print("\nnow re-fit and re-export the deck - see submission/README-export.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
