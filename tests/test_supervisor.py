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

    # REGRESSION (live finding 2026-05-29): an idle child with a FRESH heartbeat (age 0)
    # must still read 'waiting'. The real idle TUI animates, so the content hash never
    # goes stale -> a naive 'age<8 => working' check (run before the prompt check) read
    # every idle child as 'working' forever. The idle-prompt check must win over age.
    existing.add("idlefresh"); panes["idlefresh"] = "❯ "
    res.append(("idle reads waiting even with a fresh heartbeat (prompt beats age<8)",
                st("idlefresh") == "waiting"))

    # stuck: frozen a long time, no prompt
    existing.add("frz"); panes["frz"] = "running a long thing with no prompt and no spinner"
    res.append(("stuck when frozen > STUCK_SEC with no prompt",
                st("frz", seeded_hb("frz", supervisor.STUCK_SEC + 10)) == "stuck"))

    # needs-human: a permission/approval prompt
    existing.add("perm"); panes["perm"] = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
    res.append(("needs-human on a permission prompt", st("perm") == "needs-human"))

    # scrollback false-positive (the bug relay fixed live 2026-05-29): an idle child
    # whose SCROLLBACK merely mentions a working marker must read 'waiting' — the
    # marker isn't in the live bottom region.
    existing.add("scroll")
    panes["scroll"] = ("the docs note 'esc to interrupt' cancels a run\n"
                       + "\n".join(["filler"] * 20) + "\n❯ ")
    res.append(("working marker in scrollback does not false-positive (region scan)",
                st("scroll") == "waiting"))

    # TOCTOU: a child that dies between exists() and read() reads 'crashed', and the
    # raw DriverError does not abort the scan.
    def _raise_read(n, lines=60):
        if n == "toctou":
            raise driver.DriverError("gone")
        return panes.get(n, "")
    existing.add("toctou"); driver.read = _raise_read
    res.append(("read DriverError mid-scan -> crashed (no abort)", st("toctou") == "crashed"))
    driver.read = lambda n, lines=60: panes.get(n, "")   # restore the simple stub

    # summary aggregates + flags what needs the operator. Seed the persistent heartbeat so
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
