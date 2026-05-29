# Overseer — monetization strategy (2026-05-29, from 2 market-research passes)

Synthesis of two adversarial research streams (competitor/pricing + moat/reliability).
Honest, sourced. TL;DR: the category is crowded and mostly free, but the exact
intersection Overseer sits on is the one monetizable, defensible gap.

## The wedge (lead with this, not "another agent monitor")

> **Get pinged only when a specific agent TRULY needs you — with live cost/token
> guardrails that stop runaway spend.**

Both research passes converged on this. It works because:
- **Demand is documented + unresolved upstream.** Open Anthropic issues for the
  notification problem (#11665, #12048, #13024, #36885 — "no way to know which of
  many sessions is blocked"). Cost-runaway horror stories ($8k–$47k from unattended
  subagents). The "babysitting / 3-agent attention ceiling" is a named, recurring pain.
- **The incumbent deliberately doesn't do it.** Anthropic shipped **Agent View** (May
  2026): six lifecycle states, process-liveness icons, `claude agents --json`. But it
  has **no notifications, no per-session token/cost, no budget enforcement, no parent
  orchestrator AI** — and closed every watchdog/heartbeat/timeout/per-session-cost
  request as **"not planned"** (#37521, #28482, #49150, #23250, #26171).
- **It attaches to money.** Post-2026-06-15 billing changes + runaway-cost fear make
  per-session cost visibility a willingness-to-pay trigger (unlike "nicer monitor").

## The moat (why it's defensible, not just first)

**Liveness/progress TRUTH** — distinguishing a *truly progressing* agent from one that
just *looks busy* (process alive, **token count frozen** = silent hang). Agent View's
"Working" means "process alive"; it never exposes token-delta-over-time, which is the
signal the community converged on. Overseer derives `progressing / stalled / spiraling
/ idle` from token-delta (`overseer progress`). Everything else — auto-restart, cost-
runaway kill, loop detection, attention routing — is a downstream application of this
one engine, so the features reinforce rather than dilute the moat.

## What Overseer already has (maps onto the wedge)

| Wedge/moat element | Status in Overseer |
|---|---|
| True-state monitoring (content-aware, not busy/idle) | ✅ `state.py` |
| **Progress-truth: silent-hang/runaway via token-delta** (THE moat) | ✅ `progress.py` (`overseer progress`) |
| Notifications only on needs-you/crashed/blocked (debounced) | ✅ in-app + always-on daemon |
| Per-session token usage visibility | ✅ `usage.py` (`overseer usage`) |
| Parent orchestrator you talk to | ✅ `parent.py` + the cockpit |
| Safety boundary + fleet audit log | ✅ driver registry + `driver-audit.log` |
| Works on your OWN sessions, not just spawned ones | ✅ (reads transcripts) |

## Feature priorities (research-ranked)

**P0 — the wedge bundle (mostly DONE; finish the cost half):**
1. Smart needs-you notification router — ✅ (add reply-from-notification + Slack/push later).
2. **Per-session cost/limit cockpit with guardrails** — PARTIAL: have token usage +
   spiral detection; ADD: per-session token/$ cap with auto-pause/kill on breach, and
   "about to blow your weekly limit" projection. (The one piece no free tool does.)
3. Parent orchestrator — ✅ (deepen: scoped briefs + acceptance/kill criteria).

**P1 — defensible expansion:** cross-tool fleet (Claude + Codex + Gemini in one truth-
pane); safety-bounded autonomy (budget caps, scope freeze, auto-pause); review
compression (cross-fleet prioritized diff/test-gate queue).

**P2 — retention:** cross-machine/mobile glance (omnara-style, anchored to the Mac
cockpit — relevant for Tom's Mac-mini-over-SSH setup; note the still-open headless
remote-control gap #30447); crash/stuck auto-recovery + session restore.

## Pricing / packaging

A pure monitor can't charge (bundled free in Claude Code). Charge for **cost governance
+ notification routing + orchestration**:

- **Free:** monitor + true-state + desktop notifications, ~3 concurrent agents, Claude only.
- **Pro — $12–15/mo or $99 one-time** (JetBrains-style perpetual-fallback after 12mo for
  the privacy/offline native-Mac crowd): unlimited agents, cost/limit cockpit + budget
  guardrails, parent orchestrator, cross-tool, Slack/push routing. Anchor above omnara's
  $9 by adding cost-governance ("one prevented $47k runaway pays for 250 years").
- **Team — $20–29/seat/mo:** shared fleet view, cross-dev cost attribution, session
  handoff, SSO. Where recurring revenue lives (Nimbalyst's Team tier is still unpriced).

## Honest risks

- Red ocean: Conductor, Nimbalyst, cmux, CodeAgentSwarm, Vibe Kanban (sunsetting),
  Warp Oz, Cursor, GitHub Agent HQ, omnara. Most are free/bundled.
- Anthropic owns the runtime and ships weekly; if they reverse course on watchdog/cost,
  the moat erodes. The window is that they've explicitly declined it.
- A standalone monitor is commoditized; the defensible product is the **cost-aware
  notification + progress-truth + orchestrator** fusion — which is exactly Overseer.

## Next build (to fully own the wedge)

1. Per-session token/$ budget + auto-pause on breach (completes P0 #2 — the money hook).
2. Wire `progress` stalled/spiraling into the notifications + the cockpit (surface the moat).
3. Reply-from-notification.
Then P1 (cross-tool adapters) as the expansion moat.

Sources: see the two research transcripts; key — Agent View / costs / security docs at
code.claude.com, claude-code GitHub issues (#37521/#28482/#49150/#23250/#36885/#11665/
#26171/#30447), CloudZero cost writeups, Addy Osmani "agents need a manager", omnara pricing.

## Feature roadmap v2 (research round 2, mid-2026)

Competitive update: Conductor (free/BYO), Cursor 3.0 "Agents Window", Claude /goal
+ supervisor, AgentsRoom (mobile), Nimbalyst. Monitoring/notifications/worktree-spin
are now contested → defensible value moved to the AFTER-run (review/validate/integrate)
and DELEGATE-in / unattended layers.

Ranked NEW features (beyond what we have):
1. [SHIPPED] Validation gate — auto-run a child's tests/lint/build, label pass/fail
   so the needs-you queue is pre-validated ("only look at work that passed"). The #1
   stated pain ("validation is the bottleneck"). `overseer validate`.
2. Integration / merge-queue + worktree GC — serialized rebase-on-main → re-validate →
   merge/flag; prune orphaned worktrees. The merge tax that makes parallelism net-neg
   past ~3 agents. Effort M-L.
3. Ticket-in delegation — Linear/GitHub issue tagged → spawn sandboxed worker in a
   worktree with the issue as spec → PR back. (Cursor proved it; open Anthropic
   FR #12925.) Turns the cockpit from dashboard into a delegation surface. Effort M.
4. Fleet-wide HARD spend cap + auto-kill — aggregate ledger across all sessions, daily/
   monthly burn-down vs the (post-June-15 split) credit pools, auto-pause worst offender.
   Cheapest "paid for itself" story ($8k–$47k runaway cases). Effort S-M. Extends budget.py.
5. Cross-model routing — Opus builds, cheaper model/Codex reviews; per-session model tag. M.
6. Unattended overnight queue + auto-resume after rate-limit reset (4 open GH issues). M.
7. (low) Batch/remote permission approval — mostly covered by needs-you + sandbox. S.

WTP (anecdotal, directional): "$200/mo without thinking" pro buyers exist; individuals
resist before a project proves out (→ free entry tier). Comparable: Intent $20→$200/mo;
Conductor free. Suggested tiers: Free monitoring · $20–40/mo Pro (validation gate +
merge queue + fleet cost-cap) · $100–200/mo (ticket-in delegation + multi-model + unattended).
Pricing landmine: June 15 2026 interactive/programmatic credit-pool split — price the
unattended tier carefully.

Build order (next): #4 fleet hard-cap (quick, extends budget) → #2 merge-queue/GC → #3 ticket-in.
