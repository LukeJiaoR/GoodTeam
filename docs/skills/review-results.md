# Controlled Reviewer results (#445 / #446)

This continuation connects the existing Skill Worker preflight to a parent-owned
result channel, durable report store, task-scoped read API, and current-task UI.
It does **not** close #445/#446 or claim live custom-Skill acceptance.

## What executes and what remains closed

`WorkerJobStore.run_job` → original protected preflight → `continue_review_worker`
→ `SkillContextProvider` → complete required input and fixed method resources
→ adapter-owned envelope → parent-owned bounded stdin/stdout process channel
→ validated immutable outcome → task execution detail and report.

The Worker now has this continuation rather than an unreferenced parser. Tests
exercise a real child process through that entry point, with an explicitly
labelled constant contract fixture. Production `selected_review_runtime` is
still closed. There is no job flag, environment switch, API parameter, package
field, or configurable fake adapter that opens it.

**Still missing:** the admitted pinned Codex 0.155.1/Linux ARM64 implementation,
verified outer isolation including escaped descendants, native method discovery,
provider-only egress, model/call/cost limits, and end-to-end live Worker evidence.
The existing nine offline helper checks do not establish any of these. No live
provider call, credential discovery/reuse, package script, deployment, or approval
is performed by this continuation. Do not merge this Draft as completed S5/S6.

## Exact Git-change source input and retry

A Reviewer can now select `review_target = {kind: "git_change", base_sha,
head_sha}` with two full commit IDs in its registered Worker root. The parent
captures all changed files from those exact commits, both base/head sides,
**before** registering/publishing the immutable Worker input. It never substitutes
the current worktree, a branch tip, a Builder summary or a model-provided path.
The ordinary single-artifact target remains supported without migrating historical
requests. Absent `review_target` is omitted, preserving old request hashes.

The existing direct dispatcher exposes `--review-base` / `--review-head` for one
`code_review` track, with no shell verification command. It requires the existing
registered project/task and locked Reviewer methods; it cannot enable the closed
Runtime. CLI return/status remains advisory, not automatic workflow success.
Automatic selection of a PR candidate across all other producers is not claimed.

Git objects are read locally with a fresh environment and bounded process/output
limits. Raw `diff-tree -z --no-renames --ignore-submodules=none` and `cat-file blob`
do not run external diff/textconv/filter programs; hooks/fsmonitor, replacement
objects, lazy fetch and network protocols are disabled. Binary, invalid UTF-8,
LFS pointers, symlinks, submodules and administrative paths reject the entire
capture, rather than silently disappearing from a successful review. Limits:
128 changed files, 256 KiB per side/file, 768 KiB complete serialized bundle, 30
seconds; complete model-input budget is checked separately without truncation.
Renames are deletion+addition; executable-mode changes retain both modes.

The protected bundle records commit IDs, repository-relative names, modes, blob
IDs, SHA-256 byte digests and exact text. Report evidence names `base/<path>` or
`head/<path>` explicitly; the same filename from the other side cannot satisfy a
quote. `SKILL.md` in reviewed source remains **source data**, not a selected method.
Runtime source files are under `sources/`, separate from locked `methods/`.
The scope is **all changed files on both sides**, not every unchanged dependency,
full repository history or a guarantee the Reviewer checked every file.

`POST /api/projects/{project}/runs/{run}/skill-reviews/{job}/retry` accepts only
the displayed `snapshot_digest` and `context_digest`. The task/workflow guard
prevents concurrent task transitions. The server selects the original request,
source bundle, TaskSkillLock, methods, model, readonly policy and a new reviewer
session. It uses the existing deterministic next-attempt identity. No provider,
model, source path, package or replacement target can be supplied by this POST.

A retry requires a reconciled durable terminal failure. Failed Worker status
alone is insufficient: running/stopping, unconfirmed process termination, partial
preparation and pending outcome recovery are rejected. Successful reports are not
retryable here. Historical tasks remain read-only. Duplicate submissions reuse
the same next job and existing outcome, never produce another paid attempt.
After Role/worktree changes, even loss of the original Git checkout, retry uses
saved original source bytes and methods. Changed/revoked packages still block it.

The page shows the exact pair and file versions on expansion, and offers an
explicit **Retry with locked inputs** action only for eligible failures. Creating
a new attempt is not displayed as a successful report. Refresh and task-switch
protection apply to the new request too. Aborting an HTTP view does **not** mean
the execution is cancelled; public cancellation and admitted outer-process
termination remain separate outstanding work.

Git command semantics: https://git-scm.com/docs/git-diff-tree and
https://git-scm.com/docs/git-cat-file. The fixed-source tests verify the actual
local Git behavior; these are not native Codex discovery/isolation proofs.

## Result and authority contract

The selected CLI emits typed JSONL through stdout captured directly by the
parent. The adapter must finish its outer-envelope cleanup before the parent
accepts a final turn. Exit zero or a string such as “done” is insufficient.
The report schema `goodteam.code-review-report.v1` contains only `summary`,
`findings`, `evidence`, and `checks`, plus its schema marker. Duplicate keys,
unknown fields, non-finite values, oversized channels, unknown evidence IDs,
missing/changed target versions and quotes that do not match captured source
lines are rejected. No JSON repair or Markdown-fence extraction is attempted.

A report with zero findings is valid when it contains source-backed inspected
checks and describes its limitations. `not_run` and `inconclusive` checks are
explicit. Matching source text verifies the reference, **not the interpretation**.
Checks are Reviewer-reported; they are not independent test-execution receipts.

