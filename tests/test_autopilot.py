"""Unit tests for autopilot — pane_state / tell / exists mocked, sleep no-op.

Verifies the decision logic (nudge when waiting, wait when working, stop on
needs-human) and the bounded loop (counts nudges, honors max, stops on needs-human
and on a gone child, refuses non-owned / empty prompt).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import autopilot, driver, relay  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


_sent: list[tuple[str, str]] = []
_owned: set[str] = set()
_alive: set[str] = set()
_states: list[str] = []


def fake_is_owned(name):
    return driver._plain(name) in _owned


def fake_exists(name):
    return driver._plain(name) in _alive


def fake_tell(name, text, context=None, force=False):
    _sent.append((driver._plain(name), text))
    return {"target": name, "chars": len(text)}


def fake_pane_state(name):
    return _states.pop(0) if _states else "waiting"


driver.is_owned = fake_is_owned
driver.exists = fake_exists
relay.tell = fake_tell
relay.pane_state = fake_pane_state

NOOP = lambda *_a, **_k: None


def reset():
    _sent.clear()
    _owned.clear()
    _alive.clear()
    _states.clear()


def raises(fn):
    try:
        fn()
        return False
    except autopilot.AutopilotError:
        return True


# 1. nudge_once refuses non-owned
reset()
check("nudge_once refuses non-owned", raises(lambda: autopilot.nudge_once("ghost", "go")))

# 2. waiting -> nudged + sends
reset(); _owned.add("w"); _states[:] = ["waiting"]
action, _ = autopilot.nudge_once("w", "continue")
check("nudge_once waiting -> nudged", action == "nudged" and _sent and _sent[-1][0] == "w")

# 3. working -> waited, nothing sent
reset(); _owned.add("w"); _states[:] = ["working"]
action, _ = autopilot.nudge_once("w", "continue")
check("nudge_once working -> waited (no send)", action == "waited" and _sent == [])

# 4. needs-human -> stopped, nothing sent
reset(); _owned.add("w"); _states[:] = ["needs-human"]
action, _ = autopilot.nudge_once("w", "continue")
check("nudge_once needs-human -> stopped", action == "stopped" and _sent == [])

# 5. needs-human ignored -> waited (still not 'waiting', so no send)
reset(); _owned.add("w"); _states[:] = ["needs-human"]
action, _ = autopilot.nudge_once("w", "continue", ignore_needs_human=True)
check("nudge_once needs-human ignored -> waited", action == "waited" and _sent == [])

# 6. run: [waiting, working, waiting, needs-human] -> 2 nudges then stop
reset(); _owned.add("w"); _alive.add("w")
_states[:] = ["waiting", "working", "waiting", "needs-human"]
res = autopilot.run("w", "continue", every=0, max_nudges=10, _sleep=NOOP)
check("run stops on needs-human after 2 nudges",
      res["nudges"] == 2 and res["stopped"] == "needs-human" and len(_sent) == 2)

# 7. run: honors max_nudges cap (always waiting)
reset(); _owned.add("w"); _alive.add("w"); _states[:] = []  # pane_state defaults waiting
res = autopilot.run("w", "go", every=0, max_nudges=3, _sleep=NOOP)
check("run honors max_nudges", res["nudges"] == 3 and res["stopped"] == "max_nudges" and len(_sent) == 3)

# 8. run: empty prompt refused
reset(); _owned.add("w"); _alive.add("w")
check("run refuses empty prompt", raises(lambda: autopilot.run("w", "   ", _sleep=NOOP)))

# 9. run: gone child -> stop gone, no nudges
reset(); _owned.add("w")  # owned but NOT alive
res = autopilot.run("w", "go", every=0, max_nudges=5, _sleep=NOOP)
check("run stops when child gone", res["stopped"] == "gone" and res["nudges"] == 0)

# 10. run: refuses non-owned child
reset()
check("run refuses non-owned child", raises(lambda: autopilot.run("ghost", "go", _sleep=NOOP)))

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
