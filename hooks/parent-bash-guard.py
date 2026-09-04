#!/usr/bin/env python3
"""parent-bash-guard — PreToolUse hook that makes the fleet parent's Bash a real
command-bridge: it may ONLY invoke the `overseer` CLI. Any other shell command
(raw tmux/kill/rm/curl/ssh/…) is BLOCKED (exit 2). So even a drifted or
prompt-injected parent cannot bypass the driver's ownership guards by calling
tmux directly — the boundary is enforced at the tool layer, not by instruction.

Wire it via a --settings file passed to the parent launch (see parent/sandbox-
settings.json). Non-Bash tools and unparseable input are left alone (fail-open
only for non-Bash; Bash that isn't clearly an overseer call is denied).
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # can't parse the hook payload -> don't interfere

tool = data.get("tool_name") or data.get("tool") or ""
if tool != "Bash":
    sys.exit(0)  # this guard only restricts Bash

cmd = ((data.get("tool_input") or {}).get("command") or "").strip()

# Allowed: the command's first token is the overseer CLI...
first_ok = bool(re.match(r'^(?:\S*/overseer/bin/overseer|overseer)\b', cmd))


def chains(command: str) -> bool:
    """True if anything here can start a SECOND command or redirect output.

    This replaced a denylist on 2026-09-04. The old test was "does a dangerous WORD follow a chain
    operator", against a list of tmux/rm/kill/curl/ssh/…, and it let through every interpreter that
    was not on the list: `overseer status; python3 -c '...'` scored allowed, as did node, osascript
    and `$(perl -e ...)`. A guard that permits unless it recognises the danger is permissive exactly
    where it understands the input least, which is the opposite of what a containment boundary is
    for. The question is now structural — is this ONE simple command — and the answer does not
    depend on knowing what any particular program is called.

    Quoted text is exempt so that `overseer tell web "build && test"` still works: what matters is
    whether the SHELL would see an operator, not whether the string contains one.
    """
    quote = None
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in ";&|`\n><":
            return True
        elif c == "$" and i + 1 < n and command[i + 1] == "(":
            return True
        i += 1
    return False


if first_ok and not chains(cmd):
    sys.exit(0)  # allow

print("BLOCKED by parent-bash-guard: the fleet parent may only run `overseer ...` "
      f"commands (not arbitrary shell). Refused: {cmd[:100]}", file=sys.stderr)
sys.exit(2)  # exit 2 = block the tool call, surface stderr to the parent
