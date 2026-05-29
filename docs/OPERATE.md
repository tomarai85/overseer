# Operate layer — driving the fleet (not just watching it)

The dashboard half of Overseer *observes* (roster + state-truth + alerts). This is
the *operate* half: safely issuing commands to workers, routing instructions,
letting workers talk to each other, and keeping a worker moving unattended.

## The one safety boundary (everything inherits it)

**Overseer only ever drives children it spawned itself** (the `ov-`-prefixed tmux
sessions in the driver registry). It never types into, or restarts, Tom's own
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

## Tests

`tests/test_relay.py` (19) · `tests/test_route.py` (11) · `tests/test_autopilot.py`
(10) — all mock tmux/pane I/O. The operate path is also **live-verified** end to
end against real spawned workers: (1) a quiescence-gated *unforced* `tell` lands
its keystrokes on an idle child; (2) **bot-to-bot `relay`** carries one worker's
payload into another through the quiescence gate (unforced, target was `waiting`).
Per the build's "tests pass ≠ it works" rule.
