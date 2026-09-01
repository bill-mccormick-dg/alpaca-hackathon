#!/usr/bin/env python3
"""Read-only web view of the journal stream — the terminal watcher, in a browser.

One page, three journals, live. Serves the raw records over SSE as they are
appended and lets the page render them: per-account colour, one line per
event, filters, and replay of any prior day via bot/journal.py's own reader.
Exists because "is it working, and what is it doing?" (bot/report.py) needs
an answer that is a link, not host access - the HA card (issue #134) shows
the recent tail; this shows everything, with scrollback.

Read-only BY CONSTRUCTION, not by configuration: it opens the journal files
and nothing else. No credentials are loaded, no MCP client is created, no
MQTT is published, and there is no POST route at all. Auth and TLS are the
exposure layer's job (a Cloudflare Tunnel + Access in front - homenetwork
#282); this binds a LAN port and trusts it.

Stdlib only, deliberately. This project has six dependencies and that is a
feature; a web framework does not ship for a page that needs exactly two
routes and a stream.

    python journal_viewer.py                    # port 8300, ./logs
    python journal_viewer.py --port 8300 --logs-dir /opt/alpaca-hackathon/logs
"""

import argparse
import json
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bot import journal

# journal.jsonl is the official account's file (bot/journal.py::journal_file
# gives the default name to official); every other account is suffixed.
OFFICIAL_FILE = "journal.jsonl"
POLL_SEC = 0.5
BACKLOG_LINES = 200  # per file, on connect - enough for the day so far
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def account_for(path: Path) -> str:
    if path.name == OFFICIAL_FILE:
        return "official"
    return path.stem.removeprefix("journal-")


def journal_files(logs_dir: Path) -> list[Path]:
    """Every account journal present right now. Globbed per call because a
    new account's file appears the first time that account journals."""
    return [p for p in sorted(logs_dir.glob("journal*.jsonl")) if p.name.count(".") == 1]


