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

    # multi-poller: the burn rate is computed over a trailing window, not consumed on
    # read. A second poller landing on a flat moment (1s after the increase was 'seen')
    # must STILL report the runaway — the old consume-on-read reported rate 0 for
    # whoever wasn't first past each increase, diluting/missing the spiral.
    snapsM = {}
    progress.classify("m", "busy", now=NOW, snaps=snapsM, tokens=0)
    progress.classify("m", "busy", now=NOW + 30, snaps=snapsM, tokens=45_000)   # +45k/30s
    r = progress.classify("m", "busy", now=NOW + 31, snaps=snapsM, tokens=45_000)  # flat poll
    res.append(("windowed rate survives an intervening flat poll (multi-poller)",
                r["rate_per_min"] >= progress.SPIRAL_TOKENS_PER_MIN))
    res.append(("  -> still flagged spiraling on the flat poll", r["verdict"] == "spiraling"))

    # old snapshot schema (no 'samples') still classifies + upgrades in place
    snapsOld = {"o": {"tokens": 10, "since": NOW - 5, "seen": NOW - 5}}
    r = progress.classify("o", "busy", now=NOW, snaps=snapsOld, tokens=10)
    res.append(("legacy snapshot schema upgrades without error", r["verdict"] in ("progressing", "idle", "stalled")))
    res.append(("  -> legacy entry gains a samples ring", isinstance(snapsOld["o"].get("samples"), list)))

    # loop overlay: an active session repeating the same tool -> verdict 'looping' + flagged.
    # The 'stuck/looping' state token-progress alone can't see (it can burn tokens forever).
    import tempfile as _tf
    from overseer import roster as _roster, usage as _usage
    progress.SNAP = Path(_tf.mkdtemp()) / "snap.json"          # isolate; don't touch real data
    _usage.session_usage = lambda sid: {"output": 500, "input": 0, "cache_read": 0,
                                        "cache_write": 0, "messages": 1}
    _loop_recs = [{"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}]}}] * 3
    progress._tail_for = lambda sid: _loop_recs if sid == "loopy" else []
    fp = progress.fleet_progress(
        leads=[_roster.Lead(session_id="loopy", name="loopy", raw_status="busy"),
               _roster.Lead(session_id="fine", name="fine", raw_status="busy")], now=NOW)
    by = {s["session_id"]: s for s in fp["sessions"]}
    res.append(("loop overlay: repeating tool -> verdict 'looping'",
                by["loopy"]["verdict"] == "looping" and by["loopy"].get("loop_tool") == "Bash"))
    res.append(("non-looping active session stays progressing", by["fine"]["verdict"] == "progressing"))
    res.append(("looping is flagged", any(s["session_id"] == "loopy" for s in fp["flagged"])))
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
