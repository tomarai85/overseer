"""conversation — a session's recent exchange, for the dashboard's detail view.

The board shows one derived line per worker (state + last_event). To issue a GOOD
command the operator often needs to SEE what the worker is actually doing or asking — which
otherwise means dropping into the tmux/terminal. This turns a session's transcript
tail into a short, readable recent exchange (the last few user/assistant turns),
so a detail drawer can show context and the operator can reply informed.

Read-only and transcript-based, so it works for ANY session (the operator's own sessions
too, not just owned children) — same source the state-truth heuristic reads. It
reuses state's transcript primitives rather than re-parsing, and skips the same
injected scaffolding (compaction summaries / local-command caveats) state skips, so
the exchange reflects REAL turns only.
"""
from __future__ import annotations

from typing import Any

from . import state

# A turn with no human-readable text (a pure tool step) is summarized, not shown raw.
_MAX_CHARS = 600          # cap each turn so one giant message can't blow up the drawer


def _summarize(rec: dict[str, Any], blocks: list[str]) -> str:
    """A short human-readable line for a turn. Prefer real text; otherwise name the
    tool step so the exchange still reads as a sequence (tool -> result -> ...)."""
    text = state._last_text(rec).strip()
    if text:
        return text[:_MAX_CHARS]
    if "tool_use" in blocks:
        return f"[tool: {state._tool_name(rec) or '?'}]"
    if "tool_result" in blocks:
        return "[tool result]"
    if "thinking" in blocks:
        return "[thinking]"
    return ""


def recent_exchange(session_id: str, turns: int = 6) -> list[dict[str, Any]]:
    """The last `turns` REAL conversational turns of a session, newest last.

    Each item: {role: 'user'|'assistant', text: str, ts: float|None}. Empty list if
    there is no transcript. Injected records (compaction / caveat / meta) are skipped
    so the exchange shows only turns that actually happened.
    """
    if turns <= 0:
        return []
    t = state.find_transcript(session_id)
    if not t:
        return []
    out: list[dict[str, Any]] = []
    for rec in state._tail_records(t):
        if rec.get("type") not in state.CONVERSATIONAL or state._is_injected(rec):
            continue
        role, blocks = state._record_role_and_blocks(rec)
        out.append({"role": role, "text": _summarize(rec, blocks),
                    "ts": state._parse_ts(rec.get("timestamp"))})
    return out[-turns:]
