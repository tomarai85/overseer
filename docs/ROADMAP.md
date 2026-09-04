# Roadmap — what's built, what's next, what's gated

## Built (overnight 2026-05-29, MVP)
- **Observe**: roster (`claude agents --json`) + state-truth (working/waiting/needs-you/
  blocked/crashed) + one board (terminal + web) + needs-you-only notify.
- **State-truth v2 (moat)**: opt-in hook lets sessions REPORT phase; trusted over the
  heuristic. (`docs/STATE-TRUTH.md`)
- **Recover (dry-run)**: plan `claude --resume` restarts; refuse live sessions.
- 27 unit tests, `doctor` self-check, launchd template.

## Next layer — S5: intent routing (DESIGN ONLY; not built)

The "manager" half of "manager of managers": the operator says *"fix the auth bug"* to one place,
and Overseer routes it to the right team-lead.

**Why it's deferred, honestly:** routing is only useful once we can (a) reliably know each
lead's *domain/ownership* and (b) actually *deliver* an instruction into a live session.
(b) has no clean native CLI today (remote-control is a UI bridge; see `recover.py`), so
routing would be a button that can't push. Build the foundations first.

**Design when foundations exist:**
1. **Per-lead domain registry** — today `cwd` is always `$HOME`, so domain is inferred
   from the transcript's project slug. Better: a `projects.yaml` the operator maintains
   (`lead name -> domain -> repo/path -> owns: [modules/keywords]`), or derive ownership
   from each lead's recent file edits (read transcript tool_use targets).
2. **Classifier** — match an instruction to a lead by domain keywords + recent activity.
   Start dumb (keyword/path overlap), not an LLM call, to keep it cheap and debuggable.
   Ambiguous -> ask the operator (don't guess and mis-route).
3. **Delivery** — depends on the drive mechanism (S4 follow-on): tmux send-keys if the
   lead runs under tmux (`claude --tmux`), else the remote-control bridge. Gated on a
   quiescence + identity check (`docs/STATE-TRUTH.md` restart-safety section).
4. **Question relay** — when a lead hits `needs-human`, surface its question to the operator in the
   one board with a reply box that routes the answer back down. Same delivery dependency.

## Gating order (do not skip)
1. Dogfood the MVP + install the lease hook -> prove state-truth holds on the real fleet.
2. Build a safe *drive* path (tmux send-keys to a disposable session first).
3. Then `restart` execution (lease + quiescence guards).
4. Then S5 routing + question relay.

## Explicitly NOT doing (avoid scope creep)
- A new "AI agent OS" from scratch (the documented mistake — stay thin on Claude primitives).
- Cloud/multi-user. This is a solo, local tool until it earns more.
- Fancy UI before state-truth is trustworthy (the operator: UI is secondary).
