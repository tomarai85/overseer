"""Unit tests for relay (tell / relay) — pane I/O mocked, no real tmux.

Quiescence is read from pane STRUCTURE (relay.pane_state), so tests feed pane text:
  quiescent target -> "...❯ " (idle input chevron, no work/permission markers)
  working target   -> "...esc to interrupt"
Verifies ownership refusal, the quiescence gate, empty/self refusals, context
attachment, force override, and bot-to-bot framing.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import driver, relay  # noqa: E402

# Isolate the audit log to a tmpdir — otherwise relay._audit writes test entries
# ('tell w1', 'relay b', ...) into the real data/relay-audit.log, which then leak
# into `overseer activity` (the live ticker). RELAY_AUDIT is bound at import, so
# redirect it explicitly, not just driver.DATA.
driver.DATA = Path(tempfile.mkdtemp())
relay.RELAY_AUDIT = driver.DATA / "relay-audit.log"

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
_pane: dict[str, str] = {}
_dead: set[str] = set()             # owned in the registry, but the tmux session is gone

QUIET = "ready\n❯ "                 # idle input chevron -> waiting
BUSY = "running a tool\nesc to interrupt"  # -> working


def fake_is_owned(name):
    return driver._plain(name) in _owned


def fake_read(name, lines=60):
    p = driver._plain(name)
    if p in _dead:                  # mirror driver.read raising on a gone session
        raise driver.DriverError(f"no child '{p}'")
    return _pane.get(p, "")


def fake_send(name, text, settle=0.4):
    _sent.append((driver._plain(name), text))


driver.is_owned = fake_is_owned
driver.read = fake_read
driver.send = fake_send
relay._CONFIRM_GAP = 0   # no real sleep in tests; double-sample reads the same mocked pane


def reset():
    _sent.clear()
    _owned.clear()
    _pane.clear()
    _dead.clear()


def raises(fn):
    try:
        fn()
        return False
    except relay.RelayError:
        return True


# 0. pane_state classifies structure correctly
reset(); _pane["w"] = QUIET
check("pane_state: idle chevron -> waiting", relay.pane_state("w") == "waiting")
_pane["w"] = BUSY
check("pane_state: interrupt marker -> working", relay.pane_state("w") == "working")
_pane["w"] = "Do you want to proceed?\n1. Yes"
check("pane_state: permission -> needs-human", relay.pane_state("w") == "needs-human")

# 0b. _clean_pane strips TUI chrome, keeps the worker's real output
_dirty = ("╭─── Claude Code v2.1.156 ───╮\n"
          "│ Welcome back ARaito!       │\n"
          "the real answer is 42\n"
          "⏵⏵ bypass permissions on (shift+tab)\n"
          "❯ ")
_cleaned = relay._clean_pane(_dirty)
check("_clean_pane keeps real output", "the real answer is 42" in _cleaned)
check("_clean_pane strips banner/status chrome",
      "Welcome back" not in _cleaned and "bypass permissions" not in _cleaned
      and "Claude Code v" not in _cleaned)

# 0c. region scan: a working-marker that appears only in SCROLLBACK (as data) must
#     not read as 'working' when the live bottom is an idle prompt (review MED).
reset(); _owned.add("w")
_pane["w"] = "\n".join(["the docs note 'esc to interrupt' cancels a run"]
                       + ["scroll filler line"] * 16 + ["❯ "])
check("pane_state ignores marker in scrollback (region-scan)", relay.pane_state("w") == "waiting")

# 1. tell refuses a non-owned target
reset()
check("tell refuses non-owned target", raises(lambda: relay.tell("ghost", "hi")))

# 2. tell refuses empty text
reset(); _owned.add("w1"); _pane["w1"] = QUIET
check("tell refuses empty text", raises(lambda: relay.tell("w1", "   ")))

# 3. tell refuses a working (non-quiescent) target, and sends nothing
reset(); _owned.add("w1"); _pane["w1"] = BUSY
check("tell refuses working target", raises(lambda: relay.tell("w1", "do x")))
check("  -> nothing sent to working target", _sent == [])

# 4. tell delivers to a quiescent target
reset(); _owned.add("w1"); _pane["w1"] = QUIET
relay.tell("w1", "do x")
check("tell delivers to quiescent target", bool(_sent) and _sent[-1][0] == "w1" and "do x" in _sent[-1][1])

# 5. tell attaches context as a labelled block
reset(); _owned.add("w1"); _pane["w1"] = QUIET
relay.tell("w1", "fix the bug", context="Traceback: KeyError 'x'")
check("tell attaches context block",
      "--- context ---" in _sent[-1][1] and "KeyError" in _sent[-1][1])

# 6. tell force=True overrides the quiescence guard
reset(); _owned.add("w1"); _pane["w1"] = BUSY
relay.tell("w1", "urgent", force=True)
check("tell force overrides quiescence", bool(_sent) and _sent[-1][0] == "w1")

# 7. relay refuses src == dst
reset(); _owned.add("w1"); _pane["w1"] = QUIET
check("relay refuses same src/dst", raises(lambda: relay.relay("w1", "w1")))

# 8. relay refuses when the source has produced nothing
reset(); _owned.update({"a", "b"}); _pane["a"] = ""; _pane["b"] = QUIET
check("relay refuses empty source", raises(lambda: relay.relay("a", "b")))

# 9. relay refuses a working target, and sends nothing
reset(); _owned.update({"a", "b"}); _pane["a"] = "src output"; _pane["b"] = BUSY
check("relay refuses working target", raises(lambda: relay.relay("a", "b")))
check("  -> nothing sent to working target", _sent == [])

# 10. relay delivers framed source output to a quiescent target
reset(); _owned.update({"a", "b"}); _pane["a"] = "the answer is 42"; _pane["b"] = QUIET
relay.relay("a", "b")
check("relay delivers to quiescent target", bool(_sent) and _sent[-1][0] == "b")
check("relay frames with source name + body",
      "relay from worker 'a'" in _sent[-1][1] and "the answer is 42" in _sent[-1][1])

# 11. relay refuses a non-owned source
reset(); _owned.add("b"); _pane["b"] = QUIET
check("relay refuses non-owned source", raises(lambda: relay.relay("ghost", "b")))

# 12. tell on an owned-but-DEAD child surfaces a clean RelayError, not a DriverError
#     traceback (is_owned is a registry check, so a crashed child is still 'owned').
reset(); _owned.add("w1"); _dead.add("w1")
check("tell on owned-but-dead child -> RelayError (no traceback)",
      raises(lambda: relay.tell("w1", "hi")))
check("  -> nothing sent to a dead child", _sent == [])

# 13. relay whose source died mid-flight also refuses cleanly (RelayError, not Driver)
reset(); _owned.update({"a", "b"}); _dead.add("a"); _pane["b"] = QUIET
check("relay with a dead source -> RelayError (no traceback)",
      raises(lambda: relay.relay("a", "b")))
check("  -> nothing sent when source is dead", _sent == [])

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
