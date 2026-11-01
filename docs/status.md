# GoodTeam Current Status

_Last updated: 2026-11-01._

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

## Current project iteration loop

- Manager's chosen existing tasks enter the ordinary Task Assistant queue; confirmed batches reuse the isolated `dispatch_tracks` pool, with up to 10 Workers concurrently. The same LocalTaskStore cards own their claims, result sources and delivery receipts.
- [Continuation](project-continuation.md) can automatically plan and execute the next approved batch after verified landed delivery. Its default authorization remains a fixed existing task list.
- Explicit [outcome discovery](project-outcome-discovery.md) adds an objective, allowed paths, new-task budget and total Manager-call budget. After the authorized inventory is delivered, Manager can propose and select one new follow-up from a verified outcome. Host validation, create-once task publication and the same queue/execution path enforce the original bounds. No-follow-up means this turn found no justified task; it does not assert the whole project is complete.
- [Candidate quality](project-batch-quality.md) is opt-in per successful batch: configured checks, protected independent review and at most one separately authorized correction. Final delivery still requires the exact reviewed candidate to be landed and explicitly accepted.
- [Recovery](project-batch-recovery.md) retries only proven failed Workers while preserving successful siblings and the original baseline. Unknown executions hold their slot. Explicit abandonment closes ended, undelivered work without manufacturing delivery.
- Accepted outcome facts are retained in the existing role-memory WAL with execution provenance. Discovery receives verified delivery/check/review facts directly, plus ordinary memory with its existing provenance and filtering. It does not treat a Worker's narrative as learned truth.

## What the evidence does and does not prove

Implementation, simulated verification and real product acceptance are separate statuses. See [`m0-product-acceptance.md`](m0-product-acceptance.md) for source links and a reusable scorecard.

- Representative tests exercise HTTP, real local subprocesses, isolated Git workspaces, retained quality/review receipts and landed delivery. Outcome discovery's offline CLI fixture uses the production Manager planning interface, then creates a follow-up that was absent from the initial ledger. These controlled responses are not evidence of real-model prioritization quality.
- The owner's lean-testing policy retains one meaningful check per distinct behavior. Automatic GitHub CI is paused; report actual local test/lint/build results for the current commit and any missing browser or real-provider evidence. Historical CI results do not validate a new revision.
- Historical real-model checks cover first questions/configuration recovery and a bounded PRD revision handoff. They are not complete coding/review/QA delivery evidence.
- No five-task/two-project real delivery acceptance is claimed here. [#411](https://github.com/LukeJiaoR/GoodTeam/issues/411) remains the product-effect gate, independent from any implementation PR or [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419) documentation closure.

A catalog, installed CLI, Worker exit code, readable report or committed change decision cannot by itself establish that the requested result is accepted. Missing verification remains missing; fake results remain simulated.

## Current naming and remaining scope

`orchestrator` remains the stable internal key; the UI uses **Task Assistant / 任务助手**. Historical documents may say Manager or Orchestrator.

The M0 acceptance target is desktop Web, an explicitly configured local Git repository, a verified real provider/model configuration and bounded coding work. Project-level repeated iterations remain the product target in #381 and the real acceptance record in #419. The implemented discovery mode has finite preapproved scope and still stops for independent delivery decisions. Arbitrary mid-Worker changes, natural-language approval, workspace-shared Team migration ([#409](https://github.com/LukeJiaoR/GoodTeam/issues/409)), external Planner/MCP expansion and commercial packaging are not silently included. Existing optional adapters are not removed, but unsupported or unverified combinations must not be advertised as accepted.

The minimum change-status contract is documented in [`conversation-change-status.md`](conversation-change-status.md). Prioritize further architecture only when a real task demonstrates the need.

## Documentation source of truth

1. Code and tests for executable behavior.
2. Stable manuals linked from [`README.md`](README.md) for current product/operator contracts.
3. This file for short-lived orientation.
4. Historical plans, audits, progress logs, research and dogfood records for bounded evidence, not blanket current acceptance.

`openwiki/` is generated/read-only. `docs/` is the canonical human-maintained documentation source.
