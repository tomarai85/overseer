"""Tests for per-session token budgets + breach detection (the money hook).

  PYTHONPATH=src python3 tests/test_budget.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import budget, driver, roster, usage  # noqa: E402


def run():
    res = []
    tmp = Path(tempfile.mkdtemp())
    driver.DATA = tmp
    budget.BUDGETS = tmp / "budgets.json"

    budget.set_budget("default", 1000)
    budget.set_budget("heavy", 100)        # per-name budget overrides default

    def fake_usage(sid):
        out = {"s_ok": 500, "s_over": 2000, "s_heavy": 150}.get(sid, 0)
        return {"output": out, "input": 0, "cache_read": 0, "cache_write": 0, "messages": 1}
    usage.session_usage = fake_usage

    leads = [roster.Lead(session_id="s_ok", name="ok"),
             roster.Lead(session_id="s_over", name="over"),
             roster.Lead(session_id="s_heavy", name="heavy")]
    by = {r["name"]: r for r in budget.status(leads=leads)["sessions"]}
    res.append(("under budget -> ok", by["ok"]["state"] == "ok"))              # 500/1000
    res.append(("over budget -> over", by["over"]["state"] == "over"))         # 2000/1000
    res.append(("per-name budget beats default", by["heavy"]["budget"] == 100 and by["heavy"]["state"] == "over"))

    st = budget.status(leads=leads)
    res.append(("breached lists over sessions", any(r["name"] == "over" for r in st["breached"])))

    # approaching at 85%
    usage.session_usage = lambda sid: {"output": 850, "input": 0, "cache_read": 0, "cache_write": 0, "messages": 1}
    a = budget.status(leads=[roster.Lead(session_id="a", name="a")])["sessions"][0]
    res.append(("85% of budget -> approaching", a["state"] == "approaching"))

    # ETA from burn rate: 150 tokens left at 150/min -> 1.0 min
    e = budget.status(leads=[roster.Lead(session_id="a", name="a")], rates={"a": 150})["sessions"][0]
    res.append(("eta_min from rate", e["eta_min"] == 1.0))

    # no budget set -> 'unset' (never a false breach)
    budget.set_budget("default", 0)        # removes the default
    u = budget.status(leads=[roster.Lead(session_id="z", name="z")])["sessions"][0]
    res.append(("no budget -> unset (no false breach)", u["state"] == "unset"))

    # ---- fleet-wide hard cap (#4) ----
    budget.LEDGER = tmp / "fleet-ledger.json"
    budget.set_budget("fleet", 1000)
    usage.session_usage = lambda sid: {"output": {"f1": 700, "f2": 1500}.get(sid, 0),
                                       "input": 0, "cache_read": 0, "cache_write": 0, "messages": 1}
    fleet_leads = [roster.Lead(session_id="f1", name="f1"), roster.Lead(session_id="f2", name="f2")]
    roster.fetch = lambda: fleet_leads
    driver.list_children = lambda: []   # no owned children -> enforce pauses nothing

    fs = budget.fleet_status(leads=fleet_leads, now_date="2026-01-01")
    res.append(("fleet total sums all sessions", fs["fleet_total"] == 2200))
    res.append(("fleet over its cap", fs["fleet_state"] == "over"))
    res.append(("worst offender = highest output", fs["worst"]["name"] == "f2"))

    budget._save_ledger({"2026-01-02": 2000})   # seed today's baseline
    fs2 = budget.fleet_status(leads=fleet_leads, now_date="2026-01-02")
    res.append(("daily burn = total - today's baseline", fs2["daily_burn"] == 200))

    en = budget.enforce_fleet(dry_run=True)
    res.append(("enforce flags the breach (dry-run, no owned to pause)",
                en["breached"] is True and en["paused"] == []))

    # ---- pause_owned_over correlates owned children by cwd, NOT by name ----
    # An owned child whose ov- name differs from its cwd basename must still match
    # (the old name-substring match silently missed it -> auto-pause never fired).
    driver._load_registry = lambda: {"w7": {"cwd": "/work/project-alpha"}}
    po_leads = [roster.Lead(session_id="sX", name=None, cwd="/work/project-alpha")]
    roster.fetch = lambda: po_leads
    budget.set_budget("default", 100)
    usage.session_usage = lambda sid: {"output": 500, "input": 0, "cache_read": 0,
                                       "cache_write": 0, "messages": 1}   # 500/100 -> over
    acted = budget.pause_owned_over(dry_run=True)
    res.append(("pause_owned_over joins owned child by cwd (name != cwd basename)",
                len(acted) == 1 and acted[0]["child"] == "w7" and acted[0]["paused"] is False))
    # a lead whose cwd matches NO registry entry is never paused (Tom's own sessions)
    roster.fetch = lambda: [roster.Lead(session_id="sY", name=None, cwd="/somewhere/else")]
    res.append(("pause_owned_over ignores a non-owned (unmatched cwd) session",
                budget.pause_owned_over(dry_run=True) == []))
    return res


def test_budget():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
