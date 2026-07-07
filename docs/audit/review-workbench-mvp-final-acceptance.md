# Review Workbench MVP Final Acceptance Audit

Date: 2026-07-07

Target issue: #216, "P0-2: Review Workbench MVP (Evidence-first Agent Review UI)"

Baseline: `origin/main` at `e069861`, the #314 merge commit.

Recommendation: close #216 as Review Workbench MVP completed after this audit PR
merges, if no missing acceptance blocker is found.

This audit uses `Refs #216` so final issue closure remains a maintainer decision.

## Scope Summary

Review Workbench MVP v0 owns the evidence-first review surface where agent output
is opened, inspected, explained, and reviewed from persisted GoodTeam execution
artifacts. The v0 scope now includes:

- canonical `review_workbench.v1` object
- evidence-backed review projection
- execution status and artifact state
- PR diff artifact projection
- file-level diff navigation
- rationale mapping
- memory provenance refs
- sandbox, runtime, path, and cleanup evidence
- review decision recording
- browser read-only Workbench HTML/JSON
- guarded browser decision POST actions

The Workbench is an evidence-first review surface. It projects the ledger,
execution evidence, diff artifacts, rationale artifacts, memory provenance, and
sandbox evidence into a single inspectable object and browser/CLI surfaces. It
does not treat agent output as valid unless the output is visible, explainable,
traceable, and reviewable.

The following are not required for the v0 acceptance boundary:

- advanced UI polish or animations
- multi-agent comparison
- enterprise RBAC
- direct GitHub approve/request-changes API calls
- automatic merge behavior

## PR Chain Mapping

| PR | Title | Merge commit | Contribution to #216 acceptance |
| --- | --- | --- | --- |
| #303 | `feat(review): add evidence-backed review workbench MVP` | `4abbb54eaccaf46abf23d77f2ddcdfd9bf7f3759` | Introduced `ExecutionAPI.get_review_workbench()`, `ExecutionAPI.record_review_decision()`, the canonical `review_workbench.v1` object, CLI/text rendering, decision ledger writes, and the first evidence-backed Workbench contract. |
| #304 | `feat(review): persist PR diff artifacts for workbench` | `36f6c7a4d35c93d0438646fee91d5b15db688a28` | Added stable `pr-diff.json` and `pr-diff.patch` artifacts, `ExecutionAPI.persist_pr_diff()`, Workbench diff projection, and idempotent `diff_artifact_persisted` ledger evidence. |
| #305 | `feat(review): add browser workbench for execution evidence` | `e3e7b635b5191a74ff83316b288ac2f91037dba1` | Added static `review-workbench.html` / `review-workbench.json` export plus local JSON and HTML routes that render the canonical object from real execution artifacts. |
| #306 | `feat(review): add guarded browser decision actions` | `ed1688db86c1b77e988ef6155663f3d0229b5878` | Added guarded browser POST decisions that delegate to `ExecutionAPI.record_review_decision()`, preserve read-only GET/static export behavior, validate allowed decisions, and persist ledger decisions idempotently. |
| #307 | `feat(review): add file-level diff navigation to workbench` | `8be89b5dadbafa46ac0317f45f158d0b814046e0` | Added unified diff parsing, stable file anchors, browser file navigation, line-level hunk rows, changed-line highlighting, and missing/malformed patch warnings. |
| #308 | `feat(review): map changed files to rationale evidence` | `34e0e5f4f96ae3bf834175cf346c7b981ab30393` | Added deterministic changed-file rationale mapping from task/run metadata, worker results, and prompt snapshots, with browser rationale evidence blocks and mapping warnings. |
| #309 | `feat(sandbox): add auditable sandbox v0 boundary` | `fcf73a0f671885f25f15bcacb26ffbc2e3a14cf9` | Replaced the prior sandbox placeholder with read-only sandbox ledger projection in `review_workbench.v1`, including sandbox metadata, command evidence, rejected/failed commands, conflict risks, and redaction markers. |
| #310 | `feat(sandbox): run executions through workspace lifecycle evidence` | `7ef26b4f20ea787c7c1451101c47f5f707b4c292` | Routed executions through GoodTeam workspace/worktree lifecycle evidence and exposed retained/destroyed sandbox states in Review Workbench for real execution paths. |
| #311 | `feat(sandbox): capture runtime adapter evidence and cleanup state` | `6aadfc0bd37e0a968bf2c41cf0d97c3682cbdbbf` | Added structured runtime adapter evidence, cleanup lifecycle evidence, cleanup-required/recovery warnings, artifact paths, and sandbox lifecycle rendering in the Workbench. |
| #312 | `feat(sandbox): infer execution path claims` | `cf92f277e66d19a090dd05b421f339c33db61598` | Added path claim inference, path claim warnings, conflict-risk evidence, and Workbench projection of claimed paths and conflict reasons. |
| #313 | `docs(audit): finalize sandbox v0 acceptance` | `ac98bc582bf8fdca296a501e5aa5178a53fe038d` | Finalized #218 Sandbox v0 acceptance and confirmed sandbox/runtime/path/cleanup evidence is a completed v0 surface, not a remaining #216 blocker. |
| #314 | `docs(audit): finalize ledger evidence acceptance` | `e0698610f6b1e0ce649711028d80da0979dea70f` | Finalized #217 Ledger & Evidence acceptance and confirmed the ledger/evidence truth layer consumed by Workbench is complete for v0. |