Task, job, content context, runtime session, target version and method versions
come from protected parent records, never the model or Worker-writable status.
No successful outcome is written into the legacy `result.md`, memory, approval,
merge, deployment or gate paths. The Worker status is `review_received`, not
legacy `completed`. Every public report is advisory and declares that its
judgment has not been independently verified.

## Durability and recovery

Operator-owned records use anchored/no-follow paths, exclusive locks, immutable
attempt/outcome objects and fsync before status publication. Identical result
reception is idempotent; different bytes conflict. A durable outcome with a
missing/stale status projection is displayed as recovery-required with no report
button. An explicit Worker replay repairs that projection without another child
or model invocation. An interrupted running attempt without a durable outcome
cannot silently restart or fall back. Unknown termination remains stopping;
it cannot be converted to success. Current revocation blocks loading, reception
and ordinary report visibility. Historical revoked-content audit is separate.

A process-group kill is not proof that an escaped descendant or an external
container stopped. That proof remains the admitted outer adapter's obligation.
The process transport itself does not confer isolation or credential permission.

## Read API and UI

- `GET /api/projects/{project}/runs/{run}/skill-reviews`
- `GET /api/projects/{project}/runs/{run}/skill-reviews/{job}`
- `GET /api/projects/{project}/runs/{run}/skill-reviews/{job}/report`

These resolve current/archived task identity and original protected registry
records. They never list reports by a model-declared ID or global latest result.
No public report-submit endpoint exists. Missing task/job returns 404; invalid or
unavailable report returns 409. No private method bodies, prompts, physical
source paths, stdout transcript or raw stderr are returned.

The current-task execution pane loads details on demand and report evidence on
explicit expansion. Changing tasks/unmounting aborts requests and discards late
responses. Refresh clears prior reports before reading new state; failure cannot
leave an old green report visible. Source quotes render as text, not HTML.
Materialization, complete-input delivery and catalog delivery are parent-observed;
resource reads and script execution stay unobserved unless separately witnessed.
Neither broker reads nor fixture output become model-use evidence.

## Verification boundary

Focused Python tests cover report validation, content injection, on-demand/task-
required methods, immutable binding, actual child capture, revocation, duplicate
reception, recovery and task isolation. Frontend tests cover lazy evidence,
rejected scope/version responses, refresh failures and task-switch races.
The Playwright scenario imports a new Skill through the real UI, starts a locked
task, invokes a labelled constant subprocess through the actual Worker, opens
source-backed zero-findings reports, changes Role methods, replays the old job,
reloads, and exercises invalid reports, interrupted projection and revocation.
It never seeds report JSON or mocks the HTTP report API.

The continuation also exercises exact Git-source creation, the real HTTP retry
button after Role/worktree changes, a new labelled child context with unchanged
source/method versions, duplicate requests, refresh and source-side mismatches.
The production HTTP process remains closed; only the explicit owned test driver
substitutes a constant process after the real UI has created the retry job.

That is model-free integration evidence, **not** the requested live Reviewer
acceptance. Public cancellation UX, admitted Codex integration, actual optional
resource-read observations and live model conformance remain open. Keep exact commands, observed outcomes and final-head CI in PR #467.

## Explicit current-task cancellation

`POST /api/projects/{project}/runs/{run}/skill-reviews/{job}/cancel` takes only
`{snapshot_digest, context_digest}` from the displayed attempt. The task registry,
original Worker root and protected attempt select the process identity; the API
accepts no PID, command, report, changed model or new methods. Both HTTP servers
admit the exact route and retain their same-origin/JSON checks. Historical tasks
remain read-only. Cancellation does not take the long-running workflow guard;
otherwise a retry holding that guard could block its own stop request. The API
rechecks the current task/root under the per-attempt result lock before writing.

An immutable `cancel-request.json` is fsynced before projecting `stopping` and
returning **HTTP 202, `termination_confirmed=false`**. That acknowledges a stop
request, not termination. A failed mutable projection cannot erase the receipt;
identical replay repairs the projection without relaunching or signalling an
unrelated process. Content revocation does not remove metadata-only stop access.
The UI shows a pending stop, never an optimistic cancelled/successful result.
Aborting a view's HTTP request on navigation does not cancel an execution.

The existing execution owner checks cancellation before launch and throughout
process polling, including after all stdio pipes close. Only after transport
reaping and normal exit of the internal adapter's execution context does it save
`cancelled`. A replay after a reconciled terminal outcome returns **HTTP 200,
`termination_confirmed=true`**. Retry still requires the protected failure outcome
and immutable original inputs; a stop request alone cannot permit retry. Failure
to signal/probe/reap, or an error from outer-envelope cleanup, remains `stopping`
with no report or retry. No PID-based restart cleanup is attempted: an abandoned
owner needs independently proven envelope termination, not a reused host PID.

Cancellation and report reception use the same per-attempt lock. A received
immutable report wins over a later cancellation, which is rejected. A durable
stop receipt wins over a late report, which cannot publish success. Neither
operation advances a gate, changes the original task/Role configuration, writes
memory, approves/merges a PR, or deploys.

The cancellation tests use real HTTP servers and real bounded **constant child
processes**, including a held workflow guard, closed stdio, signal/probe failures,
cleanup failures, concurrent replay, receipt-save interruption, revocation, task
switches and both report/cancel orderings. The browser imports a user method and
uses the actual Cancel review button, then verifies the exact child has exited
and a reload shows the saved cancelled result without a report. These are not
live-model or escaped-descendant confinement evidence. `selected_review_runtime`
remains closed; the pinned Linux Codex envelope, discovery/provider-only egress,
request/spend enforcement, actual resource-read witnesses and authorized live
custom-Skill acceptance remain outstanding in #440/#445/#446.
