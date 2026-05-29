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

from . import budget, driver, notify, progress, relay, roster, route, usage
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

_MAX_BODY = 1_000_000   # cap POST bodies — operate commands are tiny; reject larger (no OOM)

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
  /* moat overlay: alerts strip + per-row insight */
  #alerts{margin-bottom:10px}
  .alert-chip{display:flex;gap:10px;align-items:baseline;padding:9px 13px;margin-bottom:6px;
    border-radius:9px;background:#1c1410;border-left:3px solid #ffb020}
  .alert-chip.stalled{border-color:#ff5c5c;background:#1c1012}
  .alert-chip.spiraling{border-color:#ffb020}
  .alert-chip.over-budget{border-color:#c98bff;background:#16121c}
  .alert-chip .k{font-weight:700;font-size:11px;letter-spacing:1px}
  .alert-chip .n{font-weight:600} .alert-chip .w{color:var(--mut);font-size:12px}
  .ins .v-stalled{color:#ff7a7a;font-weight:700}
  .ins .v-spiraling{color:#ffc04d;font-weight:700}
  .ins .b-over{color:#d6a3ff;font-weight:700} .ins .b-approaching{color:#ffc04d}
  /* operate panel */
  .op{margin-top:22px;border-top:1px solid var(--line);padding-top:16px}
  .op h2{font-size:12px;letter-spacing:2px;color:var(--mut);margin:0 0 4px;font-weight:600}
  .op .note{color:var(--mut);font-size:11px;margin-bottom:12px}
  .cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .card{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:13px}
  .card.full{grid-column:1/-1}
  .card h3{font-size:12px;margin:0 0 9px;font-weight:600;letter-spacing:.5px}
  .card label{display:block;color:var(--mut);font-size:11px;margin:7px 0 3px}
  select,input[type=text],textarea{width:100%;background:#0d1017;color:var(--fg);
    border:1px solid var(--line);border-radius:7px;padding:8px 9px;font:13px/1.4
    ui-monospace,Menlo,monospace;resize:vertical}
  textarea{min-height:46px}
  .ctl{display:flex;align-items:center;gap:12px;margin-top:10px}
  button{background:#1f6feb;color:#fff;border:0;border-radius:7px;padding:8px 16px;
    font:600 13px ui-monospace,Menlo,monospace;cursor:pointer}
  button:hover{background:#388bfd} button:disabled{opacity:.5;cursor:default}
  .chk{color:var(--mut);font-size:11px;display:flex;align-items:center;gap:5px}
  .out{margin-top:12px;min-height:20px;font-size:12px;white-space:pre-wrap;
    border-radius:7px;padding:9px 11px;border:1px solid var(--line);background:#0d1017}
  .out.ok{border-color:#3ddc8455;color:#9be9c0} .out.err{border-color:#ff5c5c55;color:#ffb3b3}
</style></head><body>
<header><h1>OVERSEER</h1><span class="sub" id="sub">connecting...</span></header>
<div class="wrap">
<div id="alerts"></div>
<div id="board"></div>
<div class="op">
  <h2>OPERATE</h2>
  <div class="note">owned workers only — Overseer never types into your own hand-started sessions.
    A target must be idle (waiting) unless you force it.</div>
  <div class="cards">
    <div class="card">
      <h3>Send a command</h3>
      <label>target worker</label><select id="t_target"></select>
      <label>command</label><textarea id="t_text" placeholder="e.g. run the test suite and fix the first failure"></textarea>
      <label>context (optional — pasted below the command)</label><textarea id="t_ctx" placeholder="an error, a file's contents, prior output..."></textarea>
      <div class="ctl"><button id="t_go">Send</button>
        <span class="chk"><input type="checkbox" id="t_force">force (interrupt if busy)</span></div>
    </div>
    <div class="card">
      <h3>Route — let Overseer pick the worker</h3>
      <label>instruction</label><textarea id="r_text" placeholder="e.g. rerun the vault sync ingest"></textarea>
      <div class="ctl"><button id="r_go">Route &amp; send</button>
        <span class="chk"><input type="checkbox" id="r_force">force</span></div>
      <div class="note" style="margin-top:9px">refuses if no single worker clearly matches (no mis-route).</div>
    </div>
    <div class="card full">
      <h3>Relay — pipe one worker's latest output into another</h3>
      <div class="ctl" style="margin-top:0">
        <span style="flex:1"><label>from</label><select id="x_src"></select></span>
        <span style="flex:1"><label>to</label><select id="x_dst"></select></span>
        <span><label>&nbsp;</label><button id="x_go">Relay</button></span>
        <span class="chk"><label>&nbsp;</label><span><input type="checkbox" id="x_force">force</span></span>
      </div>
    </div>
  </div>
  <div class="out" id="op_out">no action yet.</div>
</div>
</div>
<script>
const STATES=__STATES__;
// HTML-escape any field that can carry content we don't control before it goes
// into innerHTML: a worker name / cwd-derived domain, and last_event (which embeds
// an MCP tool name straight from the transcript) could otherwise inject markup.
const esc=s=>(s==null?'':String(s)).replace(/[&<>"]/g,c=>(
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
function age(ts){if(!ts)return'-';let d=Math.max(0,Date.now()/1000-ts);
  if(d<60)return Math.floor(d)+'s';if(d<3600)return Math.floor(d/60)+'m';return Math.floor(d/3600)+'h';}
function fmtTok(n){if(n==null)return'';if(n>=1e6)return(n/1e6).toFixed(1)+'M';
  if(n>=1e3)return Math.round(n/1e3)+'k';return''+n;}
function insBits(x){
  if(!x)return'';let o='';
  if(x.output)o+=` · ${fmtTok(x.output)} tok`;
  if(x.verdict==='stalled')o+=` · <span class="v-stalled">stalled</span>`;
  else if(x.verdict==='spiraling')o+=` · <span class="v-spiraling">spiraling`+
    (x.rate_per_min?` ${fmtTok(x.rate_per_min)}/m`:``)+`</span>`;
  if(x.budget_pct!=null){const c=x.budget_state==='over'?'b-over':
    x.budget_state==='approaching'?'b-approaching':'';
    o+=` · <span class="${c}">${Math.round(x.budget_pct*100)}% budget</span>`;}
  return o?`<span class="meta ins">${o}</span>`:'';
}
function renderAlerts(alerts){
  const el=document.getElementById('alerts');
  if(!alerts||!alerts.length){el.innerHTML='';return;}
  el.innerHTML=alerts.map(a=>`<div class="alert-chip ${esc(a.kind)}">
    <span class="k">${esc((a.kind||'').toUpperCase().replace('-',' '))}</span>
    <span class="n">${esc(a.name)}</span><span class="w">${esc(a.why||'')}</span></div>`).join('');
}
async function tick(){
  try{
    const [lr,ir]=await Promise.all([fetch('/api/leads'),fetch('/api/insight').catch(()=>null)]);
    const j=await lr.json();
    let ins={alerts:[],by_session:{}};try{if(ir)ins=await ir.json();}catch(e){}
    const bs=ins.by_session||{};
    const live=j.leads.filter(l=>l.derived_state!=='gone');
    live.sort((a,b)=>(STATES[a.derived_state]?.[2]??9)-(STATES[b.derived_state]?.[2]??9));
    const need=live.filter(l=>['needs-human','crashed','blocked'].includes(l.derived_state)).length;
    document.getElementById('sub').textContent=
      `${live.length} live · ${need} need you · ${(ins.alerts||[]).length} alerts · ${new Date().toLocaleTimeString()}`;
    renderAlerts(ins.alerts);
    const b=document.getElementById('board');
    if(!live.length){b.innerHTML='<div class="empty">no live sessions</div>';return;}
    b.innerHTML=live.map(l=>{
      const s=STATES[l.derived_state]||['?','#7c8aa5',9];
      const x=bs[l.session_id];
      const alert=(['needs-human','crashed','blocked'].includes(l.derived_state)||
        (x&&['stalled','spiraling'].includes(x.verdict)))?' alert':'';
      const label=esc(l.name||l.domain||'?');
      return `<div class="row${alert}">
        <span class="pill" style="background:${s[1]}">${s[0]}</span>
        <span><span class="name">${label}</span>
          <span class="meta"> · ${esc(l.last_event||'')}</span>${insBits(x)}</span>
        <span class="meta">${age(l.last_mtime)} ago</span>
        <span class="meta dim">${(l.session_id||'').slice(0,8)}</span>
      </div>`;}).join('');
  }catch(e){document.getElementById('sub').textContent='daemon offline — '+e;}
}
tick();setInterval(tick,2000);

// ---- operate panel ----
const $=id=>document.getElementById(id);
let CHILDREN=[];
function fillSel(sel,keepEmpty){
  const cur=sel.value;
  sel.innerHTML=(keepEmpty?'<option value="">—</option>':'')+CHILDREN.map(c=>
    `<option value="${c.name}">${c.name} · ${c.state}</option>`).join('');
  if([...sel.options].some(o=>o.value===cur))sel.value=cur;
}
async function loadChildren(){
  try{
    const j=await(await fetch('/api/children')).json();
    CHILDREN=j.children||[];
    fillSel($('t_target'),false);fillSel($('x_src'),false);fillSel($('x_dst'),true);
    if(!CHILDREN.length)showOut('no owned workers yet — `overseer spawn <name> <cwd>` to create one.',null);
  }catch(e){/* dashboard may be mid-restart */}
}
function showOut(msg,ok){const o=$('op_out');o.textContent=msg;
  o.className='out'+(ok===true?' ok':ok===false?' err':'');}
async function post(path,payload,btn){
  btn.disabled=true;showOut('sending...',null);
  try{
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    const j=await r.json();
    if(j.ok){
      if(j.action==='route')showOut(`routed -> '${j.chosen}' (${j.delivery?.chars||0} chars sent)`,true);
      else if(j.action==='relay')showOut(`relayed ${j.src} -> ${j.dst} (${j.chars} chars, target was ${j.target_state_before})`,true);
      else showOut(`sent to '${j.target}' (${j.chars} chars, was ${j.state_before})`,true);
    }else showOut(j.reason||j.error||'refused',false);
  }catch(e){showOut('request failed — '+e,false);}
  finally{btn.disabled=false;setTimeout(loadChildren,400);}
}
$('t_go').onclick=()=>{const t=$('t_target').value,x=$('t_text').value.trim();
  if(!t)return showOut('pick a target worker.',false);
  if(!x)return showOut('type a command.',false);
  post('/api/tell',{name:t,text:x,context:$('t_ctx').value.trim()||null,force:$('t_force').checked},$('t_go'));};
$('r_go').onclick=()=>{const x=$('r_text').value.trim();
  if(!x)return showOut('type an instruction.',false);
  post('/api/route',{instruction:x,force:$('r_force').checked},$('r_go'));};
$('x_go').onclick=()=>{const s=$('x_src').value,d=$('x_dst').value;
  if(!s||!d)return showOut('pick both a source and a target.',false);
  if(s===d)return showOut('source and target must differ.',false);
  post('/api/relay',{src:s,dst:d,force:$('x_force').checked},$('x_go'));};
loadChildren();setInterval(loadChildren,4000);
</script></body></html>"""


def build_insight(leads=None) -> dict:
    """The monetizable cockpit data, surfaced for the dashboard: per-session output
    tokens (runaway spotting) + progress verdict (stalled/spiraling = the moat) +
    budget %, plus a unified 'truly needs you' alerts feed.

    progress.fleet_progress() MUTATES its snapshot (it consumes the token delta to
    compute the rate), so it must run EXACTLY ONCE per poll — we therefore derive
    BOTH the per-row verdicts AND the alerts from that single call here, rather than
    also calling alerts.attention() (which would re-run progress and corrupt the
    verdict). The inline alert rules mirror alerts.attention exactly; a drift-guard
    test (test_web) asserts they stay in sync."""
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    prog = progress.fleet_progress(leads=leads)
    rates = {s["session_id"]: s["rate_per_min"] for s in prog["sessions"]}
    budg = {r["session_id"]: r for r in budget.status(leads=leads, rates=rates)["sessions"]}
    use = {s["session_id"]: s for s in usage.fleet_usage(leads=leads)["sessions"]}

    alerts = []
    for s in prog["sessions"]:
        sid, nm = s["session_id"], s["name"]
        if s["verdict"] == "stalled":
            alerts.append({"session_id": sid, "name": nm, "kind": "stalled",
                           "why": f"no new output for {s['flat_for']}s — silent hang?"})
        elif s["verdict"] == "spiraling":
            alerts.append({"session_id": sid, "name": nm, "kind": "spiraling",
                           "why": f"{s['rate_per_min']:,} output tok/min — runaway?"})
        b = budg.get(sid)
        if b and b["state"] == "over":
            eta = f", eta {b['eta_min']}m" if b.get("eta_min") else ""
            alerts.append({"session_id": sid, "name": nm, "kind": "over-budget",
                           "why": f"{int((b['pct'] or 0) * 100)}% of {b['budget']:,} tok budget{eta}"})
    alerts.sort(key=lambda a: {"stalled": 0, "spiraling": 1, "over-budget": 2}.get(a["kind"], 9))

    by_session = {}
    for s in prog["sessions"]:
        sid = s["session_id"]
        b = budg.get(sid, {})
        by_session[sid] = {
            "verdict": s.get("verdict"),
            "rate_per_min": s.get("rate_per_min"),
            "output": (use.get(sid) or {}).get("output"),
            "budget_pct": b.get("pct"),
            "budget_state": b.get("state"),
        }
    return {"alerts": alerts, "by_session": by_session}


def _poll_loop(db_path, interval: float, stop: threading.Event) -> None:
    # IMPORTANT: open the writer connection INSIDE this thread. sqlite3
    # connections are bound to their creating thread (check_same_thread), so a
    # connection made in serve()'s main thread would fail every write here —
    # silently, since the loop swallows exceptions — leaving the board frozen.
    store = Store(db_path)
    try:
        while not stop.is_set():
            try:
                scan_once(store, on_transition=notify.on_transition)
            except roster.RosterError:
                pass
            except Exception:
                pass
            stop.wait(interval)
    finally:
        store.close()


def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        timeout = 30        # drop a stalled connection (basic slow-loris guard)

        def log_message(self, *a):  # quiet
            pass

        def _local_only(self) -> bool:
            """CSRF / DNS-rebinding guard for the POST action surface: the request
            must be same-origin to a loopback dashboard. Blocks a malicious page
            POSTing to 127.0.0.1 (its Origin is a foreign host) and DNS-rebinding
            (Host is the attacker's domain resolving to loopback). The dashboard's
            own fetches send Host=127.0.0.1[:port] and either no Origin or a loopback
            one, so they pass."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in ("127.0.0.1", "localhost", "::1", ""):
                return False
            origin = self.headers.get("Origin")
            if origin:
                from urllib.parse import urlparse
                if (urlparse(origin).hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
                    return False
            return True

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
            elif self.path.startswith("/api/insight"):
                # The monetizable cockpit overlay: tokens + progress (the moat) +
                # budget + the unified alerts feed. Live (roster.fetch), keyed by
                # session_id so the page merges it onto the state board.
                try:
                    ins = build_insight()
                except Exception:
                    ins = {"alerts": [], "by_session": {}}   # never break the board
                self._send(200, json.dumps(ins), "application/json")
            elif self.path.startswith("/api/children"):
                # OWNED workers only (driver registry) + each one's quiescence,
                # so the operate panel can offer real targets and show whether it
                # is safe to type into them right now. Never lists Tom's own
                # hand-started sessions — those are not Overseer-owned.
                kids = []
                for name in driver.list_children():
                    try:
                        st = relay.pane_state(name) if driver.exists(name) else "gone"
                    except Exception:
                        st = "unknown"
                    kids.append({"name": driver._plain(name), "state": st})
                self._send(200, json.dumps({"children": kids}), "application/json")
            else:
                self._send(404, "not found", "text/plain")

        # ---- operate layer: issue commands from the dashboard (Tom's pain #1/#3) ----
        # Every action below is guarded in relay/route: OWNED children only, target
        # must be quiescent unless force=true, and each is appended to the audit log.
        # The server binds 127.0.0.1, so this surface is local-only.
        def _read_json(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n > 0 else b""
                return json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                return None

        def do_POST(self):
            actions = ("/api/tell", "/api/route", "/api/relay")
            path = next((a for a in actions if self.path.startswith(a)), None)
            if path is None:
                self._send(404, json.dumps({"error": "no such action"}), "application/json")
                return
            if not self._local_only():
                self._send(403, json.dumps({"ok": False, "error": "refused: non-local request"}),
                           "application/json")
                return
            try:
                clen = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                clen = -1
            if clen > _MAX_BODY:
                self._send(413, json.dumps({"ok": False, "error": "request body too large"}),
                           "application/json")
                return
            body = self._read_json()
            if body is None:
                self._send(400, json.dumps({"error": "invalid JSON body"}), "application/json")
                return
            force = bool(body.get("force"))
            try:
                if path == "/api/tell":
                    name = (body.get("name") or "").strip()
                    text = (body.get("text") or "").strip()
                    if not name or not text:
                        raise ValueError("tell needs a target 'name' and non-empty 'text'")
                    res = relay.tell(name, text, context=(body.get("context") or None), force=force)
                    out = {"ok": True, "action": "tell", **res}
                elif path == "/api/route":
                    res = route.route(body.get("instruction") or "", go=True, force=force)
                    out = {"ok": bool(res.get("routed")), "action": "route", **res}
                else:  # /api/relay
                    src = (body.get("src") or "").strip()
                    dst = (body.get("dst") or "").strip()
                    if not src or not dst:
                        raise ValueError("relay needs 'src' and 'dst'")
                    res = relay.relay(src, dst, force=force)
                    out = {"ok": True, "action": "relay", **res}
                self._send(200, json.dumps(out), "application/json")
            except (relay.RelayError, ValueError) as e:
                # Fail-closed refusals (not owned / not quiescent / bad input) are a
                # 200 with ok:false so the UI shows WHY rather than a generic error.
                self._send(200, json.dumps({"ok": False, "error": str(e)}), "application/json")
            except Exception as e:  # never crash the dashboard on an action
                self._send(500, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}),
                           "application/json")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8899, interval: float = 5.0,
          poll: bool = True, db_path=None) -> int:
    # Resolve the db path (a brief connection just to learn the default path),
    # then hand the PATH (not a connection) to threads. The poll thread and each
    # HTTP request open their own connections, because sqlite connections are
    # bound to their creating thread. poll=False serves a static db (demo).
    probe = Store(db_path) if db_path else Store()
    db_path = probe.db_path
    probe.close()
    stop = threading.Event()
    if poll:
        t = threading.Thread(target=_poll_loop, args=(db_path, interval, stop), daemon=True)
        t.start()
    httpd = None
    for p in range(port, port + 10):  # try a few ports if the first is taken
        try:
            httpd = ThreadingHTTPServer((host, p), make_handler(db_path))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"overseer: ports {port}-{port + 9} all in use", flush=True)
        stop.set()
        return 1
    mode = f"poll {interval}s" if poll else "static (demo)"
    print(f"overseer: dashboard at http://{host}:{port}  ({mode}, Ctrl-C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
    return 0
