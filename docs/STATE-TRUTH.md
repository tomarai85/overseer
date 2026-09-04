# State Truth — the hard core (and its known limits)

> "The hardest risk is not process supervision. It is **state truth**: knowing whether a
> lead is blocked, thinking, waiting on permission, safely restartable, or holding
> unreported context. Get that wrong and the meta-orchestrator creates duplicate work,
> conflicting edits, and hallucinated coordination." — Codex (gpt-5.5), 2026-05-29

This is the make-or-break of the whole product. The market research found that a
multi-agent *dashboard* is solved; **durable, correct semantic state over many
already-autonomous team-leads is not.** So this file is the spec for the part that
actually matters.

## v1 (shipped MVP) — heuristic derivation

`src/overseer/state.py` derives state from three observable signals:
1. **pid liveness** — `os.kill(pid, 0)`
2. **last conversational transcript record** — last `user|assistant` record in
   `~/.claude/projects/<slug>/<sessionId>.jsonl`, skipping noise types
   (`mode`, `permission-mode`, `system`, `file-history-snapshot`, `attachment`,
   `last-prompt`, `ai-title`, `queue-operation`, `agent-name`)
3. **transcript mtime age**

It is deliberately conservative (a false `crashed`/`restart` is worse than no tool).
Hardened 2026-05-29 after Codex review (see "Known mislabels" below).

## Known mislabels (Codex review 2026-05-29) — accept for v1, fix in v2

| Risk | Cause | v1 mitigation | Residual |
|---|---|---|---|
| **False `needs-human`** | `idle + last block == tool_use` conflates a real permission/input wait with a transient tail race (tool emitted, result not yet written) | require the state to persist `> NEEDS_HUMAN_MIN_AGE` (10s) before alerting | a genuinely fast human-wait inside 10s is briefly shown as `working` |
| **False `crashed`** | `pid dead + not-clean-end` mislabeled clean exits, trailing `user` tool_result, missing transcript, `pid=None` | only `crashed` when last block is `tool_use` (mid-tool death); `pid=None` trusts the roster | a process killed exactly mid-tool-result write can still read `crashed` |
| **False `working`** | `busy+fresh` / `idle+last-user` can stick forever on queued prompts, failed turn starts, hidden permission waits | none yet | real risk; v2 phase signal needed |
| **Wrong transcript** | `find_transcript` globs all project dirs and takes newest-mtime; duplicate/stale `<sid>.jsonl` could win | exact `<sessionId>.jsonl` match (not prefix) | duplicate session files across project dirs still ambiguous |

**`mtime` is not a progress signal.** It measures file writes, not agent progress.
It lies both ways: long thinking / long tool execution writes nothing (looks stale →
false `blocked`); housekeeping/noise writes update it (looks fresh → false `working`).
v1 uses it only as weak supporting evidence with conservative thresholds.

## v2 (north star) — explicit per-session state contract (lease + heartbeat)

Codex's "single highest-leverage fix". Stop inferring state from a transcript tail;
make each managed lead **report** state through a contract Overseer owns.

A per-session **lease** record, keyed by `(session_id, pid_start_identity)`:
```
lease {
  session_id, pid, pid_start_time,      # identity that survives pid reuse
  turn_id,                              # current turn
  transcript_offset,                    # bytes consumed -> real progress delta
  phase,                               # thinking | tool_running | awaiting_permission
                                       # | awaiting_input | idle | done
  last_activity_ts,                    # heartbeat
  holds_unreported_context: bool,      # unsafe to restart if true
  restartable: bool,
}
```
- **`needs-human`** comes from an explicit `awaiting_permission` / `awaiting_input`
  phase — never from a generic `tool_use` block.
- **`working`** means the heartbeat advanced OR `transcript_offset` grew OR native
  `busy` is corroborated — not just "a file got touched".
- **`crashed`** means the *same managed process* (pid + start time) died without a
  clean lease close — not "pid number is gone" (pid reuse) and not "transcript looks odd".
- **`blocked`** means heartbeat stalled while phase is `tool_running`/`thinking` past a
  per-phase budget — distinguishing a legitimately long tool from a hang.

### How a lead reports (candidate mechanisms, to test in S4)
1. **Claude Code hooks** — a Stop/Notification/PreToolUse hook in the managed session
   writes its phase to a lease file Overseer reads. (the operator already has `stop-notify.sh` /
   `notify.sh` — extend them to write a lease instead of just a sound.) Cleanest; no
   transcript guessing.
2. **`transcript_offset` delta** — track bytes consumed per session; growth = real
   progress, regardless of mtime noise. Cheap, no cooperation needed from the lead.
3. **Native corroboration** — cross-check against `claude agents --json` status.

The transcript parser stays, but demoted to *supporting evidence*, not source of truth.

## Restart safety (for S4, before ever touching a live session)

Driving/restarting a live session via `claude --remote-control` is catastrophic if it
targets the wrong session or interrupts unreported context. Required guards:
- **target by immutable identity** (session_id + pid start time), never a mutable name
- **lease ownership** check before any drive
- **quiescence** check (not mid-turn, not `holds_unreported_context`)
- **human approval** before live restart (default), auto-restart only for sessions
  explicitly opted into a restart policy
- test exclusively against **disposable** sessions until proven
