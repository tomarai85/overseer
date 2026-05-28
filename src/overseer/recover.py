"""recover — restart planning for dead leads. DRY-RUN by default.

S4 finding (2026-05-29, claude 2.1.154):
  - `claude remote-control` is a UI bridge (claude.ai / mobile -> local sessions),
    NOT a scripting API. `claude agents` has no attach/restart/respawn subcommand.
  - The clean native RESTART path is `claude --resume <sessionId>` (relaunches with
    context preserved). DRIVING a live session (sending it a new instruction) has no
    clean native CLI — it needs tmux send-keys or the RC bridge — so it is deferred.

This module ONLY plans restarts. Execution is intentionally gated off until the
lease/quiescence guards in docs/STATE-TRUTH.md exist; restarting the wrong or a
live session is the catastrophic case.
"""
from __future__ import annotations

from dataclasses import dataclass

# States where a restart is even conceptually valid.
RESTARTABLE_STATES = {"crashed", "done", "gone"}


@dataclass
class RestartPlan:
    session_id: str
    state: str
    restartable: bool
    reason: str
    command: list[str]

    def as_text(self) -> str:
        verdict = "RESTARTABLE" if self.restartable else "REFUSED"
        return (
            f"[{verdict}] {self.session_id[:8]}  state={self.state}\n"
            f"  reason : {self.reason}\n"
            f"  command: {' '.join(self.command)}"
        )


def resume_command(session_id: str, tmux: bool = False) -> list[str]:
    cmd = ["claude", "--resume", session_id]
    if tmux:
        cmd.append("--tmux")
    return cmd


def plan_restart(session_row: dict) -> RestartPlan:
    """Decide whether a lead may be restarted, and with what command. No execution."""
    sid = session_row["session_id"]
    state = session_row.get("derived_state", "unknown")
    cmd = resume_command(sid)
    if state in RESTARTABLE_STATES:
        return RestartPlan(
            session_id=sid, state=state, restartable=True,
            reason=f"state={state} is terminal; `claude --resume` brings it back with context",
            command=cmd,
        )
    return RestartPlan(
        session_id=sid, state=state, restartable=False,
        reason=(f"state={state} is live — refusing. Restarting a live/working session can "
                f"duplicate work or interrupt unreported context (see docs/STATE-TRUTH.md)."),
        command=cmd,
    )


# Execution is deliberately not implemented yet. When it is, it MUST:
#   - require lease ownership + immutable identity match (session_id + pid start time)
#   - require quiescence (not mid-turn, not holds_unreported_context)
#   - require explicit per-session restart_policy opt-in or human approval
# Until then, `overseer restart` is dry-run only.