## Acceptance Criteria Mapping

### A real agent execution can be opened in the workbench

Status: satisfied for MVP.

Evidence:

- `ExecutionAPI.get_review_workbench(execution_id)` returns the canonical
  `review_workbench.v1` object.
- `ExecutionAPI.export_review_workbench_html(execution_id)` writes
  `review-workbench.html` and `review-workbench.json` from the same canonical
  object.
- Local server routes expose the JSON and browser HTML views:
  `/api/executions/<execution_id>/review-workbench` and
  `/review-workbench/<execution_id>`.
- Completed, failed, pending, and incomplete executions render explicit status,
  warnings, and empty states.
- The browser and static surfaces consume real execution artifacts; tests assert
  that missing diff sources do not create fake changed files or mock UI data.

### A human can inspect diff + rationale + evidence

Status: satisfied for MVP.

Evidence:

- PR diff artifacts exist as stable JSON and patch artifacts and are projected
  into `review_workbench.v1`.
- File-level diff navigation exists through stable file anchors and browser
  navigation links.
- Changed lines are represented as parsed hunk rows with add/delete/context
  classifications and browser highlight classes.
- Rationale mapping links changed files to task/run metadata, worker results,
  and prompt snapshot artifacts when exact changed-file paths are present.
- Evidence manifest projection includes execution state, PR/CI/review evidence,
  ledger replay, memory provenance refs, sandbox lifecycle, runtime evidence,
  path claims/conflict risks, and cleanup state.
- Missing or incomplete artifacts produce explicit warnings instead of synthetic
  evidence.

### A decision is recorded and persisted

Status: satisfied for MVP.

Evidence:

- `ExecutionAPI.record_review_decision()` persists review decisions as append-only
  ledger events.
- The browser POST route delegates to the same API rather than using a separate
  write path.
- Allowed decisions are guarded: `approve`, `request_changes`, and `reject`.
- Browser approval is guarded by completed Workbench status, while
  `request_changes` and `reject` remain available for pending/failed review.
- Repeating the same reviewer, decision, and reason is idempotent and returns the
  existing event rather than appending a duplicate.
- Recorded decisions appear in `reviewer_decision`, browser rendering, and
  ledger timeline evidence.

### Ledger reflects the full review lifecycle

Status: satisfied for MVP.

Evidence:

- Review decisions append `review_decision_recorded` ledger events.
- PR diff persistence appends `diff_artifact_persisted` ledger events.
- Review sync, review comments, merge evidence, CI binding, PR binding, evidence
  finalization, and memory provenance surfaces are covered by the #217
  ledger/evidence truth layer.
- Sandbox lifecycle, runtime, path claim, conflict risk, command, and cleanup
  evidence are recorded as ledger-backed evidence through the #218 Sandbox v0
  chain.
- Workbench consumes ledger/evidence artifacts and does not invent mocked review
  state.

## Evidence Surfaces

The current Workbench evidence surfaces are:

- `review_workbench.v1`
- Review Workbench JSON
- Review Workbench HTML
- browser local server JSON and HTML routes
- static export artifacts
- PR diff JSON and patch artifacts
- parsed file-level diff navigation and changed-line rows
- rationale mapping
- evidence manifest
- ledger timeline/replay evidence
- memory provenance refs and integrity warnings
- sandbox evidence
- runtime adapter evidence
- path claims and conflict risks
- cleanup evidence and recovery warnings
- review decisions

## Remaining Gaps Not V0 Blockers

The following are useful follow-ups but do not block #216 MVP closure:

- React dashboard polish
- advanced UX/animations
- multi-agent comparison
- enterprise RBAC
- GitHub approve/request-changes API bridge
- richer artifact drill-down
- cleanup action UX
- cross-run evidence graph
- production auth around Browser Workbench routes if deployed beyond
  local/operator use
- normalized runtime transcript ingestion when runtimes expose a stable format
- semantic changed-logic grouping beyond unified-diff line highlighting

## Closure Recommendation

No missing #216 MVP acceptance blocker was found in this audit.

Recommendation: close #216 as Review Workbench MVP completed after this audit PR
merges, if no missing acceptance blocker is found.

Use `Refs #216` for this audit PR so issue closure remains manual.

## Validation Plan For This Audit PR

This is a docs-only audit, but it asserts Workbench behavior. Run the focused
control-plane validation before merging:

```bash
git diff --check
git diff --cached --check
.venv/bin/ruff check .
PYTHONPATH=src .venv/bin/python -m pytest tests/test_review*.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution*.py tests/test_review*.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py -q
```

Full `tests -q` remains optional for this docs-only audit if local runtime cost
is acceptable.

Refs #216
