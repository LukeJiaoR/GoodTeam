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
- `sandbox` evidence from #218 ledger events, or `sandbox_evidence_missing`
- `warnings`

## Runtime-Native Sandbox Strategy

GoodTeam does not replace Codex CLI / Claude Code CLI runtime sandboxing, and
the Workbench does not infer that native controls were enabled. If launch
arguments bypass sandboxing or approvals, GoodTeam records the unsafe mode and
surfaces it as review evidence.

GoodTeam owns:

- execution workspace/worktree lifecycle
- runtime cwd selection
- worker context
- ledger evidence
- Review Workbench rendering

Codex/Claude own their native runtime controls when those controls are enabled
by the configured launch:

- command permissions
- filesystem/network sandbox
- approval policy
- model/tool execution safety

`sandbox_context` is GoodTeam's workspace/evidence boundary context. It does not
replace Codex/Claude runtime sandboxing. Real coding executions should run from
git worktree mode or another repo-backed workspace. Empty workspace fallback
directories are metadata/evidence boundaries unless materialized as repos, so
worker status records an explicit `workspace_cwd_fallback` when the CLI must use
the project root instead.

Workers may share the repo-level `.venv`, pip cache, npm cache, pnpm store, and
tool binaries. When `<runtime_cwd>/src` exists, GoodTeam prepends it to
`PYTHONPATH` so Python imports prefer the execution workspace. `run_sandbox_command()`
is the narrow command-policy test surface, not the main Codex/Claude runtime
path.

Worker status and Workbench sandbox evidence now include runtime adapter
evidence captured from the real worker invocation:

- `runtime_adapter`, `runtime_binary`, `runtime_version`, and `runtime_model`
- `runtime_cwd`, `runtime_workspace_mode`, and `runtime_workspace_status`
- `runtime_sandbox_mode`, `runtime_approval_policy`, and `runtime_unsafe_mode`
- `runtime_policy_warning` with either `runtime_policy_unsafe` or
  `runtime_policy_unverified`
- `stdout_path`, `stderr_path`, `result_path`, and a blank
  `runtime_transcript_path` when the adapter does not expose one

Version detection is local and best-effort through `<binary> --version`. Missing
binaries or failed version commands record `runtime_version = unknown` and a
version warning. GoodTeam does not persist tokens, secret values, or full
environment dumps in runtime evidence. Unknown sandbox or approval policy is a
review warning in this MVP, not a hard execution failure.

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
summary such as `2 files changed (+130/-2)`.

The Workbench also projects the persisted patch into file-level navigation data.
Each `diff.changed_files[]` entry keeps the artifact metadata (`path`, `status`,
`additions`, `deletions`, and `patch_excerpt`) and adds:

- `anchor`: a stable browser fragment such as
  `file-src-goodteam-os-execution-api-py`
- `hunks[]`: parsed unified-diff hunks with old/new start lines
- `hunks[].lines[]`: `context`, `add`, `delete`, or `metadata` rows with old/new
  line numbers
- `rationale_refs[]`: existing artifact refs that mention the exact changed-file
  path
- `rationale_status`: `mapped`, `not_mapped`, or
  `rationale_artifact_missing`

The text renderer prints the summary and compact changed-file list. The browser
renderer uses the richer projection for navigation and highlighted file diffs.

### Changed-File Rationale Mapping

File-level rationale mapping consumes only already persisted Workbench inputs:

- `review_workbench["rationale"]["task"]`
- `review_workbench["rationale"]["run"]`
- `review_workbench["rationale"]["worker_result"]`
- `review_workbench["rationale"]["prompt_snapshots"]`
- worker `result.md`
- worker prompt snapshots: `role_prompt.md`, `task.md`, `context.md`, and
  `constraints.md`

The mapper performs deterministic exact-path matching against each changed
file's `path`. If worker `result.md` mentions the path, the file receives a
`worker_result` ref. If a prompt snapshot mentions the path, it receives a
`prompt_snapshot` ref with the snapshot `name`. Task and run payloads are also
eligible when their serialized artifact content contains the exact path.

Ref excerpts are normalized, bounded, and deterministic. They are source
snippets, not generated rationale. The mapper does not call LLMs, does not run
workers, does not fetch GitHub, and does not synthesize new reasoning.

Confidence values:

- `direct`: the source artifact contains the exact changed-file path.
- `contextual`: reserved for future execution-level evidence that is clearly
  general to the execution. The MVP prefers `not_mapped` over weak contextual
  claims when no exact path match exists.

