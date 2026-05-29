"""Tests for notify.on_transition — alert gating, debounce, escalation, prune.

  PYTHONPATH=src python3 tests/test_notify.py

osascript/afplay are stubbed (no real notifications); a fake clock drives the
debounce window deterministically.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import notify  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    ok = bool(cond)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


fired = []
notify._osascript = lambda title, msg: fired.append((title, msg))
notify._sound = lambda: None
clock = [1000.0]
notify.time = types.SimpleNamespace(time=lambda: clock[0])


def reset():
    fired.clear()
    notify._last_fired.clear()


os.environ["OVERSEER_NOTIFY"] = "0"
reset()
notify.on_transition("s", "waiting", "needs-human", "w")
check("disabled (env off) -> no fire", fired == [])

os.environ["OVERSEER_NOTIFY"] = "1"
reset()
notify.on_transition("s", "waiting", "needs-human", "w")
check("fires on entry to an alert state", len(fired) == 1)

reset()
notify.on_transition("s", "working", "waiting", "w")
check("no fire on a non-alert state", fired == [])

# same state within the debounce window -> suppressed (anti-spam)
reset()
notify.on_transition("s", "waiting", "needs-human", "w")
clock[0] += 30
notify.on_transition("s", "needs-human", "needs-human", "w")
check("same state within window is debounced", len(fired) == 1)

# THE FIX: an escalation to a DIFFERENT alert state within the window must still ring
reset()
notify.on_transition("s", "waiting", "needs-human", "w")
clock[0] += 30
notify.on_transition("s", "needs-human", "crashed", "w")
check("escalation needs-human -> crashed within window still fires", len(fired) == 2)

# same state after the window elapses -> fires again
reset()
notify.on_transition("s", "waiting", "needs-human", "w")
clock[0] += 130
notify.on_transition("s", "waiting", "needs-human", "w")
check("same state after the window fires again", len(fired) == 2)

# the _last_fired map is pruned so a 24/7 daemon can't grow it without bound
reset()
for i in range(300):
    notify.on_transition(f"s{i}", "waiting", "crashed", "w")
grew = len(notify._last_fired)
clock[0] += 130                                   # everything above is now stale
notify.on_transition("final", "waiting", "crashed", "w")   # crossing _PRUNE_AT triggers the prune
check("map grew past the cap before pruning", grew >= 256)
check("map pruned back down once entries went stale", len(notify._last_fired) <= 2)

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
