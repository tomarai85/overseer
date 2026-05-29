"""state — STATE TRUTH derivation (the hard core, per Codex's warning).

`claude agents --json` only gives busy/idle (and sometimes null). The states a
meta-orchestrator actually needs are: working / waiting / needs-human / blocked /
crashed / done. We derive them from observable signals, conservatively (a wrong
'crashed' or 'blocked' is expensive):

  1. pid liveness            -> os.kill(pid, 0)
  2. roster status           -> busy | idle (from `claude agents --json`)
  3. last CONVERSATIONAL      -> last user/assistant record (noise types skipped),
     record + its blocks         and crucially its CONTENT shape (thinking /
                                  text / tool_use / tool_result)
  4. activity timestamp      -> the `timestamp` ON the last conversational record
                                  (real activity), NOT the file mtime, which
                                  housekeeping/noise writes bump around.

Why content, not just mtime (the gap that makes naive monitors useless — e.g.
a tool that only times out at 5min idle mislabels a session that is mid-think):
  - last block `thinking`   => the model is REASONING right now -> working
                               (a long think writes nothing else; mtime-only
                                tools wrongly call this idle).
  - last conv is an assistant ending on `tool_use` with NO following user
    `tool_result` => the tool has NOT returned. Fresh = working; aged = the tool
    is stuck or a permission prompt is waiting -> blocked / needs-human.
  - a returned tool shows up as a USER record carrying `tool_result`, so if the
    LAST conversational record is that result, the tool completed cleanly.
  - assistant `text` ending in a question => it is asking the human -> needs-human.

Calibrated against real transcripts 2026-05-29. The fully trustworthy signal is
still the per-session lease/heartbeat (docs/STATE-TRUTH.md); these heuristics are
the best we can do for un-instrumented sessions.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import lease


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Conversational record types. Everything else in the transcript is noise for
# the purpose of "what is this session waiting on right now".
CONVERSATIONAL = {"user", "assistant"}

# Seconds. Conservative on purpose — false 'blocked'/'crashed'/'needs-human' is costly.
STALE_BLOCKED_SEC = 600.0       # alive + pending tool, no activity 10min -> hung suspect
NEEDS_HUMAN_MIN_AGE = 10.0      # an idle pending-tool / question must persist this long
                                # before alerting (avoids the "tool just emitted, result
                                # not written yet" tail race -> notify spam)


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


def _last_text(rec: dict[str, Any]) -> str:
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in reversed(content):
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text") or ""
    return ""


def _tool_name(rec: dict[str, Any]) -> str | None:
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):
        for b in reversed(content):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return b.get("name")
    return None


def _parse_ts(ts: Any) -> float | None:
    """ISO-8601 (with trailing Z) -> epoch seconds. Records carry these; noise
    records do not, which is fine — we only read it off conversational records."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# Records that are INJECTED scaffolding, not a real conversational turn: a
# context-compaction summary, a transcript-only meta note, or the caveat /
# local-command wrapper Claude Code writes around a slash command. They carry
# type=="user" but no human typed them. Treating one as "the last turn" misreads
# an idle (just-compacted) session as 'working' (it hits the user-just-spoke
# branch in derive) — the SAME idle-reads-as-working failure the supervisor had.
# Skip them so we classify on the last REAL turn instead.
_INJECTED_FLAGS = ("isCompactSummary", "isVisibleInTranscriptOnly", "isMeta")
_INJECTED_TEXT = ("This session is being continued from a previous",
                  "Caveat: The messages below were generated",
                  "<command-name>", "<local-command-stdout>")


def _is_injected(rec: dict[str, Any]) -> bool:
    if any(rec.get(f) for f in _INJECTED_FLAGS):
        return True
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    content = msg.get("content")
    head = content[:200] if isinstance(content, str) else ""
    return any(m in head for m in _INJECTED_TEXT)


