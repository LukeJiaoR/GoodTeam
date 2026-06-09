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
- New or expanded server-side merge/tag execution remains disabled unless a
  checkpoint explicitly allows it, an environment capability gate is present, and
  the action has same-origin, nonce, typed-confirmation, argv allowlist, and audit
  coverage. CP9 is the current exception for `land_pr` and `graduate_release`;
  `tag_release` remains disabled/report-only.
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

**Status:** completed in branch `feat/server-action-capabilities`.

**Scope:** Add a read-only capability report for server actions. It reports whether dispatch, dry-smoke, land, tag, and graduate actions are available, disabled, or blocked by missing auth/capability gates.

**Entry Gate:** CP1 merged.

**Implementation Notes:**
- Add no merge/tag execution.
- Add capability reasons such as `disabled_by_default`, `missing_env_gate`, `unsafe_origin`, and `human_command_only`.
- UI may render readiness, but not a destructive button.

**Verification:**
- API contract tests for disabled capabilities.
- Frontend contract/build if UI is touched.

**Evidence Captured:**
- `PYTHONPATH=/Users/luke/WorkSpace/codeAi/goodteam-dev-cp2-capabilities/src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py tests/test_web.py -q -k 'server_actions or devloop or release_readiness'` -> `3 passed, 206 deselected`.
- `PYTHONPATH=/Users/luke/WorkSpace/codeAi/goodteam-dev-cp2-capabilities/src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `561 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `cd frontend && npm run lint` -> exit 0.
- `cd frontend && npm run build` -> Vite build succeeded.
- `git diff --check` -> exit 0.

**Rollback:** Revert CP2 PR; existing command-copy workflow remains.

### CP3 - D3 Execution Plan v2 Validation

**Status:** completed in branch `feat/workflow-plan-v2`.

**Scope:** Introduce a read-only `workflow_execution_plan_v2` artifact/projection with nodes, dependencies, write domains, runner kind, gates, and validation errors.

**Entry Gate:** CP1 merged. No execution semantics change.

**Implementation Notes:**
- Reject unknown dependencies, cycles, missing fan-in nodes, and write-domain collisions.
- Existing workflow remains linear.
- UI graph can show the plan, but does not run it.

