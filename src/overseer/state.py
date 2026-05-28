"""state — STATE TRUTH derivation (the hard core, per Codex's warning).

`claude agents --json` only gives busy/idle. The real states a meta-orchestrator
needs are: idle / busy / blocked / needs-human / crashed / done. We derive them
from three observable signals, conservatively (a wrong 'crashed' is expensive):

  1. pid liveness         -> os.kill(pid, 0)
  2. transcript last event-> ~/.claude/projects/<slug>/<sessionId>.jsonl last line
  3. transcript mtime age -> staleness

Conservative bias: only call 'crashed' when pid is truly gone. Only call
'blocked' when busy + transcript stale beyond a long threshold. When unsure,
fall back to the raw status. Never fabricate certainty.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Thresholds (seconds). Conservative on purpose.
STALE_BLOCKED_SEC = 300.0   # busy but no transcript write for 5min -> suspect blocked/hung
DONE_GRACE_SEC = 5.0

# Last-event types (from Claude Code transcript jsonl) that imply waiting on human.
# These are heuristic; refined in S2 against real transcripts.
NEEDS_HUMAN_HINTS = ("tool_use", "permission", "question", "ask")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False


def find_transcript(session_id: str) -> Path | None:
    """Locate ~/.claude/projects/*/<session_id>.jsonl (newest if multiple)."""
    if not session_id or not PROJECTS_DIR.exists():
        return None
    matches = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _last_jsonl_record(path: Path, max_tail_bytes: int = 65536) -> dict[str, Any] | None:
    """Read the last valid JSON object from a (possibly large) jsonl file."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_tail_bytes:
                f.seek(-max_tail_bytes, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _last_event_kind(record: dict[str, Any] | None) -> str:
    if not record:
        return "unknown"
    # Claude transcript records vary; probe the common shape.
    t = record.get("type") or record.get("role") or ""
    msg = record.get("message")
    if isinstance(msg, dict):
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return f"{role or t}:tool_use"
        if role:
            return str(role)
    return str(t) if t else "unknown"


def derive(lead: Any) -> dict[str, Any]:
    """Given a roster.Lead, return enrichment dict for store.upsert_lead.

    Returns keys: domain, derived_state, last_event, last_mtime.
    `lead` is duck-typed (has .session_id, .pid, .raw_status).
    """
    now = time.time()
    transcript = find_transcript(lead.session_id)
    domain = transcript.parent.name if transcript else None
    last_mtime = transcript.stat().st_mtime if transcript else None
    record = _last_jsonl_record(transcript) if transcript else None
    last_event = _last_event_kind(record)
    age = (now - last_mtime) if last_mtime else None

    raw = (lead.raw_status or "").lower()
    alive = _pid_alive(lead.pid)

    # --- conservative state machine ---
    if not alive:
        # pid gone. If transcript ended cleanly we call it done, else crashed.
        state = "done" if last_event in ("assistant", "summary") else "crashed"
    elif raw == "busy" and age is not None and age > STALE_BLOCKED_SEC:
        state = "blocked"  # alive + busy but transcript hasn't moved -> hung suspect
    elif raw == "idle" and ("tool_use" in last_event or last_event in ("user",)):
        # idle right after a tool_use / mid-exchange often means waiting on human input
        state = "needs-human"
    elif raw == "busy":
        state = "busy"
    elif raw == "idle":
        state = "idle"
    else:
        state = raw or "unknown"

    return {
        "domain": domain,
        "derived_state": state,
        "last_event": last_event,
        "last_mtime": last_mtime,
    }
