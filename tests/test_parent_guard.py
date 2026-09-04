"""Tests for the parent Bash guard: the fleet parent may run `overseer ...` and nothing else.

  PYTHONPATH=src python3 tests/test_parent_guard.py

Written 2026-09-04 after an external review falsified the README's containment claim in one line.
The guard used to allow anything whose first token was `overseer` unless a dangerous WORD followed a
chain operator, against a list of tmux/rm/kill/curl/ssh/…. Every interpreter absent from that list
walked straight through:

    overseer status; python3 -c '...'      allowed
    overseer status && node -e '...'       allowed
    overseer status; osascript -e '...'    allowed
    overseer status $(perl -e '...')       allowed

The NEGATIVE CONTROL below is the point of this file. It reimplements the old permissive rule and
asserts that it PASSES those payloads — so if someone ever reverts the guard to a denylist, this
test goes red instead of quietly re-opening the hole. A test that only checks the fix would pass
just as happily against a guard that never worked.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "hooks" / "parent-bash-guard.py"

# what the guard used to do, kept only so the control can prove these payloads once got through
OLD_FIRST = re.compile(r"^(?:\S*/overseer/bin/overseer|overseer)\b")
OLD_DANGER = re.compile(
    r"(?:^|[;&|]\s*|\$\(|`)\s*"
    r"(?:tmux|rm|kill|killall|curl|wget|nc|ssh|scp|chmod|chown|sudo|launchctl)\b"
)

BYPASS = [
    "overseer status; python3 -c 'print(1)'",
    "overseer status && node -e 'console.log(1)'",
    "overseer status; osascript -e 'beep'",
    "overseer status $(perl -e 'print 1')",
    "overseer status | tee /tmp/x",
    "overseer status > /tmp/x",
    "overseer status\nrm -rf /tmp/x",
]
ALLOWED = [
    "overseer status",
    "overseer leads",
    'overseer tell web "build && test"',      # an operator INSIDE quotes is not an operator
    "overseer spawn helix ~/work/helix",
]
DENIED_OUTRIGHT = [
    "rm -rf /tmp/x",
    "tmux kill-session -t ov-web",
    "python3 -c 'print(1)'",
]


def guard(cmd: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return subprocess.run([sys.executable, str(GUARD)], input=payload.encode(),
                          capture_output=True).returncode


def old_guard_allows(cmd: str) -> bool:
    return bool(OLD_FIRST.match(cmd.strip())) and not OLD_DANGER.search(cmd)


def run():
    rows = []
    for c in BYPASS:
        rows.append((f"blocks chained payload: {c[:44]!r}", guard(c) != 0))
    for c in ALLOWED:
        rows.append((f"allows plain overseer call: {c[:44]!r}", guard(c) == 0))
    for c in DENIED_OUTRIGHT:
        rows.append((f"blocks non-overseer command: {c[:44]!r}", guard(c) != 0))

    # NEGATIVE CONTROL — the old rule must ACCEPT what the new rule rejects. If this ever fails,
    # the control is broken and the tests above stop proving that anything changed.
    leaked = [c for c in BYPASS if old_guard_allows(c)]
    rows.append((f"control: the old denylist accepted {len(leaked)}/{len(BYPASS)} of these",
                 len(leaked) >= 4))

    # non-Bash tools are none of this guard's business
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}})
    rc = subprocess.run([sys.executable, str(GUARD)], input=payload.encode(),
                        capture_output=True).returncode
    rows.append(("non-Bash tool is left alone", rc == 0))

    # unparseable input must not wedge the parent
    rc = subprocess.run([sys.executable, str(GUARD)], input=b"not json",
                        capture_output=True).returncode
    rows.append(("unparseable payload fails open", rc == 0))
    return rows


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
