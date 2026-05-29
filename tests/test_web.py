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
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import driver, relay, route, web  # noqa: E402

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

httpd.shutdown()
print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
