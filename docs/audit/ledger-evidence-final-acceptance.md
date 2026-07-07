# Ledger Evidence Final Acceptance Audit

Date: 2026-07-07

Target issue: #217, "P0-3: Ledger & Evidence System (Append-only
Execution Truth Layer)"

Baseline: `origin/main` at `ac98bc5`, the #313 merge commit.

Issue state note: `gh api repos/LukeJiaoR/GoodTeam/issues/217` returned
`OPEN` during this audit. This PR should use `Refs #217` rather than an
issue-closing keyword so issue closure remains an explicit maintainer decision.

## Executive Summary

Conclusion: Ledger & Evidence System v0 is complete for the GoodTeam-controlled
execution, review, provenance, and sandbox lifecycle after the #295 through
#313 chain.

#217 asked for a canonical append-only execution truth layer that records agent
execution as replayable, auditable events. The merged chain now records
execution intake, worker linkage, execution lifecycle, PR binding, CI/review/
merge sync, final memory provenance, canonical evidence manifests, Review
Workbench projections, PR diff artifacts, browser decision actions, rationale
mapping, and sandbox/runtime/path/cleanup evidence.

Recommendation: close #217 as Ledger & Evidence System MVP completed after this
audit PR merges, if no missing acceptance blocker is found.

No missing acceptance blocker was found in this audit. Remaining gaps listed
below are hardening and product follow-ups, not v0 blockers.

## Scope Summary

GoodTeam ledger/evidence v0 owns:

- append-only execution events
- execution run lifecycle
- worker job linkage
- PR binding / CI / review / merge events
- final provenance / memory refs
- evidence query surface
- PR diff artifact references
- Review Workbench evidence projection
- sandbox/runtime/path/cleanup evidence from #309 through #313

The ledger is the GoodTeam control-plane truth layer. It is the durable record
for GoodTeam-owned execution state, evidence, review decisions, GitHub
lifecycle observations, provenance closure, and sandbox evidence.

It is not a complete observability warehouse. v0 does not require a realtime
analytics dashboard, multi-run analytics, or full normalization of every
third-party runtime internal transcript. External runtime transcripts can remain
referenced as artifacts until the runtime exposes a stable machine-readable
contract.

## PR Chain Mapping

