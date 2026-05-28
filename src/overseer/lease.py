"""lease — the v2 state-truth source: sessions REPORT their phase via hooks.

This is Codex's "single highest-leverage fix": stop inferring state from a noisy
transcript tail; have each managed session write an explicit phase through a Claude
Code hook (see hooks/overseer-lease.py). Overseer prefers a fresh lease over the
transcript heuristic; the heuristic remains as fallback for un-instrumented sessions.

A lease is one JSON file per session at data/leases/<session_id>.json:
    {"session_id", "pid", "phase", "turn", "ts"}

Phases reported by hooks -> derived state:
    tool_running / working / thinking  -> working
    waiting                            -> waiting
    needs-human                        -> needs-human
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LEASE_DIR = Path(os.environ.get(
    "OVERSEER_LEASE_DIR", str(Path.home() / "overseer" / "data" / "leases")))
LEASE_MAX_AGE = 180.0  # a lease older than this is stale -> fall back to heuristic

PHASE_TO_STATE = {
    "tool_running": "working",
    "working": "working",
    "thinking": "working",
    "waiting": "waiting",
    "needs-human": "needs-human",
    "idle": "waiting",
    "done": "done",
}
# phases that mean "was actively doing something" -> if pid then died, that's a crash
ACTIVE_PHASES = {"tool_running", "working", "thinking"}


def lease_path(session_id: str) -> Path:
    return LEASE_DIR / f"{session_id}.json"


def write(session_id: str, phase: str, pid: int | None = None, turn: str | None = None) -> None:
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"session_id": session_id, "pid": pid, "phase": phase,
           "turn": turn, "ts": time.time()}
    tmp = lease_path(session_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(lease_path(session_id))  # atomic


def read(session_id: str) -> dict | None:
    p = lease_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def is_fresh(rec: dict, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    ts = rec.get("ts")
    return isinstance(ts, (int, float)) and (now - ts) <= LEASE_MAX_AGE
