# Operate layer — driving the fleet (not just watching it)

The dashboard half of Overseer *observes* (roster + state-truth + alerts). This is
the *operate* half: safely issuing commands to workers, routing instructions,
letting workers talk to each other, and keeping a worker moving unattended.

## The one safety boundary (everything inherits it)

**Overseer only ever drives children it spawned itself** (the `ov-`-prefixed tmux
sessions in the driver registry). It never types into, or restarts, the operator's own
hand-started sessions. This is enforced in code (`driver.is_owned` — `send`/`kill`
refuse anything unowned, and every action is appended to an audit log), not by
prompting a model to behave. Plus a **quiescence gate**: a command is only typed
into a child that is *idle at its prompt*; a child that is mid-work (a tool /
generation running) is left alone unless `--force` is passed.

## Verbs (`~/overseer/bin/overseer …`)

| verb | what | safety |
|---|---|---|
| `spawn <name> [cwd]` | launch a new owned worker in tmux | registers it as owned |
| `read <name>` | print the worker's current screen | read-only |
| `tell <name> "<cmd>" [--force]` | issue a command — **only if the worker is idle** | owned + quiescence |
| `route "<instruction>" [--go]` | pick the right owned worker for an instruction and deliver it | refuses if ambiguous (no mis-route) |
| `relay <src> <dst> [--force]` | pipe one owned worker's latest output into another (bot-to-bot) | both owned, target quiescent |
| `autopilot <name> "<continue>" [--every N] [--max K]` | re-nudge an idle worker to continue a long task | stops on needs-human; bounded by `--max` |
| `kill <name>` | stop a worker | owned only |

`tell` is the safe default over the raw `send` (which always types, even mid-work).

## Quiescence (how "is it safe to type now?" is decided)

`relay.pane_state(name)` reads the worker's pane **structure** (not a content
hash): `esc to interrupt` / `✻` / `Cogitating` ⇒ **working**; a permission prompt's
markers ⇒ **needs-human**; otherwise the idle `❯` chevron ⇒ **waiting** (safe).
This was hardened after a live test showed a content-hash heartbeat reads an idle
claude as "working" forever (its TUI animates). See
`.harness/feedback/finding-quiescence-idle-reads-working-2026-05-29.md` — the same
fix is recommended for `supervisor.child_state` (the dashboard path).

## Examples

```
overseer spawn helix ~/work/helix/ios       # start a worker
overseer tell helix "run the test suite and fix the first failure"
overseer route "rerun the vault sync ingest" --go   # don't name the worker — routed
overseer relay builder reviewer               # reviewer reads builder's latest output
overseer autopilot helix "continue; if blocked, summarize and stop" --max 5
```

## From the dashboard (GUI, not just the CLI)

`overseer serve` now *drives* the fleet, not just watches it — the three pains
above are solvable from the browser:

- an **OPERATE panel** under the board: a *command* box (pick an owned worker,
  type a command + optional context, Send), a *route* box (type an instruction,
  Overseer picks the worker), and a *relay* src->dst control. Every button posts to
  a guarded endpoint (`/api/tell`, `/api/route`, `/api/relay`) that inherits the
  exact same boundary as the CLI: owned-only, target must be quiescent unless you
  tick force, every action audited. A refusal (not owned / not idle / ambiguous
  route) comes back as a readable reason, not a failure.
- `/api/children` lists only OWNED workers + each one's live quiescence, so the
  target dropdowns can't even offer one of the operator's own sessions.

The board also surfaces the **moat overlay** (`/api/insight`): a "truly needs you"
alerts strip (stalled / spiraling / over-budget, with the why) above the board, and
per-worker token burn + progress verdict + budget % on each row. `build_insight()`
runs each aggregator once per poll — it must NOT call `alerts.attention()` on top of
`progress.fleet_progress()`, because progress mutates its snapshot (consuming the
token delta), so a second pass corrupts the verdict; the alert rules are rebuilt
inline and kept in sync with `alerts.attention` by a drift-guard test.

The server stays bound to `127.0.0.1` — this is a local-only action surface.

## Tests & live verification

`tests/test_relay.py` (20) · `tests/test_route.py` (11) · `tests/test_autopilot.py`
(12) — all mock tmux/pane I/O.

**Live-verified** end to end against real spawned workers (the build's "tests pass ≠
it works" rule — which earned its keep here, catching three real bugs the mocked
tests missed):
1. a quiescence-gated *unforced* `tell` lands its keystrokes on an idle child;
2. **bot-to-bot `relay`** carries one worker's payload into another through the
   quiescence gate (unforced, target was `waiting`);
3. **multi-line** payloads arrive as ONE message (bracketed paste) instead of
   fragmenting at the first newline;
4. an idle child reads `waiting` (not `working`) in BOTH `relay.pane_state` and the
   dashboard's `supervisor.child_state`, including a fresh spawn whose welcome box
   pushes the `❯` above the status footer;
5. `autopilot` re-nudges an idle worker each time it goes quiescent and stops at the
   cap (live: two nudges, then `stopped: max_nudges`).
6. the **GUI operate path** end to end: a real spawned owned child, driven via
   `POST /api/tell` through the live dashboard server — the command landed in the
   real tmux pane and the relay audit recorded the owned+quiescent delivery.
7. **`/api/insight`** served the real fleet (7 sessions, real token counts, correct
   progressing/idle verdicts), and `build_insight`'s inline alerts match
   `alerts.attention` (drift guard).

Hardened against an Opus adversarial review (ownership boundary sound; fixed: the
multi-line submit bug, marker-as-data via region-scanned working/permission markers,
the inter-step false-`waiting` gap via a double-sample, and an autopilot `max_runtime`
cap + fail-safe loop).