**Verification:**
- Unit tests for validation failures and a valid linear plan.
- Existing workflow graph tests remain green.

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'execution_plan_v2'` first failed with `ImportError: cannot import name 'validate_workflow_execution_plan_v2'`.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges tests/test_web.py::test_build_workflow_graph_emits_forward_compat_error_and_blocked_edges -q` first failed because `workflow_execution_plan_v2` was absent from the graph payload.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'execution_plan_v2'` -> `3 passed, 19 deselected`.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges tests/test_web.py::test_build_workflow_graph_emits_forward_compat_error_and_blocked_edges -q` -> `2 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py tests/test_web.py tests/test_api.py -q -k 'workflow_graph or workflow_node or workflow_dag or execution_plan or dashboard_stepper or api_workflow_graph'` -> `22 passed, 209 deselected`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `564 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Revert CP3 PR; old workflow graph projection remains.

### CP4 - Sequential Dynamic Topology Runner

**Status:** completed in branch `feat/sequential-topo-runner`.

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

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'topology_runner or legacy_runner_by_default'` first failed because `advance_approved_workflow_topology` and graph-local `workflow_execution_plan_v2` did not exist.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_state.py -q -k 'disambiguates_revisited'` first failed because checkpoint JSON did not include `checkpoint.node` / `checkpoint.sequence` metadata.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py::test_topology_runner_consumes_plan_v2_nodes_without_legacy_node_lookup -q` failed while the topology path delegated to the legacy `workflow_node()` lookup.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'topology_runner or legacy_runner_by_default'` -> `4 passed, 22 deselected`.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_state.py -q -k 'disambiguates_revisited'` -> `2 passed, 5 deselected`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py tests/test_graph_sample.py tests/test_state.py tests/test_web.py -q -k 'topology_runner or legacy_runner_by_default or advance_approved_workflow or disambiguates_revisited or workflow_graph or dashboard_stepper'` -> `16 passed, 246 deselected`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/graph.py src/goodteam_os/state.py tests/test_workflow_table.py tests/test_state.py` -> `All checks passed!`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `568 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Disable the feature flag or revert CP4 PR; legacy driver remains available.

### CP5 - G3 Shadow Routing

**Status:** completed in branch `feat/g3-shadow-routing`.

**Scope:** Let failure-pattern recommendations produce `would_route_to` evidence while deterministic routing remains authoritative.

**Entry Gate:** CP1 merged. Existing failure-pattern readiness metrics available.

**Implementation Notes:**
- No route change in this checkpoint.
- Record pattern signature, hit count, confidence inputs, deterministic disposition, and shadow disposition.
- Add readiness summary fields for false-positive review.

**Verification:**
- Tests prove actual disposition still comes from deterministic table.
- Tests prove shadow evidence is recorded when a pattern matches.

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py::test_failure_pattern_library_reports_routing_readiness_without_applying_patterns -q` first failed because readiness reports lacked `shadow_routing`, `false_positive_review_required`, `would_route_to`, and `confidence_inputs`.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition -q` first failed because triage actions lacked `would_route_to` shadow evidence.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py::test_failure_pattern_library_reports_routing_readiness_without_applying_patterns -q` -> `1 passed`.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event -q` -> `2 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_cli.py -q -k 'failure_pattern or triage or worker_failure or retry_exhausted or failure_patterns_outputs'` -> `12 passed, 78 deselected`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/failure_patterns.py src/goodteam_os/graph.py tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_cli.py` -> `All checks passed!`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `568 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Revert CP5 PR; deterministic G3a/G3b behavior remains.

### CP6 - Server-Side Land/Tag Readiness Only

**Status:** completed in branch `feat/land-tag-readiness`.

**Scope:** Surface exact readiness and copyable command data for `land-pr.sh` and `graduate-release.sh` without executing them from the server.

**Entry Gate:** CP2 merged. Cross-origin and config hardening verified.

**Implementation Notes:**
- Server recomputes PR/worktree/ref from trusted local state.
- Report missing CI, missing pre-review, dirty worktree, missing rationale, and disabled server execution.
- No `gh pr merge`, `git tag`, or `git checkout` from HTTP handlers.

**Verification:**
- API tests for ready/not-ready states.
- UI contract/build if rendered.

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_dev_loop_pre_reviews_reflect_report_presence -q` first failed because the readiness projection did not include review rationale command wiring or worktree dirty-state checks.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_dev_loop_pre_reviews_reflect_report_presence -q` -> `1 passed`.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_dev_loop_pre_reviews_reflect_report_presence tests/test_api.py::test_api_server_actions_reports_disabled_destructive_capabilities -q` -> `2 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py tests/test_api.py -q -k 'dev_loop or server_actions or release_readiness or frontend_contract_exposes_release_readiness'` -> `11 passed, 198 deselected`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/web.py tests/test_web.py tests/test_api.py` -> `All checks passed!`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `568 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.
- Static endpoint/command scan: `rg -n "gh pr merge|git tag|git checkout|do_POST.*land|land_pr|tag_release|graduate_release|server_execution" src/goodteam_os/web.py tests/test_web.py tests/test_api.py` -> no new land/tag execution handler or destructive command; only disabled capability/readiness fields and tests.

**Rollback:** Revert CP6 PR; manual scripts still work.

### CP7 - D3 Parallel Read-Only/Fake Execution

