"""Tests for the durable supervisor's state derivation from pane + heartbeat.

  PYTHONPATH=src python3 tests/test_supervisor.py

Stubs driver.exists/read/list_children — no tmux, no Claude.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import driver, supervisor  # noqa: E402


def run():
    res = []
    tmp = Path(tempfile.mkdtemp())
    driver.DATA = tmp
    supervisor.HEARTBEAT = tmp / "hb.json"

    panes: dict[str, str] = {}
    existing: set[str] = set()
    driver.exists = lambda n: n in existing
    driver.read = lambda n, lines=60: panes.get(n, "")

    NOW = 1000.0

    def seeded_hb(name: str, age: float) -> dict:
        digest = hashlib.sha1(supervisor._clean(panes[name]).encode()).hexdigest()
        return {name: {"hash": digest, "since": NOW - age}}

    def st(name, hb=None):
        return supervisor.child_state(name, now=NOW, hb=hb if hb is not None else {})["state"]

    # crashed: tmux session gone
    res.append(("crashed when session is gone", st("ghost") == "crashed"))

    # working: a spinner / interrupt marker on screen
    existing.add("w1"); panes["w1"] = "⏺ editing...\n  esc to interrupt"
    res.append(("working when spinner/interrupt showing", st("w1") == "working"))

    # working: pane just changed (first observation, age 0)
    existing.add("w2"); panes["w2"] = "⏺ ran the tests, 3 passed"
    res.append(("working when pane just changed (fresh)", st("w2") == "working"))

    # waiting: idle at the prompt, pane quiet for a while
    existing.add("idle"); panes["idle"] = "❯ "
    res.append(("waiting when idle at prompt + quiet", st("idle", seeded_hb("idle", 100)) == "waiting"))

    # stuck: frozen a long time, no prompt
    existing.add("frz"); panes["frz"] = "running a long thing with no prompt and no spinner"
    res.append(("stuck when frozen > STUCK_SEC with no prompt",
                st("frz", seeded_hb("frz", supervisor.STUCK_SEC + 10)) == "stuck"))

    # needs-human: a permission/approval prompt
    existing.add("perm"); panes["perm"] = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
    res.append(("needs-human on a permission prompt", st("perm") == "needs-human"))

    # summary aggregates + flags what needs Tom. Seed the persistent heartbeat so
    # 'frz' reads as stuck (staleness needs history across supervise calls — the
    # first observation always looks fresh, which is correct).
    import json as _json
    supervisor.HEARTBEAT.write_text(_json.dumps(seeded_hb("frz", supervisor.STUCK_SEC + 10)))
    driver.list_children = lambda: ["w1", "idle", "frz", "perm"]
    s = supervisor.summary(now=NOW)
    flagged = {c["name"] for c in s["needs_attention"]}
    res.append(("summary flags stuck + needs-human", "frz" in flagged and "perm" in flagged))
    res.append(("summary does not flag healthy", "w1" not in flagged and "idle" not in flagged))
    return res


def test_supervisor():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
