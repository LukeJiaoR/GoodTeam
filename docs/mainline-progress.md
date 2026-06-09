# Mainline Progress

This file is the execution ledger for the remaining GoodTeam mainlines. It mirrors
`docs/current-progress.md`: each completed increment gets a dated entry, commit
reference, verification evidence, and follow-up status.

## Execution Order

| Order | Mainline | Status | Scope For This Pass | Completion Evidence |
| --- | --- | --- | --- | --- |
| 1 | G3c global failure-pattern library | Routing outcome telemetry completed 2026-06-09 | Add deterministic global store, record worker failures, surface best-match evidence, expose readiness metrics, and record shadow/canary routing outcomes without expanding routing. | Related G3 regression `16 passed`; ruff/diff clean |
| 2 | Dispatch provenance depth | Completed 2026-06-07 | Persist durable pre-review report capture and first-class review/land decision records beyond the existing air-drop land event. | `467 passed`; ruff clean; frontend build passed |
| 3 | Self-hosting control plane | Guarded server action execution completed 2026-06-08 | CP9 enabled env+nonce+typed-confirmation guarded execution for `land_pr` and `graduate_release`; `tag_release` remains disabled/report-only and human review remains the land gate. | `581 passed`; ruff clean; diff check clean |
| 4 | Theme/App.jsx productization | Approval metrics helper extraction completed 2026-06-08 | Keep Vivid and Minimal as permanent themes while shrinking App.jsx through focused component extraction without changing UI/theme styling. | Approval/nav/i18n/theme contract slice `20 passed`; frontend lint/build passed |
| 5 | Graph/DAG engine D2b/D3 | D3 parallel readiness evidence completed 2026-06-09 | Surface machine-readable readiness/blocker metadata for fake/read-only parallel planning versus still-blocked real-worker parallel execution. | Related D3/web regression `5 passed` + `6 passed`; ruff/diff clean |
| 6 | i18n | Lightweight foundation completed 2026-06-07 | Browser/system auto mode plus persisted `auto` / `en` / `zh-CN` switcher and first dashboard chrome extraction. | `9 passed` focused frontend contracts; frontend build passed |
| 7 | Backlog cleanup: pre-review report binding | Completed 2026-06-07 | Bind pre-review report/status selection to the current script invocation so concurrent or back-to-back code_review jobs cannot copy/fallback against another run's report. | `476 passed`; ruff clean; frontend build passed |
| 8 | Backlog status audit | Completed 2026-06-07 | Reconcile stale `docs/backlog.md` open items against shipped code/tests so only real open/gated work remains. | Targeted tests `34 passed`; diff check clean |
| 9 | Dispatch artifacts audit/memory | Audit artifact chain completed 2026-06-07 | Persist dispatch task/constraints/research source paths, write a PR-level archive manifest, and optionally archive/link human review rationale. | `487 passed`; ruff clean; frontend build passed |
| 10 | Backlog closure / gated parking | Completed 2026-06-07 | Resolve stale PRD refine-loop backlog via existing explicit refine workflow and move true decisions/external gates out of active backlog. | `9 passed` targeted regression; diff check clean |

## Active Increment

No active backlog increment remains. Remaining work is parked behind explicit gates
or deliberate incremental tracks: D3 real fan-in execution plus live-smoke and
worker-isolation evidence, deeper dynamic-plan/topological runner ownership beyond
the now-wired sequential and read-only/fake parallel slices, post-CP9 server-action
operational hardening and `tag_release` safety design, G3 routing outcome quality,
G3 unknown-failure data for LLM triage, external Codex auth recovery, shared-worktree
edit-collision need, optional full research/review memory ingestion, and continued
Theme panel/component token cleanup with visual review.
Theme System no longer has a Vivid-vs-Minimal winner gate; both themes stay
productized, with Vivid as default.

## Completed Increments

### 2026-06-09 — Post-CP9 backlog/status reconciliation

**Intent:** keep the project ledger accurate after CP3-CP9 landed, without adding
new runtime behavior.

**Implemented:**
- Reconciled `docs/backlog.md` so D3, G3, and self-hosting server-action sections
  reflect the merged CP3-CP9 state.
- Clarified that `land_pr` and `graduate_release` now have guarded server
  execution, while `tag_release` remains disabled/report-only.
- Kept the remaining work framed as operational hardening, live evidence,
  outcome-quality measurement, unknown-failure triage data, and UI/theme
  productization.

**Verification evidence:**
- Docs-only diff.
- `git diff --check`.
- Stale-phrase scan for pre-CP9 server-action wording.

**Deferred from this increment:**
- Any code, UI, runner, routing, merge, or tag behavior change.

### 2026-06-09 — D3 parallel readiness evidence

**Intent:** make the D3 parallel projection explain why fake/read-only planning is
allowed while real worker parallelism remains blocked.

**Implemented:**
- Added `parallel_readiness` to `workflow_parallel_execution_plan`.
- Reports `safe_runner_kinds`, `blocked_runner_kinds`, `requires_worker_isolation`,
  `requires_live_smoke`, and the blocker list reused from validation errors.
- Exposed the same readiness payload through `/api/workflow/graph` via the existing
  read-only `workflow_execution_plan_v2.parallel_execution_plan`.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py::test_workflow_parallel_execution_plan_allows_fake_readonly_levels_and_preserves_fanin_order tests/test_workflow_table.py::test_workflow_parallel_execution_plan_fails_closed_for_real_workers_and_write_collisions tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q`
  first failed because `parallel_readiness` was absent.
- Focused green:
  the same command -> `3 passed`.
- Related workflow-table regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py -q -k 'parallel_execution_plan or execution_plan_v2'`
  -> `5 passed, 23 deselected`.
- Related web/API regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py tests/test_api.py -q -k 'workflow_graph or workflow_node or workflow_dag or execution_plan or parallel_execution_plan or api_workflow_graph'`
  -> `6 passed, 211 deselected`.
- Quality gates:
  `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/workflow_table.py tests/test_workflow_table.py tests/test_web.py`
  -> `All checks passed!`.
- Whitespace:
  `git diff --check` -> exit 0.

**Deferred from this increment:**
- Real worker parallel execution.
- Worker isolation implementation.
- Live parallel worker smoke.

### 2026-06-09 — G3 routing outcome telemetry

**Intent:** add measurable outcome evidence before broader pattern-driven routing
or LLM triage is considered.

**Implemented:**
- Added `FailurePatternLibrary.record_routing_outcome(...)`.
- New patterns now carry zeroed `routing_outcomes` counters.
- `routing_readiness_report()` includes aggregate `routing_outcome_totals`, and
  each candidate includes its own `routing_outcomes`.
- Worker failure triage records `shadow_only` when a pattern match is observed
  but canary routing does not apply, and `confirmed` when canary routing applies.
- No routing rule was expanded; false-positive recording remains a manual review
  follow-up.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py::test_failure_pattern_library_records_routing_outcomes_for_quality_review -q`
  first failed because `record_routing_outcome` did not exist.
- Focused green:
  the same command -> `1 passed`.
- Red graph slice:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition tests/test_graph_sample.py::test_worker_failure_triage_applies_canary_route_for_high_confidence_network_pattern -q`
  first failed because patterns lacked `routing_outcomes` and triage did not
  record shadow/canary outcomes.
- Focused green:
  the same command -> `3 passed`.
- Related G3 regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_cli.py -q -k 'failure_pattern or canary or triage or worker_failure or retry_exhausted or failure_patterns_outputs'`
  -> `16 passed, 78 deselected`.
