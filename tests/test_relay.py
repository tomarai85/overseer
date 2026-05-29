"""Unit tests for relay (tell / relay) — all I/O guards mocked, no real tmux.

Verifies the safety boundary: ownership refusal, quiescence guard (never type into
a working target), empty/self refusals, context attachment, and bot-to-bot framing.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import driver, relay, supervisor  # noqa: E402

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


# ---- fakes (replace all real tmux / pane I/O) ----
_sent: list[tuple[str, str]] = []
_owned: set[str] = set()
_state: dict[str, str] = {}
_output: dict[str, str] = {}


def fake_is_owned(name):
    return driver._plain(name) in _owned


def fake_read(name, lines=60):
    return _output.get(driver._plain(name), "")


def fake_send(name, text, settle=0.4):
    _sent.append((driver._plain(name), text))


def fake_child_state(name, now=None, hb=None):
    return {"name": name, "state": _state.get(driver._plain(name), "unknown")}


driver.is_owned = fake_is_owned
driver.read = fake_read
driver.send = fake_send
supervisor.child_state = fake_child_state


def reset():
    _sent.clear()
    _owned.clear()
    _state.clear()
    _output.clear()


def raises(fn):
    try:
        fn()
        return False
    except relay.RelayError:
        return True


# 1. tell refuses a non-owned target
reset()
check("tell refuses non-owned target", raises(lambda: relay.tell("ghost", "hi")))

# 2. tell refuses empty text
reset(); _owned.add("w1"); _state["w1"] = "waiting"
check("tell refuses empty text", raises(lambda: relay.tell("w1", "   ")))

# 3. tell refuses a working (non-quiescent) target, and sends nothing
reset(); _owned.add("w1"); _state["w1"] = "working"
check("tell refuses working target", raises(lambda: relay.tell("w1", "do x")))
check("  -> nothing sent to working target", _sent == [])

# 4. tell delivers to a waiting target
reset(); _owned.add("w1"); _state["w1"] = "waiting"
relay.tell("w1", "do x")
check("tell delivers to waiting target", bool(_sent) and _sent[-1][0] == "w1" and "do x" in _sent[-1][1])

# 5. tell attaches context as a labelled block
reset(); _owned.add("w1"); _state["w1"] = "waiting"
relay.tell("w1", "fix the bug", context="Traceback: KeyError 'x'")
check("tell attaches context block",
      "--- context ---" in _sent[-1][1] and "KeyError" in _sent[-1][1])

# 6. tell force=True overrides the quiescence guard
reset(); _owned.add("w1"); _state["w1"] = "working"
relay.tell("w1", "urgent", force=True)
check("tell force overrides quiescence", bool(_sent) and _sent[-1][0] == "w1")

# 7. relay refuses src == dst
reset(); _owned.add("w1"); _state["w1"] = "waiting"; _output["w1"] = "hi"
check("relay refuses same src/dst", raises(lambda: relay.relay("w1", "w1")))

# 8. relay refuses when the source has produced nothing
reset(); _owned.update({"a", "b"}); _state["b"] = "waiting"; _output["a"] = ""
check("relay refuses empty source", raises(lambda: relay.relay("a", "b")))

# 9. relay refuses a working target, and sends nothing
reset(); _owned.update({"a", "b"}); _state["b"] = "working"; _output["a"] = "src out"
check("relay refuses working target", raises(lambda: relay.relay("a", "b")))
check("  -> nothing sent to working target", _sent == [])

# 10. relay delivers framed source output to a quiescent target
reset(); _owned.update({"a", "b"}); _state["b"] = "waiting"; _output["a"] = "the answer is 42"
relay.relay("a", "b")
check("relay delivers to quiescent target", bool(_sent) and _sent[-1][0] == "b")
check("relay frames with source name + body",
      "relay from worker 'a'" in _sent[-1][1] and "the answer is 42" in _sent[-1][1])

# 11. relay refuses a non-owned source
reset(); _owned.add("b"); _state["b"] = "waiting"
check("relay refuses non-owned source", raises(lambda: relay.relay("ghost", "b")))

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
