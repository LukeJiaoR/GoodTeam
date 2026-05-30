# GoodTeam — The First AI Engineering Org You Actually Own

*A Series Seed thesis. Written for investors, design partners, and the first 10 engineers we'll hire.*

---

## 1. The 90-second pitch

GoodTeam is a self-hosted AI engineering team that runs on your laptop. You give it a product idea; it dispatches a virtual product manager, architect, engineers, QA, reviewer, and release captain — each as a specialized AI agent — and ships real, production-grade software through explicit human-approved gates. You own the code, the institutional memory, and the audit trail. None of it leaves your filesystem unless you push it.

We're not another coding assistant. We're a **team OS**:

- **vs. Copilot / Cursor** — they autocomplete code. We deliver products.
- **vs. Claude Code / Codex CLI / Gemini CLI** — they're one agent in a loop. We're a coordinated team with roles, gates, and dissent.
- **vs. Devin / Replit Agent** — they're cloud autonomous engineers that forget every session. We're local-first with compounding memory you actually own.

Validated on two real targets: an internal content-ops studio (CONTENTOPS) shipped to release approval through real Codex workers, and a multi-service mesh deployment (AgentsMesh) shipped to staging on an actual VPS with delivery-contract evidence. 221 tests green; two end-to-end deliveries through human-approved release gates.

Raising **$2.5M Seed** to ship the two technical moats — multi-agent collaboration graphs and compounding memory — and onboard 50 design partners over 12 months.

---

## 2. The thesis: "team," not "agent"

For three years the industry has been racing to make a *single* agent smarter — bigger context window, better tool use, longer horizon. That race is converging. Every frontier model in 2026 can write a CRUD app, debug a stack trace, refactor a module.

What no single agent does — and what no founder, no PM, and no enterprise lead actually needs — is **a single function**. They need a *delivery*. A delivery is:

1. An idea turned into a decision-quality PRD.
2. A PRD turned into an architecture with explicit tradeoffs.
3. An architecture turned into code, tests, infra, docs — *in parallel*.
4. Code reviewed by someone who didn't write it.
5. Verified on staging with evidence a human can sign.
6. Released with a rollback story.
7. Lessons captured so the next delivery is faster.

A single agent does (3) decently and approximates the rest. A real team does all seven, with **specialization, dissent, and memory**. GoodTeam is built around the bet that the next decade of AI-native software gets built by teams, not agents — and that the operating system for those teams is a category-defining product.

---

## 3. Why now

Four forces convert in 2026:

**Model capability has caught up to the unit of work.** Coding agents can now reliably ship a 500-line feature. The bottleneck moved from "can it code" to "can it coordinate, remember, and be trusted with the keys."

**Cloud-only autonomous engineers hit a compliance ceiling.** Devin and Replit Agent are growing fast in unregulated SaaS, but stalling in healthtech, fintech, govtech, defense, and most of the European and Asian enterprise base. These customers cannot send proprietary code to a third-party cloud. They want the agent *inside the perimeter*.

**Founders and small teams discovered that single-agent CLIs (Claude Code, Codex, Gemini) are powerful but lonely.** They scale you to about 2× output. They don't scale you to 10×. The ceiling is coordination, not code generation.

**Memory has become the obvious next frontier — and nobody owns the layer.** OpenAI, Anthropic, and Cursor have all shipped variants of "memory" in the last 18 months. None are portable, none are queryable, none compound across projects. The team that builds the memory layer that engineers actually trust will own a primitive on the order of git.

GoodTeam is positioned at the intersection: **local-first, multi-agent, memory-native**. None of the well-funded competitors are at all three corners.

---

## 4. The wedge: founder-engineers shipping real internet products

We do not start with enterprise. We do not start with vibe-coders. We start with the segment that feels the pain most sharply today: **technical solo founders and 2-5 person dev teams who are shipping real internet products** — production SaaS, internal platforms, vertical apps. They have:

