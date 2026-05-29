"""LIVE end-to-end cockpit smoke — NOT a unit test (no test_ prefix, so the unit
runner skips it). Spawns a REAL owned Claude child and drives the WHOLE dashboard
through it: the four read endpoints (leads / insight / children / conversation) and
the operate write path (POST /api/tell, through the CSRF guard + real relay), then
cleans up. This is the build's "tests pass != it works" gate at the system level —
the mocked unit tests can't prove the pieces compose against a real child.

  PYTHONPATH=src python3 tests/live_cockpit.py

Takes ~30-60s (a real claude child has to boot + answer). Exit 0 = all green.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import driver, relay, roster, web  # noqa: E402

NAME = "ovsmoke"
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def GET(port, path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=8).read())


def POST(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=12).read())


def main():
    tmp = tempfile.mkdtemp(prefix="ovsmoke-")
    try:
        if driver.exists(driver.session_name(NAME)):
            driver.kill(NAME)
    except Exception:
        pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(db_path="/tmp/ov_cockpit_smoke.db"))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"dashboard live on :{port}")

    driver.spawn(NAME, cwd=tmp)
    print(f"spawned owned child '{NAME}' in {tmp}")
    try:
        # 1. it shows up as an OWNED, quiescent child within ~50s
        state = None
        for _ in range(25):
            time.sleep(2)
            kids = {c["name"]: c["state"] for c in GET(port, "/api/children").get("children", [])}
            state = kids.get(NAME)
            if state == "waiting":
                break
        check("child appears OWNED + quiescent on /api/children", state == "waiting", f"state={state}")

        # 2. operate write path: a command lands through the CSRF guard + real relay
        MARK = "COCKPIT_SMOKE_OK"
        res = POST(port, "/api/tell", {"name": NAME, "text": f"Reply with exactly: {MARK}"}) \
            if state == "waiting" else {"ok": False}
        check("POST /api/tell -> ok via the live server", res.get("ok") and res.get("target") == NAME)
        time.sleep(1.5)
        check("command landed in the real tmux pane", MARK in driver.read(NAME, lines=60))
        audit = relay.RELAY_AUDIT.read_text() if relay.RELAY_AUDIT.exists() else ""
        check("relay audit recorded the owned+quiescent delivery", '"action": "tell"' in audit and NAME in audit)

        # 3. correlate the child's session_id (the claude session in our temp cwd)
        time.sleep(4)            # let the child produce transcript activity
        sid = None
        want = os.path.realpath(tmp)   # macOS: /tmp is a symlink to /private/tmp; roster reports the resolved path
        for l in roster.fetch():
            if os.path.realpath(l.cwd or "/\0") == want:
                sid = l.session_id
                break
        check("child correlated to a roster session_id (by cwd)", bool(sid), f"cwd={want}")

        if sid:
            # 4. the read endpoints all reflect the live child. /api/leads is
            # store-backed (the poll thread feeds it); this bare server has no poll
            # thread, so do one scan_once to populate the same db the handler reads.
            from overseer.engine import scan_once
            from overseer.store import Store
            st_store = Store("/tmp/ov_cockpit_smoke.db")
            try:
                scan_once(st_store)
            finally:
                st_store.close()
            leads = {l["session_id"] for l in GET(port, "/api/leads").get("leads", [])}
            check("/api/leads (store-backed) shows the child's session", sid in leads)
            ins = GET(port, "/api/insight")
            check("/api/insight has a verdict for the child", sid in ins.get("by_session", {}),
                  f"keys={list(ins.get('by_session',{}))[:3]}")
            conv = GET(port, f"/api/conversation?sid={sid}&n=6")
            check("/api/conversation returns turns for the child (200, list)",
                  conv.get("session_id") == sid and isinstance(conv.get("turns"), list))
    finally:
        try:
            driver.kill(NAME)
            print(f"killed '{NAME}' (cleanup)")
        except Exception as e:
            print("cleanup warn:", e)
        httpd.shutdown()

    print(f"\n{PASS}/{PASS + FAIL} live cockpit checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
