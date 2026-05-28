# Overseer — Business Memo

> Placeholder name. Sober assessment for a skeptical technical founder, not a pitch.
> Sources: `~/.claude/docs/orchestrator-research-2026-05-29.md` (two-pass market research), `~/overseer/.harness/spec.md` (product def). Drafted 2026-05-29.

## 1. The problem & who has it

The pain is real and widely felt, but the specific niche is empty. Across HN there is a front-page "Ask HN: agent orchestrator" thread roughly monthly from late 2025 into 2026 (e.g. item 46993479), and independent builders keep scratch-building the same thing in isolation (ChatML, Seshions, Bridge ACE, AgenC, claude_code_agent_farm) — multi-agent tooling inquiries are up ~1,445% per Scopir. Both Anthropic (Agent View) and GitHub (Agent HQ) shipping mission-control products confirms the category is mainstreaming. But the cohort that hurts most is small: most operators cap out at 3-5 parallel agents on cognitive load alone, and the bottleneck they report is not generation — it is review and coordination (5 agents finishing at once = 5 PRs to review, plus silent corruption from shared DB/browser/file state, and 5-10x cost that's hard to see). Running 7-8+ sessions where each is *already* an Agent-Teams team-lead puts you in a power-user ("agentmaxxer") band that almost nobody is in. The only genuine hierarchical example shipping today is Gas Town (Steve Yegge), where a "Mayor" supervises 20-30 workers — and it fails for everyone who tries it because it runs too fast to comprehend and merges out of control. The concept is right; the UX is raw. GitHub issue #58608 (8 sessions silently wiped by an update) is the rawest version of the "if it dies, restart it" pain this product targets.

## 2. Competitive landscape

| Tool | What it is | Why it's NOT the solo manager-of-managers |
|---|---|---|
| Claude Agent View (Anthropic) | Native roster, background sessions, `claude agents --json`, routines/cron | Flat session list, not hierarchical; no derived state-truth, no manager-of-team-leads layer. Platform owner — encroaching, not a peer. |
| amux (mixpeek) | tmux fleet backbone, REST peer mesh, SQLite state, self-healing watchdog | Peer-mesh, not hierarchy; no semantic state-truth over team-leads. Closest on plumbing, wrong topology. |
| Cogpit (OSS) | Reads Claude/Codex JSONL → dashboard + messaging, tracks subagents/teams | Observation layer only, flat. Best thing to try *today* for visibility — but it watches, it doesn't manage or restart. |
| OctoAlly (OSS) | tmux-backed Claude/Codex session grid | Flat grid, observation only. No state derivation, no recovery. |
| Conductor | Mac-native Claude Code multitasking | Single-machine multitask UI, flat; not a meta-control plane over autonomous leads. |
| Crystl | macOS-native Claude Code multitasking terminal | Same class as Conductor; Codex support early. Flat, no manager layer. |
| claude-squad (smtg-ai, ~8k★) | TUI managing multiple Claude/Codex in worktree isolation | Strong isolation, but monitoring is thin; flat list, no state-truth/needs-human derivation. |
| OpenHands ACP | Enterprise control plane, registry, cost split, hierarchy-capable | Structure is correct but it's enterprise SaaS / K8s-heavy / non-Claude-native. Wrong weight and runtime for solo. |
| GitHub Agent HQ | Repo/PR cloud mission control, multi-vendor agents | Cloud, repo-rooted, flat — not local multi-terminal. Platform owner — encroaching. |
| AgenC | "CEO command center for a fleet of Claudes" | Concept is closest (CEO = 1 session over a fleet) but it's a reference idea, not a shipped solo product. |
| Gas Town (Yegge) | Mayor supervises 20-30 workers | The only real hierarchy that exists — and it fails: too fast to follow, runaway merges. Proof the UX layer is unsolved. |

Dead: Terragon (shut 2026-02-09), Cognosys (acquired/rebranded by Cohere), Vibe Kanban (Bloop dissolved 2026-04).

## 3. Moat — honest

The layer is thin by design. Overseer sits directly on Claude native primitives (`claude agents --json` for roster/status, `claude --remote-control <name>` to drive, transcript JSONL + `ps -p` to derive state). That is the right call — building a Temporal-class "AI agent OS" from scratch is the documented mistake — but it means the surface area a competitor would copy is small, and the platform risk is concrete: **Anthropic** (Agent View) and **GitHub** (Agent HQ) are both moving toward this space and own the substrate. If Anthropic ships hierarchical state + needs-human derivation natively, the floor falls out.

Moat candidates that could actually hold:
- **State-truth quality** — the hard, under-served core. Correctly distinguishing busy / blocked / needs-human / safely-restartable / holding-unreported-context is what prevents duplicate work, conflicting edits, and phantom coordination (Codex's top warning). This is craft, not a feature flag, and it's where incumbents are weakest.
- **Multi-runtime (Claude + Codex together)** — Anthropic will never first-class a competitor's runtime. A neutral layer over both has structural reason to exist.
- **Intent routing + workflow memory** — durable "send this to the right lead, remember what each is doing" is genuinely differentiated *if* it works; it's also the hardest and least proven.

Won't hold: the dashboard itself (Cogpit/OctoAlly/Agent View already render it), process supervision (PM2/supervisord/watchdogs are commodity), and the launcher/poller plumbing (trivial to replicate).

## 4. Monetization

TAM is honestly narrow today: the agentmaxxer power-user cohort running 7-8+ autonomous leads, not the 3-5-agent mainstream — yet. The mainstream is trending up and to the right, so this is an early bet on a growing band, not a current mass market.

Realistic models:
- **OSS core + paid tier** (most credible). Free local daemon + dashboard for adoption; paid tier for what a power-user actually pays to avoid pain: reliable crash recovery without losing context, cross-runtime (Claude+Codex) state-truth, intent routing, and per-agent cost/attribution. ~$15-30/mo solo, $50-100/mo for a small team running fleets.
- **Local app, one-time** ($49-99). Fits a privacy-conscious "runs on my Mac, no cloud" buyer; weaker recurring revenue, simpler trust story.
- **Services / consulting** — set up fleet orchestration for teams adopting agent dev. Real near-term cash, doesn't scale, easily strangles the product.

What a power-user pays for: not the board (they'll use free), but **"my 8 sessions never silently die, and when one needs me I'm told — exactly once, not 40 times."** Reliability of state-truth and recovery is the willingness-to-pay line.

## 5. Sharpest wedge / positioning

**Durable, correct state-truth for a solo operator's already-autonomous team-leads — the one thing incumbents won't bother building at solo scale.** Anthropic and GitHub optimize for the median user (3-5 agents) and the team/enterprise account; neither will invest in conservative, false-positive-averse "is this lead safely restartable / does it hold unreported context" logic for one person running eight team-leads on a laptop. Be the layer that turns eight noisy autonomous sessions into one trustworthy board that interrupts the human *only* when a lead truly needs them, and restarts dead ones without losing work. Not "another multi-agent dashboard" (solved) — **the manager of managers that gets the state right.**

## 6. Name candidates

- **Overseer** (placeholder) — clear, but generic and slightly authoritarian; common in software.
- **Foreman** — runs the crew of crews; blue-collar, concrete, memorable; "Procfile foreman" association is mild.
- **Reeve** — medieval estate manager who oversaw other managers; distinctive, evokes hierarchy without "boss."
- **Steward** — manages on the owner's behalf; calm, trustworthy, fits "interrupt only when needed."
- **Wheelhouse** — the bridge from which you steer; "mission control" feel, no clash with Conductor/Crystal/amux.
- **Helm** — you hold the helm over autonomous crews; short, ownable, nautical mission-control.
- **Quartermaster** — coordinates and supplies the units below; hierarchy + logistics, less taken.
- **Belfry** — the tower you watch the fleet from; oversight metaphor, unusual and brandable.

(Avoided: Conductor, Crystal/Nimbalyst, amux — already taken in this space.)

## 7. Go / no-go

**Go — but dogfood-first, productize-second.** Build it for yourself now: the value is 100% certain because you have the exact pain (7-8+ leads, the silent-death problem, the needs-human-noise problem) and the substrate is already on your Mac (`daemon/roster.json`, `jobs/`, `agents/`). The thin-layer scope (roster poll → state-truth → one board → notify → eventual auto-restart) is small enough to earn its keep as a personal tool regardless of any business outcome. That makes the downside near-zero.

Productize *only if it earns its keep* — i.e. if your own daily use proves state-truth is reliably correct (the make-or-break, per Codex) and others outside your head want it. Be honest about the risks: (1) **platform race** — Anthropic and GitHub own the substrate and are moving into this space; a native hierarchical Agent View could erase the wedge, so the build must stay thin and cheap to abandon; (2) **narrow TAM today** — agentmaxxers are few, and the mainstream may stall at 3-5 agents; (3) **state-truth is genuinely hard** — a wrong restart causes conflicting edits and lost context, which is worse than no tool. Decision: build the personal tool, instrument whether state-truth holds up under your real fleet, and revisit "is this a product" only after it has saved you from a real incident. No commitment beyond the dogfood build until then.
