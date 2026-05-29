"""supervisor — durable, deterministic watch over OWNED children (Codex's #1).

For each owned child (driver registry ∩ live tmux), derive its TRUE state from
its tmux pane plus a heartbeat (pane-content change over time), and produce an
away-mode summary. No LLM — cheap, safe, runs unattended. This is the basis for
the away-mode dashboard, proactive alerts, and (next) safe auto-restart of only
crashed owned children.

State per owned child:
  crashed   tmux session gone (the child died)
  needs-human   a permission/approval prompt is on screen
  working   pane changed recently, or a spinner / "esc to interrupt" is showing
  waiting   idle at the `❯` prompt (turn done), pane quiet
  stuck     pane unchanged > STUCK_SEC and not sitting at an idle prompt
"""
from __future__ import annotations

import hashlib
import json
import time

from . import driver

HEARTBEAT = driver.DATA / "heartbeat.json"
STUCK_SEC = 180.0          # owned child frozen this long (no prompt) -> stuck
READY_PROMPT = "❯"

# screen lines that are TUI chrome, not conversation (mirrors the GUI cleaner)
_NOISE = ("bypass permissions", "Welcome back", "Tips for getting started",
          "/release-notes", "MCP server", "What's new", "Opus 4.8 is here",
          "Claude Code v", "for agents", "shift+tab")
_BOX = "│╭╮╰╯─━┄┈ ▐▛▜▌▝▘▀\t"
_WORKING_MARKERS = ("esc to interrupt", "✻", "Cogitat", "Thinking", "tokens", "⏵")
_PERMISSION_MARKERS = ("Do you want to proceed", "Do you want to make this edit",
                       "❯ 1.", "1. Yes", "Allow this", "approve")


def _clean(screen: str) -> str:
    out = []
    for line in screen.splitlines():
        s = line.strip(_BOX).strip()
        if not s:
            continue
        if s.startswith("Weekly ") or s.startswith("Code "):
            continue
        if any(n in s for n in _NOISE):
            continue
        out.append(s)
    return "\n".join(out)


def _load_hb() -> dict:
    try:
        return json.loads(HEARTBEAT.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hb(hb: dict) -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps(hb))


def child_state(name: str, now: float | None = None, hb: dict | None = None) -> dict:
    """Derive an owned child's state from its pane + heartbeat. Returns
    {name, state, last_change_age, hint}. `hb` is the heartbeat map (mutated)."""
    now = now if now is not None else time.time()
    if hb is None:
        hb = _load_hb()

    if not driver.exists(name):
        return {"name": name, "state": "crashed", "last_change_age": None,
                "hint": "tmux session gone"}

    raw = driver.read(name, lines=60)
    cleaned = _clean(raw)
    digest = hashlib.sha1(cleaned.encode("utf-8", "ignore")).hexdigest()

    rec = hb.get(name)
    if not rec or rec.get("hash") != digest:
        hb[name] = {"hash": digest, "since": now}      # content changed -> heartbeat
        last_change = now
    else:
        last_change = rec.get("since", now)
    age = now - last_change

    tail = cleaned[-600:]
    if any(m in tail for m in _PERMISSION_MARKERS):
        state = "needs-human"
    elif any(m in raw for m in _WORKING_MARKERS):
        state = "working"
    elif age < 8:
        state = "working"                               # pane just changed
    elif raw.rstrip().endswith(READY_PROMPT) or f"{READY_PROMPT} " in raw[-200:]:
        state = "waiting"                               # idle at the prompt
    elif age >= STUCK_SEC:
        state = "stuck"                                 # frozen, no prompt
    else:
        state = "waiting"
    return {"name": name, "state": state, "last_change_age": round(age, 1),
            "hint": tail.splitlines()[-1][:80] if tail else ""}


def summary(now: float | None = None) -> dict:
    """Away-mode summary over all owned children: per-child state + what needs Tom."""
    now = now if now is not None else time.time()
    hb = _load_hb()
    children = []
    for name in driver.list_children():
        children.append(child_state(name, now=now, hb=hb))
    _save_hb(hb)

    needs = [c for c in children if c["state"] in ("needs-human", "crashed", "stuck")]
    counts: dict[str, int] = {}
    for c in children:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
    if not children:
        line = "no owned workers"
    elif not needs:
        line = f"{len(children)} workers, all healthy"
    else:
        line = "needs you: " + ", ".join(f"{c['name']}({c['state']})" for c in needs)
    return {"children": children, "counts": counts, "needs_attention": needs, "summary": line}
