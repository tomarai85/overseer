"""autopilot — keep an OWNED worker moving (Tom's "auto-send a prompt every few
minutes so it keeps working forever").

Each time the worker goes quiescent (idle at its prompt) the autopilot sends a
continuation prompt, so a long task that the worker keeps pausing on advances
unattended. Bounded and safe by construction:

  - OWNED children only (relay/driver enforce) — never Tom's own sessions;
  - quiescence-gated: nudges only a child in 'waiting' (turn done). A 'working'
    child is left to work; we just wait and re-check;
  - STOPS on 'needs-human' (a genuine question / permission prompt) — never spam a
    worker past a point where it actually needs Tom, unless ignore_needs_human=True;
  - hard cap on total nudges (max_nudges) so it can never run away;
  - stops if the child disappears;
  - every decision is audited.
"""
from __future__ import annotations

import json
import time

from . import driver, relay

AUTOPILOT_AUDIT = driver.DATA / "autopilot-audit.log"


class AutopilotError(RuntimeError):
    pass


def _audit(action: str, child: str, detail: str = "") -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    with open(AUTOPILOT_AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "action": action,
                            "child": child, "detail": str(detail)[:200]}) + "\n")


def nudge_once(child: str, prompt: str, ignore_needs_human: bool = False) -> tuple[str, str]:
    """One decision step. Returns (action, state) where action is one of:
    'nudged'  — child was waiting; the continuation prompt was sent.
    'stopped' — child needs a human (and we are not ignoring that).
    'waited'  — child is working / unknown; nothing sent, try again later.
    """
    if not driver.is_owned(child):
        raise AutopilotError(f"'{child}' is not an Overseer-owned child")
    st = relay.pane_state(child)
    if st == "waiting":
        relay.tell(child, prompt)                 # tell re-checks quiescence itself
        _audit("nudge", child, prompt[:80])
        return ("nudged", st)
    if st == "needs-human" and not ignore_needs_human:
        _audit("stop-needs-human", child)
        return ("stopped", st)
    _audit("wait", child, st)
    return ("waited", st)


def run(child: str, prompt: str, every: float = 120.0, max_nudges: int = 10,
        ignore_needs_human: bool = False, on_event=None,
        max_runtime: float = 3600.0, _sleep=time.sleep, _now=time.monotonic) -> dict:
    """Nudge the child each time it is quiescent, up to max_nudges. Stops on
    needs-human (unless ignored), when the child is gone, when ownership is lost, or
    after max_runtime seconds — a child that never goes quiescent ('working'/'unknown'
    forever) must not loop unbounded in time (review LOW). Blocking loop."""
    if not driver.is_owned(child):
        raise AutopilotError(f"'{child}' is not an Overseer-owned child")
    if not (prompt and prompt.strip()):
        raise AutopilotError("empty continuation prompt")

    nudges = 0
    start = _now()
    _audit("start", child, f"every={every} max={max_nudges} runtime={max_runtime}")
    while nudges < max_nudges:
        if max_runtime and (_now() - start) > max_runtime:
            _audit("stop-runtime", child, f"nudges={nudges}")
            return {"child": child, "nudges": nudges, "stopped": "max_runtime"}
        if not driver.exists(child):
            _audit("stop-gone", child)
            if on_event:
                on_event("gone", child, "gone", nudges)
            return {"child": child, "nudges": nudges, "stopped": "gone"}
        try:
            action, st = nudge_once(child, prompt, ignore_needs_human)
        except AutopilotError:                          # ownership lost mid-loop -> stop
            _audit("stop-unowned", child)
            return {"child": child, "nudges": nudges, "stopped": "unowned"}
        except (relay.RelayError, driver.DriverError) as e:  # transient (state flip / tmux blip)
            _audit("nudge-error", child, str(e)[:80])
            action, st = "waited", "error"              # fail-safe: don't crash the loop
        if on_event:
            on_event(action, child, st, nudges + (1 if action == "nudged" else 0))
        if action == "nudged":
            nudges += 1
        elif action == "stopped":
            return {"child": child, "nudges": nudges, "stopped": "needs-human"}
        _sleep(every)
    _audit("stop-maxed", child, nudges)
    return {"child": child, "nudges": nudges, "stopped": "max_nudges"}