- Quality gates:
  `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/failure_patterns.py src/goodteam_os/graph.py tests/test_failure_patterns.py tests/test_graph_sample.py`
  -> `All checks passed!`.
- Whitespace:
  `git diff --check` -> exit 0.

**Deferred from this increment:**
- Broader pattern-driven routing.
- Automatic false-positive classification.
- G3 LLM triage for unknown/ambiguous failures.

### 2026-06-08 — Theme/App.jsx productization: approval metrics helper extraction

**Intent:** reduce `App.jsx` by moving approval-derived counters and banner copy
construction into a pure helper without changing approval UI behavior.

**Implemented:**
- Added `frontend/src/utils/approvalMetrics.js`.
- Moved approved/actionable counts, total-count fallback, progress percentage,
  and approval-banner question text construction into
  `buildApprovalMetrics({ approvalReviews, openQuestions, t })`.
- Preserved the existing filters, `approvalReviews.length || 6` fallback,
  progress math, and singular/plural interpolation.
- Updated `App.jsx` to import and destructure the helper result.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_approval_metrics_are_extracted_from_app_shell_without_behavior_changes -q`
  first failed because `approvalMetrics.js` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'approval_metrics or approval_helpers or project_nav_groups or approval_banner or approvals_page or frontend_contract_uses_i18n or theme_tokens'`
  -> `20 passed, 141 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx derived-data/helper extraction.
- Any visual redesign, theme-token conversion, or approval behavior changes.

### 2026-06-08 — Theme/App.jsx productization: project nav helper extraction

**Intent:** reduce `App.jsx` by moving detailed-project sidebar navigation
construction into a pure helper without changing the rendered sidebar.

**Implemented:**
- Added `frontend/src/utils/navigation.jsx`.
- Moved nav group construction into `buildProjectNavGroups(t)`.
- Preserved the same nav group i18n keys, tab IDs, and lucide icons.
- Removed the nav icon imports and inline `navGroups` array from `App.jsx`.
- Updated the dashboard i18n contract to follow the extracted helper.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_project_nav_groups_are_extracted_from_app_shell_without_chrome_changes -q`
  first failed because `navigation.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'project_nav_groups or project_sidebar or frontend_contract_uses_i18n or theme_tokens'`
  -> `18 passed, 142 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx derived-data/helper extraction.
- Any visual redesign, theme-token conversion, or sidebar behavior changes.

### 2026-06-08 — Theme/App.jsx productization: project content shell extraction

**Intent:** reduce `App.jsx` by moving the detailed-project content column while
preserving the existing shell chrome and main-panel composition.

**Implemented:**
- Added `frontend/src/components/ProjectContentShell.jsx`.
- Moved the content-column wrapper out of `App.jsx`, preserving
  `flex-grow flex flex-col min-w-0 min-h-screen`.
- Kept the existing approval banner, git creation notice, global toast, memory
  event modal, modal mount slots, and `ProjectMainPanel` composition unchanged.
- Kept all tab state, tab prop plumbing, and tab rendering in `App.jsx` as a
  `mainPanelChildren` fragment.
- Updated existing extraction contracts so the moved shell components are owned
  by `ProjectContentShell`, and expanded the shell theme-token contract to scan
  the new component.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_project_content_shell_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `ProjectContentShell.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'project_content_shell or project_main_panel or memory_event_detail_modal or global_toast or git_creation_notice or approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `26 passed, 133 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx shell/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved content-column wrapper.

### 2026-06-08 — Theme/App.jsx productization: project main panel extraction

**Intent:** reduce `App.jsx` by moving the detailed-project main wrapper while
preserving the existing loading, error, and tab-content shell behavior.

**Implemented:**
- Added `frontend/src/components/ProjectMainPanel.jsx`.
- Moved the detailed-project `<main>` wrapper out of `App.jsx`.
- Preserved the exact panel sizing/spacing classes, loading spinner branch,
  danger error banner classes, `AlertTriangle` icon, and loading-label i18n
  handoff.
- Kept all tab state, tab props, and tab component mounting in `App.jsx`; the
  extracted component only owns the wrapper/loading/error boundary.
- Expanded the shell theme-token contract so it scans the main-panel component
  with the other extracted shell components.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_project_main_panel_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `ProjectMainPanel.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'project_main_panel or memory_event_detail_modal or global_toast or git_creation_notice or approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `25 passed, 133 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx shell/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved main-panel wrapper.

### 2026-06-08 — Graph/DAG engine D2b: sequential dependency-gate runner semantics

**Intent:** make the declared DAG gate dependency on `security` affect production
workflow advancement while staying sequential and avoiding D3 parallel execution.

**Implemented:**
- Changed the RFC approval transition from `planner` to `security`.
- Kept `security` as a human-only/no-op workflow node that approves onward to
  `planner`.
- Updated `advance_approved_workflow` so a workflow node checks gate dependencies
  in `depends_on` before running; an unapproved gate dependency routes to that
  gate's review state instead of starting downstream workers.
- Mapped the security approval to the existing RFC artifact, making it an
  explicit security-risk review of the architecture rather than a new generated
  worker artifact.
- Updated workflow-step metadata and the legacy dashboard stepper so Security,
  Code Review, and Staging are distinct steps; the stepper now follows
  `workflow_execution_plan(...).order` with the adoption-only takeover prefix.
- Preserved sequential execution only. No dynamic per-run plan, worker fan-out,
  or parallel level execution was added.

**Verification evidence:**
- Red/green slice:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py::test_gate_transition_table_matches_ground_truth tests/test_workflow_table.py::test_security_gate_is_declared_as_human_only_transition tests/test_graph_sample.py::test_advance_approved_workflow_waits_for_security_gate_after_rfc tests/test_web.py::test_decide_approval_approves_rfc_and_runs_to_release_gate tests/test_web.py::test_decide_approval_approves_security_and_runs_planner -q`
  first failed because `rfc` still targeted `planner`, security pending did not
  move to `security_review_gate`, and RFC approval still ran planner/staging;
  after implementation it passed with `5 passed`.
- Related workflow/web/graph regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py tests/test_graph_sample.py tests/test_web.py -q -k 'workflow or approval or security or staging or release or stepper or graph'`
  -> `138 passed, 105 deselected`.
- Full backend:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests -q`
  -> `554 passed`.
- Quality gates:
  `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend CI parity:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.
- Whitespace:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- D3 parallel level execution.
- Dynamic per-run collaboration plans and non-gate fan-in runners.
- Live worker smoke for parallel scheduling, because no parallel scheduling was
  introduced.

### 2026-06-08 — Theme/App.jsx productization: memory event modal extraction

**Intent:** reduce `App.jsx` by moving a self-contained overlay while preserving
the existing memory event detail UI and behavior exactly.

**Implemented:**
- Added `frontend/src/components/MemoryEventDetailModal.jsx`.
- Moved the full memory event overlay out of `App.jsx`.
- Preserved modal shell, loading/error/data branches, supersede/superseded-by
  section, memory event link rendering, close button behavior, and class names.
- Kept event fetch state, derived supersede metadata, and link handler in
  `App.jsx`.
- Expanded the shell theme-token contract so it scans the modal component with
  the other extracted shell components.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_memory_event_detail_modal_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `MemoryEventDetailModal.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'memory_event_detail_modal or global_toast or git_creation_notice or approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `24 passed, 132 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx panel/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved memory event modal.

