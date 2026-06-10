# GoodTeam Product Strategy v0.1: Own the Engineering Org, Integrate the Tools

Date: 2026-06-10

## Executive Decision

GoodTeam should stop expanding sideways into replacements for mature developer
tools. The product should own the AI engineering organization layer: roles,
workflow protocol, approval gates, evidence, memory, review, and provenance.

Everything else should be integrated through adapters when mature open-source or
standard developer tools already exist.

Short version:

> GoodTeam owns the development protocol, not every subsystem.

## Why This Change

The original GoodTeam notes framed the product as a LangGraph-based AI Team OS.
That was useful for the V1 skeleton: durable artifacts, resumable workflow,
state checkpoints, and human approval gates.

The product has now evolved beyond that skeleton. Current GoodTeam already has a
local-first runtime, React dashboard, project configs, pluggable agent workers,
approval gates, delivery contracts, message bus, debate, memory WAL,
failure-pattern evidence, dispatch provenance, and self-hosting control-plane
work.

The risk is now different. If GoodTeam keeps implementing every adjacent
subsystem itself, it becomes a broad but shallow clone of Plane, GitHub, CI,
deployment tooling, browser testing, and agent runtimes. That dilutes the core.

The stronger product position is:

> GoodTeam is the local-first control layer that organizes existing tools into a
> reviewable, memory-bearing AI engineering team.

## Product Boundary

### GoodTeam Must Own

These are core product assets and should remain first-party:

- Role chain: product, architect, planner, implementation, QA, code review,
  release, retro memory.
- Workflow state: current run, checkpoints, approval status, node progression.
- Human gates: approve, request changes, reject, with rationale and evidence.
- Worker dispatch contract: task, constraints, context, expected artifacts,
  role pickup packet, result protocol.
- Review workbench: why a human can approve or should block a gate.
- Approval clients: dashboard for deep review, Telegram for complete mobile
  approval, and future chat clients that preserve the same gate semantics.
- Evidence model: tests, screenshots, logs, CI, deployment checks, QA reports,
  and delivery-contract satisfaction.
- Memory model: role memory, project memory, global failure patterns, user
  preferences, supersede/conflict handling, citations.
- Message/debate protocol: structured `ask`, `assert`, `reject`, `propose`,
  decision records, quorum evidence.
- Provenance chain: PR to dispatch job to task contract to research/report to
  pre-review to land/release memory.
- Safety policy: same-origin gates, typed confirmations, destructive-action
  boundaries, local-first data ownership.
- Remote supervision trace: who reviewed from which client, what they saw, which
  evidence items they checked or flagged, and which decision moved the workflow.

These are the moat. They define how AI work becomes trustworthy engineering
delivery.

### GoodTeam Should Integrate, Not Replace

These should be adapters, not core reimplementations:

- Project management: Plane, Linear, GitHub Issues.
- Git hosting and PRs: GitHub first, GitLab later if needed.
- CI: GitHub Actions first, other CI later.
- Browser/E2E testing: Playwright.
- Deployment: Docker Compose over SSH first; Kubernetes, Fly, Railway, Vercel,
  or Render as adapters.
- Documentation and personal knowledge: Markdown, Obsidian, Git.
- Agent execution: Codex CLI, Claude Code, Gemini, opencode, or future CLIs.
- Search/vector infrastructure: local BM25/embedding libraries or external
  stores, while GoodTeam owns schema and provenance.
- Notifications: email, Slack, Discord as channels, not sources of truth.

Telegram is a special case: it should not be treated as a shallow notification
adapter. It is a first-class approval client for GoodTeam's human gate protocol.
It still must not become the source of truth; it drives decisions through the
same GoodTeam ledger, approval state, evidence model, and stale-button safety
rules as the dashboard.

Adapters may read, write, or sync external systems, but the GoodTeam ledger
remains the source of truth for workflow decisions, evidence, memory, and
provenance.

## Adapter Principle

Every adapter should obey five rules:

1. External tools are replaceable.
2. GoodTeam-owned IDs and events remain stable.
3. Secrets stay outside project config and committed artifacts.
4. Sync failures do not corrupt local workflow state.
5. Every imported or exported fact links back to the GoodTeam ledger.

Adapter output should be treated as evidence, not authority. For example:

- GitHub Actions can report CI status; GoodTeam decides whether the release gate
  is approvable.
- Plane can display tasks; GoodTeam owns the role/task contract and provenance.
- Playwright can produce screenshots; GoodTeam owns whether those screenshots
  satisfy delivery evidence.
- Codex can implement code; GoodTeam owns the dispatch boundary, review gate,
  and memory write-back.

## Architecture Direction

### Core Runtime

Keep the core runtime small and explicit:

