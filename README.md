# Overseer

[![tests](https://github.com/tomarai85/overseer/actions/workflows/tests.yml/badge.svg)](https://github.com/tomarai85/overseer/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local **fleet cockpit** for a solo developer running many AI coding agents at once.
You talk to **one** parent AI; it spawns and drives child Claude Code workers for you —
instead of babysitting 7-8 terminals by hand.

Native macOS app. Stdlib-only Python core. No cloud, no account, no dependencies.

---

## The problem

You run many Claude Code sessions in parallel. Each is capable, but:

- they **stop and wait** for input — and you don't notice which one needs you;
- when one **crashes** (e.g. a `thinking blocks cannot be modified` 400) nothing restarts it;
- a session can **silently hang** or quietly burn tokens while *looking* busy;
- the bottleneck stops being the AI's speed and becomes **your attention**.

Knowing *which* of N agents is waiting / stuck / dead — and operating them from one
place — is the whole game. (See `docs/BUSINESS.md` for the honest market read: the
"manager of agents" space is now crowded; the defensible edge is **state-truth**, not
"show me terminals in a grid.")

## What it is

| Layer | Role | Where |
|---|---|---|
| **Eyes** — state-truth | Derive the TRUE state (`working / waiting / needs-you / blocked / stuck / crashed`) from each session's transcript *content* + pid + record timestamps — not the raw `busy/idle` that naive monitors mislabel (a long *think* is not *idle*). | `src/overseer/state.py` |
| **Hands** — driver | Spawn child workers in tmux and drive them from outside (`tmux send-keys`); read their screen back. | `src/overseer/driver.py` |
| **Brain** — parent | One Claude session given a "fleet manager" role, driven headlessly turn-by-turn (`claude -p --resume`). You talk to it; it operates the children. | `src/overseer/parent.py` |
| **Supervisor** | Unattended watch over owned children: per-child heartbeat, derived state, away-mode summary, alerts. | `src/overseer/supervisor.py` |
| **Cockpit** — GUI | One native window: parent chat ⟷ each worker's live screen, fleet status, "+ new worker", notifications. | `app/` (Swift) |

The key thing to understand: **the parent and children are not linked by magic.** The
parent just runs the `overseer` CLI (`spawn` / `send` / `read`), which drives children
via tmux — exactly what a human would type, automated. No special API, no AI-to-AI
protocol. That is why it works on stock Claude Code.

## Quick start

```bash
cd ~/overseer

# 1) See your whole fleet's true state in the terminal
./bin/overseer status

# 2) Build the native app and open it (double-click cockpit)
./bin/build-app.sh && open dist/Overseer.app

# 3) Or talk to the fleet parent straight from the terminal
./bin/overseer-parent
#   then: "start a worker in ~/myrepo and run the tests"  /  "check on it"  /  "OK"
```

In the app: left = the parent chat + your workers (with live state) + the monitored
fleet; the right pane switches between the parent conversation and a selected worker's
screen. You type to the parent; it spawns / sends / reads workers and reports back.
Notifications fire only when something needs you (toggle with the bell).

## Safety boundary (enforced, not promised)

Overseer **only ever drives children it spawned itself** (an internal registry of
`ov-`-prefixed tmux sessions). `send` / `kill` **refuse** any target not in that registry,
and every `spawn` / `send` / `kill` is appended to `data/driver-audit.log`. So even a
drifted or prompt-injected parent **cannot** type into your own hand-started terminals —
the boundary lives in the CLI, not in a model instruction.

## Commands

```
# monitor
overseer status         one-shot board (terminal)
overseer serve [port]   live web board (default :8899)
overseer leads          live sessions as JSON (used by the GUI)
overseer supervise      away-mode summary over owned children (JSON)
overseer doctor         environment self-check
overseer demo [port]    web board seeded with every state (synthetic)

# the moat / wedge (work on ALL sessions, incl. your own)
overseer progress       liveness TRUTH: progressing / stalled (silent hang) / spiraling, via token-delta
overseer usage          token usage per session + fleet total (spot the heavy worker)
overseer budget [set <k> <n> | fleet | enforce [--go]]   per-session + fleet-wide token caps + auto-pause
overseer validate [name | set <name> <cmd>]   run a child's tests -> pass/fail (the validation gate)
overseer alerts         unified "truly needs you" feed (stalled + spiraling + over-budget)
overseer guard [secs]   always-on watcher: notify the moment a session needs you (24/7 via launchd)

# drive owned children (ov- only; never your own sessions)
overseer spawn <name> [cwd]    start a child worker (waits until ready)
overseer send  <name> <text>   instruct a child
overseer tell  <name> <text>   like send, but only if the child is idle (won't interrupt)
overseer route "<instruction>" --go   auto-pick the right owned worker (refuses if ambiguous)
overseer relay <src> <dst>     pipe one child's output into another
overseer autopilot <name> "<continue>" --max N   nudge an idle child to continue; stops when it needs you
overseer read  <name> [lines]  read a child's screen
overseer children              list owned children (JSON)
overseer kill  <name>          stop a child
overseer restart [prefix]      dry-run restart planner

# the parent (talk to it; it operates children — sandboxed to overseer-only via the guard hook)
overseer parent-send <text>    one headless turn with the fleet parent (JSON)
overseer parent-history | parent-reset
```

Env: `OVERSEER_NOTIFY=1` (alert on entry to needs-you/crashed/blocked),
`OVERSEER_DB=/path` (SQLite location), `OVERSEER_DATA=/path` (registry/heartbeat/audit).

## State truth (the part that matters)

`claude agents --json` only gives `busy | idle | null` — not enough to know whether a
session is working, waiting for you, hung, or dead. Overseer reads each session's
transcript (`~/.claude/projects/<slug>/<sessionId>.jsonl`), skips the noise record types,
and reads the **content** of the last conversational record:

- a last block of `thinking` ⇒ **working** (the case naive mtime monitors get wrong);
- an assistant `tool_use` with no following `tool_result` ⇒ a pending tool — fresh =
  working, aged = blocked/hung, idle = waiting on you;
- assistant `text` ending in a question ⇒ **needs-you**;
- activity age comes from the record's own timestamp, not the file mtime (housekeeping
  writes bump mtime but never the conversational timestamp).

Conservative by design — a false `crashed`/`restart` is expensive. The fully trustworthy
signal is the opt-in per-session lease/heartbeat hook (`docs/STATE-TRUTH.md`,
`hooks/overseer-lease.py`); when a fresh lease exists, Overseer trusts it over the
heuristic. Overseer never edits your `~/.claude/settings.json` — the hook is opt-in.

## Project layout

```
src/overseer/   roster, state (truth), store (sqlite), engine, driver, parent,
                supervisor, lease (v2 hook), notify, recover, web, cli
app/            Swift cockpit (Cockpit.swift + main.swift) + Info.plist + tools/make-icon.swift
bin/            overseer (CLI launcher), overseer-parent, build-app.sh
hooks/          overseer-lease.py (opt-in per-session heartbeat)
docs/           BUSINESS.md (market read), STATE-TRUTH.md (the moat + v2 design), ROADMAP.md
tests/          stdlib tests (state / core / engine / driver / supervisor)
```

## Tests

```bash
for t in state core engine driver supervisor; do PYTHONPATH=src python3 tests/test_$t.py; done
```

## Status

Working tool, dogfood stage. The state-truth heuristic is good; the fully trustworthy
signal is the opt-in lease hook. Distribution (a notarized `.app`) and any
productization are deliberate later steps. Honest competitive read in `docs/BUSINESS.md`.
