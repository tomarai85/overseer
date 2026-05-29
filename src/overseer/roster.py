"""roster — read the live Claude Code session roster.

Source of truth: `claude agents --json` (verified 2026-05-29 to return a JSON
array of {pid, cwd, kind, startedAt, sessionId, name?, status}).

This module ONLY reads. It never drives or kills a session.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


CLAUDE_BIN_CANDIDATES = ("claude",)


@dataclass
class Lead:
    """One Claude Code session (a 'team-lead' in Overseer's model)."""

    session_id: str
    pid: int | None = None
    name: str | None = None
    cwd: str | None = None
    kind: str | None = None
    raw_status: str | None = None  # busy | idle (from claude json)
    started_at: float | None = None  # epoch seconds
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Lead":
        started = obj.get("startedAt")
        # claude reports startedAt in epoch milliseconds.
        started_sec = float(started) / 1000.0 if isinstance(started, (int, float)) else None
        known = {"pid", "cwd", "kind", "startedAt", "sessionId", "name", "status"}
        return cls(
            session_id=str(obj.get("sessionId", "")),
            pid=obj.get("pid"),
            name=obj.get("name"),
            cwd=obj.get("cwd"),
            kind=obj.get("kind"),
            raw_status=obj.get("status"),
            started_at=started_sec,
            extra={k: v for k, v in obj.items() if k not in known},
        )


class RosterError(RuntimeError):
    pass


def _claude_bin() -> str:
    for name in CLAUDE_BIN_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    raise RosterError("claude CLI not found in PATH")


def fetch_raw(timeout: float = 15.0) -> list[dict[str, Any]]:
    """Run `claude agents --json` and return the parsed list. Raises RosterError."""
    binpath = _claude_bin()
    try:
        out = subprocess.run(
            [binpath, "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RosterError(f"`claude agents --json` timed out after {timeout}s") from e
    if out.returncode != 0:
        raise RosterError(
            f"`claude agents --json` exited {out.returncode}: {out.stderr.strip()[:200]}"
        )
    text = out.stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RosterError(f"could not parse claude json: {e}") from e
    if not isinstance(data, list):
        raise RosterError("expected a JSON array from `claude agents --json`")
    return data


def fetch() -> list[Lead]:
    """Return the live roster as Lead objects. Self's own session is included.

    Defensively skips any non-dict / session-id-less array element: fetch_raw
    guarantees a JSON *list* but not that every element is an object, and a single
    malformed entry calling `.get` on a non-dict would raise AttributeError — which
    leaks past every caller's `except RosterError` guard (usage/progress/budget/
    alerts/CLI) and crashes them. One weird entry must not blank the whole roster."""
    return [Lead.from_json(o) for o in fetch_raw()
            if isinstance(o, dict) and o.get("sessionId")]
