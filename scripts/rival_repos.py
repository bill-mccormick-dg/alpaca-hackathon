#!/usr/bin/env python3
"""Track which rival hackathon repos are public, so the closed ones can be
re-checked after the deadline (#post-competition).

Alpaca's FAQ lets a repo stay private *during* the hackathon, and lablab
requires a public repo as a submission item - so a repo that reads "private"
tonight is a snapshot, not a verdict. Most of them are expected to open at or
after Fri Sep 4 10:00 CDT, and that is when the interesting code becomes
readable.

    rival_repos.py scan          read the lablab listing, resolve each project's
                                 GitHub link, record public/private/none
    rival_repos.py recheck       re-test only the ones that were NOT public,
                                 and report which ones opened since
    rival_repos.py report        what we know, grouped by state

State lives in submission/rival-repos.json (gitignored - it is a scratch
record of other people's projects, not part of the submission).

Stdlib only; uses `gh api` when available for a higher rate limit, else
anonymous api.github.com (60 requests/hour - the scan is one call per repo, so
mind the ceiling on a cold run).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
STATE = HERE / "submission" / "rival-repos.json"
EVENT = "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon"
OURS = "razorsedge"
GH_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)", re.IGNORECASE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"first_scan": None, "last_check": None, "projects": {}}


def save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")


def fetch(url: str, timeout: int = 20) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "rival-repos/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def repo_state(owner: str, name: str) -> tuple[str, dict]:
    """'public' | 'private-or-404' | 'unknown', plus whatever metadata came back.

    GitHub returns 404 for both private and nonexistent repos to an
    unauthenticated caller - they are indistinguishable from outside, which is
    why the two are one bucket rather than a guess.
    """
    api = f"https://api.github.com/repos/{owner}/{name}"
    try:  # gh first: authenticated, 5000/hr instead of 60
        out = subprocess.run(["gh", "api", f"repos/{owner}/{name}"],
                             capture_output=True, text=True, timeout=25, check=False)
        if out.returncode == 0:
            j = json.loads(out.stdout)
            return "public", {"stars": j.get("stargazers_count"),
                              "pushed_at": j.get("pushed_at"),
                              "language": j.get("language"),
                              "description": (j.get("description") or "")[:200]}
        if "Not Found" in (out.stderr or ""):
            return "private-or-404", {}
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    body = fetch(api)
    if body is None:
        return "unknown", {}
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return "unknown", {}
    if j.get("full_name"):
        return "public", {"stars": j.get("stargazers_count"),
                          "pushed_at": j.get("pushed_at"),
                          "language": j.get("language"),
                          "description": (j.get("description") or "")[:200]}
    return "private-or-404", {}


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def listing_html() -> str | None:
    """The listing is client-side rendered - plain urllib sees a JS shell with
    no project links in it. Render it with headless Chrome (the same binary
    the deck export uses), and fall back to urllib in case that ever changes."""
    if Path(CHROME).exists():
        try:
            out = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu",
                 "--virtual-time-budget=15000", "--dump-dom", EVENT],
                capture_output=True, text=True, timeout=120, check=False)
            if out.stdout and "alpaca-ai-trading-agents-hackathon/" in out.stdout:
                return out.stdout
        except (subprocess.TimeoutExpired, OSError):
            pass
    return fetch(EVENT)


def project_urls() -> list[str]:
    html = listing_html()
    if not html:
        print("could not fetch the listing page", file=sys.stderr)
        return []
    pat = re.compile(r'href="(/ai-hackathons/alpaca-ai-trading-agents-hackathon/([^/"]+)/([^/"?#]+))"')
    seen = {}
    for full, team, slug in pat.findall(html):
        if team == "live" or slug == "submission":
            continue
        seen["https://lablab.ai" + full] = None
    return list(seen)


def scan(args: argparse.Namespace) -> int:
    d = load()
    urls = project_urls()
    if not urls:
        print("no project pages found - the listing may be JS-rendered; pass --urls-file")
        if not args.urls_file:
            return 1
    if args.urls_file:
        urls = [x.strip() for x in Path(args.urls_file).read_text().splitlines() if x.strip()]
    print(f"{len(urls)} project page(s)")
    d["first_scan"] = d.get("first_scan") or now()
    for i, u in enumerate(urls, 1):
        team = u.rstrip("/").split("/")[-2]
        if team == OURS:
            continue
        rec = d["projects"].setdefault(u, {"team": team})
        if rec.get("repo") and not args.force:
            continue  # already resolved; recheck handles state changes
        page = fetch(u)
        repos = []
        if page:
            for owner, name in GH_RE.findall(page):
                name = name.removesuffix(".git")
                if owner.lower() in {"alpacahq", "lablab-ai", "modelcontextprotocol"}:
                    continue  # links to the sponsor's own repos, not theirs
                repos.append(f"{owner}/{name}")
        rec["repo"] = repos[0] if repos else None
        rec["all_repos"] = sorted(set(repos))[:5]
        if rec["repo"]:
            owner, name = rec["repo"].split("/", 1)
            state, meta = repo_state(owner, name)
            rec["state"], rec["meta"] = state, meta
        else:
            rec["state"] = "no-repo-listed"
        rec["checked"] = now()
        print(f"  [{i}/{len(urls)}] {team:28} {rec['repo'] or '-':40} {rec['state']}")
    d["last_check"] = now()
    save(d)
    return report(args)


def recheck(args: argparse.Namespace) -> int:
    """Re-test only what was not public. This is the post-deadline pass."""
    d = load()
    todo = [(u, r) for u, r in d["projects"].items()
            if r.get("repo") and r.get("state") != "public"]
    if not todo:
        print("nothing to recheck - every project with a repo was already public")
        return report(args)
    print(f"rechecking {len(todo)} repo(s) that were not public\n")
    opened = []
    for u, r in todo:
        owner, name = r["repo"].split("/", 1)
        state, meta = repo_state(owner, name)
        was = r.get("state")
        r["state"], r["meta"], r["checked"] = state, meta, now()
        flag = ""
        if was != "public" and state == "public":
            opened.append((r["team"], r["repo"], meta))
            r["opened_at"] = now()
            flag = "  <-- OPENED"
        print(f"  {r['team']:28} {r['repo']:40} {was} -> {state}{flag}")
    d["last_check"] = now()
    save(d)
    if opened:
        print(f"\n{len(opened)} repo(s) opened since the last check - worth reading:")
        for team, repo, meta in opened:
            lang = (meta or {}).get("language") or "?"
            push = ((meta or {}).get("pushed_at") or "?")[:10]
            print(f"  https://github.com/{repo}  ({lang}, last push {push})")
    else:
        print("\nnone opened since the last check")
    return 0


def report(args: argparse.Namespace) -> int:
    d = load()
    p = d["projects"]
    if not p:
        print("no state yet - run `rival_repos.py scan`")
        return 1
    buckets: dict[str, list] = {}
    for r in p.values():
        buckets.setdefault(r.get("state", "unknown"), []).append(r)
    print(f"\nfirst scan {d.get('first_scan')}   last check {d.get('last_check')}")
    print(f"{len(p)} project(s) tracked\n")
    for state in ("public", "private-or-404", "no-repo-listed", "unknown"):
        rows = buckets.get(state, [])
        print(f"  {state:16} {len(rows)}")
    pub = sorted(buckets.get("public", []),
                 key=lambda r: ((r.get("meta") or {}).get("pushed_at") or ""), reverse=True)
    if pub:
        print("\npublic repos, most recently pushed first:")
        for r in pub[:40]:
            m = r.get("meta") or {}
            print(f"  {r['team']:26} {r['repo']:40} {m.get('language') or '?'!s:12}"
                  f" push {str(m.get('pushed_at') or '?')[:10]}  stars {m.get('stars', '?')}")
    closed = buckets.get("private-or-404", [])
    if closed:
        print(f"\nnot public yet ({len(closed)}) - recheck after the deadline:")
        for r in closed:
            print(f"  {r['team']:26} {r['repo']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="resolve every project's repo and record its state")
    s.add_argument("--force", action="store_true", help="re-resolve repos already recorded")
    s.add_argument("--urls-file", help="newline-separated project URLs, if the listing is JS-rendered")
    s.set_defaults(fn=scan)
    r = sub.add_parser("recheck", help="re-test only the repos that were not public")
    r.set_defaults(fn=recheck)
    p = sub.add_parser("report", help="what we know, grouped by state")
    p.set_defaults(fn=report)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
