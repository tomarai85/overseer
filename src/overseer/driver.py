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

import json
import os
import subprocess
import time
from pathlib import Path

PREFIX = "ov-"            # our sessions are ov-<name>; we never touch others
READY_PROMPT = "❯"   # the claude TUI input chevron ❯ = booted + idle
TRUST_MARKERS = ("Do you trust the files", "trust the files in this folder")

# Ownership boundary (Codex 2026-05-29): "parent told not to touch user sessions"
# is NOT a boundary — a model can drift or be prompt-injected. The real boundary
# is enforced HERE: send/kill REFUSE any target not in the owned-children
# registry, and every spawn/send/kill is appended to an audit log. So even a
# misbehaving parent cannot drive a session Overseer did not itself spawn.
DATA = Path(os.environ.get("OVERSEER_DATA", str(Path(__file__).resolve().parents[2] / "data")))
REGISTRY = DATA / "children.json"
AUDIT = DATA / "driver-audit.log"


class DriverError(RuntimeError):
    pass


def _plain(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def _load_registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(reg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def _audit(action: str, target: str, detail: str = "") -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "action": action,
                            "target": target, "detail": detail[:200]}) + "\n")


def is_owned(name: str) -> bool:
    """True only if Overseer itself spawned this child (in the registry)."""
    return _plain(name) in _load_registry()


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
    reg = _load_registry()
    reg[_plain(name)] = {"tmux": sn, "cwd": cwd, "spawned_at": time.time()}
    _save_registry(reg)
    _audit("spawn", _plain(name), cwd or "")
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
    if not is_owned(name):
        _audit("send-REFUSED", _plain(name), "not an owned child")
        raise DriverError(f"refusing to send to '{name}': not an Overseer-owned child")
    sn = session_name(name)
    if not exists(sn):
        raise DriverError(f"no child '{name}'")
    if not text:
        raise DriverError("refusing to send empty text")
    _tmux("send-keys", "-t", sn, "-l", text)
    time.sleep(settle)
    _tmux("send-keys", "-t", sn, "Enter")
    _audit("send", _plain(name), text)


def list_children() -> list[str]:
    """Owned children that are still alive (registry ∩ live tmux). Strict: an
    unregistered ov- session is NOT listed (and send/kill would refuse it)."""
    live = set()
    r = _tmux("list-sessions", "-F", "#{session_name}", check=False)
    if r.returncode == 0:
        live = {s[len(PREFIX):] for s in r.stdout.split() if s.startswith(PREFIX)}
    return sorted(n for n in _load_registry() if n in live)


def kill(name: str) -> None:
    """Terminate a child (and its tmux session). No-op if already gone."""
    _tmux("kill-session", "-t", session_name(name), check=False)
    reg = _load_registry()
    if _plain(name) in reg:
        del reg[_plain(name)]
        _save_registry(reg)
    _audit("kill", _plain(name))


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
