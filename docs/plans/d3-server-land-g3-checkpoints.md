# D3 / Server Land / G3 Checkpoint Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkpoint IDs for review and traceability.

**Goal:** Build D3 dynamic topology, server-side land/tag safety, and G3 routing/LLM triage incrementally without bypassing human gates or weakening the local dashboard security boundary.

**Architecture:** GoodTeam keeps `StateStore` as the owned ledger. New DAG/routing/server-action behavior is added first as read-only evidence, then shadow execution, then gated execution. Every checkpoint lands as its own PR with a document update, targeted tests, verification evidence, and an explicit rollback point.

**Tech Stack:** Python stdlib HTTP server, GoodTeam local JSON/YAML-like config files, `StateStore`, workflow table/graph modules, React dashboard, pytest, ruff, Vite.

---

## Traceability Rules

- Every checkpoint gets its own PR.
- Every PR updates this file with status, scope, verification, and follow-up notes.
- Any implementation PR that changes workflow execution, routing, merge/tag behavior, or dashboard security must include a regression test for the old failure mode.
- Server-side merge/tag remains disabled until the checkpoint explicitly says otherwise and an environment capability gate is present.
- Human review remains the land gate throughout this sequence.

## Checkpoints

### CP0 - Checkpoint Ledger

**Status:** completed in branch `feat/server-safety-checkpoints`.

**Scope:** Create this traceability document and reconcile current `origin/main` against the parked items.

**Entry Gate:** Current `origin/main` is `87324c5` or newer. Work happens in an isolated worktree, not the divergent root checkout.

**Exit Evidence:**
- This file exists under `docs/plans/`.
- The first implementation PR links its scope to a checkpoint ID.
- Worktree branch was cut from `origin/main` at `87324c5`.

**Rollback:** Revert the docs-only PR or the docs commit.

### CP1 - Local Web Safety Baseline

**Status:** completed in branch `feat/server-safety-checkpoints`.

**Scope:** Close the local-dashboard security gaps that directly block future server-side actions. Current `origin/main` already rejects unregistered absolute artifact reads, avoids wildcard CORS by default, rejects cross-origin POSTs, uses path-relative containment for file-manager open, and makes dispatch file collisions fail `all_ok`. This checkpoint finishes the remaining config writer/parser hardening for scalar values that can otherwise be interpreted as pseudo-YAML syntax.

**Entry Gate:** No server-side land/tag execution is added in this checkpoint.

**Implementation Notes:**
- Preserve existing UI and theme behavior.
- Keep project config files human-readable.
- Quote only scalar values that need protection, so existing simple config output remains stable.
- Parser must preserve literal quote-wrapped command strings.

**Verification:**
- Targeted config writer/parser pytest.
- Related API/web security pytest.
- `ruff` on touched Python files.
- `git diff --check`.

