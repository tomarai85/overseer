"""state — STATE TRUTH derivation (the hard core, per Codex's warning).

`claude agents --json` only gives busy/idle (and sometimes null). The states a
meta-orchestrator actually needs are: working / waiting / needs-human / blocked /
crashed / done. We derive them from three observable signals, conservatively
(a wrong 'crashed' or 'blocked' is expensive):

  1. pid liveness          -> os.kill(pid, 0)
  2. last CONVERSATIONAL    -> last user/assistant record in the transcript,
     transcript record         skipping the noise types (mode, permission-mode,
                                system, file-history-snapshot, attachment,
                                last-prompt, ai-title, queue-operation, agent-name)
  3. transcript mtime age  -> staleness

Calibrated against real transcripts 2026-05-29:
  - a working session's last conversational record is an assistant message whose
    final content block is `tool_use` (it is mid-tool), and mtime is fresh.
  - an idle session's tail is mostly system/mode noise; the last conversational
    record is an assistant `text` turn (it finished, waiting for the human).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import lease


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Conversational record types. Everything else in the transcript is noise for
# the purpose of "what is this session waiting on right now".
CONVERSATIONAL = {"user", "assistant"}

# Seconds. Conservative on purpose — false 'blocked'/'crashed' is costly.
# NOTE (Codex 2026-05-29): mtime is NOT a reliable progress signal — long thinking
# or long tool execution can write nothing (looks stale), and housekeeping/noise
# writes can update it (looks fresh). These thresholds are heuristics for the MVP;
# the trustworthy answer is the per-session lease/heartbeat contract in docs/STATE-TRUTH.md.
STALE_BLOCKED_SEC = 600.0       # busy + no transcript write for 10min -> suspect hung
NEEDS_HUMAN_MIN_AGE = 10.0      # idle+tool_use must persist this long before alerting
                                # (avoids the "tool emitted, result not yet written" race)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def find_transcript(session_id: str) -> Path | None:
    if not session_id or not PROJECTS_DIR.exists():
        return None
    matches = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _tail_records(path: Path, max_tail_bytes: int = 262144) -> list[dict[str, Any]]:
    """Parse the last chunk of a jsonl file into record dicts (in file order)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_tail_bytes:
                f.seek(-max_tail_bytes, os.SEEK_END)
                f.readline()  # discard partial first line
            data = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    out = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _record_role_and_blocks(rec: dict[str, Any]) -> tuple[str | None, list[str]]:
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    role = msg.get("role") or rec.get("role") or rec.get("type")
    content = msg.get("content")
    blocks: list[str] = []
    if isinstance(content, list):
        blocks = [b.get("type") for b in content if isinstance(b, dict) and b.get("type")]
    elif isinstance(content, str):
        blocks = ["text"]
    return role, blocks


def last_conversational(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rec in reversed(records):
        if rec.get("type") in CONVERSATIONAL:
            return rec
    return None


def derive(lead: Any) -> dict[str, Any]:
    """Given a roster.Lead, return enrichment for store.upsert_lead:
    keys = domain, derived_state, last_event, last_mtime.
    """
    now = time.time()
    transcript = find_transcript(lead.session_id)
    domain = transcript.parent.name if transcript else None
    last_mtime = transcript.stat().st_mtime if transcript else None
    age = (now - last_mtime) if last_mtime else None

    records = _tail_records(transcript) if transcript else []
    conv = last_conversational(records)
    role, blocks = _record_role_and_blocks(conv) if conv else (None, [])
    ended_tool_use = bool(blocks) and blocks[-1] == "tool_use"
    last_event = f"{role}:{blocks[-1]}" if role and blocks else (role or "unknown")

    raw = (lead.raw_status or "").lower()
    # Codex 2026-05-29: a session listed by `claude agents --json` is live by
    # definition, so only trust a dead-pid signal when we actually HAVE a pid.
    # pid=None => assume alive (the roster vouched for it). Disappearance from the
    # roster is handled separately by store.mark_gone, not here.
    alive = True if lead.pid is None else _pid_alive(lead.pid)

    # ---- v2: prefer an explicit hook-reported lease over transcript heuristics ----
    # (Codex's highest-leverage fix; see docs/STATE-TRUTH.md + hooks/overseer-lease.py)
    lrec = lease.read(lead.session_id)
    if lrec and lease.is_fresh(lrec, now):
        phase = lrec.get("phase", "")
        if not alive:
            state = "crashed" if phase in lease.ACTIVE_PHASES else "done"
        else:
            state = lease.PHASE_TO_STATE.get(phase, "working")
        return {
            "domain": domain,
            "derived_state": state,
            "last_event": f"lease:{phase}",
            "last_mtime": last_mtime,
        }

    # ---- fallback: conservative transcript heuristic (un-instrumented sessions) ----
    if not alive:
        # Only the unambiguous mid-tool death reads as 'crashed'. A clean
        # assistant text end, a trailing user/tool_result, or a missing
        # transcript reads as 'done' (Codex: avoid false crashed).
        state = "crashed" if ended_tool_use else "done"
    elif raw == "busy":
        if age is not None and age > STALE_BLOCKED_SEC:
            state = "blocked"            # alive + busy but transcript frozen -> hung suspect
        else:
            state = "working"
    else:
        # idle, or null status — derive from the conversation
        if role == "user":
            state = "working"            # human just spoke; agent about to respond
        elif role == "assistant" and ended_tool_use:
            # idle + last block tool_use: could be a real permission/input wait,
            # OR a transient tail race (tool just emitted, result not written yet).
            # Require it to have persisted before alerting, to avoid notify spam.
            state = "needs-human" if (age is not None and age > NEEDS_HUMAN_MIN_AGE) else "working"
        elif role == "assistant":
            state = "waiting"            # turn complete, waiting for Tom's next prompt
        elif raw == "idle":
            state = "waiting"
        else:
            # null status + no conversational record: fall back on freshness
            state = "working" if (age is not None and age < 60) else "waiting"

    return {
        "domain": domain,
        "derived_state": state,
        "last_event": last_event,
        "last_mtime": last_mtime,
    }
