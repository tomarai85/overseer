"""Tests for conversation.recent_exchange — the detail-drawer backend.

  PYTHONPATH=src python3 tests/test_conversation.py

Synthetic transcripts (no Claude, no tmux). Verifies turn extraction, injected-record
skipping, tool-step summaries, truncation, and the turns limit.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import conversation, state  # noqa: E402

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


def _write(tmp, sid, records):
    proj = tmp / "-Users-example-testproj"
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records))
    return f


def _asst_text(txt, ts="2026-05-29T00:00:00Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": txt}]}}


def _asst_blocks(blocks, tool=None):
    content = []
    for b in blocks:
        d = {"type": b}
        if b == "tool_use":
            d["name"] = tool or "Bash"
        content.append(d)
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _user(txt):
    return {"type": "user", "message": {"role": "user", "content": txt}}


def _user_toolresult():
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "content": "ok"}]}}


def _compact():
    return {"type": "user", "isCompactSummary": True,
            "message": {"role": "user", "content": "This session is being continued from a previous..."}}


NOISE = [{"type": "system"}, {"type": "file-history-snapshot"}]

tmp = Path(tempfile.mkdtemp())
state.PROJECTS_DIR = tmp

# 1. no transcript -> []
check("no transcript -> []", conversation.recent_exchange("ghost") == [])

# 2. basic: a few real turns, newest last
_write(tmp, "s1", NOISE + [_user("do X"), _asst_text("did X"), _user("now Y"), _asst_text("did Y")])
ex = conversation.recent_exchange("s1", turns=6)
check("returns real turns in order (newest last)",
      [e["role"] for e in ex] == ["user", "assistant", "user", "assistant"]
      and ex[-1]["text"] == "did Y")

# 3. turns limit keeps the LAST n
ex = conversation.recent_exchange("s1", turns=2)
check("turns limit keeps the most recent n",
      len(ex) == 2 and ex[0]["text"] == "now Y" and ex[1]["text"] == "did Y")

# 4. turns<=0 -> []
check("turns<=0 -> []", conversation.recent_exchange("s1", turns=0) == [])

# 5. injected records (compaction) are skipped
_write(tmp, "s2", NOISE + [_asst_text("real turn"), _compact()])
ex = conversation.recent_exchange("s2")
check("compaction record skipped -> last shown turn is the real one",
      len(ex) == 1 and ex[0]["text"] == "real turn")

# 6. tool steps summarized when there's no text
_write(tmp, "s3", [_asst_blocks(["text", "tool_use"], tool="Edit"), _user_toolresult(),
                   _asst_blocks(["thinking"])])
ex = conversation.recent_exchange("s3")
texts = [e["text"] for e in ex]
check("tool_use summarized with its name", "[tool: Edit]" in texts)
check("tool_result summarized", "[tool result]" in texts)
check("thinking summarized", "[thinking]" in texts)

# 7. long text truncated to the cap
_write(tmp, "s4", [_asst_text("A" * 5000)])
ex = conversation.recent_exchange("s4")
check("long turn truncated to _MAX_CHARS", len(ex[0]["text"]) == conversation._MAX_CHARS)

# 8. timestamp parsed to epoch when present
_write(tmp, "s5", [_asst_text("hi", ts="2026-05-29T00:00:00Z")])
ex = conversation.recent_exchange("s5")
check("timestamp parsed to epoch float", isinstance(ex[0]["ts"], float) and ex[0]["ts"] > 0)

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
