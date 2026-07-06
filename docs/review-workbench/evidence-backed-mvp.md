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

- Persist and render a real PR-level diff artifact.
- Add a browser UI that consumes `review_workbench.v1`.
- Add richer rationale mapping from individual changed files to prompts and
  instructions.
- Add #218 sandbox/action evidence when that surface exists.
- Add UI polish, file-level navigation, and changed logic highlighting.

Use `Refs #216`, not `Closes #216`.