Empty states:

- `mapped`: at least one source artifact directly references the file.
- `not_mapped`: rationale artifacts exist, but no source artifact references
  the file path.
- `rationale_artifact_missing`: no task/run/worker rationale source is
  available.

Warnings:

- `rationale_mapping_missing`: changed files exist and rationale artifacts are
  available, but no file maps to any rationale ref.
- `rationale_mapping_partial`: some changed files map and others remain
  `not_mapped`.
- `rationale_artifact_missing`: no rationale artifacts exist, so file-level
  mapping cannot be evaluated.

Workbench reads remain read-only. They never fetch GitHub data, write artifacts,
append ledger events, or repair missing artifact state.

### Patch Parser Limits

The parser is deliberately small and deterministic. It parses:

- `diff --git a/... b/...`
- `---` and `+++` file headers
- hunk headers in `@@ -old,count +new,count @@` form
- context, added, deleted, and metadata lines

It does not fully interpret Git extended metadata, binary patches, rename
similarity records, or submodule summaries. Unsupported or malformed patch text
does not crash Workbench. Instead, the Workbench emits an explicit warning and
keeps parsed hunks empty for reviewer safety.

## Browser Rendering

The browser MVP renders `review_workbench.v1` into HTML and writes the same
object as `review-workbench.json`. HTML embeds the JSON in
`script#review-workbench-data`, so the page can be opened without a backend
service after export.

The page displays execution identity, source issue, PR ref, branch, commit, diff
summary, file navigation, changed-line highlights, CI summary, review
observation counts, file-level rationale evidence, memory provenance integrity,
warnings, current decision, a decision form, sandbox evidence, and ledger
timeline. The GET page and JSON routes are read-only. The only browser write
path is the explicit local-server decision POST described above.

Changed-line CSS classes are stable:

- `diff-line-add`
- `diff-line-delete`
- `diff-line-context`
- `diff-line-metadata`

## Empty States

This MVP does not fake missing artifacts.

- Missing persisted PR diff: `diff.available = false` and
  `diff.reason = diff_artifact_missing`
- Missing persisted patch while `pr-diff.json` exists:
  `diff.patch_state = missing`, warning `patch_missing`, file navigation still
  renders from `changed_files[]`, and hunks stay empty
- Malformed persisted patch:
  `diff.patch_state = parse_failed`, warning `patch_parse_failed`, and hunks
  stay empty
- Missing rationale surface: `rationale.available = false` and
  `rationale.reason = rationale_artifact_missing`
- Changed files with rationale artifacts but no exact-path refs:
  warning `rationale_mapping_missing`
- Mixed mapped/unmapped changed files:
  warning `rationale_mapping_partial`
- Pending, incomplete, or failed executions: `status`, `incomplete_reason`, and
  `warnings` explain the current state
- No sandbox/action evidence:
  `sandbox.available = false` and
  `sandbox.reason = sandbox_evidence_missing`
- Sandbox/action evidence exists:
  `sandbox.available = true`, `sandbox.schema_version = sandbox.v1`, and the
  surface lists runtime adapter evidence, cleanup warnings, command history,
  rejected commands, conflict risks, and raw sandbox ledger events. Real
  `run_execution()` paths now create an execution workspace/worktree by default;
  workspace fallback can be destroyed immediately, while successful worktree
  executions are retained for PR/diff handoff and expose
  `sandbox_retained_for_pr` until explicit cleanup writes
  `sandbox_cleanup_requested` and `sandbox_destroyed`.
- Cleanup observability:
  `sandbox.status = retained_for_pr` or `cleanup_failed` sets
  `sandbox.cleanup_required = true`. Destroy failures keep the execution outcome
  intact, persist `cleanup_failed`, surface
  `sandbox_cleanup_recovery_required`, and remain retryable by calling explicit
  cleanup again.

## Remaining #216 Gaps

This PR is a usable MVP slice, not full #216 completion. Remaining gaps:

- Deeper #218 integration remains outside the Review Workbench MVP:
  machine-readable Codex/Claude sandbox mode / approval policy introspection
  beyond launch-argument evidence, real path claim inference, optional Docker/VM
  isolation for future non-Codex/Claude runtimes, cleanup actions outside the
  read-only Workbench, and a GitHub/CI typed broker only if needed for host-side
  operations.
- Add deeper React dashboard polish around warnings, review decisions, and
  artifact drill-downs.
- Run the final #216 acceptance audit before closing the issue.

Use `Refs #216`, not `Closes #216`.