| PR | Title | Merge commit | Contributed capability | #217 acceptance contribution |
| --- | --- | --- | --- | --- |
| #295 | `test(execution): add first runnable P0-1 dogfood path` | `89039cc9764c6108e32832c396537e509835333c` | Added the first offline runnable execution path with deterministic task/run IDs, worker job creation, ordered ledger events, replay payload, and idempotent reruns. | Establishes the initial full execution event chain and replayable local path. |
| #296 | `feat(execution): add PR creation boundary for completed runs` | `f2df7dfcba14ac129c5bd6e6b3bc816325141934` | Added idempotent PR creation/binding for completed execution runs and appends `pr_opened` ledger events with GitHub actor and commit metadata. | Satisfies GitHub PR linkage and PR binding lifecycle. |
| #298 | `feat(execution): sync GitHub PR lifecycle into ledger` | `a53540ea85ab1cfb0461e4165744c74d94898277` | Added CI observation, review/comment sync, and merge sync with idempotent ledger writes for `ci_observed`, `review_recorded`, `pr_comment_recorded`, and `merge_completed`. | Satisfies PR/CI/review/merge traceability and end-to-end timeline reconstruction. |
| #299 | `feat(execution): finalize memory provenance` | `2cfde57b8a8731ebd7514dfaf35ceadfa1d59ea4` | Added final execution provenance from ledger replay, writes one idempotent memory event, and appends `provenance_written`. | Satisfies memory linkage and final provenance closure. |
| #300 | `docs(audit): finalize GitHub integration loop closure` | `63adc1b583622e5288ceafa312ece22717b915f4` | Recorded #219 system closure for issue -> task -> agent -> PR -> review -> merge -> ledger -> memory. | Documents the GitHub loop evidence chain consumed by this #217 audit. |
| #301 | `feat(execution): add canonical evidence query surface` | `1c8221a5953f96a56746bf6745b49b734561ec0d` | Added `ExecutionAPI.get_evidence()` / `ExecutionEvidenceService.get_evidence()` and `execution_evidence_manifest.v1` built from ledger replay and memory WAL state. | Satisfies queryability, replay projection, and timeline reconstruction surface. |
| #302 | `feat(execution): link memory provenance to ledger events` | `2ee3e73dda0cfbfebf8f15c32eb7032dae8dd6f7` | Promoted memory provenance refs into a first-class ledger-linked contract with integrity audit and read-only projection. | Satisfies ledger links to memory entries. |
| #303 | `feat(review): add evidence-backed review workbench MVP` | `4abbb54eaccaf46abf23d77f2ddcdfd9bf7f3759` | Added `review_workbench.v1`, `ExecutionAPI.get_review_workbench()`, `record_review_decision()`, CLI/text surfaces, and append-only review decision events. | Satisfies Review Workbench consuming ledger/evidence and review decision linkage. |
| #304 | `feat(review): persist PR diff artifacts for workbench` | `36f6c7a4d35c93d0438646fee91d5b15db688a28` | Added `ExecutionAPI.persist_pr_diff()`, stable `pr-diff.json` / `pr-diff.patch` artifacts, Workbench diff projection, and `diff_artifact_persisted` events. | Strengthens replayable PR evidence and Review Workbench artifact traceability. |
| #305 | `feat(review): add browser workbench for execution evidence` | `e3e7b635b5191a74ff83316b288ac2f91037dba1` | Added browser-visible Workbench HTML/JSON artifacts and local read-only server routes backed by `review_workbench.v1`. | Satisfies evidence projection into a human review surface. |
| #306 | `feat(review): add guarded browser decision actions` | `ed1688db86c1b77e988ef6155663f3d0229b5878` | Added guarded browser decision POST route delegating to `ExecutionAPI.record_review_decision()` with ledger idempotency and guardrails. | Satisfies browser review decisions linked to ledger events. |
| #307 | `feat(review): add file-level diff navigation to workbench` | `8be89b5dadbafa46ac0317f45f158d0b814046e0` | Added deterministic unified diff parsing, file anchors, hunk rows, changed-line highlighting, and warning states. | Strengthens PR diff evidence reconstruction and review inspection. |
| #308 | `feat(review): map changed files to rationale evidence` | `34e0e5f4f96ae3bf834175cf346c7b981ab30393` | Added deterministic changed-file to rationale/prompt mapping from existing artifacts with read-only Workbench enrichment. | Strengthens end-to-end explanation evidence for reviewed changes. |
| #309 | `feat(sandbox): add auditable sandbox v0 boundary` | `fcf73a0f671885f25f15bcacb26ffbc2e3a14cf9` | Added sandbox metadata, command policy, secret scrubbing, lifecycle events, conflict risk, and Workbench sandbox projection. | Adds sandbox evidence to the execution truth layer. |
| #310 | `feat(sandbox): run executions through workspace lifecycle evidence` | `7ef26b4f20ea787c7c1451101c47f5f707b4c292` | Routed execution through workspace/worktree lifecycle evidence, runtime cwd selection, worker context, retention for PR/diff handoff, and explicit cleanup. | Links agent runs and runtime workspace evidence to ledger replay. |
| #311 | `feat(sandbox): capture runtime adapter evidence and cleanup state` | `6aadfc0bd37e0a968bf2c41cf0d97c3682cbdbbf` | Captured adapter/binary/model/cwd/policy evidence, transcript/result artifact paths, cleanup requested/destroyed/failed, and recovery warnings. | Strengthens agent run, runtime, transcript artifact, and cleanup auditability. |
| #312 | `feat(sandbox): infer execution path claims` | `cf92f277e66d19a090dd05b421f339c33db61598` | Added path claim inference, warnings, exact/prefix conflict evidence, and Workbench claimed path surface. | Adds filesystem scope evidence and conflict risk to the timeline. |
| #313 | `docs(audit): finalize sandbox v0 acceptance` | `ac98bc582bf8fdca296a501e5aa5178a53fe038d` | Recorded #218 final acceptance audit and scoped non-blocking sandbox follow-ups. | Confirms sandbox/runtime/path/cleanup evidence can be treated as v0 closure evidence for #217. |

## Acceptance Criteria Mapping

