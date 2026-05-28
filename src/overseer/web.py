"""web — single-pane live dashboard (stdlib http.server, zero deps).

`overseer serve` starts:
  - a background poll thread (engine.scan_once into SQLite)
  - an HTTP server: GET / -> dashboard HTML, GET /api/leads -> JSON board

The page polls /api/leads every 2s. Sessions that need Tom float to the top.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import notify, roster
from .engine import scan_once
from .store import Store


# state -> (label, css color, sort priority)
STATES = {
    "needs-human": ("NEEDS YOU", "#ff5c5c", 0),
    "crashed":     ("CRASHED",   "#ff3b3b", 1),
    "blocked":     ("BLOCKED?",  "#ffb020", 2),
    "waiting":     ("waiting",   "#7c8aa5", 3),
    "working":     ("working",   "#3ddc84", 4),
    "done":        ("done",      "#5a6b7b", 5),
    "gone":        ("gone",      "#3a3f4a", 6),
    "unknown":     ("unknown",   "#7c8aa5", 7),
}

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Overseer</title>
<style>
  :root{--bg:#0d1017;--card:#161a22;--line:#232936;--fg:#e6e9ef;--mut:#7c8aa5}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;
    align-items:baseline;gap:16px}
  h1{font-size:16px;margin:0;letter-spacing:2px;font-weight:600}
  .sub{color:var(--mut);font-size:12px}
  .wrap{padding:16px 24px;max-width:1100px;margin:0 auto}
  .row{display:grid;grid-template-columns:130px 1fr 90px 90px;gap:12px;
    align-items:center;padding:11px 14px;border:1px solid var(--line);
    border-radius:10px;background:var(--card);margin-bottom:8px}
  .pill{font-size:11px;font-weight:700;letter-spacing:1px;padding:4px 9px;
    border-radius:6px;text-align:center;color:#0d1017}
  .name{font-weight:600} .meta{color:var(--mut);font-size:12px}
  .dim{color:var(--mut)} .alert{box-shadow:0 0 0 1px #ff5c5c33}
  .empty{color:var(--mut);padding:40px;text-align:center}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
</style></head><body>
<header><h1>OVERSEER</h1><span class="sub" id="sub">connecting...</span></header>
<div class="wrap" id="board"></div>
<script>
const STATES=__STATES__;
function age(ts){if(!ts)return'-';let d=Math.max(0,Date.now()/1000-ts);
  if(d<60)return Math.floor(d)+'s';if(d<3600)return Math.floor(d/60)+'m';return Math.floor(d/3600)+'h';}
async function tick(){
  try{
    const r=await fetch('/api/leads');const j=await r.json();
    const live=j.leads.filter(l=>l.derived_state!=='gone');
    live.sort((a,b)=>(STATES[a.derived_state]?.[2]??9)-(STATES[b.derived_state]?.[2]??9));
    const need=live.filter(l=>['needs-human','crashed','blocked'].includes(l.derived_state)).length;
    document.getElementById('sub').textContent=
      `${live.length} live leads · ${need} need you · updated ${new Date().toLocaleTimeString()}`;
    const b=document.getElementById('board');
    if(!live.length){b.innerHTML='<div class="empty">no live sessions</div>';return;}
    b.innerHTML=live.map(l=>{
      const s=STATES[l.derived_state]||['?','#7c8aa5',9];
      const alert=['needs-human','crashed','blocked'].includes(l.derived_state)?' alert':'';
      const label=l.name||l.domain||'?';
      return `<div class="row${alert}">
        <span class="pill" style="background:${s[1]}">${s[0]}</span>
        <span><span class="name">${label}</span>
          <span class="meta"> · ${l.last_event||''}</span></span>
        <span class="meta">${age(l.last_mtime)} ago</span>
        <span class="meta dim">${(l.session_id||'').slice(0,8)}</span>
      </div>`;}).join('');
  }catch(e){document.getElementById('sub').textContent='daemon offline — '+e;}
}
tick();setInterval(tick,2000);
</script></body></html>"""


def _poll_loop(store: Store, interval: float, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            scan_once(store, on_transition=notify.on_transition)
        except roster.RosterError:
            pass
        except Exception:
            pass
        stop.wait(interval)


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                page = PAGE.replace(
                    "__STATES__",
                    json.dumps({k: [v[0], v[1], v[2]] for k, v in STATES.items()}),
                )
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path.startswith("/api/leads"):
                # Open a per-request connection: ThreadingHTTPServer dispatches
                # each request on its own thread, and sqlite connections are
                # not shareable across threads.
                s = Store(db_path)
                try:
                    rows = [dict(r) for r in s.all_leads()]
                finally:
                    s.close()
                self._send(200, json.dumps({"leads": rows}), "application/json")
            else:
                self._send(404, "not found", "text/plain")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8899, interval: float = 5.0) -> int:
    # The poll thread owns one writer connection. Each HTTP request opens its
    # own short-lived reader connection (sqlite is not thread-shareable).
    writer = Store()
    db_path = writer.db_path
    stop = threading.Event()
    t = threading.Thread(target=_poll_loop, args=(writer, interval, stop), daemon=True)
    t.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"overseer: dashboard at http://{host}:{port}  (poll {interval}s, Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        writer.close()
    return 0
