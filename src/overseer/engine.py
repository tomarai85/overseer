"""engine — one poll cycle: roster -> state-truth -> store, with transition logging."""
from __future__ import annotations

from . import roster, state
from .store import Store


def scan_once(store: Store) -> dict[str, int]:
    """Fetch the live roster, derive state truth, persist, log transitions.

    Returns a small summary dict of state counts. Never raises on a single
    bad lead; roster fetch failure propagates (caller decides retry).
    """
    leads = roster.fetch()
    present_ids = []
    counts: dict[str, int] = {}
    for lead in leads:
        present_ids.append(lead.session_id)
        enrich = state.derive(lead)
        row = {
            "session_id": lead.session_id,
            "pid": lead.pid,
            "name": lead.name,
            "cwd": lead.cwd,
            "raw_status": lead.raw_status,
            "started_at": lead.started_at,
            **enrich,
        }
        prev = store.upsert_lead(row)
        new_state = enrich["derived_state"]
        counts[new_state] = counts.get(new_state, 0) + 1
        if prev is not None and prev != new_state:
            store.log_event(
                lead.session_id, "transition", f"{prev} -> {new_state}"
            )
        elif prev is None:
            store.log_event(lead.session_id, "first_seen", new_state)
    gone = store.mark_gone(present_ids)
    for sid in gone:
        store.log_event(sid, "transition", "-> gone")
    if gone:
        counts["gone"] = counts.get("gone", 0) + len(gone)
    return counts