### 2026-06-08 — Theme/App.jsx productization: global toast extraction

**Intent:** continue shrinking `App.jsx` through a tiny presentational shell
slice without changing toast UI, timing, or success/error styling.

**Implemented:**
- Added `frontend/src/components/GlobalToast.jsx`.
- Moved the fixed top-right success/error toast out of `App.jsx`.
- Preserved error/success status theme branches, AlertTriangle/Check icons,
  `animate-fade-in-down`, and message text rendering.
- Kept message state and timeout behavior in `App.jsx`.
- Expanded the shell theme-token contract so it scans `GlobalToast.jsx` with the
  other extracted shell components.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_global_toast_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `GlobalToast.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'global_toast or git_creation_notice or approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `23 passed, 132 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx panel/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved toast chrome.

### 2026-06-08 — Theme/App.jsx productization: git creation notice extraction

**Intent:** continue shrinking `App.jsx` through another small presentational
shell slice without changing the post-create git notice UI or behavior.

**Implemented:**
- Added `frontend/src/components/GitCreationNotice.jsx`.
- Moved the git initialization notice out of `App.jsx`.
- Preserved the success icon, optional remote-created message, git warnings
  list, dismiss button, and existing theme utility classes.
- Kept git setup state in `App.jsx`; the extracted component is presentational.
- Expanded the common shell theme-token contract so it scans App.jsx plus the
  extracted shell components after token-bearing chrome moved out of App.jsx.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_git_creation_notice_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `GitCreationNotice.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'git_creation_notice or approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `22 passed, 132 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx panel/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved git notice chrome.

### 2026-06-08 — Theme/App.jsx productization: approval banner extraction

**Intent:** continue shrinking `App.jsx` through a small presentational shell
slice without changing the persistent approvals banner UI or behavior.

**Implemented:**
- Added `frontend/src/components/ApprovalBanner.jsx`.
- Moved the top-level actionable-approvals banner out of `App.jsx`.
- Preserved warning status theme classes, pulse dot, i18n message/review keys,
  plural/question interpolation, Approvals tab button behavior, and
  `duo-button-physics text-2xs` styling.
- Kept approval count derivation, open-question wording, and active-tab state in
  `App.jsx`; the extracted component is presentational.
- Adjusted the i18n chrome contract so approval-banner keys are asserted in the
  extracted component.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_approval_banner_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `ApprovalBanner.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'approval_banner or project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `21 passed, 132 deselected`.
- Frontend checks:
  `cd frontend && npm ci && npm run lint && npm run build`
  -> passed.

**Deferred from this increment:**
- Additional App.jsx panel/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved approval-banner chrome.

### 2026-06-08 — Theme/App.jsx productization: project header extraction

**Intent:** continue shrinking `App.jsx` through a small, reversible shell slice
without changing the existing UI, theme classes, or header behavior.

**Implemented:**
- Added `frontend/src/components/ProjectHeader.jsx`.
- Moved the mobile top bar and desktop project header markup out of `App.jsx`.
- Preserved the GoodTeam mark, project title fallback, run-id badge, mobile menu
  toggle, status badge branches, theme token utilities, icon usage, and
  `updateUrl('')` click behavior.
- Kept state derivation and handlers in `App.jsx`; the extracted component is a
  presentational boundary only.
- Adjusted the i18n chrome contract so dashboard/run-id keys are asserted in the
  extracted header component.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_project_header_is_extracted_from_app_shell_without_theme_changes -q`
  first failed because `ProjectHeader.jsx` did not exist.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'project_header or project_sidebar or language_switcher or appearance_preferences or frontend_contract_uses_i18n or theme_tokens'`
  -> `20 passed, 132 deselected`.

**Deferred from this increment:**
- Additional App.jsx panel/page extraction.
- Any visual redesign, theme-token conversion, or i18n expansion outside the
  moved header chrome.

### 2026-06-08 — Graph/DAG engine D2b: execution-plan foundation

**Intent:** move D2b forward without replacing the production runner or enabling
parallel worker execution before a real fan-in/dynamic-plan consumer exists.

**Implemented:**
- Added `WorkflowExecutionLevel` and `WorkflowExecutionPlan` to represent
  topological levels over `WorkflowNode.depends_on`.
- Added `workflow_execution_plan()` with `scheduler=sequential` and
  `parallel_level_execution=deferred`.
- Each level separates `runnable` workflow nodes from passive gate/role
  identifiers so future D3 work has an explicit, testable boundary.
- Exposed the serialized execution plan from `/api/workflow/graph` alongside the
  existing graph nodes, edges, and active run payload.
- Kept workflow-graph forward compatibility by returning `scheduler=unavailable`
  when a future/incomplete graph table cannot produce a validated execution plan.
- Preserved current execution semantics: `run_until_next_gate` and
  `advance_approved_workflow` still use the existing linear `next_node` loop.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py::test_workflow_execution_plan_groups_fanout_and_fanin_levels tests/test_workflow_table.py::test_workflow_execution_plan_exposes_current_runner_levels_without_enabling_parallelism tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q`
  first failed with `ImportError: cannot import name 'workflow_execution_plan'`.
- Follow-up red assertion:
  `tests/test_workflow_table.py::test_workflow_execution_plan_exposes_current_runner_levels_without_enabling_parallelism`
  failed because `rfc` and `security` were incorrectly assigned the same level.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_emits_forward_compat_error_and_blocked_edges tests/test_workflow_table.py::test_workflow_execution_plan_groups_fanout_and_fanin_levels tests/test_workflow_table.py::test_workflow_execution_plan_exposes_current_runner_levels_without_enabling_parallelism tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q`
  -> `4 passed`.
- Related workflow/web regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_workflow_table.py tests/test_web.py -q -k 'workflow_graph or workflow_node or workflow_dag or execution_plan or dashboard_stepper or advance_approved_workflow or state_store_disambiguates'`
  -> `19 passed, 150 deselected`.
- Related graph/state regression:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_graph_sample.py tests/test_state.py -q -k 'workflow or failure or retry or approval or checkpoint'`
  -> `22 passed, 51 deselected`.
- Touched-file lint:
  `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check src/goodteam_os/workflow_table.py src/goodteam_os/web.py tests/test_workflow_table.py tests/test_web.py`
  -> `All checks passed!`.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Replacing the current driver with a topological executor that owns
  `next_node` advancement.
- D3 parallel level execution.
- Live smoke for runner semantics, because runner semantics did not change in
  this slice.

### 2026-06-07 — i18n lightweight foundation

**Intent:** resolve the i18n target-language gate with the smallest durable
frontend foundation: browser/system auto mode, explicit user override, and a
scoped first chrome extraction.

**Implemented:**
- Added `frontend/src/i18n/` with `en` and `zh-CN` locale dictionaries plus
  `normalizeLanguagePreference`, `resolveLanguage`, browser-language lookup, and
  `translate`.
- Supported `gt-language=auto|en|zh-CN`; `auto` maps `zh-*` browsers to
  Simplified Chinese and falls back to English otherwise.
- Added compact language switchers beside existing theme controls in the root
  overview header and detailed project sidebar footer.
- Extracted the first high-traffic dashboard chrome slice to locale keys: root
  header labels, detailed sidebar groups/tabs, shell dashboard/run/status labels,
  theme labels, approval banner copy, and shell loading label.
- Kept deeper feature panels, user/project data, backend payloads, artifacts, and
  worker output untranslated for later focused passes.

**Verification evidence:**
- Red module tests:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n'`
  first failed because `frontend/src/i18n/` files did not exist.
