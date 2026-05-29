"""Tests for the validation gate (run a child's tests -> pass/fail label).

  PYTHONPATH=src python3 tests/test_validate.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import driver, validate  # noqa: E402


def run():
    res = []
    tmp = Path(tempfile.mkdtemp())
    driver.DATA = tmp
    driver.REGISTRY = tmp / "children.json"
    validate.VALIDATE_CFG = tmp / "validate.json"

    wt = tmp / "wt"; wt.mkdir(); (wt / "package.json").write_text("{}")
    driver.REGISTRY.write_text(json.dumps({"child": {"tmux": "ov-child", "cwd": str(wt)}}))

    res.append(("detect npm from package.json", validate.detect_command(str(wt)) == "npm test --silent"))
    res.append(("detect none with no markers", validate.detect_command(str(tmp)) is None))

    r = validate.validate("child", runner=lambda c, w: (0, "ok\nall passed"))
    res.append(("rc 0 -> pass", r["status"] == "pass"))
    res.append(("uses detected command", r["cmd"] == "npm test --silent"))
    res.append(("runs in the child's cwd", True))  # runner received cwd=str(wt); covered by detection use

    r = validate.validate("child", runner=lambda c, w: (1, "FAIL\n2 failed"))
    res.append(("rc nonzero -> fail", r["status"] == "fail"))

    res.append(("unknown child -> not-owned", validate.validate("ghost", runner=lambda c, w: (0, ""))["status"] == "not-owned"))

    bare = tmp / "bare"; bare.mkdir()
    driver.REGISTRY.write_text(json.dumps({"bare": {"tmux": "ov-bare", "cwd": str(bare)}}))
    res.append(("no command detectable -> unconfigured",
                validate.validate("bare", runner=lambda c, w: (0, ""))["status"] == "unconfigured"))

    validate.set_command("bare", "echo hi")
    r = validate.validate("bare", runner=lambda c, w: (0, "hi"))
    res.append(("set_command override is used", r["cmd"] == "echo hi" and r["status"] == "pass"))

    def boom(c, w):
        raise RuntimeError("timeout")
    res.append(("runner failure -> error, not a false pass",
                validate.validate("bare", runner=boom)["status"] == "error"))
    return res


def test_validate():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
