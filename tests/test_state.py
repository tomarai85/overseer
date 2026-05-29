"""Unit tests for the STATE TRUTH derivation.

Runnable two ways:
  python3 -m pytest tests/            (if pytest present)
  PYTHONPATH=src python3 tests/test_state.py   (plain, no deps)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import lease, roster, state  # noqa: E402


def _write_transcript(dirpath: Path, sid: str, records: list[dict]) -> Path:
    proj = dirpath / "-Users-example-testproj"
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records))
    return f


def _assistant(blocks: list[str]) -> dict:
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": b} for b in blocks]}}


def _user(text: str = "hi") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_text(text: str) -> dict:
    """An assistant turn whose final block is real text (for question detection)."""
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _assistant_ts(blocks: list[str], ts_epoch: float) -> dict:
    """Assistant record carrying its own activity timestamp (real transcripts do)."""
    r = _assistant(blocks)
    r["timestamp"] = _iso(ts_epoch)
    return r


NOISE = [{"type": "system"}, {"type": "mode"}, {"type": "permission-mode"},
         {"type": "file-history-snapshot"}]


def _derive(tmp: Path, sid: str, records, *, pid, raw, mtime_age=0.0, lease_rec=None):
    f = _write_transcript(tmp, sid, records)
    if mtime_age:
        old = time.time() - mtime_age
        os.utime(f, (old, old))
    state.PROJECTS_DIR = tmp  # monkeypatch
    lease.LEASE_DIR = tmp / "leases"  # isolate leases per test run
    lp = lease.lease_path(sid)
    if lease_rec is not None:
        lease.LEASE_DIR.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps(lease_rec))
    elif lp.exists():
        lp.unlink()
    lead = roster.Lead(session_id=sid, pid=pid, raw_status=raw)
    return state.derive(lead)["derived_state"]


def run(tmp: Path) -> list[tuple[str, bool, str]]:
    live = os.getpid()
    dead = 999_999_999
    cases = [
        ("working: busy + tool_use + fresh",
         _derive(tmp, "s1", NOISE + [_assistant(["thinking", "text", "tool_use"])], pid=live, raw="busy"),
         "working"),
        ("waiting: idle + assistant text turn-end",
         _derive(tmp, "s2", NOISE + [_assistant(["text"])], pid=live, raw="idle"),
         "waiting"),
        ("needs-human: idle + assistant ended on tool_use, persisted >10s",
         _derive(tmp, "s3", NOISE + [_assistant(["text", "tool_use"])], pid=live, raw="idle", mtime_age=30),
         "needs-human"),
        ("working: idle + tool_use but FRESH (<10s) = transient tail race, not needs-human",
         _derive(tmp, "s3b", NOISE + [_assistant(["text", "tool_use"])], pid=live, raw="idle"),
         "working"),
        ("working: idle but last record is user (about to respond)",
         _derive(tmp, "s4", NOISE + [_user()], pid=live, raw="idle"),
         "working"),
        ("done (not crashed): pid dead but last conv is a user tool_result",
         _derive(tmp, "s9", NOISE + [_user("tool_result")], pid=dead, raw="idle"),
         "done"),
        ("not crashed: pid is None (roster vouches it is live)",
         _derive(tmp, "s10", NOISE + [_assistant(["text"])], pid=None, raw="idle"),
         "waiting"),
        ("crashed: pid dead, last was mid-tool",
         _derive(tmp, "s5", NOISE + [_assistant(["tool_use"])], pid=dead, raw="busy"),
         "crashed"),
        ("done: pid dead, clean assistant text end",
         _derive(tmp, "s6", NOISE + [_assistant(["text"])], pid=dead, raw="idle"),
         "done"),
        ("blocked: busy but transcript stale 20min",
         _derive(tmp, "s7", NOISE + [_assistant(["tool_use"])], pid=live, raw="busy", mtime_age=1200),
         "blocked"),
        ("null status + fresh -> working",
         _derive(tmp, "s8", NOISE + [_assistant(["text"])], pid=live, raw=None),
         "waiting"),
        # ---- content-aware refinements (2026-05-29): the signals naive mtime monitors miss ----
        ("working: idle but last block is 'thinking' (mid-reasoning, NOT idle)",
         _derive(tmp, "t1", NOISE + [_assistant(["text", "thinking"])], pid=live, raw="idle", mtime_age=30),
         "working"),
        ("needs-human: assistant text ends in a question, persisted",
         _derive(tmp, "q1", NOISE + [_assistant_text("どっちがいい？")], pid=live, raw="idle", mtime_age=30),
         "needs-human"),
        ("waiting: assistant text WITHOUT a question (clean turn end)",
         _derive(tmp, "q2", NOISE + [_assistant_text("やっておきました。")], pid=live, raw="idle", mtime_age=30),
         "waiting"),
        ("blocked: record-timestamp 20min old beats a FRESH file mtime (true staleness)",
         _derive(tmp, "ts1", NOISE + [_assistant_ts(["tool_use"], time.time() - 1200)], pid=live, raw="busy"),
         "blocked"),
        ("working: record-timestamp fresh beats a STALE file mtime (noise didn't really stall it)",
         _derive(tmp, "ts2", NOISE + [_assistant_ts(["tool_use"], time.time() - 2)], pid=live, raw="busy", mtime_age=1200),
         "working"),
        # ---- v2 lease overrides heuristic ----
        ("lease fresh 'needs-human' overrides heuristic (transcript says working)",
         _derive(tmp, "l1", NOISE + [_assistant(["tool_use"])], pid=live, raw="busy",
                 lease_rec={"session_id": "l1", "phase": "needs-human", "ts": time.time()}),
         "needs-human"),
        ("lease 'tool_running' + pid dead -> crashed",
         _derive(tmp, "l2", NOISE + [_assistant(["text"])], pid=dead, raw="idle",
                 lease_rec={"session_id": "l2", "phase": "tool_running", "ts": time.time()}),
         "crashed"),
        ("stale lease ignored -> falls back to heuristic (waiting)",
         _derive(tmp, "l3", NOISE + [_assistant(["text"])], pid=live, raw="idle",
                 lease_rec={"session_id": "l3", "phase": "working", "ts": time.time() - 9999}),
         "waiting"),
    ]
    results = []
    for name, got, want in cases:
        results.append((name, got == want, f"got={got} want={want}"))
    return results


def test_state_machine(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    for name, ok, detail in run(tmp):
        assert ok, f"{name}: {detail}"


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    rows = run(tmp)
    passed = sum(1 for _, ok, _ in rows if ok)
    for name, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
