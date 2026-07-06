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

The canonical `diff` field now projects persisted PR diff artifacts into a
file-level review model. When `.goodteam/executions/<execution_id>/artifacts/pr-diff.json`
is present, `diff.changed_files[]` includes stable anchors, parsed hunks from
`pr-diff.patch`, and file-level rationale evidence refs:

```json
{
  "path": "src/goodteam_os/execution/api.py",
  "status": "modified",
  "additions": 10,
  "deletions": 2,
  "anchor": "file-src-goodteam-os-execution-api-py",
  "hunks": [
    {
      "header": "@@ -10,3 +10,4 @@",
      "old_start": 10,
      "new_start": 10,
      "lines": [
        {"kind": "context", "old_line": 10, "new_line": 10, "text": " def get_review_workbench():"},
        {"kind": "delete", "old_line": 11, "new_line": null, "text": "-    return diff"},
        {"kind": "add", "old_line": null, "new_line": 11, "text": "+    return rich_diff"}
      ]
    }
  ],
  "rationale_refs": [
    {
      "kind": "worker_result",
      "title": "Worker result",
      "path": ".goodteam/workers/jobs/<worker-id>/result.md",
      "excerpt": "Implemented src/goodteam_os/execution/api.py...",
      "confidence": "direct"
    }
  ],
  "rationale_status": "mapped"
}
```

Rationale mapping is evidence-backed only. The mapper reads existing
`review_workbench["rationale"]` sources, worker `result.md`, and worker prompt
snapshot files (`role_prompt.md`, `task.md`, `context.md`, `constraints.md`).
It never calls an LLM and never invents reasoning. A ref is emitted only when a
source artifact contains the exact changed-file path. Current refs use
`confidence = direct`; `contextual` is reserved for future rules that can show a
general execution-level rationale without implying a fake file-specific reason.
If no source can be mapped to a file, the file keeps `rationale_refs = []` and
`rationale_status = not_mapped`; if no rationale artifacts exist,
`rationale_status = rationale_artifact_missing`.

## Rendered Sections

The MVP page shows:

- execution ID and final status
- source issue
- PR ref, branch, and commit
- diff summary, file navigation, and changed-line highlights from the persisted
  PR diff artifacts
- rationale evidence under each changed file, including status, source kind,
  artifact path, bounded excerpt, and confidence
- CI summary and review observation counts
- memory provenance integrity and memory refs
- warnings and incomplete reason
- current reviewer decision
- local-server decision form with reviewer, decision, reason, and submit button
- sandbox evidence from #218 ledger events, including status, workspace/worktree
  paths, command history, rejected commands, conflict risks, and redaction
  markers
- ledger timeline

The diff section renders a file navigation list above the diff body. Each file
links to `#<file-anchor>` and displays status, additions, and deletions. File
sections render hunk headers, old/new line numbers, and line text. Changed-line
CSS classes are stable for tests and future styling:

- `diff-line-add`
- `diff-line-delete`
- `diff-line-context`
- `diff-line-metadata`

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
- missing patch file with available changed-file metadata: `patch_missing`
- malformed patch file: `patch_parse_failed`
- missing rationale artifact: `rationale_artifact_missing`
- changed files with no mapped rationale refs:
  `rationale_mapping_missing`
- partially mapped changed files: `rationale_mapping_partial`
- pending execution: `status = pending` plus `incomplete_reason`
- failed execution: `status = failed` plus failure evidence
- sandbox evidence missing: `sandbox_evidence_missing`
- no decision: `No review decision recorded.`

When `pr-diff.json` exists but `pr-diff.patch` is missing, the browser still
shows file navigation from `changed_files[]` and renders each file with an
explicit `patch_missing` empty hunk state. When the patch is malformed, the
parser does not crash; the browser shows `patch_parse_failed` and does not show
partial hunks as complete review evidence.

When rationale artifacts exist but a file has no exact-path match, the file
renders `No file-level rationale mapping available.` When rationale artifacts
are absent, it renders `Rationale artifacts missing.` These are explicit empty
states, not hidden failures.

## Patch Parser Limits

The patch parser is a small deterministic unified-diff parser. It supports
`diff --git a/... b/...`, `---`/`+++` file headers, hunk headers in
`@@ -old,count +new,count @@` form, and context/add/delete/metadata lines. It
does not interpret rename similarity metadata, binary patches, submodule
summaries, or Git extended headers beyond preserving file identity and parsed
hunks. Unsupported or malformed input is surfaced as an explicit empty state
instead of being silently ignored.

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

- deeper #218 integration beyond the current ledger-backed sandbox evidence:
  mandatory migration of all agent execution paths, typed GitHub/CI broker,
  stronger filesystem isolation, cleanup/recovery hardening, and optional
  Docker/VM isolation
- deeper product polish inside the React dashboard
- final #216 acceptance audit after the remaining surfaces land

Use `Refs #216`, not `Closes #216`.
