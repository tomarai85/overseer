"""usage — token usage per session (spot the heavy / runaway worker).

Transcripts carry per-assistant-message token usage but no dollar cost, and on
Claude Max billing is a flat subscription anyway — so the useful signal is not $
but TOKENS: which worker is burning the most output / context. A worker whose
output keeps climbing with no human input is the "burning tokens off the rails"
case the state-truth heuristic can't see from status alone.
"""
from __future__ import annotations

import json

from . import state

FIELDS = ("input", "output", "cache_read", "cache_write")


def session_usage(session_id: str) -> dict | None:
    """Sum token usage across a session's whole transcript. None if no transcript."""
    t = state.find_transcript(session_id)
    if not t:
        return None
    tot = {f: 0 for f in FIELDS}
    tot["messages"] = 0
    try:
        with open(t, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"usage"' not in line:          # cheap pre-filter before json.loads
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "assistant":
                    continue
                u = (r.get("message") or {}).get("usage")
                if not isinstance(u, dict):
                    continue
                tot["input"] += u.get("input_tokens", 0) or 0
                tot["output"] += u.get("output_tokens", 0) or 0
                tot["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
                tot["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                tot["messages"] += 1
    except OSError:
        return None
    return tot


def fleet_usage(leads=None) -> dict:
    """Per-session token usage + fleet totals, heaviest output first (runaway spotting)."""
    from . import roster
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    sessions = []
    total = {f: 0 for f in FIELDS}
    total["messages"] = 0
    for l in leads:
        u = session_usage(l.session_id)
        if not u:
            continue
        for k in total:
            total[k] += u[k]
        sessions.append({"session_id": l.session_id,
                         "name": l.name or l.cwd or l.session_id[:8], **u})
    sessions.sort(key=lambda r: r["output"], reverse=True)
    return {"sessions": sessions, "total": total}
