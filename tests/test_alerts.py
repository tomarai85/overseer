"""Tests for the unified attention feed (stalled + spiraling + over-budget fused).

  PYTHONPATH=src python3 tests/test_alerts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import alerts, budget, progress, roster  # noqa: E402


def run():
    res = []

    # stub the two signal sources
    progress.fleet_progress = lambda leads=None, now=None: {"sessions": [
        {"session_id": "a", "name": "stalled-one", "verdict": "stalled", "flat_for": 200, "rate_per_min": 0},
        {"session_id": "b", "name": "runaway", "verdict": "spiraling", "flat_for": 0, "rate_per_min": 120000},
        {"session_id": "c", "name": "healthy", "verdict": "progressing", "flat_for": 0, "rate_per_min": 500},
        {"session_id": "d", "name": "broke", "verdict": "progressing", "flat_for": 0, "rate_per_min": 100},
        {"session_id": "e", "name": "looper", "verdict": "looping", "flat_for": 0, "rate_per_min": 0, "loop_tool": "Bash"},
    ]}
    budget.status = lambda leads=None, rates=None: {"sessions": [
        {"session_id": "a", "name": "stalled-one", "state": "ok", "pct": 0.3, "budget": 1000, "eta_min": None},
        {"session_id": "d", "name": "broke", "state": "over", "pct": 1.5, "budget": 1000, "eta_min": None},
    ]}
    roster.fetch = lambda: [roster.Lead(session_id=x) for x in ("a", "b", "c", "d")]

    a = alerts.attention()
    kinds = {x["name"]: x["kind"] for x in a["alerts"]}
    res.append(("stalled session flagged", kinds.get("stalled-one") == "stalled"))
    res.append(("spiraling session flagged", kinds.get("runaway") == "spiraling"))
    res.append(("over-budget session flagged", kinds.get("broke") == "over-budget"))
    res.append(("looping session flagged", kinds.get("looper") == "looping"))
    res.append(("looping why names the repeated tool",
                any("Bash" in x["why"] for x in a["alerts"] if x["kind"] == "looping")))
    res.append(("healthy session NOT flagged", "healthy" not in kinds))
    res.append(("count excludes healthy", a["count"] == 4))
    res.append(("stalled ranked first, looping before spiraling",
                a["alerts"][0]["kind"] == "stalled" and a["alerts"][1]["kind"] == "looping"))
    return res


def test_alerts():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