**Status:** completed in branch `feat/d3-parallel-readonly`.

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

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'parallel_execution_plan'` first failed because `workflow_parallel_execution_plan` did not exist.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'parallel_execution_plan'` -> `2 passed, 26 deselected`.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q` first failed because `workflow_execution_plan_v2` did not expose `parallel_execution_plan`.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q` -> `1 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'parallel_execution_plan or execution_plan_v2'` -> `5 passed, 23 deselected`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/workflow_table.py tests/test_workflow_table.py tests/test_web.py` -> `All checks passed!`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py tests/test_web.py tests/test_api.py -q -k 'workflow_graph or workflow_node or workflow_dag or execution_plan or parallel_execution_plan or api_workflow_graph'` -> `23 passed, 214 deselected`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `570 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Disable the feature flag or revert CP7 PR.

### CP8 - G3 Canary Routing

**Status:** completed in branch `feat/g3-canary-routing`.

**Scope:** Apply pattern recommendations only to high-confidence, low-risk failure classes.

**Entry Gate:** CP5 has accumulated reviewable shadow data.

**Implementation Notes:**
- Allowed classes start with auth/rate-limit/network style causes.
- Unknown, scaffold, code-bug, and ambiguous-spec remain deterministic/human-gated.
- Every canary route logs evidence and can be disabled.

**Verification:**
- Tests for threshold failures and allowed canary successes.
- Regression proving unsafe classes do not route.

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py::test_canary_routing_decision_applies_only_high_confidence_low_risk_patterns tests/test_graph_sample.py::test_worker_failure_triage_applies_canary_route_for_high_confidence_network_pattern -q` first failed because `canary_routing_decision` did not exist.
- Targeted green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py::test_canary_routing_decision_applies_only_high_confidence_low_risk_patterns tests/test_graph_sample.py::test_worker_failure_triage_applies_canary_route_for_high_confidence_network_pattern tests/test_graph_sample.py::test_worker_failure_triage_can_disable_canary_route_with_env_gate tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event -q` -> `5 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_cli.py tests/test_workflow_table.py -q -k 'failure_pattern or canary or triage or worker_failure or retry_exhausted or disposition_for_failure_class or failure_patterns_outputs'` -> `16 passed, 105 deselected`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/failure_patterns.py src/goodteam_os/graph.py tests/test_failure_patterns.py tests/test_graph_sample.py` -> `All checks passed!`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `573 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Turn off canary flag or revert CP8 PR.

### CP9 - Server-Side Land/Tag Execution Gate

**Status:** completed in branch `feat/server-action-execution-gate`.

**Scope:** Execute the destructive server actions only behind an explicit environment capability, same-origin enforcement, action nonce, typed confirmation, command allowlist, readiness-derived argv construction, and audit record. This checkpoint enables guarded execution for `land_pr` and `graduate_release`; `tag_release` stays disabled/report-only.

**Entry Gate:** CP6 readiness has been used manually without incident. Founder explicitly approves enabling server execution.

**Implementation Notes:**
- Use argv allowlists, never arbitrary shell from request bodies.
- Require `GOODTEAM_SERVER_ACTIONS=1` and non-empty `GOODTEAM_SERVER_ACTION_NONCE`.
- Compare the nonce with `hmac.compare_digest`.
- Keep POST same-origin enforcement on `/api/server-actions/run`.
- Require exact typed confirmations: `LAND PR <number>` or `GRADUATE RELEASE <ref>`.
- Derive `land_pr` worktree and review-rationale paths from local Dev Loop readiness, not request-supplied paths.
- Reject unsafe release refs before subprocess execution.
- Execute out-of-process with `shell=False`, fixed argv, timeout, captured output, and noninteractive `graduate-release.sh --yes`.
- Write JSON audit records under `.goodteam/server-actions/` with actor, origin, argv, timestamps, return code, stdout/stderr tails, and rollback hint.

**Verification:**
- Unit tests for rejected origins, missing nonce, wrong typed confirmation, unsafe refs, and disabled env.
- Dry-run integration before any live execution.

**Evidence Captured:**
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py::test_api_server_action_run_rejects_unsafe_release_refs_before_subprocess -q` first failed because `../../abc1234` was accepted and execution returned `202`.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py::test_api_server_action_run_derives_land_paths_from_readiness_not_request -q` first failed because request-supplied `/tmp/spoofed-worktree` was used in argv.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py::test_api_server_action_run_executes_graduate_release_non_interactively -q` first failed because `--yes` was missing from the graduate argv.
- Targeted red: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py::test_api_server_action_run_writes_audit_when_subprocess_times_out -q` first failed because timeout raised through the route as `400` without an audit record.
- Focused green: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py::test_api_server_action_run_rejects_until_env_nonce_and_confirmation_pass tests/test_api.py::test_api_server_action_run_rejects_unsafe_release_refs_before_subprocess tests/test_api.py::test_api_server_action_run_executes_allowlisted_land_pr_and_writes_audit tests/test_api.py::test_api_server_action_run_derives_land_paths_from_readiness_not_request tests/test_api.py::test_api_server_action_run_executes_graduate_release_non_interactively tests/test_api.py::test_api_server_action_run_writes_audit_when_subprocess_times_out tests/test_api.py::test_api_server_action_run_resolves_project_root_from_project_config tests/test_api.py::test_api_post_same_origin_guard_rejects_cross_origin_and_allows_trusted_requests tests/test_web.py::test_build_server_actions_marks_destructive_actions_available_only_with_env_gate tests/test_web.py::test_build_dev_loop_reports_release_graduation_readiness_without_mutating -q` -> `10 passed`.
- Related regression: `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_api.py tests/test_web.py -q -k 'server_action or server_actions or same_origin or dev_loop or release_readiness'` -> `20 passed, 197 deselected`.
- `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q` -> `581 passed`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/web.py tests/test_api.py tests/test_web.py` -> `All checks passed!`.
- `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/` -> `All checks passed!`.
- `git diff --check` -> exit 0.

**Rollback:** Remove the env gate or revert CP9 PR; command-copy workflow remains.

### CP10 - Post-CP9 Docs and Backlog Reconciliation

**Status:** completed in branch `feat/post-cp9-docs-audit`.

**Scope:** Reconcile project status docs after CP3-CP9 so backlog and progress
ledgers no longer describe server-side land/graduate execution, D3, or G3 as
still wholly parked behind pre-implementation design gates.

**Entry Gate:** CP9 merged. No code, API, UI, runner, routing, merge, tag, or
release behavior changes are allowed in this checkpoint.

**Implementation Notes:**
- Keep this docs-only.
- Preserve the CP9 boundary: guarded execution exists for `land_pr` and
  `graduate_release`; `tag_release` remains disabled/report-only.
- Keep D3 remaining work framed around real fan-in, worker isolation,
  write-domain safety, and live smoke evidence.
- Keep G3 remaining work framed around outcome-quality measurement and
  unknown/ambiguous-failure evidence before broader routing or LLM triage.

**Verification:**
- `git diff --check`.
- Stale-phrase scan for pre-CP9 server-action wording.
- Self-review of docs-only diff.

**Rollback:** Revert the docs-only PR; runtime behavior is unaffected.

### CP11 - D3 Parallel Readiness Evidence

**Status:** completed in branch `feat/d3-parallel-readiness`.

**Scope:** Make the existing parallel-level projection report machine-readable
readiness for fake/read-only execution versus real-worker execution. This does
not execute real workers in parallel and does not change workflow advancement.

**Entry Gate:** CP7 read-only/fake parallel plan exists and CP10 docs are merged.

**Implementation Notes:**
- Add `parallel_readiness` to `workflow_parallel_execution_plan`.
- Preserve fail-closed behavior for sequential/human/real worker runner kinds.
- Report safe runner kinds, blocked runner kinds, whether worker isolation and
  live smoke are still required, and the blockers that prevent real parallel
  execution.
- Keep the dashboard workflow graph API read-only; it only exposes the new
  readiness metadata.

**Verification:**
- Red/green tests for fake/read-only readiness and blocked real-worker readiness.
- Workflow graph contract test proves the readiness metadata is surfaced.
- Related workflow/web regression.
- `ruff` on touched files.
- `git diff --check`.

**Rollback:** Revert CP11; CP7 parallel planning remains disabled for real
workers and runtime behavior is unaffected.
