"""Tests for driver: tmux spawn/send/read mechanics.

  PYTHONPATH=src python3 tests/test_driver.py

Fast unit tests stub tmux (no subprocess). The live end-to-end test actually
spawns a Claude child and is gated behind OVERSEER_LIVE_TEST=1 (costs tokens).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overseer import driver  # noqa: E402


def run():
    res = []

    # isolate the owned-children registry + audit log to a temp dir
    import json as _json
    import tempfile
    _tmp = Path(tempfile.mkdtemp())
    driver.DATA = _tmp
    driver.REGISTRY = _tmp / "children.json"
    driver.AUDIT = _tmp / "audit.log"
    driver.REGISTRY.write_text(_json.dumps({"worker1": {"tmux": "ov-worker1"}}))  # owned, for send tests

    # --- session_name always ends up ov-prefixed, exactly once ---
    res.append(("name gets prefix", driver.session_name("foo") == "ov-foo"))
    res.append(("prefixed name unchanged", driver.session_name("ov-foo") == "ov-foo"))

    # --- record every tmux call so we can assert on the command shape ---
    calls = []

    def fake_tmux(*args, check=True):
        calls.append(list(args))
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    orig_tmux, orig_exists = driver._tmux, driver.exists
    driver._tmux = fake_tmux
    try:
        # send: literal text THEN a separate Enter (submission is deliberate)
        driver.exists = lambda name: True
        calls.clear()
        driver.send("worker1", "do the thing")
        literal = [c for c in calls if "-l" in c]
        enters = [c for c in calls if c[-1] == "Enter"]
        res.append(("send uses -l literal", literal and literal[0][-1] == "do the thing"))
        res.append(("send submits with separate Enter", len(enters) == 1))
        res.append(("send targets ov-prefixed session",
                    all("ov-worker1" in c for c in calls)))

        # send refuses empty text (no accidental bare Enter)
        try:
            driver.send("worker1", "")
            res.append(("send refuses empty", False))
        except driver.DriverError:
            res.append(("send refuses empty", True))

        # fail-closed boundary: refuse a target Overseer does not own (Codex #2)
        try:
            driver.send("not_owned_xyz", "hi")
            res.append(("send refuses UNOWNED target (boundary enforced in CLI)", False))
        except driver.DriverError:
            res.append(("send refuses UNOWNED target (boundary enforced in CLI)", True))

        # spawn refuses an existing child (no silent clobber)
        driver.exists = lambda name: True
        try:
            driver.spawn("dup")
            res.append(("spawn refuses duplicate", False))
        except driver.DriverError:
            res.append(("spawn refuses duplicate", True))

        # spawn on a free name: new-session with -c <cwd> (NO shell `cd {cwd} &&`), launch claude
        driver.exists = lambda name: False
        calls.clear()
        sn = driver.spawn("fresh", cwd="/tmp")
        res.append(("spawn returns ov name", sn == "ov-fresh"))
        res.append(("spawn creates session", any(c[0] == "new-session" for c in calls)))
        res.append(("spawn sets cwd via -c flag (no shell cd interpolation)",
                    any("-c" in c and "/tmp" in c for c in calls)
                    and not any("cd /tmp &&" in " ".join(c) for c in calls)))
        res.append(("spawn registers the child as owned", driver.is_owned("fresh")))

        # spawn rejects an unsafe name (shell/path metacharacters)
        driver.exists = lambda name: False
        try:
            driver.spawn("bad;rm -rf", cwd="/tmp")
            res.append(("spawn rejects unsafe name", False))
        except driver.DriverError:
            res.append(("spawn rejects unsafe name", True))

        # kill REFUSES an unowned target (the boundary hole Codex caught: kill had no guard)
        driver.exists = lambda name: True
        try:
            driver.kill("zzz_not_owned")
            res.append(("kill refuses unowned target", False))
        except driver.DriverError:
            res.append(("kill refuses unowned target", True))
        # kill on an OWNED child proceeds
        calls.clear()
        driver.kill("worker1")
        res.append(("kill on owned touches ov-worker1", any("ov-worker1" in c for c in calls)))

        # read also refuses an unowned target
        try:
            driver.read("zzz_not_owned")
            res.append(("read refuses unowned target", False))
        except driver.DriverError:
            res.append(("read refuses unowned target", True))
    finally:
        driver._tmux, driver.exists = orig_tmux, orig_exists

    # --- registry writes are atomic: a torn children.json would orphan every owned
    #     child (corrupt -> _load_registry returns {} -> is_owned False for all) ---
    driver._save_registry({"helix": {"cwd": "/x"}, "note": {"cwd": "/y"}})
    res.append(("registry roundtrips", driver._load_registry() == {"helix": {"cwd": "/x"}, "note": {"cwd": "/y"}}))
    res.append(("no leftover .tmp after save", list(_tmp.glob("*.tmp")) == []))
    # a pre-existing temp file must not corrupt the next save (rename overwrites it)
    (_tmp / "children.json.tmp").write_text("garbage{")
    driver._save_registry({"only": {}})
    res.append(("stale .tmp does not survive a save", driver._load_registry() == {"only": {}}))

    # --- activity: merge + ts-sort the audit logs (the GUI's live 'what is the
    #     parent doing' ticker). Must survive a malformed line and a missing log. ---
    import contextlib
    import io
    from overseer import cli
    driver.AUDIT.write_text('{"ts": 2, "action": "send", "target": "b"}\nnot json\n')
    (_tmp / "relay-audit.log").write_text('{"ts": 1, "action": "relay", "target": "a"}\n')
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        cli.cmd_activity(["10"])
    _acts = _json.loads(_buf.getvalue())
    res.append(("activity merges audit logs (skips malformed lines)", len(_acts) == 2))
    res.append(("activity sorts by ts ascending", [a["action"] for a in _acts] == ["relay", "send"]))

    return res


def run_live():
    """End-to-end: spawn a real Claude child, drive it, read the answer, kill.

    Gated: set OVERSEER_LIVE_TEST=1 to run (spawns a real session, uses tokens).
    """
    res = []
    name = "selftest"
    driver._tmux("kill-session", "-t", driver.session_name(name), check=False)  # raw cleanup (kill() now requires ownership)
    driver.spawn(name, cwd=str(Path.home() / "overseer"))
    ready = driver.wait_ready(name, timeout=45)
    res.append(("live child boots to prompt", ready))
    if ready:
        driver.send(name, "2 たす 2 は？答えは数字だけ、一言で。")
        import time
        deadline = time.time() + 30
        answered = False
        while time.time() < deadline:
            if "4" in driver.read(name):
                answered = True
                break
            time.sleep(1.0)
        res.append(("live child executes injected prompt (answers 4)", answered))
    driver.kill(name)
    return res


def test_driver():
    for name, ok in run():
        assert ok, name


if __name__ == "__main__":
    rows = run()
    if os.environ.get("OVERSEER_LIVE_TEST") == "1":
        print("  (running LIVE end-to-end test — spawns a real Claude child)")
        rows += run_live()
    passed = sum(1 for _, ok in rows if ok)
    for name, ok in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(rows)} passed")
    sys.exit(0 if passed == len(rows) else 1)
