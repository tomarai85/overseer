"""notify — alert Tom ONLY when a lead needs him (needs-human / crashed / blocked).

This is the "ring only where I need to look" feature. macOS-native via osascript
+ afplay; no third-party deps. Disabled unless OVERSEER_NOTIFY is truthy so tests
and headless runs stay silent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

ALERT_STATES = {"needs-human", "crashed", "blocked"}
_SOUND = "/System/Library/Sounds/Submarine.aiff"
# simple per-session debounce so a flapping session doesn't spam
_last_fired: dict[str, float] = {}
_DEBOUNCE_SEC = 120.0


def enabled() -> bool:
    return os.environ.get("OVERSEER_NOTIFY", "").lower() in ("1", "true", "yes", "on")


def _as_str(s: str) -> str:
    """Escape a string for an AppleScript double-quoted literal (backslash, quote,
    newline) so a session name/state can't break out of the string or inject."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _osascript(title: str, msg: str) -> None:
    if not shutil.which("osascript"):
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{_as_str(msg)}" with title "{_as_str(title)}"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _sound() -> None:
    if os.path.exists(_SOUND) and shutil.which("afplay"):
        try:
            subprocess.Popen(["afplay", _SOUND])
        except Exception:
            pass


def on_transition(session_id: str, prev: str | None, new: str, label: str) -> None:
    """engine transition callback. Fires only on entry into an ALERT state."""
    if not enabled() or new not in ALERT_STATES:
        return
    now = time.time()
    if now - _last_fired.get(session_id, 0) < _DEBOUNCE_SEC:
        return
    _last_fired[session_id] = now
    titles = {"needs-human": "Needs you", "crashed": "Crashed", "blocked": "Blocked?"}
    _osascript(f"Overseer — {titles.get(new, new)}", f"{label}  ({prev or '?'} -> {new})")
    _sound()
