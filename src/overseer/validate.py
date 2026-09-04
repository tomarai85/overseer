"""validate — the validation gate (research #1: "validation is the bottleneck").

Before you spend attention reviewing an owned child's work, run its project's
tests/lint/build in the child's worktree and label it pass / fail. This turns the
needs-you queue into a RANKED, pre-validated review queue — "only look at work that
already passed." Anthropic's own /goal supervisor validates this direction; we do
it fleet-wide with the user's own test command.

OWNED children only (we know their cwd from the registry); the operator's own sessions are
never run. On-demand by design (`overseer validate [name]`) — running project test
commands is a side-effecting action, so it is explicit, not an unattended auto-gate.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import driver

VALIDATE_CFG = driver.DATA / "validate.json"   # {"default": "<cmd>", "<child-name>": "<cmd>"}

# auto-detect a sensible test command from what's in the worktree
_DETECT = [
    ("package.json", "npm test --silent"),
    ("pyproject.toml", "pytest -q"),
    ("pytest.ini", "pytest -q"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
    ("Makefile", "make test"),
]


def _cfg() -> dict:
    try:
        return json.loads(VALIDATE_CFG.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def set_command(key: str, cmd: str) -> None:
    c = _cfg()
    if cmd:
        c[key] = cmd
    else:
        c.pop(key, None)
    driver.DATA.mkdir(parents=True, exist_ok=True)
    tmp = VALIDATE_CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(c, indent=2))
    tmp.replace(VALIDATE_CFG)


def detect_command(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = Path(cwd)
    for marker, cmd in _DETECT:
        if (p / marker).exists():
            return cmd
    if (p / "tests").is_dir():
        return "pytest -q"
    return None


def command_for(name: str, cwd: str | None, cfg: dict | None = None) -> str | None:
    cfg = cfg if cfg is not None else _cfg()
    return cfg.get(driver._plain(name)) or cfg.get("default") or detect_command(cwd)


def validate(name: str, timeout: float = 300.0, runner=None) -> dict:
    """Run the validation command in an OWNED child's worktree; return pass/fail.
    `runner(cmd, cwd)` is injectable for tests (defaults to a real shell run)."""
    entry = driver._load_registry().get(driver._plain(name))
    if not entry:
        return {"name": name, "status": "not-owned"}
    cwd = entry.get("cwd")
    cmd = command_for(name, cwd)
    if not cmd:
        return {"name": name, "status": "unconfigured",
                "hint": f"set one: overseer validate set {driver._plain(name)} '<test cmd>'"}
    if runner is None:
        def runner(cmd, cwd):  # noqa: E306
            r = subprocess.run(["/bin/zsh", "-lc", cmd], cwd=cwd,
                               capture_output=True, text=True, timeout=timeout)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
    try:
        rc, out = runner(cmd, cwd)
    except Exception as e:  # timeout / spawn failure -> error, not a false pass
        return {"name": name, "status": "error", "cmd": cmd, "hint": str(e)[:120]}
    return {"name": name, "status": "pass" if rc == 0 else "fail",
            "cmd": cmd, "returncode": rc, "tail": "\n".join(out.splitlines()[-15:])}


def validate_all(timeout: float = 300.0) -> dict:
    rows = [validate(n, timeout=timeout) for n in driver.list_children()]
    passed = [r["name"] for r in rows if r["status"] == "pass"]
    failed = [r["name"] for r in rows if r["status"] == "fail"]
    return {"results": rows, "passed": passed, "failed": failed,
            "summary": (f"{len(passed)} pass, {len(failed)} fail" if rows else "no owned workers")}