- StateStore for run state and checkpoints.
- ArtifactStore for durable outputs.
- WorkerJobStore for agent job protocol.
- RoleMemoryStore and failure-pattern store for compounding memory.
- Message bus for inter-agent coordination.
- Workflow table for nodes, gates, dependencies, and routing.
- Web/API layer for review, approvals, dispatch, evidence, and control plane.
- Telegram approval layer for complete mobile gate review over the same approval
  protocol.

Do not make LangGraph, Plane, GitHub, or any agent CLI the owner of workflow
truth. They can be execution or sync backends.

### Graph / Workflow Engine

Do not adopt LangGraph as the central runtime now.

GoodTeam should keep an internal workflow abstraction and only add an execution
adapter if a real need appears. The useful part is not framework traversal; it is
the product protocol around gates, evidence, isolation, and review.

Near-term graph work should focus on:

- clearer node/dependency metadata;
- worktree-per-worker isolation evidence;
- fan-in review semantics;
- plan versioning;
- UI visibility of workflow health.

Real parallel execution should remain gated until a live fan-in consumer proves
write-domain safety and review coverage.

## Product Roadmap

### P0: Reframe the Product Narrative

Goal: remove stale "LangGraph-based" positioning and align docs with the actual
product.

Deliverables:

- Update package and README language from "LangGraph-based" to "local-first AI
  engineering team OS".
- Add this boundary: GoodTeam owns the engineering-org protocol; external tools
  are adapters.
- Update product vision status with what has already shipped since the original
  thesis: message bus, debate, memory WAL, cross-project retrieval, provenance,
  failure-pattern readiness, and self-hosting control-plane slices.
- Mark i18n as polish, not core strategy.

Acceptance:

- A new reader can understand GoodTeam without knowing the old LangGraph plan.
- The product promise is not "we built a workflow framework"; it is "we make AI
  delivery reviewable, recoverable, and compounding."

### P1: Review Workbench First

Goal: make the approval moment the strongest product surface.

Why: GoodTeam wins when a human can decide, quickly and confidently, whether a
gate should pass.

Deliverables:

- Gate summary: what changed, what evidence exists, what is missing.
- Decision recommendation: approve / request changes / reject, with reasons.
- Evidence matrix: PRD/RFC artifacts, worker outputs, tests, CI, screenshots,
  deployment checks, blockers, debate, memory citations.
- Request-changes path: human note drives the correct revision loop.
- Provenance panel: task contract, worker job, pre-review, CI, land/release
  record.
- Telegram parity: every gate can be reviewed and decided from Telegram without
  needing the dashboard for the happy path.

Adapters used:

- GitHub Actions for CI status.
- Playwright for browser evidence.
- GitHub PR metadata where available.
- Existing local artifacts and memory as primary source.

### P1b: Telegram Approval Client

Goal: support a complete approval flow in Telegram while keeping the dashboard
as the richer investigation surface.

Current direction:

- Telegram is the mobile review client for GoodTeam gates.
- Dashboard remains the deep workbench for full artifacts, diffs, long evidence,
  and historical exploration.
- Both clients write through the same approval API and state ledger.

Required Telegram capabilities:

- Gate overview card: project, run, gate, stage, summary, current blocker state,
  and evidence status.
- Quick decision: approve, request changes, reject.
- Step-by-step review: compact item cards for acceptance, evidence, health,
  regression, security, release notes, or PRD/RFC sections.
- Request-changes note capture: the user can flag an item and send a concrete
  note from Telegram; that note must drive the same revision loop as the
  dashboard.
- Debate/evidence summary: show key objections, owner responses, CI/test status,
  and links to full artifacts when the message would be too long.
- Stale button protection: callbacks must be tied to project, run, gate,
  pending approval state, and current actionable node.
- Sensitive-data masking: file paths, IPs, SSH targets, tokens, and deployment
  directories must be redacted before message send.
- Cross-client sync: if the dashboard decides a gate, the active Telegram card is
  marked decided; if Telegram decides a gate, the dashboard reflects it.
- Remote supervision: Telegram can show current run progress, active workers,
  blockers, pending gates, recent evidence, and safe next actions so an operator
  can supervise GoodTeam away from the main workstation.

Approval audit requirements:

- Every approval decision records `decision_origin`: `web`, `telegram`, `cli`,
  `api`, or `system`.
- Every human approval records a non-secret `actor_ref`, such as local dashboard
  user, Telegram user id, or configured operator alias.
- Telegram decisions record `telegram_chat_id`, `telegram_user_id`,
  `telegram_message_id`, and `tg_review_session_id` when available. These are
  identifiers, not secrets.
- Web decisions record the web/API route origin and, when auth exists, the web
  user/operator id.
- Decisions record `review_mode`: quick approval, step-by-step review,
  request-changes with notes, or reject-confirmed.
