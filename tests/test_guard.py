"""Tests for the guard watcher: fires (debounced) when a session truly needs Tom.

  PYTHONPATH=src python3 tests/test_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import alerts, guard, supervisor  # noqa: E402


def run():
    res = []
    alerts.attention = lambda leads=None: {"alerts": [
        {"session_id": "a", "name": "hung-one", "kind": "stalled", "why": "no output 200s"},
    ]}
    supervisor.summary = lambda now=None: {"needs_attention": [
        {"name": "dead-one", "state": "crashed", "hint": "tmux gone"},
    ]}

    fired = []
    cb = lambda title, body: fired.append(body)
    last: dict = {}

    f1 = guard.tick(last, now=1000.0, fire=cb)
    res.append(("fires for stalled + crashed", len(f1) == 2))
    res.append(("notification names the session + kind", any("hung-one — stalled" in b for b in fired)))

    fired.clear()
    f2 = guard.tick(last, now=1000.0 + 10, fire=cb)        # within debounce window
    res.append(("debounced within window (no re-fire)", len(f2) == 0 and not fired))

    f3 = guard.tick(last, now=1000.0 + guard.DEBOUNCE_SEC + 1, fire=cb)   # after window
    res.append(("re-fires after debounce window", len(f3) == 2))

    alerts.attention = lambda leads=None: {"alerts": []}
    supervisor.summary = lambda now=None: {"needs_attention": []}
    res.append(("nothing needed -> no fire", guard.tick({}, now=2000.0, fire=cb) == []))
    return res


def test_guard():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