def last_conversational(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rec in reversed(records):
        if rec.get("type") in CONVERSATIONAL and not _is_injected(rec):
            return rec
    return None


def session_title(records: list[dict[str, Any]]) -> str | None:
    """The session's latest Claude-generated title. Claude Code writes periodic
    `{type:'ai-title', aiTitle:...}` records and the freshest sits within a few
    lines of the tail, so reading it off the records we already have is free. This
    is what lets a fleet of same-cwd sessions be told apart: without it every
    hand-started session in ~ collapses to the shared 'domain' slug. Returns None
    when the session has no title yet (callers fall back to the slug)."""
    for rec in reversed(records):
        if rec.get("type") == "ai-title":
            t = rec.get("aiTitle")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return None


def detect_loop(records: list[dict[str, Any]], window: int = 8, repeat: int = 3) -> str | None:
    """Flag a likely LOOP: the same (tool, input) fingerprint repeated `repeat`+ times
    within the last `window` tool calls. The 'stuck/looping' state NO shipped competitor
    derives (the research's white space). Deliberately CONSERVATIVE — exact (name, input)
    match + a high repeat threshold — so a legitimate retry (same tool, different args)
    never false-fires: a false 'stuck' would be the exact notification spam the product
    exists to kill. Returns the repeated tool name, or None.

    NOTE (2026-05-30): standalone + tested, NOT yet wired into the live state path. Wiring
    it into a 'stuck/looping' verdict needs the drift-guard + threshold tuning done in
    daylight (see docs CONCEPT-SHARPENED 'tomorrow'). Shipping the tested primitive now."""
    fps: list[str] = []
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                try:
                    key = json.dumps(b.get("input"), sort_keys=True, default=str)[:400]
                except (TypeError, ValueError):
                    key = str(b.get("input"))[:400]
                fps.append(f"{b.get('name')}\x00{key}")
    recent = fps[-window:]
    counts: dict[str, int] = {}
    for fp in recent:
        counts[fp] = counts.get(fp, 0) + 1
    for fp, n in counts.items():
        if n >= repeat:
            return fp.split("\x00", 1)[0]            # the repeated tool's name
    return None


def _looks_like_question(txt: str) -> bool:
    """The turn ends by asking the human. Beyond a strict trailing '?', also catch a
    posed choice whose '?' sits just above a trailing enumerated options block
    ("...which approach?\n1. A\n2. B") — there the final line is an option, not the
    question, so the old `endswith('?')` missed it and the session silently stalled
    (no needs-human ping). Deliberately does NOT treat a ':' lead-in as a question:
    that would false-fire on result summaries ("Here's what I did:\n1. ...\n2. ...")."""
    if not txt:
        return False
    lines = [ln.strip() for ln in txt.strip().splitlines() if ln.strip()]
    if not lines:
        return False
    if lines[-1].endswith(("?", "？")):
        return True
    enum = ("1.", "2.", "3.", "4.", "5.", "6.", "1)", "2)", "3)",
            "- ", "* ", "・", "①", "②", "③")
    i = len(lines)
    while i > 0 and lines[i - 1].startswith(enum):
        i -= 1                              # skip a trailing enumerated options block
    return 0 < i < len(lines) and lines[i - 1].endswith(("?", "？"))


class TailSignals:
    """What the transcript tail tells us about the session, content-aware."""

    __slots__ = ("role", "last_block", "pending_tool", "thinking", "asked_question",
                 "is_tool_result", "tool_name", "activity_ts", "last_event")

    def __init__(self, conv: dict[str, Any] | None):
        role, blocks = _record_role_and_blocks(conv) if conv else (None, [])
        last_block = blocks[-1] if blocks else None
        self.role = role
        self.last_block = last_block
        # A returned tool comes back as a USER record carrying tool_result, so if
        # the LAST conversational record is an assistant ending on tool_use, the
        # result has NOT arrived yet -> the tool is pending.
        self.pending_tool = (role == "assistant" and last_block == "tool_use")
        self.thinking = (role == "assistant" and last_block == "thinking")
        self.is_tool_result = (role == "user" and last_block == "tool_result")
        self.tool_name = _tool_name(conv) if (conv and self.pending_tool) else None
        txt = _last_text(conv) if (conv and role == "assistant" and last_block == "text") else ""
        self.asked_question = _looks_like_question(txt)
        self.activity_ts = _parse_ts(conv.get("timestamp")) if conv else None
        # human-readable "why" for the board (observability)
        if self.pending_tool:
            self.last_event = f"tool_use:{self.tool_name or '?'} (pending)"
        elif self.thinking:
            self.last_event = "thinking"
        elif self.asked_question:
            self.last_event = "text (question)"
        elif role and last_block:
            self.last_event = f"{role}:{last_block}"
        else:
            self.last_event = role or "unknown"


def derive(lead: Any) -> dict[str, Any]:
    """Given a roster.Lead, return enrichment for store.upsert_lead:
    keys = domain, derived_state, last_event, last_mtime.
    """
    now = time.time()
    transcript = find_transcript(lead.session_id)
    domain = transcript.parent.name if transcript else None
    file_mtime = transcript.stat().st_mtime if transcript else None

    records = _tail_records(transcript) if transcript else []
    conv = last_conversational(records)
    sig = TailSignals(conv)
    title = session_title(records)  # human-readable label; None falls back to domain

    # Activity age from the record's OWN timestamp when present (true activity),
    # else fall back to the file mtime. This is what makes staleness trustworthy:
    # noise/housekeeping writes bump file mtime but never the conversational ts.
    activity_ts = sig.activity_ts if sig.activity_ts is not None else file_mtime
    age = (now - activity_ts) if activity_ts is not None else None

    raw = (lead.raw_status or "").lower()
    # Codex 2026-05-29: a session listed by `claude agents --json` is live by
    # definition, so only trust a dead-pid signal when we actually HAVE a pid.
    alive = True if lead.pid is None else _pid_alive(lead.pid)

    # ---- v2: prefer an explicit hook-reported lease over transcript heuristics ----
    lrec = lease.read(lead.session_id)
    if lrec and lease.is_fresh(lrec, now):
        phase = lrec.get("phase", "")
        if not alive:
            state = "crashed" if phase in lease.ACTIVE_PHASES else "done"
        else:
            state = lease.PHASE_TO_STATE.get(phase, "working")
        return {"domain": domain, "title": title, "derived_state": state,
                "last_event": f"lease:{phase}", "last_mtime": activity_ts}

    # ---- fallback: content-aware transcript heuristic (un-instrumented sessions) ----
    if not alive:
        # Only an unambiguous mid-tool death reads as 'crashed' (the tool never
        # returned). A clean text end / trailing tool_result / no transcript = done.
        state = "crashed" if sig.pending_tool else "done"
    elif sig.pending_tool:
        # The tool has not returned. Fresh = running normally. Aged splits by
        # whether the process is still computing (busy=hung tool) or parked
        # (idle=almost certainly a permission/approval prompt waiting on Tom).
        if age is not None and age >= STALE_BLOCKED_SEC:
            state = "blocked"                       # tool stuck for 10min+ -> hung
        elif raw == "busy":
            state = "working"                       # tool actively running
        elif age is not None and age >= NEEDS_HUMAN_MIN_AGE:
            state = "needs-human"                   # idle + unresolved tool = waiting on Tom
        else:
            state = "working"                       # transient tail race
    elif sig.thinking:
        state = "working"                           # mid-reasoning (NOT idle) — the
                                                    # signal naive mtime monitors miss
    elif raw == "busy":
        if age is not None and age >= STALE_BLOCKED_SEC:
            state = "blocked"                       # busy but frozen -> hung suspect
        else:
            state = "working"
    elif sig.role == "user" and not sig.is_tool_result:
        # Human just spoke; a live agent should be responding -> working. BUT if this
        # prompt has sat unanswered (idle, no progress) well past any turn-start, the
        # agent never picked it up — queued / failed start / hung — and a plain
        # 'working' here sticks FOREVER (STATE-TRUTH mislabel #3, previously
        # "v1 mitigation: none"). Conservatively drop to 'waiting' rather than claim
        # progress that isn't happening. (raw=='busy' was already handled above, so
        # this only fires on an idle/null session whose prompt is genuinely stale.)
        if age is not None and age >= STALE_BLOCKED_SEC:
            state = "waiting"
        else:
            state = "working"                       # human just spoke; agent about to respond
    elif sig.role == "assistant" and sig.asked_question:
        # a clean turn that ends in a question is genuinely asking Tom
        state = "needs-human" if (age is not None and age >= NEEDS_HUMAN_MIN_AGE) else "working"
    elif sig.role == "assistant":
        state = "waiting"                           # turn complete, awaiting Tom's prompt
    elif sig.is_tool_result:
        state = "working"                           # result just landed; agent resuming
    elif raw == "idle":
        state = "waiting"
    else:
        # null status + no usable conversational record: fall back on freshness
        state = "working" if (age is not None and age < 60) else "waiting"

    return {"domain": domain, "title": title, "derived_state": state,
            "last_event": sig.last_event, "last_mtime": activity_ts}