- Clear product taste and architectural opinions.
- No bandwidth to staff a 6-person team.
- High tolerance for self-hosted tooling (they already run their own infra).
- Strong preference for owning code, IP, and history.
- A growing intuition that single-agent CLIs aren't enough.

This is the same segment that adopted Linux, git, Vercel, Supabase, and Cursor early. It's a market of ~500K technical founders globally, growing ~30% YoY as AI-native solo entrepreneurship accelerates.

We win this wedge by being the only product where one founder credibly ships at the velocity of a 6-person team — not by writing code faster, but by *not having to be the bottleneck in the workflow itself.*

From the wedge, three expansion paths open in year 2-3:

- **Agencies and dev shops** that bill by delivery — GoodTeam multiplies their throughput per engineer.
- **Internal platform teams in mid-large enterprises** — compliance moat against cloud Devin.
- **Vertical SaaS players in regulated industries** — healthtech, fintech, govtech, edtech.

---

## 5. The product

### 5.1 What exists today (validated)

A working V1 local-first system that already runs end-to-end:

- **12 specialized agent roles** with stable prompts and per-role model routing (`role_prompts.json`, `roles.py`, `models.yaml`). Roles can be matched to different models per project — Opus for architecture, Codex-tuned models for implementation, Haiku for cheap drafts.
- **Explicit human-approved gates** at PRD, RFC, staging, security, and release (`state.py:7-12`). Each decision has three outcomes: approve, request changes, reject. Each gate writes a checkpoint a human can audit.
- **Pluggable worker backends** — an extensible CLI registry (`CLI_BACKENDS` in `workers.py`) drives real agentic coding workers: Codex CLI and Claude Code (`claude`), plus `fake` (for smoke), `human`, and `noop`. Each role picks its own backend, and `doctor` preflights every backend's CLI-on-PATH and credential readiness. Worker lifecycle includes queued → running → completed/failed, heartbeat, stdout/stderr separation, and role-pickup packets.
- **Local agile board** (`tasks.py`) projecting Kanban over project-local `.goodteam/tasks/tasks.json`. Smoke runs and failures auto-materialize as cards.
- **Smoke harness** (`smoke.py`) for self-test and target-test loops with classification of failures (auth, scaffold, QA, workflow, unknown).
- **Delivery contracts** (`staging.py`) — projects declare required functional evidence; GoodTeam refuses to advance staging without it, even if commands exit 0.
- **Frontend-backend separation** — React dashboard talks to Python backend via `/api/*` with CORS, dev proxy, and static `dist/` serving in production.
- **Telegram-native review channel** — interactive approval on mobile with progress, item-level decisions, and sensitive-data masking.
- **One-command onboarding** — `pip install -e .` plus `goodteam-os init` scaffolds a project config and runs a readiness doctor. A new user reaches their first human approval gate with no model credentials at all, because the PRD is generated deterministically and the default worker backend is `fake`.
- **221 passing tests** covering every layer. Two real deliveries shipped: CONTENTOPS (Node/Fastify/React, multi-platform draft generation) reached release approval; AgentsMesh (multi-service Docker Compose over SSH) shipped to working staging on a real VPS.

This is not a prototype. It's a working V1 with two live design-partner targets.

### 5.2 What's missing (and is the startup)

The V1 proves the workflow. It doesn't yet have the moats. Two pieces of architecture turn GoodTeam from "a useful workflow" into a defensible category:

1. **Multi-agent collaboration graphs** — agents that actually work as a team, not as a queue.
2. **Compounding memory** — every run makes the next run cheaper, faster, sharper.

The rest of this document is what those two moats look like, and how we ship them.

### 5.3 Implementation status (as of 2026-05-30)

Work since this thesis was drafted has been "make it usable, then start the moats":

