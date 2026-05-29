"""Tests for liveness/progress truth — the moat: stall (silent hang) + spiral
detection from token-delta over time.

  PYTHONPATH=src python3 tests/test_progress.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import progress  # noqa: E402


def run():
    res = []
    NOW = 1000.0

    snaps = {}
    r = progress.classify("s1", "busy", now=NOW, snaps=snaps, tokens=100)
    res.append(("first observation busy -> progressing", r["verdict"] == "progressing"))

    r = progress.classify("s1", "busy", now=NOW + 10, snaps=snaps, tokens=200)
    res.append(("output climbing -> progressing", r["verdict"] == "progressing"))

    # flat but only briefly (since last change at NOW+10) -> still progressing
    r = progress.classify("s1", "busy", now=NOW + 20, snaps=snaps, tokens=200)
    res.append(("flat briefly + busy -> progressing", r["verdict"] == "progressing"))

    # flat for > STALL_SEC while busy -> STALLED (the silent hang)
    r = progress.classify("s1", "busy", now=NOW + 10 + progress.STALL_SEC + 5,
                          snaps=snaps, tokens=200)
    res.append(("flat > STALL_SEC + busy -> stalled (silent hang)", r["verdict"] == "stalled"))
    res.append(("stalled reports flat_for", r["flat_for"] >= progress.STALL_SEC))

    # huge token burst in 1s -> spiraling (runaway)
    snaps2 = {}
    progress.classify("s2", "busy", now=NOW, snaps=snaps2, tokens=0)
    r = progress.classify("s2", "busy", now=NOW + 1, snaps=snaps2,
                          tokens=progress.SPIRAL_TOKENS_PER_MIN * 3)
    res.append(("huge burn rate -> spiraling", r["verdict"] == "spiraling"))

    # flat + idle -> idle (not stalled — idle is fine)
    snaps3 = {"s3": {"tokens": 50, "since": NOW - 500, "seen": NOW - 10}}
    r = progress.classify("s3", "idle", now=NOW, snaps=snaps3, tokens=50)
    res.append(("flat + idle -> idle (not flagged)", r["verdict"] == "idle"))

    # no token data -> unknown (never a false stall)
    r = progress.classify("s4", "busy", now=NOW, snaps={}, tokens=None)
    res.append(("no usage data -> unknown", r["verdict"] == "unknown"))
    return res


def test_progress():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
