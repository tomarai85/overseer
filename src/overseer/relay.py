"""relay — the operate layer's "talk" verbs: owned + quiescence guarded (S6).

Two of Tom's three stated pains with driving a fleet — "hard to issue commands",
"can't pass detailed context when I do", "the bots can't talk to each other" —
are solved here, without ever risking a live session:

  tell(name, text, context=)  issue a command to ONE owned child, but only while
                              it is quiescent (idle at its prompt), with optional
                              attached context (a file's text, an error, prior
                              output). Richer + safer than a raw keystroke.
  relay(src, dst, frame=)     take an owned child's latest output and deliver it to
                              ANOTHER owned child as input. Bot-to-bot conversation,
                              both ends owned, target quiescent.

Guards (same boundary as driver.send/kill, plus quiescence):
  - both src and dst MUST be Overseer-owned (driver.is_owned) — never Tom's own
    hand-started sessions;
  - the TARGET must be quiescent (supervisor.child_state == 'waiting') before we
    type into it; a working / needs-human / stuck / crashed target is refused
    unless force=True is passed explicitly (and that override is audited). This is
    the guard that stops a relay from interrupting a worker mid-tool;
  - every action is appended to a relay audit log (the driver audits the keystroke).
"""
from __future__ import annotations

import json
import time

from . import driver

RELAY_AUDIT = driver.DATA / "relay-audit.log"
# target states that accept an unforced message (quiescent == safe to type into)
SAFE_TARGET = {"waiting"}

# Quiescence is read from the child's pane STRUCTURE, not a content hash. A content
# hash falsely reads an *idle* claude as 'working' forever: the idle TUI animates
# (cursor / status line), so the hash changes every poll (confirmed by a live test
# 2026-05-29 — an idle child read 'working' for 24s straight). Pane structure is
# robust: 'working' shows an interrupt/spinner marker; a permission prompt shows its
# own markers; otherwise the idle input chevron means the turn is done = safe to type.
READY_PROMPT = "❯"
# 'esc to interrupt' is the definitive working signal — shown only during active
# generation / tool execution, absent at the idle prompt (verified on a live child
# 2026-05-29). '✻'/'Cogitating' are the working spinner. NOTE: '⏵⏵' is NOT a working
# marker — it is the always-present "⏵⏵ bypass permissions" status line (an earlier
# guess wrongly included it, which read every idle child as 'working' forever).
_WORKING_MARKERS = ("esc to interrupt", "✻", "Cogitating")
_PERMISSION_MARKERS = ("Do you want to proceed", "Do you want to make this edit",
                       "❯ 1.", "1. Yes", "Allow this", "approve?")


def pane_state(name: str) -> str:
    """Quiescence from the child's pane: waiting | working | needs-human | unknown.
    `waiting` == quiescent == safe to type into (turn done, no work/permission)."""
    raw = driver.read(name, lines=50)
    if any(m in raw for m in _PERMISSION_MARKERS):
        return "needs-human"
    if any(m in raw for m in _WORKING_MARKERS):
        return "working"
    if READY_PROMPT in raw:
        return "waiting"
    return "unknown"


class RelayError(RuntimeError):
    pass


def _audit(action: str, target: str, detail: str = "") -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    with open(RELAY_AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "action": action,
                            "target": target, "detail": str(detail)[:200]}) + "\n")


def _require_owned(name: str, role: str) -> None:
    if not driver.is_owned(name):
        _audit(f"{role}-REFUSED", name, "not an owned child")
        raise RelayError(f"{role} '{name}' is not an Overseer-owned child")


def _check_quiescent(name: str, force: bool) -> str:
    """Return the target's current pane state; raise unless quiescent (or forced)."""
    st = pane_state(name)
    if st in SAFE_TARGET or force:
        return st
    raise RelayError(
        f"target '{name}' is '{st}', not quiescent — refusing to interrupt "
        f"(pass force=True to override)")


def tell(name: str, text: str, context: str | None = None, force: bool = False) -> dict:
    """Issue a command to one owned child, quiescence-guarded, optional context."""
    _require_owned(name, "target")
    if not (text and text.strip()):
        raise RelayError("refusing to send an empty command")
    st = _check_quiescent(name, force)
    body = text if not context else f"{text}\n\n--- context ---\n{context}"
    driver.send(name, body)
    _audit("tell", name, f"state={st} forced={force} chars={len(body)}")
    return {"target": name, "state_before": st, "chars": len(body), "forced": force}


def latest_output(name: str, max_chars: int = 2000) -> str:
    """An owned child's most recent meaningful pane text (TUI chrome stripped)."""
    _require_owned(name, "source")
    raw = driver.read(name, lines=140)
    cleaned = supervisor._clean(raw)
    return cleaned[-max_chars:].strip()


def relay(src: str, dst: str, frame: str | None = None,
          force: bool = False, max_chars: int = 2000) -> dict:
    """Deliver src's latest output to dst as a framed message (bot-to-bot)."""
    _require_owned(src, "source")
    _require_owned(dst, "target")
    if driver.session_name(src) == driver.session_name(dst):
        raise RelayError("source and target are the same child")
    body = latest_output(src, max_chars=max_chars)
    if not body:
        raise RelayError(f"source '{src}' has no output to relay yet")
    st = _check_quiescent(dst, force)
    frame = frame or (f"[relay from worker '{src}' — its latest output follows; "
                      f"read it and respond / continue as appropriate]")
    msg = f"{frame}\n\n{body}"
    driver.send(dst, msg)
    _audit("relay", dst, f"from={src} state={st} forced={force} chars={len(msg)}")
    return {"src": src, "dst": dst, "target_state_before": st,
            "chars": len(msg), "forced": force}