- **Usable by someone other than the founder.** Real install path (`pip install -e .`), a `goodteam-os init` scaffolder, a committed config schema (`projects/example.yaml`), and a de-personalized README. First run reaches an approval gate with zero credentials.
- **Real multi-provider workers.** Codex and Claude Code workers behind one extensible `CLI_BACKENDS` registry, selectable per role; a worker-auth preflight surfaces missing CLIs/credentials (and known traps like a stale `CODEX_API_KEY` shadowing `~/.codex/auth.json`) at `doctor`/`init` time instead of after a failed run.
- **Moat #2 started.** BM25 hybrid retrieval (M2) ships; the larger memory pieces (write-back M1, embeddings, cross-project store, grounded-citation rendering) remain open.
- **Hygiene.** 221 tests green and a ruff-clean tree.

Moat #1 (collaboration graphs) has not started; it remains the highest-leverage next investment.

---

## 6. Moat #1 — Multi-agent collaboration as a graph

### 6.1 The diagnosis

The V1 workflow is a fixed serial pipeline (`graph.py`, `role_prompts.json:185-283`). The `implementation` node hardcodes three jobs (UI → impl → QA) and runs them in sequence (`graph.py:1024-1039`). Workers talk to each other only by writing markdown to a result file. Failures dead-end into `blocked` with no retry, no triage, no escalation. Review gates are single-reviewer, binary-ish decisions with no debate trail.

This is fine for a single linear delivery. It is not fine for a *team*. Real teams:

- Work in parallel on independent slices.
- Ask peers for clarification mid-task.
- Disagree, debate, and produce decision evidence.
- Recover from failure without escalating every time.
- Synthesize ad-hoc expertise when the problem demands it.

### 6.2 What we build

**Collaboration plan (replacing fixed graph).** A run produces a *plan* — a DAG where nodes are tasks, edges are dependencies *and* communication channels. Each task has one owner agent and zero-to-many advisor agents. The plan is generated by the architect role at RFC time and committed as a versioned artifact. Approval gates are plan nodes too.

**Inter-agent message bus.** Agents emit structured messages (`ask`, `assert`, `reject`, `propose`) on a per-run channel under `state/projects/<KEY>/messages/<run>/`. Messages are subscribed by downstream agents, by the human via Telegram/web, and by the triage agent. This replaces "everyone reads everyone else's result.md" with a real conversation graph.

**Triage agent and self-correction loop.** Every worker failure flows through a triage agent that classifies (`auth`, `infra`, `scaffold`, `code_bug`, `flaky`, `ambiguous_spec`) and routes: retry-with-backoff, escalate-to-human, route-back-to-spec-author, or synthesize-expert. The failure modes that today eat hours of footprint (`INVALID_API_KEY` five times, `better-sqlite3` ABI drift, npm registry 504) become first-class recipes the system applies automatically.

**Committee and debate at high-stakes gates.** Release, security, and architecture decisions can declare quorum (`2 of {architect, security, product}`) and `debate_rounds`. A debate round writes challenger questions, responder rebuttals, and a final decision — all to `artifacts/runs/<run>/debate/<gate>.md`. The reviewer sees not a single binary, but a structured argument they arbitrate. This is the audit primitive that wins regulated industries.

**Ephemeral expert synthesis.** Architect can declare `required_experts: [pg_index, k8s_helm, oauth_pkce]`. The system materializes domain-specialist agents on the fly — prompt = expert template + memory.search(domain) + cited references — runs them, captures the artifact, and promotes the successful brief into a cross-project expert library. Over time, the library covers the long tail no static role roster could.

### 6.3 Why this is defensible

Coordination is the part of an engineering team that is hardest to copy from a model release. It compounds with usage: every debate, every triage decision, every expert brief becomes structured training signal for the next iteration. Twelve months in, our collaboration graph reflects 10,000 hours of real shipping decisions. A frontier model alone cannot replicate that. A competitor starting from zero cannot collapse that gap.

---

## 7. Moat #2 — Compounding memory

### 7.1 The diagnosis

V1 has memory plumbing (`memory.py`, 336 lines): per-project `project-memory.md` with three horizon sections, per-role folders with `long/mid/short-term.md` plus `events.jsonl` and `index.json`. The structure is right. The behavior is not yet:

