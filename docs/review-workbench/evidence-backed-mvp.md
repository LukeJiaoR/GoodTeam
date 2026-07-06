# Evidence-Backed Review Workbench MVP

## Purpose

The Review Workbench MVP opens a GoodTeam execution by `execution_id` and
projects its canonical evidence into a reviewable contract:

```python
ExecutionAPI(project_root).get_review_workbench(execution_id)
```

The textual renderer is available through:

```bash
python -m goodteam_os.execution.review_workbench <execution_id> --project-root <repo>
```

## Evidence Source Contract

`review_workbench.v1` consumes `ExecutionAPI.get_evidence()` directly. It does
not call GitHub, run workers, refresh CI, synthesize reviews, or mock UI data.
The embedded `evidence` field is the canonical
`execution_evidence_manifest.v1` object.

The workbench object exposes:

- `task`, `source_issue`, `pr`, `status`
- `diff` empty state or persisted diff artifact metadata
- `rationale` from task/run metadata, worker result, and worker prompt snapshots
- `evidence` with ledger timeline, CI/review/merge summaries, final status, and
  incomplete reason
- `memory_refs` and `memory_provenance_integrity`
- `reviewer_decision`
- `sandbox` future hook for #218
- `warnings`

## Memory Provenance Integrity

The workbench displays the exact `memory_provenance_refs` and
`memory_provenance_integrity` from the evidence manifest. Broken, duplicate, or
orphan provenance remains visible as warnings; evidence queries and workbench
reads do not repair or mutate memory.

## Review Decision Ledger Event

Review decisions are recorded through:

```python
ExecutionAPI(project_root).record_review_decision(
    execution_id,
    "approve",
    "reviewer",
    reason="Evidence checks out.",
)
```

Allowed decisions are `approve`, `request_changes`, and `reject`. The write path
appends one ledger event with:

- `event_type = review_recorded`
- `payload.transition = review_decision_recorded`
- `payload.source = review_workbench`
- `decision`, `reviewer`, `reason`, `reviewed_at`
- current evidence schema version and evidence hash

Repeating the same `reviewer + decision + reason` returns the existing event and
does not append another ledger row. The decision path does not write memory and
does not mutate existing ledger events.

## PR Diff Artifact Contract

PR-level diffs are persisted through:

```python
ExecutionAPI(project_root, github_client=client).persist_pr_diff(execution_id)
```

The producer is offline-testable and only consumes a client-provided
`fetch_pr_diff(repo, pr_number)` payload when that method exists. If no real diff
source is available, it returns:

```json
{"status": "unavailable", "reason": "diff_source_unavailable", "changed_files": []}
```

It does not fabricate changed files. Pending executions without a PR binding
return `pr_binding_missing`; failed executions return `execution_not_completed`.

The stable artifact paths are:

```text
.goodteam/executions/<execution_id>/artifacts/pr-diff.json
.goodteam/executions/<execution_id>/artifacts/pr-diff.patch
```

`pr-diff.json` uses `schema_version = execution_pr_diff.v1` and records:

- `execution_id`, `repo`, `pr_number`, `branch_name`, `base_ref`, `head_sha`
- `changed_files[]` with `path`, `status`, `additions`, `deletions`, and
  `patch_excerpt`
- `patch_path` when patch content was available
- `created_at`

When the artifact is persisted, GoodTeam appends one idempotent ledger event:

- `event_type = status_changed`
- `payload.transition = diff_artifact_persisted`
- `payload.source = review_workbench`
- `payload.schema_version = execution_pr_diff.v1`
- `payload.artifact_path`, `payload.patch_path`, and
  `payload.changed_file_count`

Repeating `persist_pr_diff(execution_id)` for the same artifact returns the
existing ledger event and does not append a duplicate row.

## Workbench Diff Rendering

`get_review_workbench(execution_id)` checks the stable `pr-diff.json` path first.
When present, `diff.available = true`, `diff.reason = ""`, and the Workbench
returns changed files, `artifact_path`, optional `patch_path`, and a deterministic
summary such as `2 files changed (+130/-2)`. The text renderer prints this
summary and a compact changed-file list with additions and deletions.

Workbench reads remain read-only. They never fetch GitHub data, write artifacts,
append ledger events, or repair missing artifact state.

## Empty States

This MVP does not fake missing artifacts.

- Missing persisted PR diff: `diff.available = false` and
  `diff.reason = diff_artifact_missing`
- Missing rationale surface: `rationale.available = false` and
  `rationale.reason = rationale_artifact_missing`
- Pending, incomplete, or failed executions: `status`, `incomplete_reason`, and
  `warnings` explain the current state
- Sandbox/action evidence for #218:
  `sandbox.available = false` and
  `sandbox.reason = sandbox_evidence_not_implemented`

## Remaining #216 Gaps

This PR is a usable MVP slice, not full #216 completion. Remaining gaps:

- Add a browser UI that consumes `review_workbench.v1`.
- Add file-level navigation and changed logic highlighting for diff review.
- Add richer rationale mapping from individual changed files to prompts and
  instructions.
- Add #218 sandbox/action evidence when that surface exists.
- Add UI polish around warnings, review decisions, and artifact drill-downs.

Use `Refs #216`, not `Closes #216`.
