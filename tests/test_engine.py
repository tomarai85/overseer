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


def test_engine():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
