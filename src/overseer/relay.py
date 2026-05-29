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
  - the TARGET must be quiescent (relay.pane_state == 'waiting') before we type into
    it; a working / needs-human target is refused unless force=True is passed
    explicitly (and that override is audited). This is the guard that stops a relay
    from interrupting a worker mid-tool;
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
_STATUS_LINES = 15      # only the live bottom region carries current status (not scrollback)
_CONFIRM_GAP = 0.6      # 2nd-sample delay to reject the inter-step false-'waiting' gap

# TUI chrome to strip when extracting a worker's actual output for relay. Kept
# self-contained so relay depends only on `driver` (not a sibling module's private
# helper that is under active concurrent edit).
_BOX = "│╭╮╰╯─━┄┈ ▐▛▜▌▝▘▀\t"
_CHROME = ("bypass permissions", "Welcome back", "Tips for getting started",
           "/release-notes", "What's new", "Opus 4.8", "esc to interrupt",
           "? for shortcuts", "for agents", "MCP server", "Claude Code v")


def _clean_pane(text: str) -> str:
    """Strip claude TUI chrome (box borders, banners, status line) so what remains
    is the worker's actual conversation output."""
    out = []
    for line in text.splitlines():
        s = line.strip(_BOX).strip()
        if not s or s.startswith(("Weekly ", "Code ")):
            continue
        if any(n in s for n in _CHROME):
            continue
        out.append(s)
    return "\n".join(out)


def pane_state(name: str) -> str:
    """Quiescence from the child's pane: waiting | working | needs-human | unknown.
    `waiting` == quiescent == safe to type into (turn done, no work/permission).

    Only the live BOTTOM region carries the current status (spinner / input prompt /
    permission box). Scanning the full scrollback (review MED) would misread a worker
    that merely *mentions* a marker string somewhere in its output."""
    raw = driver.read(name, lines=50)
    region = "\n".join(raw.splitlines()[-_STATUS_LINES:])
    if any(m in region for m in _PERMISSION_MARKERS):
        return "needs-human"
    if any(m in region for m in _WORKING_MARKERS):
        return "working"
    if READY_PROMPT in raw:
        return "waiting"                # idle chevron present ANYWHERE in the pane — robust to the
                                        # fresh-spawn layout where the welcome box pushes ❯ well above
                                        # the status footer + trailing blank rows (so a last-N-lines
                                        # scan misses it). working/permission are matched first (in the
                                        # live region), so this only fires when the child is truly idle.
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
    """Return the target's current pane state; raise unless quiescent (or forced).

    When unforced and the first read says 'waiting', confirm with a second read a beat
    later: a working child briefly shows no spinner between tool steps (review MED), so
    a single 'waiting' can be a false gap. Require two consecutive 'waiting' reads."""
    st = pane_state(name)
    if force:
        return st
    if st == "waiting" and _CONFIRM_GAP > 0:
        time.sleep(_CONFIRM_GAP)
        st2 = pane_state(name)
        if st2 != "waiting":
            st = st2                       # transient inter-step gap — surface real state
    if st in SAFE_TARGET:
        return st
    raise RelayError(
        f"target '{name}' is '{st}', not quiescent — refusing to interrupt "
        f"(pass force=True to override)")


def tell(name: str, text: str, context: str | None = None, force: bool = False) -> dict:
    """Issue a command to one owned child, quiescence-guarded, optional context."""
    _require_owned(name, "target")
    if not (text and text.strip()):
        raise RelayError("refusing to send an empty command")
    # is_owned() is a registry check, not a liveness check, so an owned child that
    # has since died is still 'owned' — reading/sending then raises DriverError deep
    # in driver. Convert it to a RelayError so every caller (CLI tell/route, the web
    # operate panel) that already handles RelayError refuses cleanly instead of
    # surfacing a traceback / 500 for what is just "that worker is gone".
    try:
        st = _check_quiescent(name, force)
        body = text if not context else f"{text}\n\n--- context ---\n{context}"
        driver.send(name, body)
    except driver.DriverError as e:
        _audit("tell-REFUSED", name, f"unreachable: {e}")
        raise RelayError(f"owned child '{name}' is unreachable (gone?): {e}") from e
    _audit("tell", name, f"state={st} forced={force} chars={len(body)}")
    return {"target": name, "state_before": st, "chars": len(body), "forced": force}


def latest_output(name: str, max_chars: int = 2000) -> str:
    """An owned child's most recent meaningful pane text (TUI chrome stripped)."""
    _require_owned(name, "source")
    raw = driver.read(name, lines=140)
    return _clean_pane(raw)[-max_chars:].strip()


def relay(src: str, dst: str, frame: str | None = None,
          force: bool = False, max_chars: int = 2000) -> dict:
    """Deliver src's latest output to dst as a framed message (bot-to-bot)."""
    _require_owned(src, "source")
    _require_owned(dst, "target")
    if driver.session_name(src) == driver.session_name(dst):
        raise RelayError("source and target are the same child")
    # Same gone-but-owned guard as tell(): either end may have died since the
    # registry listed it; surface that as a clean RelayError, not a DriverError.
    try:
        body = latest_output(src, max_chars=max_chars)
        if not body:
            raise RelayError(f"source '{src}' has no output to relay yet")
        st = _check_quiescent(dst, force)
        frame = frame or (f"[relay from worker '{src}' — its latest output follows; "
                          f"read it and respond / continue as appropriate]")
        msg = f"{frame}\n\n{body}"
        driver.send(dst, msg)
    except driver.DriverError as e:
        _audit("relay-REFUSED", dst, f"unreachable: {e}")
        raise RelayError(f"relay failed — '{src}' or '{dst}' is unreachable (gone?): {e}") from e
    _audit("relay", dst, f"from={src} state={st} forced={force} chars={len(msg)}")
    return {"src": src, "dst": dst, "target_state_before": st,
            "chars": len(msg), "forced": force}