- **Retrieval was the gap; the first increment now ships.** The original "search" was `summarize_markdown` — top 8 headings + top 12 bullets concatenated, which at 100K characters produces noise, not signal. BM25 hybrid retrieval over `events.jsonl` now feeds role pickup packets task-relevant facts with stable `mem:` handles; embeddings and cross-project retrieval are the remainder of M2.
- **No automatic write-back.** Workers read memory on entry (`workers.py:58-67`) but no code path writes memory on exit. Every lesson is captured only when a human edits markdown by hand.
- **No cross-project knowledge.** All memory lives under each target's `.goodteam/`. The same failure mode rediscovers itself in every new project.
- **No user-preference layer.** Hard user preferences ("use Codex CLI," "don't run smoke in the active workspace," "use $SHELL -lc for verification") live as ambient knowledge in the founder's head and decay between sessions.
- **No conflict or staleness.** `events.jsonl` is append-only forever. Contradicting facts both persist.
- **No grounded citations.** When an agent uses a fact from memory, the artifact doesn't say which fact, from which event, on which date.

### 7.2 What we build

**Hybrid retrieval layer.** Each event in `events.jsonl` is embedded at write time using a small local model (bge-small or similar) into `embeddings.parquet`. Retrieval at agent pickup time is cosine-top-K over the task description + role + recent blockers, with BM25 as a deterministic fallback. The pickup packet sent to the worker becomes *retrieved relevant facts + horizon summaries + last 24h short-term*, not the full markdown corpus.

**Automatic write-back.** Worker `result.md` is parsed for `## Learned`, `## Caveats`, `## Decisions`, `## Open Questions` sections; each becomes an `append_memory_event` to the appropriate role and horizon. Review-gate decisions write reviewer comments to mid-term. Triage agent's root-cause analyses write to the failure library (below). Every run silently grows the corpus.

**Cross-project knowledge layer.** A global store at `~/.goodteam/global/` holds four classes of knowledge: `failure_patterns/` (signature, classifier hint, recommended action, evidence links, hit count), `expert_briefs/` (synthesized domain knowledge), `tool_recipes/` (verified ways to run Node/Postgres/Docker on this machine), and `user_prefs/` (style, forbidden actions, decision history). New projects start warm.

**User-preference layer.** Decoupled from project memory and sharing schema with Claude Code's `auto memory` system. Hard preferences become injected constraints in every role pickup ("never run smoke in this directory," "always verify through `$SHELL -lc`"). Soft preferences become style hints ("terse output, no trailing summaries").

**Failure pattern library.** Today the footprint contains a beautifully detailed hand-curated record of dozens of failure modes and their resolutions. We make that the *primary deliverable shape* of every failure. New failure: triage classifies, library is searched, recipe is applied if matched. Hit rate becomes a north-star product metric.

**Conflict and staleness.** Events gain `supersedes` and `last_referenced_at`. Memory rebuild skips superseded events. Events not referenced for N runs are tiered to cold storage. The active corpus stays warm and current.

**Grounded citations.** Every fact in the pickup packet carries a `mem:<id>` handle. Prompts require artifacts to cite the handles. Artifact renderer transforms `[mem:abc123]` into clickable links back to the source event. Every claim in every artifact is provenance-tracked.

**Artifact ↔ memory backlinks.** PRDs, RFCs, QA reports, and release notes auto-fan-out their decisions, contracts, constraints, and open questions into the project memory's appropriate section. Memory entries link back to the artifact they came from. The two layers become one searchable knowledge graph.

### 7.3 Why this is defensible

Memory is the only part of the stack that *gets better with use without any model upgrade*. A user with 200 runs of GoodTeam history has a fundamentally different product than a new user, even on day-one of using the same frontier model. Switching cost grows on a curve we control. Twelve months of compounded memory is the difference between "another AI tool" and "the system of record for how my products get built."

---

## 8. Competitive landscape

