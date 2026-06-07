# Mainline Progress

This file is the execution ledger for the remaining GoodTeam mainlines. It mirrors
`docs/current-progress.md`: each completed increment gets a dated entry, commit
reference, verification evidence, and follow-up status.

## Execution Order

| Order | Mainline | Status | Scope For This Pass | Completion Evidence |
| --- | --- | --- | --- | --- |
| 1 | G3c global failure-pattern library | Completed 2026-06-07 | Add a deterministic global store with signature, classifier hint, recommended disposition, hit count, and query helpers; wire failed worker triage to record patterns. | `466 passed`; ruff clean; frontend build passed |
| 2 | Dispatch provenance depth | Completed 2026-06-07 | Persist durable pre-review report capture and first-class review/land decision records beyond the existing air-drop land event. | `467 passed`; ruff clean; frontend build passed |
| 3 | Self-hosting control plane | Dry-smoke slice completed 2026-06-07 | Add a web-visible safe Run-Smoke action on top of the existing dev-loop/readiness surface; keep merge/land as a human gate unless a later auth design changes that. | `470 passed`; ruff clean; frontend build passed |
| 4 | Theme token conversion | Planned | Convert remaining high-volume hardcoded colors/font-size outliers to semantic tokens after preserving the existing theme switcher. | Pending |
| 5 | Graph/DAG engine D2 | Planned, gated | Add node dependency/topological driver foundation only after failure routing and checkpoint/stepper prerequisites stay green. | Pending |
| 6 | i18n | Deferred, gated | Requires target-market/language decision and stabilized copy; not started in this pass unless that decision is made. | Pending |

## Active Increment

Next active increment: theme token conversion.

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
