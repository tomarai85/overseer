"""Tests for token usage summing + fleet aggregation.

  PYTHONPATH=src python3 tests/test_usage.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import roster, state, usage  # noqa: E402


def _write(tmp: Path, sid: str, records: list[dict]) -> None:
    proj = tmp / "-Users-example-testproj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in records))


def _asst(inp: int, out: int, cr: int = 0, cw: int = 0) -> dict:
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text"}],
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def run():
    res = []
    tmp = Path(tempfile.mkdtemp())
    state.PROJECTS_DIR = tmp

    _write(tmp, "s1", [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _asst(10, 100, 5, 2),
        _asst(20, 200, 3, 0),
        {"type": "mode"},   # noise, no usage -> ignored
    ])
    u = usage.session_usage("s1")
    res.append(("sums input tokens", u["input"] == 30))
    res.append(("sums output tokens", u["output"] == 300))
    res.append(("sums cache_read", u["cache_read"] == 8))
    res.append(("sums cache_write", u["cache_write"] == 2))
    res.append(("counts assistant messages", u["messages"] == 2))
    res.append(("missing transcript -> None", usage.session_usage("nope") is None))

    _write(tmp, "s2", [_asst(5, 5000)])   # a heavy-output worker
    leads = [roster.Lead(session_id="s1"), roster.Lead(session_id="s2")]
    fu = usage.fleet_usage(leads=leads)
    res.append(("fleet total output", fu["total"]["output"] == 5300))
    res.append(("fleet total messages", fu["total"]["messages"] == 3))
    res.append(("heaviest output sorted first", fu["sessions"][0]["session_id"] == "s2"))
    return res


def test_usage():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