- Step-by-step review records item-level state: reviewed ok, flagged, skipped,
  and note text after sensitive-data masking.
- The action log records the evidence snapshot seen at decision time: artifact
  refs, CI status, Playwright/screenshot refs, debate refs, blocker count, and
  provenance refs. It should not depend on later mutable UI state.
- Cross-client updates are also auditable: if Web decides a gate and Telegram is
  marked decided, that sync event is recorded; same for Telegram to Web.

Non-goals:

- Telegram is not the durable artifact store.
- Telegram is not the memory store.
- Telegram does not bypass review gates or server-action safety policy.
- Telegram does not auto-approve based on green checks.

Acceptance:

- A user can start from a Telegram approval notification, review each required
  item, add request-change notes when needed, and complete approve/request
  changes/reject without opening the dashboard.
- The resulting state, action log, memory/provenance trail, and dashboard view
  are identical to a dashboard-originated decision.
- A later reviewer can answer: was this approved from Web or Telegram, by whom,
  after which review path, with which evidence snapshot, and with what notes?

### P1c: Remote Supervision Mode

Goal: make Telegram useful not only for a single approval, but for supervising
GoodTeam while away from the development machine.

Telegram should support read-only status commands or buttons for:

- active projects and current run stage;
- pending approvals;
- active worker jobs and elapsed time;
- latest blockers or failed jobs;
- CI / smoke / QA evidence status;
- recent dispatch/pre-review/land events;
- links back to dashboard or artifacts for deep inspection.

Mutating remote actions should stay narrow and gate-shaped:

- approve/request changes/reject an actionable approval gate;
- run safe, explicitly guarded actions only when they already exist in GoodTeam's
  server-action policy;
- never run arbitrary shell commands from Telegram;
- never bypass typed-confirmation or same-origin/server-action gates where those
  gates apply.

Remote supervision is a product feature because it lets a human operator keep
GoodTeam moving while away from the workstation, without turning GoodTeam into an
unsupervised deployment bot.

### P2: Integration-First Dev Loop

Goal: make GoodTeam-on-GoodTeam and user projects run through mature external
tools while preserving GoodTeam as the control layer.

Deliverables:

- GitHub PR/CI adapter is first-class in Dev Loop.
- Plane/GitHub Issues task sync remains secondary to GoodTeam task contracts.
- Dispatch/pre-review/land provenance becomes visible and understandable.
- Server actions stay guarded; no server-side auto-merge without explicit human
  gate and audit.

Adapters used:

- GitHub CLI/API for PRs and CI.
- Existing scripts for dispatch/pre-review/land until promoted into workflow
  nodes.

### P3: Memory as Product, Not Storage

Goal: turn memory into a visible quality advantage, not hidden plumbing.

Deliverables:

- Show what facts each role retrieved and used.
- Show memory citations in artifacts and review pages.
- Promote failure-pattern matches into actionable review evidence.
- Keep user preferences separate from project memory.
- Treat full research/review ingestion as a deliberate retrieval-budget product
  decision, not a default dump.

Adapters used:

- Optional embedding backend.
- Optional Obsidian backlinks for human reading.

### P4: External Tool Adapters

Goal: broaden compatibility without diluting core.

Order:

1. GitHub Actions / PR evidence.
2. Plane or GitHub Issues task sync.
3. Obsidian note export/backlinks.
4. Deployment evidence adapters beyond SSH Compose.
5. Slack/Discord/email notification channels.

Each adapter ships only when it strengthens review, evidence, memory, or
workflow continuity.

## What To Stop Doing

- Do not deepen the built-in agile board into a full project manager.
- Do not build a custom CI system.
- Do not build a deployment platform.
- Do not turn the dashboard into a generic repo browser.
- Do not make LangGraph adoption a goal by itself.
- Do not prioritize translation over product trust, review clarity, and demo
  reliability.
- Do not auto-merge from the server merely because checks are green.

## Product Definition

Recommended external-facing definition:

> GoodTeam is a local-first AI engineering team OS. It coordinates specialized
> coding agents, project tools, CI, review, deployment evidence, and memory into
> one owned workflow ledger, so a human can ship software through explicit,
> reviewable gates.

Shorter version:

> GoodTeam is the control layer for AI software delivery.

## Immediate Next Step

Before adding new feature slices, run a product-level reconciliation pass:

1. Update stale LangGraph-centered wording.
2. Produce a Review Workbench PRD.
3. Audit the 30-minute demo from a new-user perspective.
4. Decide which external adapter improves the review surface first.

Recommended first build after this strategy:

> Review Workbench v1: an approval page that explains whether a gate is safe to
> approve, using local artifacts plus GitHub/CI/Playwright evidence where
> available.
