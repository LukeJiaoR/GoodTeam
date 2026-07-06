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

The browser-visible MVP is available through static export:

```bash
goodteam-os execution export-review-workbench \
  --execution-id <execution_id> \
  --project-root <repo>
```

It writes:

```text
.goodteam/executions/<execution_id>/artifacts/review-workbench.html
.goodteam/executions/<execution_id>/artifacts/review-workbench.json
```

When using the local server, the same canonical object is exposed at:

```text
/api/executions/<execution_id>/review-workbench?project_root=<repo>
/review-workbench/<execution_id>?project_root=<repo>
```

The guarded browser decision action is available only through the local server:

```text
POST /api/executions/<execution_id>/review-workbench/decision
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

## Browser Decision Route

Browser decisions use the same ledger contract as the Python API. The local
server accepts:

```json
{
  "project_root": "<repo>",
  "decision": "approve",
  "reviewer": "LukeJiaoR",
  "reason": "Evidence checks out."
}
```

The route calls:

```python
ExecutionAPI(project_root).record_review_decision(
    execution_id,
    decision,
    reviewer,
    reason,
)
```

It returns the record result plus the refreshed reviewer decision projection:

```json
{
  "status": "recorded",
  "execution_id": "<execution_id>",
  "event": {"payload": {"transition": "review_decision_recorded"}},
  "reviewer_decision": {"available": true, "decision": "approve"},
  "workbench_status": "completed"
}
```

Guardrails:

- invalid decision returns 400 and writes no ledger event
- missing reviewer returns 400 and writes no ledger event
- missing execution returns 400/404 and does not create an execution ledger file
- `approve` is allowed only when the Workbench status is `completed`
- `request_changes` and `reject` are allowed for pending or failed executions
- GET Workbench JSON and HTML routes remain read-only
- no GitHub review/approve/request-changes API is called
- no PR merge is attempted
- no worker is run
- no memory is written
- existing ledger events are not modified

Static export keeps the same form visible but disabled with
`Decision actions require local server route.` Static HTML does not include the
POST script, so opening the exported file cannot accidentally write a ledger
decision.

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
existing ledger event and does not append a duplicate row. If a later diff
source would change the already-persisted artifact, the producer returns
`diff_artifact_changed` without overwriting `pr-diff.json`, overwriting
`pr-diff.patch`, or appending another ledger event.

## Workbench Diff Rendering

`get_review_workbench(execution_id)` checks the stable `pr-diff.json` path first.
When present, `diff.available = true`, `diff.reason = ""`, and the Workbench
returns changed files, `artifact_path`, optional `patch_path`, and a deterministic
summary such as `2 files changed (+130/-2)`. The text renderer prints this
summary and a compact changed-file list with additions and deletions.

Workbench reads remain read-only. They never fetch GitHub data, write artifacts,
append ledger events, or repair missing artifact state.

## Browser Rendering

The browser MVP renders `review_workbench.v1` into HTML and writes the same
object as `review-workbench.json`. HTML embeds the JSON in
`script#review-workbench-data`, so the page can be opened without a backend
service after export.

The page displays execution identity, source issue, PR ref, branch, commit, diff
summary, changed files, CI summary, review observation counts, memory provenance
integrity, warnings, current decision, a decision form, sandbox empty state, and
ledger timeline. The GET page and JSON routes are read-only. The only browser
write path is the explicit local-server decision POST described above.

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

- Add file-level navigation and changed logic highlighting for diff review.
- Add richer rationale mapping from individual changed files to prompts and
  instructions.
- Add #218 sandbox/action evidence when that surface exists.
- Add deeper React dashboard polish around warnings, review decisions, and
  artifact drill-downs.

Use `Refs #216`, not `Closes #216`.
