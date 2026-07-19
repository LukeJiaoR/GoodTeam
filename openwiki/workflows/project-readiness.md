---
type: Workflow
title: Project import and readiness
description: GoodTeam's safe, persisted preparation workflow that classifies a local project, performs bounded readiness steps, and gates delivery-workflow start.
tags: [workflow, project-import, readiness, safety]
---

# Project import and readiness

Project readiness prevents “a directory is loaded” from being confused with “it is safe to start AI-assisted delivery.” It **gates** [the control plane](../architecture/control-plane.md): only a backend-computed capability unlocks workflow start after the project root, required steps, artifacts, and execution state are all complete and bound.

## Safe import and persistence

`project_import.py` imports an absolute readable directory into the GoodTeam workspace. The registration is idempotent by canonical root: importing the same root returns the existing project instead of creating a second record. Registration and readiness state stay in the GoodTeam workspace; import does not initialize Git or otherwise mutate the selected target.

Import and later readiness mutation use a workspace lock plus descriptor/inode anchoring and atomic writes. The code rejects workspace/target overlap, avoids symlink traversal, verifies the recorded root identity, and protects configuration publication/rollback. These are intentional security and recovery properties, not implementation details to relax.

Readiness lives at:

```text
<workspace>/state/projects/<PROJECT_KEY>/readiness.json
```

The executor upgrades initial readiness data to schema version 2, which records root identity, lifecycle, step/execution/attempt identity, inputs, artifacts, errors, recovery, and revision state. An execution-specific artifact key has the form `readiness://<execution-id>/<attempt>/<step-id>`.

## Modes and execution lifecycle

The classifier treats an empty directory as `new_project`, a directory with repository/source signals as `existing_project`, and another non-empty directory as `unconfirmed`. A user can resolve the ambiguous case through the API/UI.

| Mode | Required preparation behavior |
| --- | --- |
| All modes | Project record, safe preflight, and project-mode decision. |
| New project | Initialization details, a goal/brief, then dry-run worker preflight. |
| Existing project | Repository understanding, user confirmation of candidate commands and constraints, then dry-run worker preflight. |

Existing-project inspection is intentionally bounded and read only: it opens the anchored root, skips symlinks and generated/vendor areas, reads selected manifests/docs with limits, and discovers—but never executes—candidate commands. A command must be explicitly confirmed. New-project preparation similarly stores inputs/artifacts without creating target-root files.

Steps can be pending, running, blocked for input, complete, failed, or cancelled; readiness is preparing, attention-required, or ready. Start payload idempotency, runner heartbeats/leases, execution/attempt matching, and interrupted-run detection prevent a stale runner from publishing after cancellation or retry. This safety model is important to [operations and quality](../operations/operator-and-quality.md), where readiness tests cover isolated and read-only behavior.

## Capability gate and HTTP resources

The backend alone computes `start_workflow`. It requires schema v2; the exact mode-specific required-step set; complete, project/root-bound steps and artifacts; a complete bound execution; and readiness lifecycle `ready`. The workflow endpoint repeats that check before bridging to the existing workflow view.

Key resources implemented in `web.py`:

```text
GET  /api/projects
POST /api/projects/import
GET  /api/projects/{key}/home
POST /api/projects/{key}/mode
POST /api/projects/{key}/readiness/start|input|retry|cancel
GET  /api/projects/{key}/readiness/steps/{step_id}/artifact
POST /api/projects/{key}/workflow
```

Errors have a stable envelope: `code`, `message`, `details`, `recovery`, and `request_id`.

## UI relationship

[The web experience](../product/web-experience.md) presents this server-persisted model through `/projects` and `/projects/:projectKey/home`. It polls while an executor is running, offers structured input/confirmation and recovery actions, renders artifacts, and disables workflow start using the backend capability and reason. The browser never marks a project ready itself.

## Change guide and verification

Primary sources:

- `src/goodteam_os/project_import.py` — canonical-root import, locks, root bindings, hardened filesystem operations.
- `src/goodteam_os/project_readiness.py` — classification, state/store, capability calculation.
- `src/goodteam_os/project_readiness_executor.py` — executor steps, inputs/artifacts, retries, leases, bounded scans, dry-run preflight.
- `src/goodteam_os/web.py` — resource handlers and product-route fallback.

Protect the project/root binding, read-only inspection policy, atomic persistence, and idempotency/retry behavior when changing this area. Focused backend coverage is in `tests/test_project_import.py`, `tests/test_project_readiness.py`, `tests/test_project_readiness_executor.py`, and `tests/test_project_readiness_web.py`; route/component coverage also lives under `frontend/src/features/projects/` and `frontend/e2e/project-load-home.spec.js`.

`docs/readiness-executors.md` is the current source-level guide. An older statement in `docs/operator-guide.md` that mode-specific readiness execution is not implemented conflicts with the executor, API/UI, and test evidence and should not be used as current behavior.
