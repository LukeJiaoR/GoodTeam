# GoodTeam Current Status

_Last updated: 2026-09-06._

This file is the short-lived current-state entry point for contributors and agents. It describes the product and architecture that are true on `main` today. Historical implementation footprints remain in [`current-progress.md`](current-progress.md); active work should be tracked in GitHub Issues and pull requests rather than appended here as a backlog.

## Current product surface

The canonical product flow is:

```text
Projects → Work / Team / Review / Knowledge
```

- `/projects` imports or opens a local project.
- `/projects/<PROJECT_KEY>/work` is the primary interaction surface.
- Task Assistant / 任务助手 is the user-facing conversational entry for project understanding, work intake, clarification, progress, retries, and server-authorized actions.
- Team is the project configuration surface.
- Review is the evidence-backed review and approval surface.
- Knowledge exposes repository understanding, artifacts, and memory.
- Legacy `/inbox`, `/activity`, `/settings`, and project Home routes are compatibility redirects only; their dedicated product UI has been retired.

## Current control-plane model

Task Assistant is a conversational projection over GoodTeam-owned state. It does not replace the control plane.

```text
Project import
  ↓
Task Assistant conversation
  ↓
read-only understanding / answer / proposed work
  ↓
explicit human confirmation when required
  ↓
workflow + bounded worker execution
  ↓
verification + review evidence
  ↓
human decision
  ↓
ledger + artifacts + provenance + memory
```

Important invariants:

- Readiness remains backend-owned safety state even though it is no longer a separate user journey.
- User text is preserved; model interpretation is advisory and cannot authorize consequential actions.
- A workflow starts only through a server-issued action and explicit human confirmation.
- Pre-confirmation model turns run inside a verifiable read-only boundary or fail closed.
- Worker results and reviewer recommendations are evidence, not approval authority.
- GoodTeam-owned state, action history, provenance, and project-local memory remain the source of truth.

## Current naming

- `orchestrator` remains a stable internal role key for compatibility.
- The user-facing label is **Task Assistant / 任务助手**.
- Historical documents may still use **Manager** or **Orchestrator**. Treat those names as historical unless a stable reference explicitly says otherwise.

## Active design work

The Role → TeamRole → Worker control-plane separation is active design work and is **not yet a stable documentation contract**. Until that implementation lands, [`config-reference.md`](config-reference.md) and [`worker-backends.md`](worker-backends.md) describe the currently implemented configuration model.

When that model lands, update the stable references and architecture in the same PR or an immediately following docs PR; do not rewrite historical plans as if they were current contracts.

## Documentation source of truth

Use documentation in this order:

1. Stable manuals linked from [`README.md`](README.md).
2. This current-status file for short-lived product orientation.
3. Code and tests for executable behavior.
4. Historical plans, audits, progress logs, and generated OpenWiki pages only for design history and evidence.

`openwiki/` is a generated/read-only projection. `docs/` is the canonical human-maintained documentation source.
