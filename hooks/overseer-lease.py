#!/usr/bin/env python3
"""Overseer lease hook — install into Claude Code to give Overseer TRUE state.

Self-contained (no PYTHONPATH needed at hook time). Reads the Claude Code hook
JSON on stdin, takes the phase as argv[1], and writes a lease file that Overseer
reads as authoritative state truth (better than guessing from the transcript).

Install (opt-in — the operator edits their own settings.json; Overseer never edits it):

  "hooks": {
    "PreToolUse":      [{"hooks":[{"type":"command","command":"python3 ~/overseer/hooks/overseer-lease.py tool_running"}]}],
    "PostToolUse":     [{"hooks":[{"type":"command","command":"python3 ~/overseer/hooks/overseer-lease.py working"}]}],
    "UserPromptSubmit":[{"hooks":[{"type":"command","command":"python3 ~/overseer/hooks/overseer-lease.py working"}]}],
    "Stop":            [{"hooks":[{"type":"command","command":"python3 ~/overseer/hooks/overseer-lease.py waiting"}]}],
    "Notification":    [{"hooks":[{"type":"command","command":"python3 ~/overseer/hooks/overseer-lease.py needs-human"}]}]
  }

The hook only writes to ~/overseer/data/leases/. It reads nothing sensitive and
always exits 0 (never blocks the session).
"""
import json
import os
import sys
import time
from pathlib import Path

LEASE_DIR = Path(os.environ.get(
    "OVERSEER_LEASE_DIR", str(Path.home() / "overseer" / "data" / "leases")))


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "working"
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    sid = data.get("session_id") or data.get("sessionId") or ""
    if not sid:
        return 0  # nothing to key on; do not block the session
    rec = {
        "session_id": sid,
        "pid": os.getppid(),  # best-effort: the claude process is our parent
        "phase": phase,
        "turn": data.get("transcript_path") or data.get("turn"),
        "ts": time.time(),
    }
    try:
        LEASE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LEASE_DIR / f"{sid}.json.tmp"
        tmp.write_text(json.dumps(rec))
        tmp.replace(LEASE_DIR / f"{sid}.json")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # a hook must never break the session