| #217 acceptance criterion | Audit result | Evidence | Caveat |
| --- | --- | --- | --- |
| A full agent execution produces a complete event chain | MVP satisfied for GoodTeam-controlled execution and review lifecycle. | Current covered chain includes `intake_created`, `worker_job_created`, `execution_started`, sandbox lifecycle events, `execution_completed` or `execution_failed`, `pr_opened`, `ci_observed`, `review_recorded`, `pr_comment_recorded`, `merge_completed`, `provenance_written`, `diff_artifact_persisted`, and `review_decision_recorded`. Tests assert ordered transitions across execution, PR, CI, review, merge, provenance, Workbench, and sandbox paths. | Original issue examples such as `task_created`, `agent_assigned`, and `tests_run` are represented by GoodTeam's current task/run/worker/evidence transitions rather than a one-to-one literal event name set. External runtime internals may remain artifact references. |
| Ledger can reconstruct execution timeline end-to-end | Satisfied for v0. | `ExecutionLedger.replay()` reads task, run, and append-only `events.jsonl`; `ExecutionAPI.replay()` exposes status; `ExecutionAPI.get_evidence()` returns deterministic `execution_evidence_manifest.v1`; Workbench embeds the same evidence. | Replay reconstructs GoodTeam-owned control-plane events. Future richer replay CLI and cross-run graphing remain follow-ups. |
| Ledger links to GitHub PRs | Satisfied. | #296 and #298 store PR number, repo, branch, commit SHA, CI observations, reviews/comments, merge state, and idempotent lifecycle events. #304 persists PR diff artifacts with paths linked by ledger event. | Live webhook delivery and typed GitHub/CI broker hardening remain future work. |
| Ledger links to Review decisions | Satisfied. | #303 added `ExecutionAPI.record_review_decision()` and #306 added browser decision POST. Decisions append `review_recorded` events with `payload.transition = review_decision_recorded`, decision, reviewer, reason, evidence hash, and idempotency. | Browser decisions do not call GitHub approve/request-changes APIs or merge PRs in v0. |
| Ledger links to Memory entries | Satisfied. | #299 writes final provenance memory and a `provenance_written` ledger closure. #302 joins ledger closure and memory WAL IDs through `memory_provenance_refs` and `memory_provenance_integrity`. | Pending, incomplete, and failed executions report provenance pending reasons instead of writing memory. |
| Ledger links to Agent runs | Satisfied. | Execution run model stores task/run identity, adapter, branch, status, PR number, and worker job ID. Worker job status and invocation evidence capture runtime cwd, sandbox context, adapter/binary/model evidence, stdout/stderr/result paths, and memory citations. | Runtime-owned internal transcripts are referenced through artifacts unless adapters expose stable normalized data. |
| Review Workbench reads from ledger | Satisfied. | `review_workbench.v1` consumes `ExecutionAPI.get_evidence()` directly, projects ledger events, PR diff artifacts, memory refs, review decisions, sandbox evidence, and browser/static HTML surfaces. | Deep React dashboard polish is out of scope for #217 v0. |
| Memory system references ledger events | Satisfied. | `memory_provenance_refs` include `memory_event_id`, `ledger_event_id`, source, role, horizon, evidence hash, timestamps, supersession, status, and integrity issues. | Future repair tooling can act on broken/orphan refs, but detection is already surfaced. |

### Complete Event Chain

MVP satisfied for GoodTeam-controlled execution and review lifecycle.

Current event coverage includes:

- intake / execution start: `intake_created`, `worker_job_created`,
  `execution_started`
- worker completion/failure: `execution_completed`, `execution_failed`, worker
  job status, and invocation evidence
- PR opened / PR bound: `pr_opened` plus stored GitHub binding
- CI synced: `ci_observed`
- review synced or decision recorded: `review_recorded`,
  `pr_comment_recorded`, `review_decision_recorded`
- merge synced: `merge_completed`
- final provenance / memory refs: `provenance_written`,
  `memory_provenance_refs`, `memory_provenance_integrity`
- review artifacts: `diff_artifact_persisted`, PR diff artifacts, rationale
  mapping
- sandbox lifecycle / runtime evidence / cleanup / conflict risk:
  `sandbox_created`, `sandbox_retained_for_pr`,
  `sandbox_cleanup_requested`, `sandbox_destroyed`,
  `sandbox_destroy_failed`, command evidence, runtime invocation evidence, path
  claims, and conflict risk

External runtime internal transcripts may remain referenced as stdout/stderr/
result artifacts rather than normalized into every ledger event.

### Timeline Reconstruction

Satisfied for v0.

Existing APIs and tests reconstruct:

