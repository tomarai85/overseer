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
# ...and it doesn't chain to / contain a dangerous second command.
chains_danger = bool(re.search(r'(?:^|[;&|]\s*|\$\(|`)\s*(?:tmux|rm|kill|killall|curl|wget|nc|ssh|scp|chmod|chown|sudo|launchctl)\b', cmd))

if first_ok and not chains_danger:
    sys.exit(0)  # allow

print("BLOCKED by parent-bash-guard: the fleet parent may only run `overseer ...` "
      f"commands (not arbitrary shell). Refused: {cmd[:100]}", file=sys.stderr)
sys.exit(2)  # exit 2 = block the tool call, surface stderr to the parent
