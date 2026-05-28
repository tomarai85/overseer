# Overseer

> Working name. A local **meta-orchestrator** for a solo dev running many Claude Code
> sessions at once — the "manager of managers" that nobody ships yet (see `docs/BUSINESS.md`).

You run 7-8+ Claude Code terminals, each itself an Agent-Teams team-lead with its own
sub-agents and Codex workers. The pain: you have to babysit them — if you don't watch,
they stop; when one crashes (e.g. a `thinking blocks cannot be modified` 400) nothing
restarts it; and you can't tell at a glance which pane needs you.

Overseer sits one level above all of them:

- **Roster** — reads every live session from `claude agents --json`.
- **State truth** — derives `working / waiting / needs-you / blocked / crashed` from
  three signals (pid liveness + last conversational transcript record + mtime age),
  because the raw API only gives `busy/idle`. This is the hard core (`src/overseer/state.py`).
- **One board** — a single dashboard (terminal or web) of all sessions, the ones that
  need you floated to the top.
- **Notify** — pings you *only* when a session enters `needs-you / crashed / blocked`.
- **(planned)** auto-restart of dead leads via `claude --remote-control`.

Stdlib-only Python. No install, no cloud, no deps.

## Quick start

```bash
cd ~/overseer

# one-shot board in the terminal
PYTHONPATH=src python3 -m overseer.cli status

# live web dashboard (polls every 5s) -> http://127.0.0.1:8899
PYTHONPATH=src python3 -m overseer.cli serve

# headless poll loop (logs transitions; pair with OVERSEER_NOTIFY=1 for alerts)
OVERSEER_NOTIFY=1 PYTHONPATH=src python3 -m overseer.cli daemon 5
```

## Commands

| Command | Does |
|---|---|
| `status` | scan once, print the board, exit |
| `scan`   | scan once, print state counts as JSON (for scripting) |
| `serve [port]` | background poll + web dashboard (default :8899) |
| `daemon [interval]` | foreground poll loop, logs transitions |

Env:
- `OVERSEER_NOTIFY=1` — desktop alert (osascript+afplay) on entry to needs-you/crashed/blocked
- `OVERSEER_DB=/path` — SQLite location (default `~/overseer/data/overseer.db`)

## How state truth works (the part that matters)

`claude agents --json` only tells you `busy | idle | null`. That is not enough to know
whether a session is *working*, *waiting for you*, *hung*, or *dead*. Overseer enriches
each session by reading its transcript at `~/.claude/projects/<slug>/<sessionId>.jsonl`,
skipping the noise record types (`mode`, `permission-mode`, `system`,
`file-history-snapshot`, ...) to find the **last conversational record**, and combining
that with `os.kill(pid, 0)` liveness and file mtime age. Rules live in `state.py`; they
are deliberately conservative — a false `crashed` or false `restart` is expensive.

Tested: `python3 tests/test_state.py` (8/8, stdlib runner; also pytest-compatible).

## Layout

```
src/overseer/
  roster.py   read `claude agents --json` (read-only)
  state.py    STATE TRUTH derivation (the hard core)
  store.py    SQLite roster + event log (decoupled writer/reader)
  engine.py   one poll cycle (+ transition callback)
  notify.py   needs-you/crashed alerts (macOS, gated by OVERSEER_NOTIFY)
  web.py      single-pane live dashboard (stdlib http.server)
  cli.py      status | scan | serve | daemon
tests/        state-machine unit tests
docs/         BUSINESS.md (market/moat/monetization)
.harness/     spec.md (design), progress.md (build log)
```

## Safety

This MVP is **read-only** against your real sessions. It never drives or kills a live
Claude session yet. Auto-restart (S4) will be tested against disposable sessions first.

## Status

MVP / dogfood. See `.harness/progress.md` for what's done and what's next.
Built overnight 2026-05-29 as an autonomous harness run.
