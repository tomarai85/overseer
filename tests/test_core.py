"""Unit tests for roster parsing + store persistence.

  PYTHONPATH=src python3 tests/test_core.py
  (also pytest-compatible)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import roster  # noqa: E402
from overseer.store import Store  # noqa: E402


def check(name, cond, detail=""):
    return (name, bool(cond), detail)


def run():
    res = []

    # ---- roster.Lead parsing ----
    obj = {"pid": 4123, "cwd": "/Users/example", "kind": "interactive",
           "startedAt": 1779847814112, "sessionId": "abc", "name": "v", "status": "busy"}
    lead = roster.Lead.from_json(obj)
    res.append(check("parse: sessionId", lead.session_id == "abc"))
    res.append(check("parse: ms->sec startedAt", abs(lead.started_at - 1779847814.112) < 1))
    res.append(check("parse: status", lead.raw_status == "busy"))
    res.append(check("parse: name", lead.name == "v"))

    # missing/null fields don't crash and stay None
    lead2 = roster.Lead.from_json({"sessionId": "x"})
    res.append(check("parse: missing status -> None", lead2.raw_status is None))
    res.append(check("parse: missing pid -> None", lead2.pid is None))

    # extra unknown keys captured
    lead3 = roster.Lead.from_json({"sessionId": "y", "weird": 1})
    res.append(check("parse: extra keys captured", lead3.extra.get("weird") == 1))

    # fetch() defensively skips malformed array elements (non-dict / no sessionId).
    # fetch_raw guarantees a list but NOT list-of-dicts; calling .get on a non-dict
    # would raise AttributeError, which leaks past every caller's `except RosterError`.
    roster.fetch_raw = lambda: [
        {"sessionId": "ok1", "status": "idle"},
        None, "stray-string", 123,           # malformed elements must not crash fetch()
        {"no_session": True},                 # dict but no sessionId -> skipped
        {"sessionId": "", "status": "busy"},  # empty sessionId -> skipped
        {"sessionId": "ok2", "status": "busy"},
    ]
    leads = roster.fetch()
    res.append(check("fetch: skips non-dict/empty elements, keeps valid",
                     [l.session_id for l in leads] == ["ok1", "ok2"]))

    # ---- store ----
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    s = Store(tmp)
    row = {"session_id": "s1", "pid": 1, "name": None, "cwd": None, "domain": "d",
           "raw_status": "busy", "derived_state": "working", "last_event": "assistant:text",
           "last_mtime": 1.0, "started_at": 1.0}
    prev = s.upsert_lead(row)
    res.append(check("store: first upsert prev=None", prev is None))
    res.append(check("store: row persisted", len(s.all_leads()) == 1))

    # transition detection
    row["derived_state"] = "needs-human"
    prev2 = s.upsert_lead(row)
    res.append(check("store: upsert returns prev state", prev2 == "working"))

    # mark_gone
    gone = s.mark_gone(present_ids=["s2"])  # s1 not present anymore
    res.append(check("store: mark_gone returns missing", gone == ["s1"]))
    states = {r["session_id"]: r["derived_state"] for r in s.all_leads()}
    res.append(check("store: gone state set", states.get("s1") == "gone"))

    # events
    s.log_event("s1", "test", "hi")
    res.append(check("store: event logged", any(e["kind"] == "test" for e in s.recent_events())))
    s.close()
    return res


def test_core():
    for name, ok, detail in run():
        assert ok, f"{name}: {detail}"


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok, _ in rows if ok)
    for name, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