| | Surface | Locality | Memory | Multi-agent | SDLC coverage | Audit |
|---|---|---|---|---|---|---|
| GitHub Copilot / Cursor | IDE / inline | Cloud | Per-session | No | Code only | None |
| Claude Code / Codex CLI / Gemini CLI | CLI | Cloud (model) | Conversation + light file memory | No | Code + light review | None |
| Cursor Background Agents | IDE-anchored | Cloud | Per-session | Limited | Code + light QA | None |
| Devin (Cognition) | Web | Cloud-only | Per-session | Internal, not exposed | Code + ops | Limited |
| Replit Agent | Web | Cloud-only | Per-project light | Limited | Code + deploy | Limited |
| Factory.ai droids | SaaS | Cloud | Per-stage | Yes (their internal) | SDLC stages | Partial |
| **GoodTeam** | **Local CLI + web + Telegram** | **Self-host, file-owned** | **Compounding, cross-project, cited** | **Explicit graph + debate** | **PRD → release with gates** | **Built-in evidence + signature trail** |

Where we lose today: brand awareness, polished onboarding, marketing surface. Where we win structurally: ownership, compliance posture, memory compounding, multi-agent coordination, evidence trails.

The only player who could chase us is a cloud incumbent shipping an on-prem version of their stack. That move has high organizational friction (sales-led companies struggle with self-hosted), takes 12-18 months, and concedes the memory-portability narrative.

---

## 9. Business model

**Tier 1 — Solo / Indie**: open-source core under a permissive license. Free forever. Drives adoption and trust. Includes single-machine local-first runtime, basic memory, all roles, all gates.

**Tier 2 — Pro** ($49/mo): cross-project memory cloud sync (encrypted, optional), hosted Telegram bot, premium model adapters, priority support, analytics. Targets technical solo founders and 2-5 person teams.

**Tier 3 — Team** ($499/mo per team of up to 10): shared memory across team members with role-based access, audit export, SSO, on-call escalation routing, committee-mode review with multi-party signatures. Targets agencies, internal platform teams, regulated-industry product teams.

**Tier 4 — Enterprise** (custom, $50K-200K ACV): air-gapped install, custom delivery contracts, compliance reporting (SOC 2 / HIPAA / ISO 27001 evidence helpers), SAML, dedicated success engineer. Targets large enterprise internal platform groups and government.

Revenue model is land-and-expand: solo founder uses free → company they join adopts Pro → engineering org adopts Team → IT/compliance standardizes Enterprise.

---

## 10. Go-to-market