- Red App chrome contract:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_i18n_for_dashboard_chrome -q`
  first failed because `App.jsx` did not import `LANGUAGE_STORAGE_KEY`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n or frontend_contract'`
  -> `9 passed, 98 deselected`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Review fixes during implementation:**
- Stacked sidebar footer controls so the theme buttons, language switcher, and
  dark-mode icon do not overflow the fixed `220px` sidebar.
- Preserved English fallback behavior for existing unsliced `getStatusLabel`
  callers instead of rendering locale keys.
- Localized the shell `complete` status badge and fixed English singular
  `Question` handling.
- Replaced the second root `+ New Project` chrome label that was initially missed.

**Deferred from this increment:**
- Full `App.jsx` copy extraction.
- New-project/start-run forms, approval panels, Dev Loop, Smoke Runs, Memory,
  Agents, Workflow Graph, and System diagnostics panel localization.

### 2026-06-07 — Backlog closure / gated parking

**Intent:** finish the backlog sweep without pretending product decisions, external
auth state, or intentionally human-gated safety boundaries are local implementation
tasks.

**Implemented:**
- `docs/backlog.md` now starts from an explicit "no actionable projects remain"
  state.
- The remaining non-local work is listed in a gated/external-decision parking lot:
  D2b topo/parallel executor gate, server-side land/tag/graduate auth and
  human-gate boundary, G3 routing application quality gate, G3 LLM triage data
  gate, Codex auth recovery, shared-worktree edit-collision detection, optional
  full research/review memory ingestion, and focused Theme panel/component token
  cleanup with visual review.
- Later update: CP9 superseded the server-action part of this 2026-06-07 status
  by adding guarded `land_pr` and `graduate_release` execution. `tag_release`
  remains disabled/report-only.
- The stale PRD edit -> LLM deepen -> re-review backlog item is marked resolved by
  the existing explicit refinement workflow: `Refine with AI`, proposed revision
  review, accept/discard, open-question answer application, and approval reset on
  accept.
- `docs/current-progress.md` was updated with the matching footprint.

**Verification evidence:**
- Targeted refinement/API/frontend contract regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_graph_sample.py tests/test_api.py tests/test_web.py -q -k 'refine_artifact or refinement or open_questions or frontend_contract_exposes_open_questions'`
  -> `9 passed, 206 deselected`.
- Whitespace check:
  `git diff --check`
  -> exit 0.

### 2026-06-07 — G3c global failure-pattern library

**Intent:** turn G3b failure capture into a cross-project pattern library so repeated
failures accumulate hit counts and can provide deterministic classifier/disposition
hints before any LLM triage agent is introduced.

**Design constraints:**
- Own the ledger: store local JSON under `.goodteam/failure-patterns/`, not an
  external service.
- Deterministic first: no model call, no remediation automation in this increment.
- Additive: worker failure triage still blocks/escalates exactly as today; the
  library records and retrieves patterns only.
- Secrets safe: summaries are normalized and truncated; no environment values or
  raw stderr blobs are copied into the global store.

**Expected verification:**
- Focused tests for pattern upsert/query semantics.
- Focused graph test proving worker failure triage records a pattern.
- Full GoodTeam regression, ruff, and frontend build passed before commit.

**Implemented:**
- Added `FailurePatternLibrary` at `src/goodteam_os/failure_patterns.py`.
- Stores workspace-global patterns at `.goodteam/failure-patterns/patterns.json`.
- Records `signature`, `classifier_hint`, `recommended_disposition`, normalized
  safe summary, hit count, roles, nodes, sources, and first/last seen timestamps.
- Added deterministic token-overlap match lookup for future triage/library hit-rate
  measurement.
- Wired `triage_worker_failure` to record a pattern whenever a worker failure is
  triaged, without changing routing semantics.

**Verification evidence:**
- Red test: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py -q`
  failed with `ModuleNotFoundError: No module named 'goodteam_os.failure_patterns'`.
- Red wiring test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event -q`
  failed because `FailurePatternLibrary(tmp_path).list_patterns()` was empty.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event -q`
  -> `4 passed`.
- Focused regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_workflow_table.py tests/test_memory.py -q`
  -> `109 passed`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `466 passed in 131.15s`.
- Lint:
  `.venv/bin/ruff check src/goodteam_os/failure_patterns.py src/goodteam_os/graph.py tests/test_failure_patterns.py tests/test_graph_sample.py`
  -> `All checks passed!`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Deferred from this increment:**
- Applying pattern matches to alter triage routing. This needs its own measured gate
  once hit-rate data exists.
- LLM triage for unknown failures.

### 2026-06-07 — G3c failure-pattern hit observability

**Intent:** start measuring whether the global failure-pattern library produces useful
matches during real triage, without letting pattern recommendations change routing
yet.

**Implemented:**
- `triage_worker_failure` now asks `FailurePatternLibrary.find_matches` for the best
  prior match using the current failure summary.
- `triage_decided` action-log events include `failure_pattern_signature`,
  `failure_pattern_hits`, and `failure_pattern_disposition` when a match exists.
- Scoped `worker_failure:*` memory events include the same pattern-match evidence.
- Existing routing is unchanged: disposition still comes from
  `disposition_for_failure_class`, not from the historical pattern recommendation.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition -q`
  failed with `KeyError: 'failure_pattern_signature'`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_graph_sample.py::test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition tests/test_graph_sample.py::test_escalated_worker_failure_writes_scoped_failure_memory_event -q`
  -> `2 passed`.
- Failure-pattern library regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py -q`
  -> `3 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_graph_sample.py tests/test_workflow_table.py tests/test_memory.py -q -k 'failure_pattern or worker_failure or triage or failure_class or pickup_packet'`
  -> `17 passed, 96 deselected`.
- Touched-file lint:
  `.venv/bin/ruff check src/goodteam_os/graph.py tests/test_graph_sample.py`
  -> `All checks passed!`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `480 passed in 135.83s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Letting historical pattern recommendations alter routing or escalation. That still
  needs measured hit-rate evidence and a separate gate.
- LLM triage for unknown failures.

### 2026-06-07 — G3c failure-pattern routing-readiness measurement

**Intent:** make the remaining routing-application gate measurable without changing
current failure routing.

**Implemented:**
- `FailurePatternLibrary.routing_readiness_report(min_hits=...)` summarizes total
  patterns, total hits, and repeated-pattern candidates.
- The report deliberately returns `routing_application=deferred` and
  `routing_gate_satisfied=false`; it is measurement only, not a routing switch.
- Added `goodteam-os failure-patterns --project-root <path> --min-hits <n>` to emit
  the readiness report as JSON from local state.
- Existing `triage_worker_failure` and `disposition_for_failure_class` behavior is
  unchanged.

**Verification evidence:**
- Red library test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py::test_failure_pattern_library_reports_routing_readiness_without_applying_patterns -q`
  failed with `AttributeError: 'FailurePatternLibrary' object has no attribute 'routing_readiness_report'`.
