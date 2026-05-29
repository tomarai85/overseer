"""Unit tests for route (intent routing) — registry passed in, relay.tell mocked.

Verifies: clear-winner routing by name/path tokens, tie -> ambiguous (no guess),
no-signal -> ambiguous, dry-run never delivers, --go delivers via relay.tell,
empty instruction refused, generic stopwords don't cause false routing.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overseer import relay, route  # noqa: E402

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


REG = {
    "helix-ios": {"cwd": "/Users/example/work/helix/ios"},
    "vault-sync": {"cwd": "/Users/example/infra/vault-sync"},
    "note-autopost": {"cwd": "/Users/example/work/note"},
}

# 1. clear winner by name/path token overlap
chosen, _ = route.classify("fix the helix ios keyboard bug", REG)
check("classify picks helix worker", chosen == "helix-ios")

# 2. winner by cwd project dir
chosen, _ = route.classify("rerun the vault sync ingest", REG)
check("classify picks vault-sync worker", chosen == "vault-sync")

# 3. no signal -> ambiguous
chosen, _ = route.classify("please do the thing now", REG)
check("no-signal -> ambiguous (None)", chosen is None)

# 4. tie -> ambiguous (don't guess)
reg_tie = {"a-note": {"cwd": "/x/note"}, "b-note": {"cwd": "/y/note"}}
chosen, _ = route.classify("update the note", reg_tie)
check("tie -> ambiguous (None)", chosen is None)

# --- mock the delivery path ---
_sent: list[tuple[str, str]] = []


def _fake_tell(name, text, context=None, force=False):
    _sent.append((name, text))
    return {"target": name, "state_before": "waiting", "chars": len(text), "forced": force}


relay.tell = _fake_tell

# 5. dry-run picks a worker but does NOT deliver
_sent.clear()
p = route.route("fix the helix ios bug", go=False, registry=REG)
check("dry-run routed=False, chose helix", p["routed"] is False and p["chosen"] == "helix-ios")
check("  -> dry-run delivered nothing", _sent == [])

# 6. --go delivers via relay.tell
_sent.clear()
p = route.route("fix the helix ios bug", go=True, registry=REG)
check("go routed=True", p.get("routed") is True)
check("  -> delivered to helix-ios", bool(_sent) and _sent[-1][0] == "helix-ios")

# 7. ambiguous instruction refuses to route
_sent.clear()
p = route.route("do the thing", go=True, registry=REG)
check("ambiguous route refuses", p["routed"] is False and "ambiguous" in p["reason"])
check("  -> ambiguous delivered nothing", _sent == [])

# 8. empty instruction refused
p = route.route("   ", go=True, registry=REG)
check("empty instruction refused", p["routed"] is False)

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