class Tailer(threading.Thread):
    """Follows every journal file in logs_dir; parsed records go to every
    subscribed queue with the account name attached.

    One thread for all files rather than one per file: at this event rate
    (tens per ten-minute cycle) a 0.5s poll across three files is nothing,
    and there is no partial-line risk - bot/journal.py writes each record
    as a single write() of line + newline."""

    def __init__(self, logs_dir: Path):
        super().__init__(daemon=True)
        self.logs_dir = logs_dir
        self._offsets: dict[Path, int] = {}
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def _publish(self, record: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(record)
            except queue.Full:
                pass  # a stalled client loses events; it can reload

    def poll_once(self) -> list[dict]:
        """Read anything new past the stored offsets. Returns what it
        published (which is what makes this testable without the thread)."""
        out = []
        for path in journal_files(self.logs_dir):
            size = path.stat().st_size
            offset = self._offsets.get(path)
            if offset is None:
                # First sighting: start at the end. The backlog endpoint
                # covers history; the stream is only ever "from now".
                self._offsets[path] = size
                continue
            if size < offset:  # truncated/rotated - start over
                offset = 0
            if size == offset:
                continue
            with open(path) as f:
                f.seek(offset)
                chunk = f.read()
                self._offsets[path] = f.tell()
            for line in chunk.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                record["_account"] = account_for(path)
                out.append(record)
        for record in out:
            self._publish(record)
        return out

    def run(self) -> None:
        while True:
            try:
                self.poll_once()
            except OSError:
                pass  # a file mid-rotation; next poll sees the new state
            time.sleep(POLL_SEC)


def backlog(logs_dir: Path, lines_per_file: int = BACKLOG_LINES) -> list[dict]:
    """The recent past across all accounts, merged and time-ordered - the
    page renders this before the live stream starts, so a fresh load shows
    the day so far rather than a blank screen."""
    records = []
    for path in journal_files(logs_dir):
        for line in path.read_text().splitlines()[-lines_per_file:]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            record["_account"] = account_for(path)
            records.append(record)
    records.sort(key=lambda r: str(r.get("ts", "")))
    return records


def history(logs_dir: Path, day: str) -> list[dict]:
    """One prior day, every account, via bot/journal.py's own reader so the
    filtering semantics (Eastern dates, skip bad lines) match the bot's."""
    records = []
    for path in journal_files(logs_dir):
        for record in journal.read_events(day=day, journal=path):
            record["_account"] = account_for(path)
            records.append(record)
    records.sort(key=lambda r: str(r.get("ts", "")))
    return records


class Handler(BaseHTTPRequestHandler):
    tailer: Tailer  # set by serve()
    logs_dir: Path

    def log_message(self, *args) -> None:  # journald gets enough already
        pass

    def _json(self, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/history":
            day = (parse_qs(url.query).get("day") or [""])[0]
            if not DAY_RE.match(day):
                self.send_error(400, "day must be YYYY-MM-DD")
                return
            self._json(history(self.logs_dir, day))
        elif url.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = self.tailer.subscribe()
            try:
                for record in backlog(self.logs_dir):
                    self._sse(record)
                self.wfile.write(b"event: live\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        record = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")  # comment frame
                        self.wfile.flush()
                        continue
                    self._sse(record)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.tailer.unsubscribe(q)
        else:
            self.send_error(404)

    def _sse(self, record: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(record).encode() + b"\n\n")
        self.wfile.flush()


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Day Trader — journal</title>
<style>
  :root { --bg:#0f1720; --fg:#e6edf3; --dim:#8b98a5; --official:#d48ae0; --test:#6fd3d3; --mixed:#7fa7e8; }
  body { background:var(--bg); color:var(--fg); font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; margin:0; }
  header { position:sticky; top:0; background:#131e2a; padding:8px 14px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; border-bottom:1px solid #22303e; }
  header b { font-size:14px; }
  label { color:var(--dim); user-select:none; }
  #feed { padding:10px 14px 40px; }
  .row { white-space:pre-wrap; word-break:break-word; }
  .ts { color:var(--dim); }
  .acct-official { color:var(--official); } .acct-test { color:var(--test); } .acct-mixed { color:var(--mixed); }
  .ev-order_submitted { color:#69d58c; font-weight:600; }
  .ev-order_rejected, .ev-order_error, .ev-error, .ev-identity_refused { color:#e8b34b; }
  .ev-manual_halt, .ev-daily_loss_halt { color:#ef6a6a; font-weight:600; }
  .ev-decision { font-weight:600; }
  .dim { color:var(--dim); }
  .reason { color:var(--dim); padding-left:2.5em; display:block; }
  .banner { color:#e8b34b; font-weight:700; margin:10px 0; }
  input[type=date] { background:#1a2733; color:var(--fg); border:1px solid #2a3947; border-radius:4px; padding:2px 6px; }
  a { color:#6fb3e8; }
  #jump { position:fixed; right:18px; bottom:18px; z-index:5; display:none;
    background:#2a6f9e; color:#fff; border:0; border-radius:16px; padding:7px 14px;
    font:inherit; cursor:pointer; box-shadow:0 2px 10px rgba(0,0,0,.5); }
  #jump.show { display:block; }
  #trimmed { color:var(--dim); font-style:italic; padding:4px 0; }
</style></head><body>
<header>
  <b>AI Day Trader — journal</b>
  <span id="status" class="dim">connecting…</span>
  <label><input type="checkbox" class="acct" value="official" checked> official</label>
  <label><input type="checkbox" class="acct" value="test" checked> test</label>
  <label><input type="checkbox" class="acct" value="mixed" checked> mixed</label>
  <label><input type="checkbox" id="chatter"> tool/config chatter</label>
  <label>replay <input type="date" id="day"></label>
</header>
<div id="feed"></div>
<button id="jump">↓ <span id="jumpn"></span> new</button>
<script>
const feed = document.getElementById('feed'), status = document.getElementById('status');
const jump = document.getElementById('jump'), jumpn = document.getElementById('jumpn');
const NOISY = new Set(['tool_call','config']);
// A cycle writes ~10 events and there are ~40 cycles a day across three
// accounts, so an unbounded feed reaches five figures of DOM nodes by the
// close and the tab crawls. Keep a window; the whole journal is always one
// day-replay away.
const MAX_ROWS = 600;
let lastDay = null, rows = [], unseen = 0;

function fmt(ts){ try { return new Date(ts).toLocaleTimeString('en-US',{hour12:false}); } catch(e){ return '--:--:--'; } }
function dayOf(ts){ try { return new Date(ts).toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'}); } catch(e){ return ''; } }
function esc(s){ const d=document.createElement('span'); d.textContent=s==null?'':String(s); return d.innerHTML; }

function line(r){
  const e = r.event, a = r._account || '?';
  const head = `<span class="ts">${fmt(r.ts)}</span> <span class="acct-${a}">${a.padEnd(8)}</span> `;
  let body;
  if (e === 'cycle_start') body = `▶ CYCLE  equity $${Number(r.equity).toLocaleString()}  day P&L ${Number(r.day_pnl).toFixed(2)}  positions ${r.positions}${r.dry_run?' [DRY RUN]':''}`;
  else if (e === 'config') body = `<span class="dim">  model ${esc(r.model)}  review ${esc(r.review_model)}  hash ${esc(r.config_hash)}</span>`;
  else if (e === 'predictions') {
    body = ['SPY','QQQ'].filter(s=>r[s]).map(s=>{ const p=r[s];
      return `◈ PRIOR  ${s} ref ${p.reference_close}  median ${p.implied_median} (${p.implied_move_pct}%)  P(above) ${p.p_above_reference}  vol ${p.volume} → ${p.suppressed?('withheld: '+esc(p.suppressed)):'shown'}`; }).join('\n' + ' '.repeat(0));
  }
  else if (e === 'tool_call') body = `<span class="dim">  · ${esc(r.tool)} ${esc(JSON.stringify(r.args||{}).slice(0,70))} → ${r.result_chars} chars</span>`;
  else if (e === 'decision') { body = `✱ MODEL  ${r.count} proposal(s)  <span class="dim">${esc(r.model)}  ${(r.usage||{}).total_tokens} tok  ${r.latency_sec}s</span>`;
    try { let acts = JSON.parse(r.raw); if (acts && !Array.isArray(acts)) acts = acts.actions || acts.proposals || [acts];
      for (const p of acts||[]) body += `\n<span class="reason">${esc(p.side)} ${esc(p.qty)} ${esc(p.symbol)}${p.limit_price?' @ '+esc(p.limit_price):''} — ${esc(p.reason||'')}</span>`; } catch(err){}
    const c = r.citations;  // #172: figures the reason quoted that the prior never contained
    if (c && c.unsupported && c.unsupported.length) body += `\n<span class="reason">⚠ ${c.unsupported.length} unsupported prior citation(s): ${esc(c.unsupported.map(u => u.quoted + ' (nearest real: ' + u.nearest.label + ' ' + u.nearest.value + ')').join('; '))}</span>`;
    if (c && c.misattributed && c.misattributed.length) body += `\n<span class="reason">⚠ ${c.misattributed.length} misattributed prior citation(s): ${esc(c.misattributed.map(u => u.quoted + ' is ' + u.nearest.label).join('; '))}</span>`; }
  else if (e === 'order_submitted') body = `✓ ORDER  ${esc(r.side)} ${esc(r.qty)} ${esc(r.symbol)} @ ${esc(r.price)}${r.exit?' (exit)':''}\n<span class="reason">${esc(r.reason||'')}</span>`;
  else if (e === 'order_rejected') { body = `✗ BLOCKED ${esc(r.side)} ${esc(r.qty)} ${esc(r.symbol)} — ${esc(r.detail)}`;
    // Verdict first, then the model's case: they are different facts and a
    // rejection needs both (same reasoning as bot/report.py::_trade_line).
    if (r.reason) body += `\n<span class="reason">${esc(r.reason)}</span>`; }
  else if (e === 'order_error') body = `! ORDER ERROR ${esc(r.symbol)} — ${esc(r.detail)}`;
  else if (e === 'dry_run') body = `⋯ DRY    ${esc(r.side)} ${esc(r.qty)} ${esc(r.symbol)} @ ${esc(r.price)}`;
  else if (e === 'order_canceled') body = `${r.ok ? '↩ CANCELLED' : '! cancel failed'} stale buy ${esc(r.qty)} ${esc(r.symbol)}${r.limit_price?' @ '+esc(r.limit_price):''} — ${esc(r.detail||'')}`;
  else if (e === 'cycle_end') body = `<span class="dim">◀ end, ${r.actions} action(s)</span>`;
  else if (e === 'error') body = `! ERROR in ${esc(r.where)}: ${esc(String(r.detail).slice(0,600))}`;
  else if (e === 'eod_review') body = `◆ EOD ${esc(r.day)}`;
  else body = `${esc(e)} ${esc(JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k])=>!['ts','event','_account'].includes(k)))).slice(0,160))}`;
  return head + `<span class="ev-${e}">${body}</span>`;
}

function push(r, live){
  const d = dayOf(r.ts);
  if (d && d !== lastDay){ lastDay = d; const b=document.createElement('div'); b.className='banner'; b.textContent=`── ${d} ──`; feed.appendChild(b); }
  const el = document.createElement('div');
  el.className = 'row'; el.dataset.account = r._account; el.dataset.event = r.event;
  el.innerHTML = line(r);
  feed.appendChild(el); rows.push(el);
  applyFilters(el);
  trim();
  if (!live) return;
  if (pinned()) { toBottom(); }
  else { unseen++; jumpn.textContent = unseen; jump.classList.add('show'); }
}

function trim(){
  if (rows.length <= MAX_ROWS) return;
  const drop = rows.splice(0, rows.length - MAX_ROWS);
  for (const el of drop) el.remove();
  let note = document.getElementById('trimmed');
  if (!note){ note = document.createElement('div'); note.id='trimmed'; feed.prepend(note); }
  note.textContent = '… older events trimmed from view — use the date picker to replay a full day';
}

// "Pinned" means the reader is at the live end and wants to stay there.
// Anyone scrolled up is reading something and must not be yanked away.
function pinned(){ return window.innerHeight + window.scrollY >= document.body.offsetHeight - 60; }
function toBottom(){ window.scrollTo(0, document.body.scrollHeight); unseen = 0; jump.classList.remove('show'); }
jump.addEventListener('click', toBottom);
window.addEventListener('scroll', () => { if (pinned()) toBottom(); });
function applyFilters(only){
  const accts = new Set([...document.querySelectorAll('.acct:checked')].map(c=>c.value));
  const chatter = document.getElementById('chatter').checked;
  for (const el of only ? [only] : rows){
    el.hidden = !accts.has(el.dataset.account) || (!chatter && NOISY.has(el.dataset.event));
  }
}
document.querySelectorAll('.acct, #chatter').forEach(c=>c.addEventListener('change',()=>applyFilters()));

let es = null;
function connectLive(){
  feed.innerHTML=''; rows=[]; lastDay=null;
  es = new EventSource('/events');
  let live = false;
  es.addEventListener('live', ()=>{ live = true; status.textContent='live'; toBottom(); });
  es.onmessage = ev => push(JSON.parse(ev.data), live);
  es.onerror = ()=>{ status.textContent='reconnecting…'; };
}
document.getElementById('day').addEventListener('change', async ev=>{
  const day = ev.target.value;
  if (!day){ connectLive(); return; }
  if (es) es.close();
  status.textContent = `replaying ${day}`;
  const r = await fetch('/history?day='+day); const records = await r.json();
  feed.innerHTML=''; rows=[]; lastDay=null;
  records.forEach(rec=>push(rec,false));
  unseen = 0; jump.classList.remove('show');
  window.scrollTo(0, 0);  // a replay is history: start at the beginning of it
  if (!records.length) feed.innerHTML = '<div class="dim">nothing journaled that day</div>';
});
connectLive();
</script>
</body></html>
"""


def serve(port: int, logs_dir: Path) -> ThreadingHTTPServer:
    tailer = Tailer(logs_dir)
    tailer.poll_once()  # prime offsets so the stream starts "from now"
    tailer.start()
    Handler.tailer = tailer
    Handler.logs_dir = logs_dir
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    return server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--logs-dir", default=str(Path(__file__).resolve().parent / "logs"))
    args = ap.parse_args()
    server = serve(args.port, Path(args.logs_dir))
    print(f"journal viewer on :{args.port}, logs from {args.logs_dir}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