**Year 1 — Earn the technical-founder beachhead.** Open-source launch with a single 30-minute demo: idea → PRD → working web app → release on staging, with full audit trail. Founder content + technical deep-dives on the architecture moats. 50 design partners hand-picked from technical-founder communities (YC, Hacker News, Indie Hackers, Lenny's, Chinese founder Slack/WeChat ecosystems). Free Pro tier for design partners.

**Year 2 — Convert agencies and internal teams.** Case studies from year 1 ("solo founder shipped Series A product solo in 3 months on GoodTeam"). Outbound to ~200 dev agencies in NA, EU, India, China. Land 20 paying agency accounts.

**Year 3 — Enter regulated enterprise.** SOC 2 in hand. First five enterprise design partners in healthtech, fintech, govtech (one each in three geographies). Build the compliance-evidence helper layer as enterprise pull-product.

We are not running paid acquisition in years 1-2. The wedge converts on product gravity, not ads.

---

## 11. Roadmap with funding milestones

**Seed ($2.5M, 18 months runway).** Team of 4 (you + 3 engineers). Ship:

| Quarter | Outcome |
|---|---|
| Q1 | Memory write-back loop + hybrid retrieval (M1, M2). User-preference layer (M4). 10 design partners onboarded. |
| Q2 | Collaboration graph + message bus (G1, G2). Triage agent (G3). Cross-project failure library (M5). 30 design partners. |
| Q3 | Committee/debate gate (G4). Grounded citations (M8). First paying Pro customers. 50 design partners; 100 paying. |
| Q4 | Artifact↔memory backlinks (M7). Web GTM polish. SOC 2 Type 1 in flight. 500 paying Pro; first 3 paying Team accounts. |
| Q5 | Ephemeral expert synthesis (G5). Compliance helpers MVP. SOC 2 Type 1 closed. 1500 paying. |
| Q6 | Enterprise air-gap installer. First enterprise pilot. **$1M ARR run-rate trigger for Series A.** |

**Series A target ($12-15M at $50-80M post)** at 18 months: 1500-3000 paying Pro accounts, $1-2M ARR, 5+ Team accounts, 1 enterprise pilot, durable retention curves.

---

## 12. Metrics that matter

**Product (leading)**

- *Time-to-first-shipped-feature* (idea → release-approved): target reduces 50% between user's 1st and 10th delivery.
- *Memory hit rate*: % of triage decisions that match library and apply automated remediation. Target 40% by month 6, 70% by month 12.
- *Cross-project knowledge reuse*: # of facts retrieved from global memory per run. Target rises monotonically per user.
- *Human override rate at gates*: % of agent recommendations the human overrides. We want this stable, not minimal — high override means the human still adds judgment; collapsing override means the human stopped reading.

**Business (lagging)**

- Free → Pro conversion: target 4% within 60 days of first run.
- Pro → Team expansion: target 12% by month 12 of Pro use.
- Net revenue retention: target >120% by year 2.

**Trust**

- Audit-trail completeness: % of releases with full PRD → release evidence chain. Target 100%.
- Time-to-rollback after release incident: target <10 min via recorded rollback story.

---

## 13. Risks and mitigations

**Frontier model providers ship "team mode" themselves.** Mitigation: our moat is memory and ownership, not raw model capability. The day Anthropic ships an SDK for multi-agent, our adapter layer treats it as another worker backend. We assume this happens and design around it.

**Cloud Devin / Replit Agent capture the founder market first.** Mitigation: we've already lost the SaaS-only audience; we win on the ownership-and-self-host axis. We need not be the biggest. We need to be the only credible option for the compliance-aware half of the market.

**Memory becomes commoditized as a platform feature.** Mitigation: portable memory schema and a published spec. We become the *Postgres of agent memory* — even if a competitor ships memory features, ours is the format the industry standardizes on because it shipped first, locally, with proof.

**Multi-agent coordination is genuinely hard and we underestimate.** Mitigation: ship the smallest credible version first (parallel implementation + simple message bus + triage agent), measure, iterate. Do not ship debate and committee until parallel works for 100 design partners.

**China-side model reachability blocks adoption in our likely strongest early market.** Mitigation: provider abstraction already supports custom OpenAI-compatible base URLs. Ship official adapters for at least two China-side providers (e.g. DeepSeek, Qwen) in Q1.

**Founder bus factor (this currently runs on one person's vision).** Mitigation: the seed hires include a technical co-architect, not just IC engineers. Hire #1 is a senior systems engineer who can carry the moat work independently.

---

## 14. The ask

**$2.5M Seed.** 18-month runway. Lead + 1-2 strategic angels. Use of funds:

- Engineering hires (3 senior, $1.5M loaded over 18 months): one systems/runtime, one ML/retrieval, one frontend/UX.
- Cloud/model spend for design-partner program ($150K).
- Design partner success ($100K, including founder travel and one-off integrations).
- SOC 2 Type 1 + compliance counsel ($120K).
- Founder + ops salary ($400K).
- Reserve ($230K).

We are not running a marketing budget. The product is the marketing. Year-1 success is measured in design-partner retention curves and ARR run-rate, not impressions.

---

## 15. The line in one sentence

> **GoodTeam is the AI engineering org you can hire today, own forever, and watch get sharper with every product it ships.**

That sentence is the entire product strategy. Every architectural decision — local-first, multi-agent, memory-compounding, evidence-trailing — is downstream of it. The roadmap is the path to making that sentence empirically true for 50 design partners in 12 months, 1,500 paying users in 18, and a category in 36.
