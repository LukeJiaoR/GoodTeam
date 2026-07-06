# Browser Review Workbench MVP

## Purpose

The Browser Review Workbench opens a GoodTeam execution in a browser-readable
surface without inventing UI data. Both the local server page and the static
export render the canonical object from:

```python
ExecutionAPI(project_root).get_review_workbench(execution_id)
```

The browser surface is read-only. It does not call GitHub, run workers, append
ledger events, write memory, approve reviews, request changes, reject reviews,
or merge PRs.

## Entrypoints

Static export:

```bash
goodteam-os execution export-review-workbench \
  --execution-id <execution_id> \
  --project-root <project_root>
```

The command writes:

```text
.goodteam/executions/<execution_id>/artifacts/review-workbench.html
.goodteam/executions/<execution_id>/artifacts/review-workbench.json
```

Local server JSON:

```text
/api/executions/<execution_id>/review-workbench?project_root=<project_root>
```

Local server page:

```text
/review-workbench/<execution_id>?project_root=<project_root>
```

## Data Source Contract

`review-workbench.json` is the exact `review_workbench.v1` object. The HTML page
embeds the same JSON in:

```html
<script id="review-workbench-data" type="application/json">
```

The renderer consumes only that object. Missing diff or rationale inputs remain
visible as explicit empty states; the browser page does not fabricate changed
files, rationale, sandbox evidence, CI, reviews, or decisions.

## Rendered Sections

The MVP page shows:

- execution ID and final status
- source issue
- PR ref, branch, and commit
- diff summary and changed files from the persisted PR diff artifact
- CI summary and review observation counts
- memory provenance integrity and memory refs
- warnings and incomplete reason
- current reviewer decision
- sandbox empty state for #218
- ledger timeline

## Empty States

The browser surface renders these states without crashing:

- missing diff artifact: `diff_artifact_missing`
- missing rationale artifact: `rationale_artifact_missing`
- pending execution: `status = pending` plus `incomplete_reason`
- failed execution: `status = failed` plus failure evidence
- sandbox not implemented: `sandbox_evidence_not_implemented`
- no decision: `No review decision recorded.`

## Current Limitations

This slice intentionally omits browser write actions. Review decisions can still
be recorded through:

```bash
goodteam-os execution record-review-decision \
  --execution-id <execution_id> \
  --project-root <project_root> \
  --decision approve \
  --reviewer <reviewer>
```

Remaining #216 gaps:

- file-level navigation and changed-line highlighting
- richer mapping from changed files to rationale and prompts
- browser decision actions through a guarded backend route
- #218 sandbox/action evidence
- deeper product polish inside the React dashboard

Use `Refs #216`, not `Closes #216`.
