"""Tests for the dashboard's OPERATE surface (GET /api/children + POST actions).

Spins up the real ThreadingHTTPServer with make_handler and hits it over HTTP, so
the request parsing / JSON body / status codes are exercised for real. relay /
route / driver are stubbed — no tmux, no Claude. The verbs themselves are tested
in test_relay / test_route; here we test the WEB wiring + fail-closed surfacing.

  PYTHONPATH=src python3 tests/test_web.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import alerts, budget, conversation, driver, progress, relay, route, usage, web  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


# ---- stubs: an owned 'builder' (waiting) and 'runner' (waiting); 'ghost' unowned ----
_OWNED = {"builder", "runner"}
_calls = []

driver.list_children = lambda: sorted(_OWNED)
driver.exists = lambda n: driver._plain(n) in _OWNED
relay.pane_state = lambda n: "waiting"


def fake_tell(name, text, context=None, force=False):
    if driver._plain(name) not in _OWNED:
        raise relay.RelayError(f"target '{name}' is not an Overseer-owned child")
    _calls.append(("tell", name, text, context, force))
    return {"target": name, "state_before": "waiting", "chars": len(text), "forced": force}


def fake_relay(src, dst, frame=None, force=False, max_chars=2000):
    if driver._plain(src) not in _OWNED or driver._plain(dst) not in _OWNED:
        raise relay.RelayError("both ends must be owned")
    _calls.append(("relay", src, dst, force))
    return {"src": src, "dst": dst, "target_state_before": "waiting", "chars": 42, "forced": force}


def fake_route(instruction, go=False, force=False, registry=None):
    # "vault" routes to runner; everything else is ambiguous (refused)
    if "vault" in instruction.lower():
        return {"instruction": instruction, "chosen": "runner", "candidates": [],
                "routed": True, "delivery": {"target": "runner", "chars": len(instruction)}}
    return {"instruction": instruction, "chosen": None, "candidates": [],
            "routed": False, "reason": "ambiguous — no clear owner"}


relay.tell = fake_tell
relay.relay = fake_relay
route.route = fake_route


def _serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(db_path="/tmp/ov_web_test.db"))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _req(port, path, payload=None):
    """Return (status, json_body). POST if payload given, else GET."""
    url = f"http://127.0.0.1:{port}{path}"
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    def _parse(raw):
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}                      # non-JSON (e.g. the HTML page) — caller only checks status
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, _parse(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())


def _post_raw(port, path, raw_bytes):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=raw_bytes,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


httpd, port = _serve()

# GET / renders the operate panel
st, _ = _req(port, "/")
check("GET / -> 200", st == 200)
st2, _ = (200, None)
import urllib.request as _u
html = _u.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
check("dashboard HTML contains the OPERATE panel", "OPERATE" in html and "/api/tell" in html)

# GET /api/children lists OWNED workers + their quiescence
st, j = _req(port, "/api/children")
names = {c["name"] for c in j.get("children", [])}
check("GET /api/children lists owned workers only", st == 200 and names == {"builder", "runner"})
check("children carry a quiescence state", all(c.get("state") == "waiting" for c in j["children"]))

# POST /api/tell happy path
st, j = _req(port, "/api/tell", {"name": "builder", "text": "run the tests", "context": "err log"})
check("POST /api/tell -> ok, calls relay.tell", st == 200 and j.get("ok") and j.get("target") == "builder")
check("relay.tell received the context", _calls and _calls[-1][0] == "tell" and _calls[-1][3] == "err log")

# POST /api/tell missing text -> fail-closed ok:false (not a crash)
st, j = _req(port, "/api/tell", {"name": "builder", "text": "   "})
check("POST /api/tell empty text -> ok:false with reason", st == 200 and j.get("ok") is False and "text" in j.get("error", ""))

# POST /api/tell to an UNOWNED target -> RelayError surfaced as ok:false
st, j = _req(port, "/api/tell", {"name": "ghost", "text": "hi"})
check("POST /api/tell unowned -> refused (ok:false)", st == 200 and j.get("ok") is False and "owned" in j.get("error", ""))

# POST /api/route happy path (clear winner)
st, j = _req(port, "/api/route", {"instruction": "rerun the vault sync ingest"})
check("POST /api/route clear winner -> routed to runner", st == 200 and j.get("ok") and j.get("chosen") == "runner")

# POST /api/route ambiguous -> ok:false with the refusal reason
st, j = _req(port, "/api/route", {"instruction": "do the thing"})
check("POST /api/route ambiguous -> ok:false + reason", st == 200 and j.get("ok") is False and "ambiguous" in (j.get("reason") or ""))

# POST /api/relay happy path
st, j = _req(port, "/api/relay", {"src": "builder", "dst": "runner"})
check("POST /api/relay -> ok, calls relay.relay", st == 200 and j.get("ok") and j.get("src") == "builder" and j.get("dst") == "runner")

# POST /api/relay missing dst -> ok:false
st, j = _req(port, "/api/relay", {"src": "builder"})
check("POST /api/relay missing dst -> ok:false", st == 200 and j.get("ok") is False)

# unknown action -> 404
st, j = _req(port, "/api/nope", {})
check("POST unknown action -> 404", st == 404)

# invalid JSON body -> 400 (not a crash)
st, j = _post_raw(port, "/api/tell", b"{not json")
check("POST invalid JSON -> 400", st == 400 and "JSON" in j.get("error", ""))

# ---- moat overlay: build_insight + /api/insight + drift guard ----
# Canned aggregators (the moat backend is tested in its own modules; here we test
# that the WEB layer composes them correctly and stays in sync with alerts.attention).
_CANNED_PROG = {"sessions": [
    {"session_id": "a", "name": "alpha", "verdict": "spiraling", "rate_per_min": 120000, "flat_for": 0.0, "tokens": 500000},
    {"session_id": "b", "name": "beta", "verdict": "stalled", "rate_per_min": 0, "flat_for": 240.0, "tokens": 300000},
    {"session_id": "c", "name": "gamma", "verdict": "progressing", "rate_per_min": 5000, "flat_for": 0.0, "tokens": 100000},
], "flagged": [], "summary": ""}
_CANNED_BUDG = {"sessions": [
    {"session_id": "a", "name": "alpha", "output": 500000, "budget": 400000, "pct": 1.25, "state": "over", "eta_min": None},
    {"session_id": "b", "name": "beta", "output": 300000, "budget": 0, "pct": None, "state": "unset", "eta_min": None},
    {"session_id": "c", "name": "gamma", "output": 100000, "budget": 200000, "pct": 0.5, "state": "ok", "eta_min": None},
], "breached": []}
_CANNED_USE = {"sessions": [{"session_id": "a", "output": 500000}, {"session_id": "b", "output": 300000},
                            {"session_id": "c", "output": 100000}], "total": {}}
progress.fleet_progress = lambda leads=None, now=None: _CANNED_PROG
budget.status = lambda leads=None, rates=None: _CANNED_BUDG
usage.fleet_usage = lambda leads=None: _CANNED_USE

ins = web.build_insight(leads=[1, 2, 3])
got = sorted((a["kind"], a["session_id"]) for a in ins["alerts"])
check("build_insight derives stalled/spiraling/over-budget alerts",
      got == [("over-budget", "a"), ("spiraling", "a"), ("stalled", "b")])
check("build_insight merges per-session verdict + budget",
      ins["by_session"]["a"]["verdict"] == "spiraling" and ins["by_session"]["a"]["budget_state"] == "over"
      and ins["by_session"]["c"]["budget_pct"] == 0.5)
# DRIFT GUARD: progress mutates its snapshot so build_insight must NOT also call
# alerts.attention (it would re-run progress + corrupt the verdict). Instead it
# rebuilds the alert rules inline — this asserts those rules stay identical to
# alerts.attention's, on the same data, so they never silently diverge.
mine = {(a["kind"], a["session_id"]) for a in ins["alerts"]}
theirs = {(a["kind"], a["session_id"]) for a in alerts.attention(leads=[1, 2, 3])["alerts"]}
check("build_insight alerts match alerts.attention (no drift)", mine == theirs)

# /api/insight endpoint serves it
st, j = _req(port, "/api/insight")
check("GET /api/insight -> 200 with alerts + by_session",
      st == 200 and len(j.get("alerts", [])) == 3 and "a" in j.get("by_session", {}))

# /api/insight never breaks the board: if an aggregator raises, it returns empty
_boom = progress.fleet_progress
progress.fleet_progress = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
st, j = _req(port, "/api/insight")
check("GET /api/insight survives an aggregator error (empty, not 500)",
      st == 200 and j.get("alerts") == [] and j.get("by_session") == {})
progress.fleet_progress = _boom

# ---- security hardening (Opus + Codex review): CSRF guard + body cap + XSS ----
def raw_post(path, headers, body=b"{}", send_len=None):
    """POST with EXACT headers (urllib won't let us forge Host/Origin). Returns status."""
    cl = send_len if send_len is not None else len(body)
    head = f"POST {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    head += f"Content-Length: {cl}\r\n\r\n"
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(head.encode() + body)
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    s.close()
    return int(resp.split(b" ", 2)[1]) if resp else 0

OWN = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
# same-origin loopback request is allowed through the guard (reaches the handler)
check("CSRF guard: loopback Host + no Origin -> allowed (not 403)",
      raw_post("/api/tell", OWN, b'{"name":"builder","text":"hi"}') != 403)
# DNS-rebinding: a foreign Host (attacker domain resolving to 127.0.0.1) -> 403
check("CSRF guard: foreign Host -> 403",
      raw_post("/api/tell", {**OWN, "Host": "evil.example.com"}, b'{"name":"builder","text":"hi"}') == 403)
# cross-site POST: loopback Host but a foreign Origin -> 403
check("CSRF guard: foreign Origin -> 403",
      raw_post("/api/tell", {**OWN, "Origin": "http://evil.example.com"}, b'{"name":"builder","text":"hi"}') == 403)
# body-size cap: an oversized declared Content-Length -> 413 (before reading the body)
check("body cap: Content-Length over limit -> 413",
      raw_post("/api/tell", OWN, b"{}", send_len=web._MAX_BODY + 1) == 413)

# XSS: tainted fields are escaped before innerHTML
page = web.PAGE
check("XSS: esc() helper present and applied to name/last_event/alert fields",
      "const esc=" in page and "esc(l.last_event" in page
      # session labels now resolve via leadLabel() (prefers the ai-title) — it returns
      # UNescaped values (name/title/domain, title is transcript-derived = tainted), so
      # every sink must keep it inside esc(). Guard both the alert chip and the board row.
      and "esc(leadLabel(LAST[a.session_id])||a.name)" in page
      and "esc(leadLabel(l)" in page)
check("drawer renders transcript turns ESCAPED (esc(x.text))", "esc(x.text)" in page)
check("alert chips are clickable -> open the drawer for that session",
      'data-sid="${esc(a.session_id)}"' in page and "$('alerts').addEventListener" in page)

# ---- detail drawer: /api/conversation ----
conversation.recent_exchange = lambda sid, turns=8: (
    [{"role": "assistant", "text": "did the thing", "ts": 1.0},
     {"role": "user", "text": "next please", "ts": 2.0}][:turns] if sid else [])
st, j = _req(port, "/api/conversation?sid=abc&n=5")
check("GET /api/conversation -> 200 with turns", st == 200 and len(j.get("turns", [])) == 2 and j["session_id"] == "abc")
st, j = _req(port, "/api/conversation?sid=abc&n=1")
check("conversation honors the n turns limit", len(j.get("turns", [])) == 1)
st, j = _req(port, "/api/conversation")
check("conversation with no sid -> empty (no crash)", st == 200 and j.get("turns") == [])
st, j = _req(port, "/api/conversation?sid=abc&n=notanumber")
check("conversation tolerates a bad n param", st == 200 and len(j.get("turns", [])) == 2)

httpd.shutdown()
print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
