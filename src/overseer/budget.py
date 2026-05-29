"""budget — per-session token budgets + runaway guardrail (the money hook, P0 #2).

The one thing no free competitor does (research 2026-05-29): a per-session token
budget with projection and an opt-in auto-pause on breach. Built on usage.py
(current output tokens) + progress.py (burn rate -> time-to-limit). OWNED children
can be safely paused (a stop message via the guarded driver.send); Tom's own
sessions are READ-ONLY flagged — never touched.

Budgets live in data/budgets.json: {"default": N, "<name-or-session-id>": N}.
A budget of 0 / unset means "no cap" for that session.
"""
from __future__ import annotations

import json

from . import driver, usage

BUDGETS = driver.DATA / "budgets.json"
APPROACHING = 0.8        # >= 80% of budget -> warn before it blows


def _load() -> dict:
    try:
        return json.loads(BUDGETS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def set_budget(key: str, output_tokens: int) -> None:
    """Set a per-session (name or session-id) or 'default' output-token budget."""
    b = _load()
    if output_tokens <= 0:
        b.pop(key, None)            # 0 / negative removes the cap
    else:
        b[key] = int(output_tokens)
    driver.DATA.mkdir(parents=True, exist_ok=True)
    tmp = BUDGETS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(b, indent=2))
    tmp.replace(BUDGETS)


def budget_for(name: str | None, session_id: str | None, budgets: dict) -> int:
    """Most specific budget wins: name, then session-id, then default, then 0 (off)."""
    for k in (name, session_id):
        if k and budgets.get(k):
            return int(budgets[k])
    return int(budgets.get("default", 0) or 0)


def status(leads=None, rates: dict | None = None) -> dict:
    """Per-session budget status + fleet rollup. `rates` maps session_id->tokens/min
    (from progress) for ETA; optional."""
    from . import roster
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    budgets = _load()
    rates = rates or {}
    rows = []
    for l in leads:
        u = usage.session_usage(l.session_id)
        out = u["output"] if u else 0
        cap = budget_for(l.name, l.session_id, budgets)
        if not cap:
            state, pct, eta = "unset", None, None
        else:
            pct = round(out / cap, 3)
            state = "over" if pct >= 1.0 else ("approaching" if pct >= APPROACHING else "ok")
            rate = rates.get(l.session_id, 0)
            eta = round((cap - out) / rate, 1) if (rate and out < cap) else None
        rows.append({"session_id": l.session_id,
                     "name": l.name or l.cwd or l.session_id[:8],
                     "output": out, "budget": cap, "pct": pct,
                     "state": state, "eta_min": eta})
    breached = [r for r in rows if r["state"] in ("over", "approaching")]
    rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0)))
    return {"sessions": rows, "breached": breached,
            "summary": (", ".join(f"{r['name']}:{int((r['pct'] or 0)*100)}%" for r in breached)
                        if breached else "all sessions within budget")}


def pause_owned_over(dry_run: bool = True) -> list[dict]:
    """Find OWNED children over budget; if not dry_run, send each a guarded stop
    message (driver.send refuses non-owned, so Tom's own sessions can never be hit).
    Default dry_run=True — auto-enforcement is opt-in (fail-safe)."""
    from . import roster
    try:
        leads = roster.fetch()
    except roster.RosterError:
        leads = []
    owned = set(driver.list_children())
    by_name = {l.name: l for l in leads}
    acted = []
    for r in status(leads=leads)["sessions"]:
        if r["state"] != "over":
            continue
        # only OWNED children, matched by the ov- child name
        cand = next((n for n in owned if n == r["name"] or r["name"].endswith(n)), None)
        if not cand:
            continue
        action = {"child": cand, "pct": r["pct"], "paused": False}
        if not dry_run:
            try:
                driver.send(cand, "PAUSE: you have hit your token budget. Stop now, "
                                  "summarize where you are in one line, and wait for instructions.")
                action["paused"] = True
            except driver.DriverError as e:
                action["error"] = str(e)
        acted.append(action)
    return acted