- execution run: task/run JSON plus `ExecutionAPI.replay()`
- ledger events: append-only `.goodteam/executions/<execution_id>/events.jsonl`
  and `ExecutionLedger.replay()`
- evidence query surface: `ExecutionAPI.get_evidence(execution_id)` returning
  `execution_evidence_manifest.v1`
- Workbench object: `ExecutionAPI.get_review_workbench(execution_id)` returning
  `review_workbench.v1`
- PR diff artifact: `ExecutionAPI.persist_pr_diff(execution_id)` with stable
  `pr-diff.json` and `pr-diff.patch`
- sandbox evidence: sandbox metadata, ledger events, runtime invocation
  evidence, cleanup status, path claims, and Workbench projection

### GitHub PR Links

Satisfied.

PR binding lifecycle from #296, #298, and #304 covers:

- PR number and repo
- branch name and commit SHA
- PR URL/reference through GitHub binding data
- CI observations and summary
- review/comment observations
- merge metadata
- PR diff artifact JSON and patch paths
- idempotency for repeated PR creation, CI sync, review/comment sync, merge
  sync, and diff artifact persistence

### Review Decision Links

Satisfied.

#303 and #306 cover:

- `ExecutionAPI.record_review_decision(execution_id, decision, reviewer,
  reason)`
- allowed decisions: `approve`, `request_changes`, and `reject`
- append-only `review_recorded` ledger events with
  `review_decision_recorded` transition
- idempotency for repeated reviewer/decision/reason writes
- Browser Workbench POST route writes through the same API
- read-only GET/static export routes do not write decisions

### Memory Entry Links

Satisfied.

#299 and #302 cover:

- final provenance built from ledger replay
- one idempotent role memory event for completed executions
- `provenance_written` ledger closure with memory event ID and evidence hash
- `ExecutionAPI.audit_memory_provenance(execution_id)`
- integrity states for complete, missing memory event, orphan memory event,
  duplicate ledger closure, pending, failed, and incomplete executions

### Agent Run Links

Satisfied.

The v0 execution model links:

- execution ID
- task ID and source issue
- run status and branch
- worker job ID
- worker adapter and role
- sandbox/workspace context
- runtime cwd and adapter evidence
- stdout/stderr/result artifact paths
- PR binding and final provenance

## Evidence Surfaces

Current evidence surfaces:

- ledger JSONL / event store:
  `.goodteam/executions/<execution_id>/events.jsonl`
- task/run state:
  `.goodteam/executions/<execution_id>/task.json` and `run.json`
- ExecutionAPI evidence query:
  `ExecutionAPI.get_evidence(execution_id)`
- Review Workbench JSON:
  `review_workbench.v1`
- Browser Workbench HTML:
  static `review-workbench.html` export and local server route
- PR diff artifacts:
  `pr-diff.json` and `pr-diff.patch`
- memory provenance refs:
  `memory_provenance_refs` and `memory_provenance_integrity`
- sandbox metadata:
  `.goodteam/executions/<execution_id>/sandbox.json`
- worker job status / invocation evidence:
  worker job store, runtime cwd, adapter evidence, stdout/stderr/result paths
- CI/review/merge sync records:
  `ci_observed`, `review_recorded`, `pr_comment_recorded`, and
  `merge_completed` ledger events

## Remaining Gaps That Should Not Block v0

The following items should remain follow-up candidates rather than blocking
#217 MVP closure:

- normalized Codex/Claude transcript ingestion if runtime exposes it
- typed event schema hardening / migration docs
- richer event replay CLI
- analytics/dashboard visualization
- cross-run evidence graph
- external tool broker events once GitHub/CI broker exists
- stronger retention/compaction policy
- live webhook delivery for GitHub lifecycle observations
- richer repair tooling for broken/orphan memory provenance refs

These are useful hardening and product improvements, but the current
GoodTeam-owned control-plane truth layer is sufficient for v0.

## Verification Recommendation

This is a docs-only audit PR. The repository testing policy would allow Tier 0
validation, but this document asserts control-plane behavior. Recommended
validation is therefore Tier 0 plus focused execution/review/worker regression:

```bash
git diff --check
.venv/bin/ruff check .
PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution*.py tests/test_review*.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests/test_workers.py -q
```

Full `tests -q` is optional unless maintainers want a fresh repository-wide
signal before manual issue closure.
