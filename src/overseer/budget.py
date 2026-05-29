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
import time

from . import driver, usage

BUDGETS = driver.DATA / "budgets.json"
LEDGER = driver.DATA / "fleet-ledger.json"   # {date: output-tokens-at-first-read-today}
APPROACHING = 0.8        # >= 80% of budget -> warn before it blows


def _cap_state(value: int, cap: int) -> str:
    if not cap:
        return "unset"
    pct = value / cap
    return "over" if pct >= 1.0 else ("approaching" if pct >= APPROACHING else "ok")


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


def _owned_by_session(leads) -> dict:
    """session_id -> owned ov- child name, correlated by realpath(cwd).

    The registry has no session_id (the child's id only appears in `claude agents`
    seconds after spawn) and a lead has no ov- name, but both carry a cwd — so the
    cwd is the join key (same correlation the web/live cockpit use). The old code
    matched the ov- name against the lead's display name, which for an owned child is
    its cwd (name is None on the roster) — so it silently missed every child whose
    ov- name wasn't its cwd basename, and the auto-pause never fired."""
    import os

    def rp(p):
        try:
            return os.path.realpath(p) if p else None
        except OSError:
            return p
    registry = driver._load_registry()
    cwd_to_child = {rp(meta.get("cwd")): name
                    for name, meta in registry.items() if meta.get("cwd")}
    out = {}
    for l in leads:
        child = cwd_to_child.get(rp(l.cwd))
        if child:
            out[l.session_id] = child
    return out


def pause_owned_over(dry_run: bool = True) -> list[dict]:
    """Find OWNED children over budget; if not dry_run, send each a guarded stop
    message (driver.send refuses non-owned, so Tom's own sessions can never be hit).
    Default dry_run=True — auto-enforcement is opt-in (fail-safe)."""
    from . import roster
    try:
        leads = roster.fetch()
    except roster.RosterError:
        leads = []
    sid_to_child = _owned_by_session(leads)
    acted = []
    for r in status(leads=leads)["sessions"]:
        if r["state"] != "over":
            continue
        cand = sid_to_child.get(r["session_id"])     # OWNED children only, joined by cwd
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


def _load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ledger(led: dict) -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(led))
    tmp.replace(LEDGER)


def fleet_status(leads=None, now_date: str | None = None) -> dict:
    """Fleet-wide token ledger: total + today's burn vs a fleet/daily cap. The
    'paid for itself' guardrail — caps set via `overseer budget set fleet|daily <n>`.
    Daily burn = current fleet total minus the first total recorded today."""
    from . import roster
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    per = []
    total = 0
    for l in leads:
        out = (usage.session_usage(l.session_id) or {}).get("output", 0)
        total += out
        per.append({"name": l.name or l.session_id[:8], "session_id": l.session_id, "output": out})
    per.sort(key=lambda r: -r["output"])

    budgets = _load()
    fleet_cap = int(budgets.get("fleet", 0) or 0)
    daily_cap = int(budgets.get("daily", 0) or 0)

    today = now_date or time.strftime("%Y-%m-%d")
    led = _load_ledger()
    if today not in led:
        led = {today: total}          # reset baseline each new day (drop old)
        _save_ledger(led)
    daily_burn = max(0, total - led.get(today, total))

    return {
        "fleet_total": total, "fleet_cap": fleet_cap, "fleet_state": _cap_state(total, fleet_cap),
        "daily_burn": daily_burn, "daily_cap": daily_cap, "daily_state": _cap_state(daily_burn, daily_cap),
        "worst": (per[0] if per else None),
        "summary": (f"fleet {total:,} tok [{_cap_state(total, fleet_cap)}], "
                    f"today +{daily_burn:,} [{_cap_state(daily_burn, daily_cap)}]"),
    }


def enforce_fleet(dry_run: bool = True) -> dict:
    """If the fleet/daily cap is breached, pause OWNED children that are over their
    own per-session budget (precise + safe). Tom's sessions are never touched.
    Auto-enforcement is opt-in (dry_run default)."""
    fs = fleet_status()
    breached = fs["fleet_state"] == "over" or fs["daily_state"] == "over"
    acted = pause_owned_over(dry_run=dry_run) if breached else []
    return {"breached": breached, "fleet": fs, "paused": acted,
            "summary": (fs["summary"] + (f" -> {'paused' if not dry_run else 'would pause'} "
                        f"{len(acted)} owned" if breached else " (within caps)"))}
