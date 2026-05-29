"""progress — liveness/progress TRUTH (the moat, research-confirmed 2026-05-29).

Anthropic's Agent View equates "process alive" with "Working" and is structurally
blind to a SILENT HANG (process up, token count frozen). Every watchdog/heartbeat/
timeout/per-session-cost request was closed "not planned" (claude-code #37521,
#28482, #49150, #23250) — the lane is open. The ground-truth signal users
independently converged on is TOKEN-DELTA OVER TIME: if the token count is flat
while the session looks busy, the response has stalled.

This layer snapshots each session's output-token total over time and derives a
TRUE state the incumbent cannot: progressing / stalled / spiraling / idle. It
reads usage from transcripts, so it works on ANY session — including Tom's own
hand-started ones (where a silent hang is the real, unsolved pain), not just
Overseer-owned children. Call it repeatedly (daemon/poll) to build the history.
"""
from __future__ import annotations

import json
import time

from . import driver, usage

SNAP = driver.DATA / "token-snapshots.json"
STALL_SEC = 120.0                 # busy + output tokens flat this long -> stalled (silent hang)
SPIRAL_TOKENS_PER_MIN = 60_000    # sustained output burn above this -> spiraling (runaway)


def _load() -> dict:
    try:
        return json.loads(SNAP.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(snaps: dict) -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    tmp = SNAP.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snaps))
    tmp.replace(SNAP)


def classify(session_id: str, raw_status: str | None, now: float | None = None,
             snaps: dict | None = None, tokens: int | None = None) -> dict:
    """Derive progress truth for one session from its output-token delta over time.

    `snaps` (the persisted history) is mutated; pass `tokens` to inject a count in
    tests instead of reading the transcript. Returns
    {session_id, verdict, tokens, flat_for, rate_per_min}.
    """
    now = now if now is not None else time.time()
    if snaps is None:
        snaps = _load()
    if tokens is None:
        u = usage.session_usage(session_id)
        tokens = u["output"] if u else None
    raw = (raw_status or "").lower()

    prev = snaps.get(session_id)
    flat_for = 0.0
    rate = 0.0
    if tokens is None:
        verdict = "unknown"
    else:
        if prev is None:
            snaps[session_id] = {"tokens": tokens, "since": now, "seen": now}
            verdict = "progressing" if raw == "busy" else "idle"
        elif tokens > prev["tokens"]:
            dt = max(1.0, now - prev["since"])
            rate = (tokens - prev["tokens"]) / dt * 60.0           # output tokens/min
            snaps[session_id] = {"tokens": tokens, "since": now, "seen": now}
            verdict = "spiraling" if rate >= SPIRAL_TOKENS_PER_MIN else "progressing"
        else:  # flat (no new output tokens since last change)
            prev["seen"] = now
            flat_for = now - prev["since"]
            if raw == "busy" and flat_for >= STALL_SEC:
                verdict = "stalled"                                # the silent hang Agent View can't see
            elif raw == "busy":
                verdict = "progressing"                            # busy, flat but not long enough yet
            else:
                verdict = "idle"
    return {"session_id": session_id, "verdict": verdict, "tokens": tokens,
            "flat_for": round(flat_for, 1), "rate_per_min": round(rate)}


def fleet_progress(leads=None, now: float | None = None) -> dict:
    """Classify every live session's progress truth; flag stalled/spiraling."""
    now = now if now is not None else time.time()
    from . import roster
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    snaps = _load()
    rows = []
    for l in leads:
        r = classify(l.session_id, l.raw_status, now=now, snaps=snaps)
        r["name"] = l.name or l.cwd or l.session_id[:8]
        rows.append(r)
    _save(snaps)
    flagged = [r for r in rows if r["verdict"] in ("stalled", "spiraling")]
    rows.sort(key=lambda r: {"stalled": 0, "spiraling": 1, "progressing": 2,
                             "idle": 3, "unknown": 4}.get(r["verdict"], 5))
    return {"sessions": rows, "flagged": flagged,
            "summary": (", ".join(f"{r['name']}:{r['verdict']}" for r in flagged)
                        if flagged else "all sessions progressing or idle")}
