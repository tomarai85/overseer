"""Integration test for engine.scan_once: roster -> store -> transitions.

  PYTHONPATH=src python3 tests/test_engine.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import engine, roster, state  # noqa: E402
from overseer.store import Store  # noqa: E402


def run():
    res = []
    tmp = Path(tempfile.mkdtemp())

    # Stub the roster (avoid the real `claude` subprocess) and state derivation.
    fake_leads = [roster.Lead(session_id="a", pid=1, raw_status="busy"),
                  roster.Lead(session_id="b", pid=2, raw_status="idle")]
    state_for = {"a": "working", "b": "waiting"}

    roster.fetch = lambda: fake_leads
    state.derive = lambda lead: {
        "domain": "d", "derived_state": state_for[lead.session_id],
        "last_event": "x", "last_mtime": 1.0,
    }

    s = Store(tmp / "e.db")
    transitions = []

    counts = engine.scan_once(s, on_transition=lambda *a: transitions.append(a))
    res.append(("scan populates 2 leads", len(s.all_leads()) == 2))
    res.append(("counts correct", counts.get("working") == 1 and counts.get("waiting") == 1))
    res.append(("first scan = no transitions (only first_seen)", transitions == []))

    # second scan, 'a' changes working -> needs-human
    state_for["a"] = "needs-human"
    engine.scan_once(s, on_transition=lambda *a: transitions.append(a))
    res.append(("transition fired on state change", any(t[2] == "needs-human" for t in transitions)))

    # 'b' disappears from roster -> marked gone
    fake_leads.pop()  # remove b
    engine.scan_once(s)
    states = {r["session_id"]: r["derived_state"] for r in s.all_leads()}
    res.append(("disappeared lead marked gone", states.get("b") == "gone"))
    s.close()
    return res


def run_threadsafety():
    """Regression test for the serve poll-thread sqlite bug (2026-05-29).

    The writer connection must be opened INSIDE the poll thread. If it isn't,
    every write throws (check_same_thread) and is swallowed, leaving the db empty.
    This drives _poll_loop in a thread and asserts the db actually gets written.
    """
    import threading
    import time as _time
    from overseer import web
    from overseer.store import Store

    res = []
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "poll.db"

    roster.fetch = lambda: [roster.Lead(session_id="z", pid=1, raw_status="busy")]
    state.derive = lambda lead: {"domain": "d", "derived_state": "working",
                                 "last_event": "x", "last_mtime": 1.0}
    stop = threading.Event()
    t = threading.Thread(target=web._poll_loop, args=(str(db), 0.1, stop), daemon=True)
    t.start()
    deadline = _time.time() + 3
    rows = []
    while _time.time() < deadline:
        if db.exists():
            s = Store(db)
            rows = s.all_leads()
            s.close()
            if rows:
                break
        _time.sleep(0.1)
    stop.set()
    res.append(("poll thread writes to db (no check_same_thread silent failure)", len(rows) == 1))
    return res


def run_gone_notify_resilience():
    """A log/notify failure on a 'gone' transition must NOT abort the scan. Without
    the per-gone-sid guard, the first failure would skip the remaining gone sids,
    and since mark_gone reports each gone transition only ONCE, those 'session gone'
    alerts would be lost forever. Mirrors the per-lead guard."""
    res = []
    tmp = Path(tempfile.mkdtemp())
    leads = [roster.Lead(session_id="g1", pid=1, raw_status="idle"),
             roster.Lead(session_id="g2", pid=2, raw_status="idle")]
    roster.fetch = lambda: leads
    state.derive = lambda lead: {"domain": "d", "derived_state": "waiting",
                                 "last_event": "x", "last_mtime": 1.0}
    s = Store(tmp / "g.db")
    engine.scan_once(s)                       # first_seen g1, g2 (no notify)
    leads.clear()                             # both disappear -> both gone next scan
    seen = []

    def boom(sid, prev, new, label):
        seen.append(sid)
        raise RuntimeError("notify down")     # every gone notify fails

    counts = engine.scan_once(s, on_transition=boom)   # MUST NOT raise
    states = {r["session_id"]: r["derived_state"] for r in s.all_leads()}
    res.append(("scan survives a notify failure on a gone transition", counts.get("gone") == 2))
    res.append(("all gone sids still processed despite the raise", set(seen) == {"g1", "g2"}))
    res.append(("both still marked gone in the store",
                states.get("g1") == "gone" and states.get("g2") == "gone"))
    s.close()
    return res


def test_engine():
    for name, ok in run():
        assert ok, name


def test_gone_notify_resilience():
    for name, ok in run_gone_notify_resilience():
        assert ok, name


def test_threadsafety():
    for name, ok in run_threadsafety():
        assert ok, name


if __name__ == "__main__":
    rows = run() + run_gone_notify_resilience() + run_threadsafety()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
