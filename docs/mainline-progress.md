# Mainline Progress

This file is the execution ledger for the remaining GoodTeam mainlines. It mirrors
`docs/current-progress.md`: each completed increment gets a dated entry, commit
reference, verification evidence, and follow-up status.

## Execution Order

| Order | Mainline | Status | Scope For This Pass | Completion Evidence |
| --- | --- | --- | --- | --- |
| 1 | G3c global failure-pattern library | Pattern-hit observability completed 2026-06-07 | Add deterministic global store, record worker failures, and surface best-match signature/hit/disposition evidence during triage without changing routing. | `480 passed`; ruff clean; frontend build passed |
| 2 | Dispatch provenance depth | Completed 2026-06-07 | Persist durable pre-review report capture and first-class review/land decision records beyond the existing air-drop land event. | `467 passed`; ruff clean; frontend build passed |
| 3 | Self-hosting control plane | Dry-smoke slice completed 2026-06-07 | Add a web-visible safe Run-Smoke action on top of the existing dev-loop/readiness surface; keep merge/land as a human gate unless a later auth design changes that. | `470 passed`; ruff clean; frontend build passed |
| 4 | Theme token conversion | High-volume slice completed 2026-06-07 | Convert the repeated base palette and sub-11px text outliers in `App.jsx` to the existing semantic theme tokens while preserving the Vivid/Minimal switcher. | `471 passed`; ruff clean; frontend lint/build passed |
| 5 | Graph/DAG engine D2 | DAG-spec foundation completed 2026-06-07 | Add node dependency declaration, topological ordering, cycle/unknown dependency validation, and graph dependency edges without replacing the current linear driver. | `474 passed`; ruff clean; frontend build passed |
| 6 | i18n | Deferred, gated | Requires target-market/language decision and stabilized copy; not started in this pass unless that decision is made. | Pending |
| 7 | Backlog cleanup: pre-review report binding | Completed 2026-06-07 | Bind pre-review report/status selection to the current script invocation so concurrent or back-to-back code_review jobs cannot copy/fallback against another run's report. | `476 passed`; ruff clean; frontend build passed |
| 8 | Backlog status audit | Completed 2026-06-07 | Reconcile stale `docs/backlog.md` open items against shipped code/tests so only real open/gated work remains. | Targeted tests `34 passed`; diff check clean |
| 9 | Dispatch artifacts audit/memory | PR manifest slice completed 2026-06-07 | Persist dispatch task/constraints source paths, then write a PR-level archive manifest that links job artifacts, review artifacts, and land memory sources. | `479 passed`; ruff clean; frontend build passed |

## Active Increment

Next active increment: choose next real open/gated mainline. Good candidates are self-hosting graduate/tag readiness or more dispatch-audit capture; i18n remains gated on target market / primary language, and D2b topo execution remains gated on a real fan-in/dynamic-plan consumer.

## Completed Increments

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
- Broader dispatch artifacts as first-class memory/audit.

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
- First-class research report source capture.
- Human review rationale as structured provenance.
- Research and review rationale fields inside the PR-level manifest.

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