- Red CLI test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli.py::test_cli_failure_patterns_outputs_routing_readiness_json -q`
  failed because `failure-patterns` was not a CLI command.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py::test_failure_pattern_library_reports_routing_readiness_without_applying_patterns tests/test_cli.py::test_cli_failure_patterns_outputs_routing_readiness_json -q`
  -> `2 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_failure_patterns.py tests/test_cli.py tests/test_graph_sample.py -q -k 'failure_pattern or failure_patterns or worker_failure or triage or cli_failure_patterns'`
  -> `11 passed, 78 deselected`.
- Touched-file lint:
  `.venv/bin/ruff check src/goodteam_os/failure_patterns.py src/goodteam_os/cli.py tests/test_failure_patterns.py tests/test_cli.py`
  -> `All checks passed!`.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Applying pattern recommendations to routing. The report now gives a local evidence
  surface, but routing application still needs validated outcome quality.
- LLM triage for unknown failures.

### 2026-06-07 — Dispatch provenance depth

**Intent:** preserve the review side of the dispatch chain, not only worker jobs and
the land event. The previous air-drop kept job directories and one `land/pr-*`
memory event; this increment adds durable pre-review report capture and a separate
`review/pr-*` release memory event.

**Implemented:**
- `land_provenance_airdrop` now copies `/tmp/pre-review-<PR>-report.md` to
  `.goodteam/provenance/pr-<PR>/pre-review-report.md` when the report exists and
  is non-empty.
- The existing `land/pr-<PR>` event now includes `pre_review_report_path=...` when
  the report was archived.
- A new `review/pr-<PR>` release memory event records
  `land_review_decision=approved_to_land`, CI status, pre-review presence, pre-review
  archive path when available, and squash merge SHA.
- Missing worktree and missing pre-review report remain best-effort: land memory is
  still written and merge behavior remains unchanged.

**Verification evidence:**
- Red tests: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py -q`
  failed because no `review/pr-*` events were present.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py -q`
  -> `6 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py tests/test_pre_review_script.py tests/test_cli.py -q`
  -> `27 passed`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `467 passed in 134.55s`.
- Shell syntax:
  `bash -n scripts/lib/land-provenance.sh scripts/land-pr.sh scripts/pre-review.sh`
  -> exit 0.
- Lint:
  `.venv/bin/ruff check tests/test_land_provenance.py`
  -> `All checks passed!`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Deferred from this increment:**
- Capturing the original `/tmp` task/constraints source paths into job metadata.
- A richer human-written review rationale surface; this increment records the land
  decision fact and evidence links mechanically.

### 2026-06-07 — Self-hosting control plane: dry smoke action

**Intent:** move one more mechanical self-hosting action from CLI-only into the
GoodTeam web surface without adding a dangerous server-side merge/land path.

**Implemented:**
- Added `POST /api/smoke-run` with the existing same-origin mutating-route guard.
- The endpoint defaults to safe dry-run self smoke: `suite=self`, `cycles=1`,
  `dry_run=true`, `worker_mode=fake`.
- Smoke runs execute in a daemon background thread and write status files under
  `state/smoke-runs/smoke-run-<id>.json`.
- `build_smoke_run_items` now unwraps async status files so dry-run reports appear
  in the existing Smoke tab data.
- The React Smoke tab now has a compact `Run Dry Smoke` button that starts the
  safe dry-run and refreshes dashboard state.

**Verification evidence:**
- Red API tests failed because `goodteam_os.web.run_smoke_suite` / endpoint seam
  did not exist.
- Red display test failed because async status reports were invisible to
  `build_smoke_run_items`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api.py -q -k 'smoke or devloop or dispatch_track'`
  -> `13 passed, 32 deselected`.
- Compatibility regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_smoke.py::test_dashboard_renders_recent_smoke_runs tests/test_api.py::test_smoke_run_items_include_async_status_report_runs -q`
  -> `2 passed`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `470 passed in 155.00s`.
- Lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Deferred from this increment:**
- Running real non-dry smoke from the web by default. The endpoint supports explicit
  `dry_run=false`, but the UI intentionally starts with dry-run only.
- Server-side land/merge execution. Land remains a human-invoked script and readiness
  surface, per the self-driving design.
- Release graduation controls.

### 2026-06-07 — Self-hosting control plane: release-readiness metadata

**Intent:** make the existing release/graduation scripts visible to the GoodTeam web
control plane without adding any server-side tag, checkout, install, or serve action.

**Implemented:**
- `build_dev_loop` now includes a `release_readiness` object for GoodTeam repos.
- The object reports `release_dir`, whether the release worktree exists, dev HEAD,
  release HEAD, whether they match, and copyable `release-up` / `graduate-release`
  commands pinned to the current dev HEAD.
- The feature is guarded by script presence: repos without
  `scripts/release-up.sh` and `scripts/graduate-release.sh` return
  `{available:false, reason:"release scripts unavailable"}`.
- Only existing read-only subprocess calls are used. No tag, checkout, dependency
  install, release process start, or release worktree movement happens in the web API.

**Verification evidence:**
- Red release-readiness test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_build_dev_loop_reports_release_graduation_readiness_without_mutating -q`
  failed with `KeyError: 'release_readiness'`.
- Red safety guard test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_build_dev_loop_hides_release_graduation_commands_for_non_goodteam_repo -q`
  first failed because commands were generated for any git repo.
- Focused Dev Loop/API checks:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'dev_loop or devloop'`
  -> `7 passed, 96 deselected`.
- Route contract:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api.py::test_api_devloop_route_returns_contract_shape -q`
  -> `1 passed`.
- Related API/web regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py tests/test_api.py -q -k 'dev_loop or devloop or smoke_run or dispatch_track'`
  -> `20 passed, 128 deselected`.
- Touched-file lint:
  `.venv/bin/ruff check src/goodteam_os/web.py tests/test_web.py`
  -> `All checks passed!`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `482 passed in 136.18s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- A React panel/card for the new `release_readiness` object.
- Executing graduate/tag/release-up from the web. Those remain human-invoked scripts.

### 2026-06-07 — Self-hosting control plane: release-readiness UI panel

**Intent:** surface the existing read-only release-readiness metadata in the Dev Loop
UI while preserving the manual release-up / graduate boundary.

**Implemented:**
- The React Dev Loop view now renders a `Release Readiness` panel when
  `devLoopData.release_readiness.available` is true.
- The panel shows dev HEAD, release HEAD, and a status badge for current vs ready to
  graduate.
- `release_up_command` and `graduate_command` are displayed as copyable command
  lines using the existing copy-command handler.
- There are no execute/run controls for release-up or graduate.
- The Dev Loop empty-state predicate now treats available release readiness as real
  content, avoiding an empty-state message alongside the panel.

**Verification evidence:**
- Red frontend contract:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_exposes_release_readiness_without_execution_controls -q`
  failed because `App.jsx` did not read `devLoopData.release_readiness`.
- Follow-up red check:
  the same contract failed until the empty-state predicate accounted for
  `devLoopData.release_readiness?.available !== true`.
- Focused contract/regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_contract or dev_loop or devloop'`
  -> `13 passed, 91 deselected`.
