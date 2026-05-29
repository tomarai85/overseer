"""engine — one poll cycle: roster -> state-truth -> store, with transition logging."""
from __future__ import annotations

from typing import Callable

from . import roster, state
from .store import Store

# Callback signature: (session_id, prev_state, new_state, label)
TransitionCB = Callable[[str, str | None, str, str], None]


def scan_once(store: Store, on_transition: TransitionCB | None = None) -> dict[str, int]:
    """Fetch the live roster, derive state truth, persist, log transitions.

    Returns a summary dict of state counts. `on_transition` (optional) is called
    for every state change — used by the daemon/dashboard for notifications.
    Never raises on a single bad lead; roster fetch failure propagates.
    """
    leads = roster.fetch()
    present_ids = []
    counts: dict[str, int] = {}
    for lead in leads:
        present_ids.append(lead.session_id)
        # Guard per lead: a single bad transcript / derive error must not abort
        # the whole scan (the poll loop has to survive one weird session).
        try:
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
            label = lead.name or enrich.get("domain") or lead.session_id[:8]
            if prev is not None and prev != new_state:
                store.log_event(lead.session_id, "transition", f"{prev} -> {new_state}")
                if on_transition:
                    on_transition(lead.session_id, prev, new_state, label)
            elif prev is None:
                store.log_event(lead.session_id, "first_seen", new_state)
        except Exception as e:
            counts["error"] = counts.get("error", 0) + 1
            try:
                store.log_event(lead.session_id, "scan_error", str(e)[:200])
            except Exception:
                pass
    gone = store.mark_gone(present_ids)
    for sid in gone:
        # Guard per gone-sid, same as the per-lead loop: a log/notify failure must
        # not abort the scan and skip the remaining gone sessions. mark_gone reports
        # each gone transition only ONCE (the next scan sees it already 'gone'), so an
        # unguarded failure here would permanently drop that 'session gone' alert.
        try:
            store.log_event(sid, "transition", "-> gone")
            if on_transition:
                on_transition(sid, None, "gone", sid[:8])
        except Exception:
            pass
    if gone:
        counts["gone"] = counts.get("gone", 0) + len(gone)
    return counts
