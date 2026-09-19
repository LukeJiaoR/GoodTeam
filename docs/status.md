# GoodTeam Current Status

_Last updated: 2026-09-19._

This file is the short-lived current-state entry point for contributors and agents. Its implementation statements describe the code revision containing this document; a PR branch is not a deployed release. Historical implementation footprints remain in [`current-progress.md`](current-progress.md); active work belongs in GitHub Issues and pull requests rather than a second backlog here.

## Current product surface

```text
Projects → Work / Team / Review / Knowledge
```

- `/projects` imports or opens a local project.
- `/projects/<PROJECT_KEY>/work` is the primary Task Assistant / 任务助手 conversation for understanding, work intake, clarification, progress and explicit recovery.
- Work keeps project/task identity and one next step visible. The context action locates existing controls; it does not independently approve or execute work.
- Ordinary questions and bound modification discussions share one composer. Exact text can be handed to an existing review's change draft; interpretation and confirmation remain separate.
- Team exposes project-owned Roles and Worker configuration.
- Review is the evidence-backed review and approval surface.
- Knowledge exposes repository understanding, artifacts, and memory.
- Legacy `/inbox`, `/activity`, `/settings`, and project Home routes are compatibility redirects only.

## Current control-plane model

```text
Project import → Task Assistant conversation
→ read-only answer / proposed work
→ explicit, version-bound human confirmation
→ workflow + bounded Worker execution
→ verification + review evidence
→ human decision → ledger + artifacts + provenance + memory
```

Readiness remains backend-owned. Model interpretation is advisory, never authorization. Role definitions, TeamRole membership and Worker identity/lifecycle/runtime are implemented through `team_control_plane.py`; the Task Assistant is one logical owner, not a TeamRole. Team is currently project-scoped, not workspace-wide. Legacy configuration is retained as a compatibility path.

Runs bind execution identity and configuration; later configuration changes must not rewrite historical evidence. Pre-confirmation model execution requires a verifiable read-only boundary or fails closed. Conversation, artifact reads and navigation do not grant approval or dispatch authority.

## What the evidence does and does not prove

Implementation, simulated verification and real product acceptance are separate statuses. See [`m0-product-acceptance.md`](m0-product-acceptance.md) for source links and a reusable scorecard.

- The current test suite exercises real HTTP, isolated workspaces and fake Workers for the business loop, explicit change confirmation, second-task history, concurrent approvals, crash/timeout recovery, conversation continuity and report reads.
- The current-context and composer-handoff additions have their own focused tests and browser cases. Their PRs must have passing exact-head CI and inspected browser evidence before being treated as verified; an earlier green run is not a result for a changed head.
- Historical real-model checks cover first questions/configuration recovery and a bounded PRD revision handoff. They are not complete coding/review/QA delivery evidence.
- No five-task/two-project real delivery acceptance is claimed here. [#411](https://github.com/LukeJiaoR/GoodTeam/issues/411) remains the product-effect gate, independent from any implementation PR or [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419) documentation closure.

A catalog, installed CLI, Worker exit code, readable report or committed change decision cannot by itself establish that the requested result is accepted. Missing verification remains missing; fake results remain simulated.

## Current naming and remaining scope

`orchestrator` remains the stable internal key; the UI uses **Task Assistant / 任务助手**. Historical documents may say Manager or Orchestrator.

The M0 acceptance target is desktop Web, an explicitly configured local Git repository, a verified real provider/model configuration and bounded coding work. Arbitrary mid-Worker changes, automatic replan/queued changes, natural-language approval, workspace-shared Team migration ([#409](https://github.com/LukeJiaoR/GoodTeam/issues/409)), external Planner/MCP expansion and commercial packaging are not silently included. Existing optional adapters are not removed, but unsupported or unverified combinations must not be advertised as accepted.

The minimum change-status contract is documented in [`conversation-change-status.md`](conversation-change-status.md). Prioritize further architecture only when a real task demonstrates the need.

## Documentation source of truth

1. Code and tests for executable behavior.
2. Stable manuals linked from [`README.md`](README.md) for current product/operator contracts.
3. This file for short-lived orientation.
4. Historical plans, audits, progress logs, research and dogfood records for bounded evidence, not blanket current acceptance.

`openwiki/` is generated/read-only. `docs/` is the canonical human-maintained documentation source.