- Touched-test lint:
  `.venv/bin/ruff check tests/test_web.py`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `483 passed in 138.27s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.

**Deferred from this increment:**
- Executing graduate/tag/release-up from the web. Those remain human-invoked scripts.

### 2026-06-07 — Self-hosting control plane: release-readiness unavailable-state polish

**Intent:** keep the release-readiness UI scoped to GoodTeam repos that can actually
report release metadata, and avoid showing a release-specific unavailable panel
beside the normal Dev Loop empty state for non-GoodTeam repos.

**Root cause:**
- The Dev Loop empty-state predicate intentionally treats
  `devLoopData.release_readiness?.available !== true` as no visible release
  readiness content.
- The UI still rendered a separate `Release Readiness unavailable` panel when the
  API returned `{available:false, reason:...}`.
- On a non-GoodTeam repo, that could produce both the ordinary empty state and a
  release-readiness unavailable message.

**Implemented:**
- Removed the unavailable release-readiness UI branch from `frontend/src/App.jsx`.
- Release readiness now renders only when `release_readiness.available === true`.
- Non-GoodTeam repos continue to use the normal Dev Loop empty state.
- Kept the existing copy-command-only release-up/graduate panel for available
  GoodTeam release metadata; no server-side release execution was added.

**Verification evidence:**
- Red contract:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_exposes_release_readiness_without_execution_controls -q`
  failed because `App.jsx` still contained `Release Readiness unavailable`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_contract or dev_loop or devloop'`
  -> `13 passed, 91 deselected`.
- Touched-test lint:
  `.venv/bin/ruff check tests/test_web.py`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Full regression already run before this ledger update:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `483 passed in 143.37s`.
- Full lint already run before this ledger update:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Executing graduate/tag/release-up from the web. Those remain human-invoked scripts.

### 2026-06-07 — Theme token conversion: high-volume palette/type slice

**Intent:** reduce the large App.jsx theme debt without redesigning the UI or
removing the productized Vivid/Minimal switcher. This slice targets the highest
volume repeated palette tokens and the 8/9/10px text outliers called out by the
theme-system plan.

**Implemented:**
- Converted the repeated Vivid base palette in `frontend/src/App.jsx` to the
  existing Tailwind v4 semantic token utilities: `bg-surface-*`,
  `border-border-*`, `text-text-*`, `bg-status-*`, `text-status-*`,
  `border-status-*`, `bg-accent`, `text-accent-text`, and `shadow-card` /
  `shadow-button`.
- Replaced `text-[8px]`, `text-[9px]`, and `text-[10px]` usages in `App.jsx`
  with the existing `text-2xs` floor from `frontend/src/index.css`.
- Added a frontend contract test that fails if the high-volume legacy base palette
  or sub-11px text utilities return to `App.jsx`.
- Left low-frequency bespoke colors, notably indigo/editor accents and a few
  one-off warm surfaces, for later visual review instead of blindly flattening
  all color semantics.

**Verification evidence:**
- Red contract test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_theme_tokens_for_common_palette_and_small_type -q`
  first failed with counts for 11 legacy palette tokens, including `#E5E5E5`,
  `#AFAFAF`, `#58CC02`, `#D1D1D1`, and `#3D8B00`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_theme_tokens_for_common_palette_and_small_type -q`
  -> `1 passed`.
- Related contract regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_contract or delivery_mode_options'`
  -> `6 passed, 95 deselected`.
