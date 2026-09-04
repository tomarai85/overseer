"""parent — drive the FLEET PARENT headlessly, turn by turn.

The GUI needs clean text in / clean text out for the parent conversation, not a
raw TUI to scrape. So we run the parent with `claude -p --output-format json`:
each the operator message is one call; `--resume <session_id>` keeps the conversation (and
the parent's memory of the fleet) continuous. The parent still operates children
because `-p --dangerously-skip-permissions` executes tools (it runs the overseer
CLI via Bash). The role is injected once (first turn) via --append-system-prompt-file.

State:
  data/parent-session.json   {"session_id": "..."}   continuity handle
  data/parent-log.jsonl       one {role,text,ts} per line   so the GUI can show
                              history after a restart
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]            # ~/overseer

_HOME_TOKEN = "__OVERSEER_HOME__"


def _resolved_sandbox_settings() -> Path:
    """Write the launch-ready settings file and return it. Raises if the guard cannot be wired.

    The template carries `__OVERSEER_HOME__` so the repo holds no machine-specific path; the guard
    hook it points at only works once that is replaced with this checkout's root. Everything here
    that can go wrong raises, because the caller starts Claude with --dangerously-skip-permissions
    and the hook is the only thing standing between that and the host.
    """
    tmpl = HERE / "parent" / "sandbox-settings.json"
    if not tmpl.exists():
        raise RuntimeError(f"parent sandbox template missing: {tmpl} — refusing to launch the "
                           f"parent with skipped permissions and no Bash guard")
    guard = HERE / "hooks" / "parent-bash-guard.py"
    if not guard.exists():
        raise RuntimeError(f"parent Bash guard missing: {guard} — refusing to launch unguarded")
    out = HERE / "data" / "sandbox-resolved.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = tmpl.read_text().replace(_HOME_TOKEN, str(HERE))
    if _HOME_TOKEN in text:
        raise RuntimeError("sandbox template substitution failed — refusing to launch unguarded")
    out.write_text(text)
    return out
PARENT_MD = HERE / "parent" / "PARENT.md"
DATA = Path(os.environ.get("OVERSEER_DATA", str(HERE / "data")))
SESSION_FILE = DATA / "parent-session.json"
LOG_FILE = DATA / "parent-log.jsonl"
CLAUDE = os.environ.get("OVERSEER_CLAUDE", str(Path.home() / ".claude" / "tools" / "claude-retry-wrapper.sh"))


class ParentError(RuntimeError):
    pass


def _claude_bin() -> str:
    if os.access(CLAUDE, os.X_OK):
        return CLAUDE
    import shutil
    found = shutil.which("claude")
    if not found:
        raise ParentError("no claude binary found")
    return found


def _session_id() -> str | None:
    try:
        return json.loads(SESSION_FILE.read_text()).get("session_id")
    except (OSError, json.JSONDecodeError):
        return None


def _save_session(sid: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session_id": sid}))


def _log(role: str, text: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": role, "text": text, "ts": time.time()}) + "\n")


def history() -> list[dict]:
    """The conversation so far (for the GUI to render on open)."""
    if not LOG_FILE.exists():
        return []
    out = []
    for line in LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def reset() -> None:
    """Forget the current parent session (next send starts fresh)."""
    for p in (SESSION_FILE, LOG_FILE):
        try:
            p.unlink()
        except OSError:
            pass


def send(message: str, timeout: float = 300.0) -> dict:
    """Send the operator's message to the parent and return {response, session_id}.

    Blocking: the parent may spawn/check children, so a turn can take a while.
    The GUI calls this off the main thread and shows a spinner.
    """
    if not message.strip():
        raise ParentError("empty message")
    binp = _claude_bin()
    sid = _session_id()
    # command-bridge: the guard hook (parent-bash-guard.py, loaded via --settings)
    # blocks any Bash that isn't an `overseer ...` call, so the parent physically
    # cannot bypass the driver's ownership guards by running raw tmux/rm/etc.
    # (verified 2026-05-29: blocks tmux, allows overseer, even with skip-permissions).
    # The tracked settings file is a TEMPLATE: it names the guard hook by absolute path, and an
    # absolute path cannot be committed. Resolve it here, at launch, against this checkout.
    #
    # FAIL CLOSED. Until 2026-09-04 both launch paths did `if sandbox.exists(): add --settings`,
    # so a settings file that was present but unusable — which is exactly what an unsubstituted
    # __OVERSEER_HOME__ produces — meant Claude started with --dangerously-skip-permissions and NO
    # hook at all. The weaker the containment, the quieter the failure. If the guard cannot be
    # wired, this refuses to launch instead.
    sandbox = _resolved_sandbox_settings()
    cmd = [binp, "-p", "--output-format", "json", "--dangerously-skip-permissions",
           "--settings", str(sandbox)]
    if sid:
        cmd += ["--resume", sid]
    else:
        cmd += ["--append-system-prompt-file", str(PARENT_MD)]
    cmd.append(message)

    _log("user", message)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ParentError(f"parent timed out after {timeout}s") from e
    if r.returncode != 0:
        raise ParentError(f"parent exited {r.returncode}: {r.stderr.strip()[:200]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ParentError(f"could not parse parent json: {e}") from e
    new_sid = data.get("session_id")
    if new_sid:
        _save_session(new_sid)
    response = str(data.get("result", "")).strip()
    _log("parent", response)
    return {"response": response, "session_id": new_sid}
