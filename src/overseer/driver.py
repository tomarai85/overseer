"""driver — the cockpit's HANDS: spawn / send / read Claude children via tmux.

Proven 2026-05-29: typing into a running `claude` TUI from outside (via
`tmux send-keys`) is received and executed by the child. This module wraps
that mechanic into a safe, named API.

Design rules:
  - We ONLY ever touch tmux sessions we created (prefix `ov-`). A session
    without that prefix is never sent keys or killed. This keeps the driver
    from ever poking Tom's own hand-started terminals by accident.
  - `send` types the text literally (`-l`) so a prompt that happens to contain
    a tmux key name ("Enter", "C-c") is not interpreted; submission is a
    separate, deliberate Enter after a short settle.
  - Everything here is mechanical. The decision of *what* to send and *to whom*
    lives one layer up (the parent AI / the human).
"""
from __future__ import annotations

import subprocess
import time

PREFIX = "ov-"            # our sessions are ov-<name>; we never touch others
READY_PROMPT = "❯"   # the claude TUI input chevron ❯ = booted + idle
TRUST_MARKERS = ("Do you trust the files", "trust the files in this folder")


class DriverError(RuntimeError):
    pass


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise DriverError(f"tmux {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r


def session_name(name: str) -> str:
    """Normalize a child name to its tmux session name (always ov-prefixed)."""
    return name if name.startswith(PREFIX) else PREFIX + name


def exists(name: str) -> bool:
    return _tmux("has-session", "-t", session_name(name), check=False).returncode == 0


def spawn(name: str, cwd: str | None = None, claude_cmd: str = "claude",
          width: int = 220, height: int = 50) -> str:
    """Create a detached tmux session and launch a Claude child inside it.

    Refuses if a child with that name already exists (no silent clobber).
    Returns the tmux session name. The child appears in `claude agents --json`
    a few seconds later; correlate it with `find_lead(cwd, after_ts)`.
    """
    sn = session_name(name)
    if exists(sn):
        raise DriverError(f"child '{name}' already exists (kill it first)")
    _tmux("new-session", "-d", "-s", sn, "-x", str(width), "-y", str(height))
    launch = f"cd {cwd} && {claude_cmd}" if cwd else claude_cmd
    _tmux("send-keys", "-t", sn, launch, "Enter")
    return sn


def read(name: str, lines: int = 60) -> str:
    """Return the child's current screen (last `lines` non-empty-trimmed)."""
    sn = session_name(name)
    if not exists(sn):
        raise DriverError(f"no child '{name}'")
    out = _tmux("capture-pane", "-t", sn, "-p").stdout
    if lines:
        out = "\n".join(out.splitlines()[-lines:])
    return out


def wait_ready(name: str, timeout: float = 30.0, poll: float = 0.5) -> bool:
    """Block until the child shows its input prompt (booted and idle).

    Auto-accepts a first-run 'trust this folder' prompt if one appears, since
    that is a launch-time gate, not a real decision the operator needs to make.
    Returns False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        screen = read(name)
        if any(m in screen for m in TRUST_MARKERS):
            _tmux("send-keys", "-t", session_name(name), "Enter")  # accept default (trust)
            time.sleep(poll)
            continue
        if READY_PROMPT in screen:
            return True
        time.sleep(poll)
    return False


def send(name: str, text: str, settle: float = 0.4) -> None:
    """Type a prompt into the child and submit it.

    Text is sent literally; Enter is a separate keystroke after `settle` so the
    TUI registers the full line before submission.
    """
    sn = session_name(name)
    if not exists(sn):
        raise DriverError(f"no child '{name}'")
    if not text:
        raise DriverError("refusing to send empty text")
    _tmux("send-keys", "-t", sn, "-l", text)
    time.sleep(settle)
    _tmux("send-keys", "-t", sn, "Enter")


def list_children() -> list[str]:
    """Names (without prefix) of all driver-managed children currently alive."""
    r = _tmux("list-sessions", "-F", "#{session_name}", check=False)
    if r.returncode != 0:
        return []
    return [s[len(PREFIX):] for s in r.stdout.split() if s.startswith(PREFIX)]


def kill(name: str) -> None:
    """Terminate a child (and its tmux session). No-op if already gone."""
    _tmux("kill-session", "-t", session_name(name), check=False)


def find_lead(cwd: str, after_ts: float, leads=None):
    """Best-effort: match a just-spawned child to its roster Lead.

    A child launched in `cwd` shortly after `after_ts` should appear in the
    roster with that cwd and a later started_at. Returns the Lead or None.
    """
    from . import roster
    if leads is None:
        leads = roster.fetch()
    cands = [l for l in leads
             if l.cwd == cwd and (l.started_at or 0) >= after_ts - 2.0]
    cands.sort(key=lambda l: l.started_at or 0, reverse=True)
    return cands[0] if cands else None