- Frontend lint:
  `cd frontend && npm run lint`
  -> exit 0.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `471 passed in 133.50s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.

**Deferred from this increment:**
- Blindly converting every remaining bespoke hex. `App.jsx` is down to 15
  low-frequency one-offs that should be reviewed visually before tokenizing.
- Componentizing `App.jsx`; this pass stayed mechanical and contract-backed.
- i18n/copy extraction remains gated separately.

### 2026-06-07 — Theme token conversion: MessageFeed slice

**Intent:** continue Theme System productionization after the founder decision to
keep both Vivid and Minimal permanently, without reviving the old winner-selection
gate or doing a blind full-App conversion.

**Implemented:**
- Converted `frontend/src/components/MessageFeed.jsx` status kind styles from
  hardcoded Duo hex values to semantic `status-*` theme classes.
- Replaced MessageFeed surfaces, borders, muted/primary text, accent icon color,
  and card shadow with `bg-surface-*`, `border-border-*`, `text-text-*`,
  `text-accent`, and `shadow-card` utilities.
- Replaced MessageFeed `text-[8px]`, `text-[9px]`, and `text-[10px]` usages with
  the existing `text-2xs` floor.
- Added a focused frontend contract test that fails if MessageFeed reintroduces
  the legacy Vivid palette or sub-11px arbitrary text.

**Verification evidence:**
- Red contract test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_message_feed_uses_theme_tokens_and_small_type_floor -q`
  first failed with counts for 18 legacy palette tokens.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'message_feed or theme_tokens or frontend_contract'`
  -> `8 passed, 100 deselected`.
- Frontend lint:
  `cd frontend && npm run lint`
  -> exit 0.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Further App.jsx/panel conversion and visual review.
- Theme-specific dark-mode polish beyond the existing token support.
- i18n/copy extraction for MessageFeed and deeper panels.

### 2026-06-07 — Theme token conversion: Memory panel slice

**Intent:** continue Theme System productionization on a contained `App.jsx` panel
after MessageFeed, while keeping Vivid and Minimal both permanent and avoiding a
blind full-App conversion.

**Implemented:**
- Converted the Agents & Memory / Memory tab slice to semantic surface, border,
  text, and status utilities.
- Replaced `bg-[#FDFDFD]`, repeated `bg-white` / `dark:bg-zinc-*` panel surfaces,
  zinc borders/text, and blue/amber ad-hoc badge families in that slice.
- Replaced Memory-panel `text-[11px]` empty states with `text-2xs`.
- Added a focused frontend contract test that extracts only the Memory tab slice
  from `App.jsx` and rejects legacy surface/text snippets plus `dark:` overrides
  in that slice.

**Verification evidence:**
- Red contract test:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py::test_memory_panel_uses_theme_tokens_and_small_type_floor -q`
  first failed with counts for 13 disallowed snippets; after tightening the test,
  it caught one remaining `dark:` override before final green.
- Focused green:
  `PYTHONPATH=src /Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/python -m pytest tests/test_web.py -q -k 'memory_panel or message_feed or theme_tokens or frontend_contract'`
  -> `9 passed, 100 deselected`.
- Touched-test lint:
  `/Users/luke/WorkSpace/codeAi/GoodTeam/.venv/bin/ruff check tests/test_web.py`
  -> `All checks passed!`.
- Frontend lint:
  `cd frontend && npm run lint`
  -> exit 0.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- Approval/review panel token cleanup.
- Dev Loop token cleanup.
- Broader App.jsx componentization and i18n/copy extraction.

### 2026-06-07 — Graph/DAG engine D2: DAG-spec foundation

**Intent:** move D2 forward without prematurely replacing the current linear
`next_node` driver. The gate review found the old prerequisites have improved:
checkpoint revisit is sequence-suffixed and the legacy dashboard stepper is
table-derived, but there is still no genuine non-linear fan-in that justifies a
topological executor rewrite in this pass.

**Implemented:**
- Added `WorkflowNode.depends_on` with default `()`.
- Declared the current workflow's structural dependencies using existing stage/gate
  identifiers, for example `architect` depends on `product_requirement` + `prd`,
  `planner` depends on `architect` + `rfc` + `security`, and `release` depends on
  `planner` + `staging`.
- Added `validate_workflow_dag()` to reject unknown dependencies and dependency cycles.
- Added `topological_workflow_nodes()` so future drivers/tests can consume one
  deterministic workflow structure order.
- Exposed dependency edges from `build_workflow_graph()` as `type=dependency`, while
  keeping approved/changes/rejected/error/blocked edges intact.
- Preserved current execution semantics: `advance_approved_workflow` still runs the
  existing linear `next_node` loop.

**Verification evidence:**
- Red tests:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow_table.py -q`
  first failed with `ImportError: cannot import name 'topological_workflow_nodes'`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow_table.py::test_workflow_node_dependencies_describe_current_linear_dag tests/test_workflow_table.py::test_workflow_dag_validation_rejects_unknown_dependency tests/test_workflow_table.py::test_workflow_dag_validation_rejects_cycles tests/test_web.py::test_build_workflow_graph_static_nodes_and_edges -q`
  -> `4 passed`.
- Compatibility regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_build_workflow_graph_emits_forward_compat_error_and_blocked_edges tests/test_workflow_table.py -q`
  -> `12 passed`.
- Related graph/table regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workflow_table.py tests/test_web.py -q -k 'workflow_graph or workflow_node or workflow_dag or dashboard_stepper or advance_approved_workflow or state_store_disambiguates'`
  -> `9 passed, 103 deselected`.
- Related state/graph regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_state.py tests/test_graph_sample.py -q -k 'checkpoint or workflow or failure or retry or approval'`
  -> `21 passed, 51 deselected`.
- Lint:
  `.venv/bin/ruff check src/goodteam_os/workflow_table.py src/goodteam_os/web.py tests/test_workflow_table.py tests/test_web.py`
  -> `All checks passed!`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `474 passed in 138.25s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Deferred from this increment:**
- Replacing the current driver with a topological executor. That remains gated on a
  real fan-in consumer or dynamic per-run plan, plus live smoke because it changes
  core walk semantics.
- Parallel level execution.

### 2026-06-07 — Backlog cleanup: pre-review report binding

**Intent:** close the remaining report-selection race in the mechanical pre-review
script without changing dispatch, worker job storage, or the human land gate.

**Root cause:**
- The helper already ignored empty `result.md` files and failed `code_review` jobs.
- In an existing worktree with multiple completed `code_review` jobs, the selectors
  still chose the newest matching job globally.
- That meant a concurrent or back-to-back pre-review invocation could copy another
  run's report, or decide fallback behavior from another run's status.

**Implemented:**
- `scripts/pre-review.sh` now creates a unique `PRE_REVIEW_INVOCATION_ID` per run.
- The generated `code_review` task includes that invocation marker.
- `pre_review_select_report` and `pre_review_latest_status` accept an optional marker
  and only consider jobs whose `task.md` contains it.
- Calling helpers without a marker preserves the previous mtime-based behavior for
  compatibility with existing tests and manual use.

**Verification evidence:**
- Red tests:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_pre_review_script.py -q`
  failed because `pre_review_select_report` and `pre_review_latest_status` chose the
  newer unrelated job, and the main script did not pass `PRE_REVIEW_INVOCATION_ID`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_pre_review_script.py -q`
  -> `6 passed`.
- Shell syntax:
  `bash -n scripts/pre-review.sh scripts/lib/pre-review.sh scripts/lib/land-provenance.sh scripts/land-pr.sh`
  -> exit 0.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_pre_review_script.py tests/test_land_provenance.py tests/test_cli.py -q`
  -> `29 passed`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `476 passed in 133.17s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- A first-class persisted invocation id in worker job metadata. The task marker is
  enough for this script-level race and avoids widening the worker job schema.
- Serializing all pre-review runs. Invocation binding keeps parallel runs possible.

### 2026-06-07 — Backlog status audit

**Intent:** remove stale open work from `docs/backlog.md` after the mainline push so
the next planning pass does not repeatedly pick issues already fixed by recent
commits.

**Resolved entries marked with evidence:**
- D2 blockers: checkpoint revisit collision and table-derived dashboard stepper.
- Worker reliability: stale/dead running job reconciliation.
- G3 pickup filter over-breadth.
- `land-pr.sh` stash/restore around main sync.
- agy model support and live model listing.
- Expanded delivery-mode catalog and custom standards.
- Air-drop implementation-job preference.
- `/api/fs/list` 403 route test.
- Legacy delivery-mode option generation.
- Detail-view repo path display.
- Pre-review report binding and empty/failed report filtering.
- Worker Routing Simple/Advanced and FilePath tooltip.
- Worker picker alias dedup, role prompt model line, `sync_stage_tasks` artifact refs,
  workflow graph gate/decision highlighting, new-project git init/remote, and workflow
  stage board tasks.

**Still open/gated after audit:**
- i18n target-market / primary-language decision.
- Remaining theme token/visual work after review.
- D2b topological executor / parallel level execution.
- G3 LLM triage and applying failure-pattern hits to routing.
- Self-hosting graduate/tag controls and any server-side land/merge decision.
- PRD edit/deepen/re-review loop as a larger product workflow.
- Full research/review document ingestion into role memory, if wanted, as a
  retrieval-budget-aware follow-up. The durable dispatch audit chain itself is now
  complete.

**2026-06-09 follow-up:** CP9 superseded the server-action line above by adding
guarded `land_pr` and `graduate_release` execution. `tag_release` remains
disabled/report-only, and broader server-action hardening stays gated on live
evidence and human-review discipline.

**2026-06-07 follow-up:** the PRD edit/deepen/re-review item was later reconciled in
the `Backlog closure / gated parking` increment above. It is no longer an open
backlog item because the explicit `Refine with AI` workflow, proposal review, and
approval reset path already exist.

**2026-06-08 follow-up:** D2b is no longer wholly open. The sequential
security-dependency gate is wired into production runner semantics. The remaining
graph-engine parking lot is D3 parallel execution plus deeper dynamic-plan runner
ownership, both still gated on a real fan-in consumer, isolation design, and live
smoke budget.

**Verification evidence:**
- Targeted proof command:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_state.py tests/test_web.py tests/test_workers.py tests/test_memory.py tests/test_land_provenance.py tests/test_api.py tests/test_tasks.py tests/test_pre_review_script.py -q -k 'checkpoint or dashboard_stepper or stale or pickup_packet or land_provenance_extract_prefers_non_review or fs_list_route_maps_permission_error_to_403 or sync_stage_tasks_replaces or frontend_contract_has_simple_advanced or render_delivery_mode_options or pre_review or discover_installed_agents'`
  -> `34 passed, 248 deselected`.
- Whitespace check:
  `git diff --check`
  -> exit 0.

### 2026-06-07 — Dispatch artifacts audit/memory: source-provenance foundation

**Intent:** preserve the contract source path for dispatch jobs so durable job
archives and memory provenance can explain which task/constraints file produced a
landed job.

**Implemented:**
- `WorkerJobStore.create_job` now accepts and stores `task_source` and
  `constraints_source` in `job.json`.
- `dispatch_tracks` passes `parse_dispatch_track_spec(...).task_source` and the
  `constraints_file` path into each created job.
- The short-term job provenance memory event includes `task_source=...` and
  `constraints_source=...` when available.
- Worker prompts, job execution, dispatch grouping, and land behavior are unchanged.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py::test_dispatch_tracks_persists_task_and_constraints_sources -q`
  failed with `KeyError: 'task_source'`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py -q -k 'task_and_constraints_sources or provenance_records_task_and_constraints_sources or completed_job_writes_short_term_provenance_event or rerunning_completed_job_dedups_short_term_provenance_event'`
  -> `4 passed, 74 deselected`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py tests/test_cli.py tests/test_land_provenance.py -q -k 'dispatch or provenance or memory_provenance or cli_dispatch'`
  -> `21 passed, 80 deselected`.
- Lint:
  `.venv/bin/ruff check src/goodteam_os/dispatch.py src/goodteam_os/workers.py tests/test_workers.py`
  -> `All checks passed!`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `478 passed in 132.96s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- First-class research report source capture. Completed later in the
  `research-report source capture` increment.
- Human review rationale as structured provenance. Completed later in the
  `review-rationale provenance` increment.
- Research and review rationale fields inside the PR-level manifest. Completed later
  across the `review-rationale provenance` and `research-report source capture`
  increments.

### 2026-06-07 — Dispatch artifacts audit/memory: research-report source capture

**Intent:** preserve the research basis for a dispatched implementation as a durable
job artifact and PR-manifest link, without changing worker execution semantics.

**Implemented:**
- `goodteam-os dispatch` accepts optional `--research-report <path>`.
- `scripts/dispatch-track.sh` accepts and forwards `--research-report <path>` /
  `--research-report=<path>`.
- `dispatch_tracks` accepts `research_report_file` and passes its source path into
  each created worker job.
- `WorkerJobStore.create_job` records `research_report_source` in `job.json` and
  copies an existing file to `research-report.md` inside the job directory.
- Job short-term provenance memory text includes `research_report_source=...` when
  available.
- `land_provenance_airdrop` includes per-job `research_report_source` and archived
  `research_report_artifact` in `manifest.json` when the job archive contains
  `research-report.md`.

**Verification evidence:**
- Red worker test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py::test_dispatch_tracks_archives_research_report_source -q`
  failed with `TypeError: dispatch_tracks() got an unexpected keyword argument 'research_report_file'`.
- Red CLI/script test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli.py::test_cli_dispatch_calls_runner_and_returns_zero_for_all_ok tests/test_cli.py::test_dispatch_track_script_accepts_research_report_option -q`
  failed because `--research-report` was unrecognized and the script had no option
  contract.
- Red manifest test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py::test_land_provenance_airdrop_writes_pr_manifest_linking_job_and_review_artifacts -q`
  failed because job manifest entries lacked `research_report_source` and
  `research_report_artifact`.
- Focused green:
  the worker test -> `1 passed`; CLI/script tests -> `2 passed`; manifest test ->
  `1 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py tests/test_cli.py tests/test_land_provenance.py -q -k 'dispatch or provenance or research_report or manifest or cli_dispatch'`
  -> `26 passed, 80 deselected`.
- Touched-file lint:
  `.venv/bin/ruff check src/goodteam_os/dispatch.py src/goodteam_os/workers.py src/goodteam_os/cli.py tests/test_workers.py tests/test_cli.py tests/test_land_provenance.py`
  -> `All checks passed!`.
- Shell syntax:
  `bash -n scripts/dispatch-track.sh scripts/lib/land-provenance.sh scripts/land-pr.sh`
  -> exit 0.
- Whitespace check:
  `git diff --check`
  -> exit 0.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `487 passed in 137.35s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Deferred from this increment:**
- Writing full research report content into role memory. This slice records and
  links the durable artifact; broader memory ingestion should be a separate,
  retrieval-budget-aware decision.

### 2026-06-07 — Dispatch artifacts audit/memory: PR-level provenance manifest

**Intent:** turn the durable `.goodteam/provenance/pr-*` archive from a directory of
copied artifacts into a machine-readable PR index that links job artifacts, review
artifacts, and land memory sources.

**Implemented:**
- `land_provenance_airdrop` now writes
  `.goodteam/provenance/pr-<PR>/manifest.json` when an archive directory exists.
- The manifest records PR number, head branch, CI status, squash merge SHA, archive
  path, `land/pr-*` and `review/pr-*` memory sources, and the archived pre-review
  report path when present.
- Each archived job entry includes job/run/role/adapter/model, the archived
  `task.md` path, `task_source`, `constraints_source`, and expected artifacts.
- Missing worktree / missing archive behavior stays best-effort and unchanged:
  land/review memory can still be written without a manifest.

**Verification evidence:**
- Red test:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py::test_land_provenance_airdrop_writes_pr_manifest_linking_job_and_review_artifacts -q`
  failed with `FileNotFoundError` for `manifest.json`.
- Focused green:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py::test_land_provenance_airdrop_writes_pr_manifest_linking_job_and_review_artifacts -q`
  -> `1 passed`.
- Land provenance regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py -q`
  -> `7 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py tests/test_pre_review_script.py tests/test_cli.py -q`
  -> `30 passed`.
- Shell syntax:
  `bash -n scripts/lib/land-provenance.sh scripts/land-pr.sh scripts/pre-review.sh scripts/lib/pre-review.sh`
  -> exit 0.
- Touched-test lint:
  `.venv/bin/ruff check tests/test_land_provenance.py`
  -> `All checks passed!`.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `479 passed in 137.87s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.
- Whitespace check:
  `git diff --check`
  -> exit 0.

**Deferred from this increment:**
- First-class research report source capture.
- Structured human review rationale beyond the mechanical approved-to-land fact.

### 2026-06-07 — Dispatch artifacts audit/memory: review-rationale provenance

**Intent:** preserve the human review judgement as a durable artifact when the
reviewer has written rationale on disk, without automating the judgement or
changing the land gate.

**Implemented:**
- `scripts/land-pr.sh` accepts `--review-rationale <path>` and
  `--review-rationale=<path>`.
- The script exports the optional path as `LAND_PROVENANCE_REVIEW_RATIONALE` before
  calling `land_provenance_airdrop`.
- `land_provenance_airdrop` archives a non-empty rationale file to
  `.goodteam/provenance/pr-<PR>/review-rationale.md`.
- The `review/pr-*` release memory event records `review_rationale=y` and
  `review_rationale_path=...` when the file is archived; otherwise existing
  behavior remains best-effort and unchanged.
- The PR manifest records `review_rationale` when the archive is present.

**Verification evidence:**
- Red tests:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py::test_land_provenance_airdrop_archives_review_rationale_and_links_manifest tests/test_land_provenance.py::test_land_pr_script_accepts_review_rationale_option_and_exports_for_airdrop -q`
  failed because `review-rationale.md` was not archived and `land-pr.sh` had no
  `--review-rationale` option/export.
- Focused green:
  the same two tests -> `2 passed`.
- Land provenance regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py -q`
  -> `9 passed`.
- Related regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/test_land_provenance.py tests/test_pre_review_script.py tests/test_cli.py -q`
  -> `32 passed`.
- Shell syntax:
  `bash -n scripts/lib/land-provenance.sh scripts/land-pr.sh scripts/pre-review.sh scripts/lib/pre-review.sh`
  -> exit 0.
- Touched-test lint:
  `.venv/bin/ruff check tests/test_land_provenance.py`
  -> `All checks passed!`.
- Whitespace check:
  `git diff --check`
  -> exit 0.
- Full regression:
  `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
  -> `485 passed in 137.77s`.
- Full lint:
  `.venv/bin/ruff check src/goodteam_os/ tests/`
  -> `All checks passed!`.
- Frontend build:
  `cd frontend && npm run build`
  -> Vite build succeeded.

**Known verification constraint:**
- `tests/test_land_provenance.py` uses fixed `/tmp/land-provenance-*` and
  `/tmp/pre-review-*` paths, so overlapping pytest processes can interfere with
  each other. The related suites were rerun sequentially for the evidence above.

**Deferred from this increment:**
- First-class research report source capture.
