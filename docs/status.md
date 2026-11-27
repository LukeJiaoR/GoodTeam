# GoodTeam Current Status

Source baseline verified on 2026-11-24: [main at 5229031](https://github.com/LukeJiaoR/GoodTeam/commit/52290315e8e9c84c7896170f05e5f78b6bc95f8f).

This file is the short-lived current-state entry point for contributors and agents. Its implementation statements describe the code revision containing this document; a PR branch is not a deployed release. Historical implementation footprints remain in [`current-progress.md`](current-progress.md); active work belongs in GitHub Issues and pull requests rather than a second backlog here.

## Open Issues at this baseline

| Scope | Owner and remaining work |
| --- | --- |
| Project iterations | [#381](https://github.com/LukeJiaoR/GoodTeam/issues/381) tracks the product direction. [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419) owns real acceptance, including the five-task, two-project scorecard inherited from closed [#411](https://github.com/LukeJiaoR/GoodTeam/issues/411). |
| Manager runtime | [#738](https://github.com/LukeJiaoR/GoodTeam/issues/738) tracks the [Codex app-server Manager](manager-app-server.md), including real delegation, user priority and recovery. |
| Provider and Role Skills | [#438](https://github.com/LukeJiaoR/GoodTeam/issues/438) tracks cross-agent reuse. PR #752 landed [shared Codex/Pi Providers](codex-providers.md) and [locked QA Skill input](role-skill-input-contract.md) for ordinary isolated read-only QA. StockFlow browser evidence remains due in [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419). |
| Runtime permissions | [#751](https://github.com/LukeJiaoR/GoodTeam/issues/751) tracks project defaults, Role overrides, Manager selection and native sandbox and approval settings. |
| Later experiment | [#749](https://github.com/LukeJiaoR/GoodTeam/issues/749) records the proposed BabelTele context-compression experiment. |
| Parked | [#409](https://github.com/LukeJiaoR/GoodTeam/issues/409) covers workspace-wide Team configuration, [#420](https://github.com/LukeJiaoR/GoodTeam/issues/420) mobile Team layout and [#424](https://github.com/LukeJiaoR/GoodTeam/issues/424) diagnostics for a repository-context check. |

## Current product surface

```text
Projects → Work / Team / Knowledge
```

- `/projects` imports, opens or removes projects. Removal preserves the local repository and GoodTeam history. Adding the same directory restores the project.
- `/projects/<PROJECT_KEY>/work` is the primary Task Assistant / 任务助手 conversation for understanding, work intake, clarification, progress and explicit recovery.
- Work keeps project/task identity and one next step visible. The context action locates existing controls; it does not independently approve or execute work.
- The composer accepts project questions and task drafts. Confirm a draft to turn its exact revision into a batch.
- Confirm a displayed Manager recommendation to queue and run it in one action.
- Team exposes project-owned Roles and Worker configuration.
- Knowledge exposes repository understanding and memory.
- Settings exposes appearance and language controls in the workspace and project shell.
- The dashboard can read historical server-action audits and execution evidence. The legacy launch and server-action POSTs are retired.

## Current control-plane model

```text
Project import → Task Assistant conversation
→ read-only answer / proposed work
→ explicit, version-bound human confirmation
→ task batch + isolated Worker execution
→ Manager selects evidence, QA, Code Review or correction
→ scoped delivery decision + Git verification → ledger + artifacts + memory
```

Readiness remains backend-owned. Model interpretation is advisory, never authorization. Role definitions, TeamRole membership and Worker identity/lifecycle/runtime are implemented through `team_control_plane.py`; the Task Assistant is one logical owner, not a TeamRole. Team is currently project-scoped, not workspace-wide. Legacy configuration is retained as a compatibility path.

Runs bind execution identity and configuration; later configuration changes must not rewrite historical evidence. Pre-confirmation model execution requires a verifiable read-only boundary or fails closed. Conversation, artifact reads and navigation do not grant approval or dispatch authority.

## Current project iteration loop

- Manager's chosen existing tasks enter the ordinary Task Assistant queue; confirmed batches reuse the isolated `dispatch_tracks` pool, with up to 10 Workers concurrently. The same LocalTaskStore cards own their claims, result sources and delivery receipts.
- [Continuation](project-continuation.md) can automatically plan and execute the next approved batch after verified landed delivery. Its default authorization remains a fixed existing task list.
- Explicit [outcome discovery](project-outcome-discovery.md) adds an objective, allowed paths, new-task budget and total Manager-call budget. After the authorized inventory is delivered, Manager can propose and select one new follow-up from a verified outcome. Host validation, create-once task publication and the same queue/execution path enforce the original bounds. No-follow-up means this turn found no justified task; it does not assert the whole project is complete.
- [Candidate quality](project-batch-quality.md) is opt-in per successful batch: configured checks, protected independent review and at most one separately authorized correction. Configured quality requires passing checks and independent acceptance of the landed candidate. For ordinary batches, Manager can accept the exact candidate using the observed batch evidence. Delivery verifies the Git result.
- [Recovery](project-batch-recovery.md) retries only proven failed Workers while preserving successful siblings and the original baseline. Unknown executions hold their slot. Explicit abandonment closes ended, undelivered work without manufacturing delivery.
- Accepted outcome facts are retained in the existing role-memory WAL with execution provenance. Discovery receives verified delivery/check/review facts directly, plus ordinary memory with its existing provenance and filtering. It does not treat a Worker's narrative as learned truth.

## What the evidence does and does not prove

Implementation, simulated verification and real product acceptance are separate statuses. See [`m0-product-acceptance.md`](m0-product-acceptance.md) for source links and a reusable scorecard.

- Representative tests exercise HTTP, real local subprocesses, isolated Git workspaces, retained quality/review receipts and landed delivery. Outcome discovery's offline CLI fixture uses the production Manager planning interface, then creates a follow-up that was absent from the initial ledger. These controlled responses are not evidence of real-model prioritization quality.
- The owner's lean-testing policy retains one meaningful check per distinct behavior. Automatic GitHub CI is paused; report actual local test/lint/build results for the current commit and any missing browser or real-provider evidence. Historical CI results do not validate a new revision.
- The [two-iteration run at d190831](https://github.com/LukeJiaoR/GoodTeam/issues/419#issuecomment-7033914597) recorded two autonomous selections. The first delivered. Independent review blocked the second. The five-task, two-project scorecard remains open in [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419).

A catalog, installed CLI, Worker exit code, readable report or committed change decision cannot by itself establish that the requested result is accepted. Missing verification remains missing; fake results remain simulated.

## Current naming and remaining scope

`orchestrator` remains the stable internal key; the UI uses **Task Assistant / 任务助手**. Historical documents may say Manager or Orchestrator.

The M0 acceptance target is desktop Web, an explicitly configured local Git repository, a verified real provider/model configuration and bounded coding work. Project-level repeated iterations remain the product target in #381 and the real acceptance record in #419. The implemented discovery mode has finite preapproved scope and still stops for independent delivery decisions. Arbitrary mid-Worker changes, natural-language approval, workspace-shared Team migration ([#409](https://github.com/LukeJiaoR/GoodTeam/issues/409)), external Planner/MCP expansion and commercial packaging are not silently included. Use the supported runtime documented in the project’s verification records.

## Documentation source of truth

1. Code and tests for executable behavior.
2. Stable manuals linked from [`README.md`](README.md) for current product/operator contracts.
3. This file for short-lived orientation.
4. Historical plans, audits, progress logs, research and dogfood records for bounded evidence, not blanket current acceptance.

`openwiki/` is generated/read-only. `docs/` is the canonical human-maintained documentation source.
