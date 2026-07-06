# Browser Review Workbench MVP

## Purpose

The Browser Review Workbench opens a GoodTeam execution in a browser-readable
surface without inventing UI data. Both the local server page and the static
export render the canonical object from:

```python
ExecutionAPI(project_root).get_review_workbench(execution_id)
```

Workbench reads remain read-only. The local server page adds one guarded write
action: an explicit decision POST that records a local GoodTeam review decision
through the existing ledger API. Static exports remain read-only and show that
decision actions require the local server route.

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

Local server decision action:

```text
POST /api/executions/<execution_id>/review-workbench/decision
```

Request JSON:

```json
{
  "project_root": "<project_root>",
  "decision": "approve",
  "reviewer": "LukeJiaoR",
  "reason": "Evidence checks out."
}
```

Response JSON:

```json
{
  "status": "recorded",
  "execution_id": "<execution_id>",
  "event": {"payload": {"transition": "review_decision_recorded"}},
  "reviewer_decision": {"available": true, "decision": "approve"},
  "workbench_status": "completed"
}
```

`status` can be `recorded` or `deduped`; dedupe follows the existing
`reviewer + decision + reason` idempotency contract.

## Data Source Contract

`review-workbench.json` is the exact `review_workbench.v1` object. The HTML page
embeds the same JSON in:

```html
<script id="review-workbench-data" type="application/json">
```

The renderer consumes only that object. Missing diff or rationale inputs remain
visible as explicit empty states; the browser page does not fabricate changed
files, rationale, sandbox evidence, CI, reviews, or decisions.

GET endpoints do not write ledger events. Only the explicit decision POST can
append a `review_decision_recorded` event.

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
- local-server decision form with reviewer, decision, reason, and submit button
- sandbox empty state for #218
- ledger timeline

## Decision Guardrails

Allowed browser decisions are:

- `approve`
- `request_changes`
- `reject`

Guardrails:

- invalid decision returns 400 and does not append a ledger event
- missing reviewer returns 400 and does not append a ledger event
- missing execution returns 400/404 and does not create
  `.goodteam/executions/<missing>/events.jsonl`
- `approve` requires `workbench_status = completed`
- `request_changes` and `reject` are allowed for pending or failed executions so
  a reviewer can leave a local negative/return-to-work decision
- no GitHub approve/request-changes API is called
- no PR merge is attempted
- no worker is run
- no memory is written
- existing ledger events are not modified

The route delegates the ledger write to:

```python
ExecutionAPI(project_root).record_review_decision(
    execution_id,
    decision,
    reviewer,
    reason,
)
```

It does not duplicate the append-only ledger write logic.

## Empty States

The browser surface renders these states without crashing:

- missing diff artifact: `diff_artifact_missing`
- missing rationale artifact: `rationale_artifact_missing`
- pending execution: `status = pending` plus `incomplete_reason`
- failed execution: `status = failed` plus failure evidence
- sandbox not implemented: `sandbox_evidence_not_implemented`
- no decision: `No review decision recorded.`

## Static Export Limitation

The static artifact can still be opened directly from disk, but its decision
form is disabled and displays:

```text
Decision actions require local server route.
```

Static HTML does not include POST JavaScript and does not imply that a local
ledger write can happen without the server.

## CLI Decision Fallback

Review decisions can also be recorded through:

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
- #218 sandbox/action evidence
- deeper product polish inside the React dashboard

Use `Refs #216`, not `Closes #216`.