**Evidence Captured:**
- `PYTHONPATH=/Users/luke/WorkSpace/codeAi/goodteam-dev-server-safety/src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_init.py tests/test_projects.py tests/test_web.py -q -k 'parse_simple_yaml or create_local_project_config or load_project_target or update_worker_backend_config or project_config'` -> `22 passed, 157 deselected`.
- `PYTHONPATH=/Users/luke/WorkSpace/codeAi/goodteam-dev-server-safety/src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py tests/test_web.py tests/test_workers.py -q -k 'artifact_route_rejects_unregistered_absolute_path or Access-Control-Allow-Origin or cross_origin or open_path_in_file_manager_rejects_sibling_prefix_path or dispatch_tracks_reports_file_collisions or create_local_project_config or parse_simple_yaml'` -> `19 passed, 268 deselected`.
- `PYTHONPATH=/Users/luke/WorkSpace/codeAi/goodteam-dev-server-safety/src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `560 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/models.py src/goodteam_os/web.py tests/test_init.py tests/test_web.py` -> `All checks passed!`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `cd frontend && npm run lint` -> exit 0.
- `cd frontend && npm run build` -> Vite build succeeded.
- `git diff --check` -> exit 0.

**Rollback:** Revert the CP1 PR. Future server-action checkpoints stay blocked until an equivalent safety baseline exists.

### CP2 - Server Action Capability Model

**Status:** planned.

**Scope:** Add a read-only capability report for server actions. It reports whether dispatch, dry-smoke, land, tag, and graduate actions are available, disabled, or blocked by missing auth/capability gates.

**Entry Gate:** CP1 merged.

**Implementation Notes:**
- Add no merge/tag execution.
- Add capability reasons such as `disabled_by_default`, `missing_env_gate`, `unsafe_origin`, and `human_command_only`.
- UI may render readiness, but not a destructive button.

**Verification:**
- API contract tests for disabled capabilities.
- Frontend contract/build if UI is touched.

**Rollback:** Revert CP2 PR; existing command-copy workflow remains.

### CP3 - D3 Execution Plan v2 Validation

**Status:** planned.

**Scope:** Introduce a read-only `workflow_execution_plan_v2` artifact/projection with nodes, dependencies, write domains, runner kind, gates, and validation errors.

**Entry Gate:** CP1 merged. No execution semantics change.

**Implementation Notes:**
- Reject unknown dependencies, cycles, missing fan-in nodes, and write-domain collisions.
- Existing workflow remains linear.
- UI graph can show the plan, but does not run it.

**Verification:**
- Unit tests for validation failures and a valid linear plan.
- Existing workflow graph tests remain green.

**Rollback:** Revert CP3 PR; old workflow graph projection remains.

### CP4 - Sequential Dynamic Topology Runner

**Status:** planned.

**Scope:** Add a sequential topo runner behind a feature flag or explicit project capability. It consumes validated plan v2 but executes one node at a time.

**Entry Gate:** CP3 merged and validated on a real sample plan.

**Implementation Notes:**
- Fix checkpoint identity before node revisits: checkpoint keys need sequence/attempt metadata, not just `current_node`.
- Derive dashboard progress from the workflow table/plan instead of hardcoded step order.
- Keep fan-in behavior explicit and auditable.

**Verification:**
- Red/green tests for node revisit checkpoint preservation.
- Red/green tests for plan-derived progress.
- Focused graph regression.

**Rollback:** Disable the feature flag or revert CP4 PR; legacy driver remains available.

### CP5 - G3 Shadow Routing

**Status:** planned.

**Scope:** Let failure-pattern recommendations produce `would_route_to` evidence while deterministic routing remains authoritative.

**Entry Gate:** CP1 merged. Existing failure-pattern readiness metrics available.

**Implementation Notes:**
- No route change in this checkpoint.
- Record pattern signature, hit count, confidence inputs, deterministic disposition, and shadow disposition.
- Add readiness summary fields for false-positive review.

**Verification:**
- Tests prove actual disposition still comes from deterministic table.
- Tests prove shadow evidence is recorded when a pattern matches.

**Rollback:** Revert CP5 PR; deterministic G3a/G3b behavior remains.

### CP6 - Server-Side Land/Tag Readiness Only

**Status:** planned.

**Scope:** Surface exact readiness and copyable command data for `land-pr.sh` and `graduate-release.sh` without executing them from the server.

**Entry Gate:** CP2 merged. Cross-origin and config hardening verified.

**Implementation Notes:**
- Server recomputes PR/worktree/ref from trusted local state.
- Report missing CI, missing pre-review, dirty worktree, missing rationale, and disabled server execution.
- No `gh pr merge`, `git tag`, or `git checkout` from HTTP handlers.

**Verification:**
- API tests for ready/not-ready states.
- UI contract/build if rendered.

**Rollback:** Revert CP6 PR; manual scripts still work.

### CP7 - D3 Parallel Read-Only/Fake Execution

**Status:** planned.

**Scope:** Enable parallel level execution only for read-only advisors, debate nodes, or fake workers with declared disjoint write domains.

**Entry Gate:** CP4 merged. No real shared-repo writes in parallel.

**Implementation Notes:**
- Fail closed on write-domain collision.
- Fan-in nodes wait for all dependencies.
- Preserve result ordering for downstream evidence.

**Verification:**
- Fake-worker tests prove parallel level scheduling.
- Collision tests prove fail-closed behavior.
- No real worker smoke until isolation strategy is selected.

**Rollback:** Disable the feature flag or revert CP7 PR.

### CP8 - G3 Canary Routing

**Status:** planned.

**Scope:** Apply pattern recommendations only to high-confidence, low-risk failure classes.

**Entry Gate:** CP5 has accumulated reviewable shadow data.

**Implementation Notes:**
- Allowed classes start with auth/rate-limit/network style causes.
- Unknown, scaffold, code-bug, and ambiguous-spec remain deterministic/human-gated.
- Every canary route logs evidence and can be disabled.

**Verification:**
- Tests for threshold failures and allowed canary successes.
- Regression proving unsafe classes do not route.

**Rollback:** Turn off canary flag or revert CP8 PR.

### CP9 - Server-Side Land/Tag Execution Gate

**Status:** parked until CP1, CP2, CP6, and enough operational evidence are complete.

**Scope:** Execute land/tag only behind an explicit environment capability, same-origin enforcement, CSRF/action nonce, typed confirmation, command allowlist, and audit record.

**Entry Gate:** CP6 readiness has been used manually without incident. Founder explicitly approves enabling server execution.

**Implementation Notes:**
- Use argv allowlists, never arbitrary shell from request bodies.
- Execute out-of-process and never mutate the serving checkout unexpectedly.
- Record actor/session, command argv, preflight, stdout/stderr summary, result, and rollback hint.

**Verification:**
- Unit tests for rejected origins, missing nonce, wrong typed confirmation, unsafe refs, and disabled env.
- Dry-run integration before any live execution.

**Rollback:** Remove the env gate or revert CP9 PR; command-copy workflow remains.
