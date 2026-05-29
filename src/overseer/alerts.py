"""alerts — the unified "ping me only when an agent TRULY needs me" feed (the wedge).

Fuses the moat guardrail signals — progress.stalled (silent hang), progress.spiraling
(runaway burn), and budget over-cap — into ONE ranked list across ALL sessions
(owned children AND Tom's own). This is what a notifier / cockpit / parent should
consume to decide when to interrupt the human. needs-human/crashed already flow
through the existing state/notify path; this adds the signals nobody else surfaces.
"""
from __future__ import annotations

from . import budget, progress

_ORDER = {"stalled": 0, "spiraling": 1, "over-budget": 2}


def attention(leads=None) -> dict:
    """One ranked feed of sessions that genuinely need attention, with a reason."""
    from . import roster
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    prog = progress.fleet_progress(leads=leads)
    rates = {s["session_id"]: s["rate_per_min"] for s in prog["sessions"]}
    budg = {r["session_id"]: r for r in budget.status(leads=leads, rates=rates)["sessions"]}

    alerts = []
    for s in prog["sessions"]:
        if s["verdict"] == "stalled":
            alerts.append({"session_id": s["session_id"], "name": s["name"], "kind": "stalled",
                           "why": f"no new output for {s['flat_for']}s — silent hang?"})
        elif s["verdict"] == "spiraling":
            alerts.append({"session_id": s["session_id"], "name": s["name"], "kind": "spiraling",
                           "why": f"{s['rate_per_min']:,} output tok/min — runaway?"})
        b = budg.get(s["session_id"])
        if b and b["state"] == "over":
            eta = f", eta {b['eta_min']}m" if b.get("eta_min") else ""
            alerts.append({"session_id": s["session_id"], "name": s["name"], "kind": "over-budget",
                           "why": f"{int((b['pct'] or 0) * 100)}% of {b['budget']:,} tok budget{eta}"})

    alerts.sort(key=lambda a: _ORDER.get(a["kind"], 9))
    return {"alerts": alerts, "count": len(alerts),
            "summary": ("; ".join(f"{a['name']}: {a['kind']}" for a in alerts)
                        if alerts else "no guardrail alerts")}
