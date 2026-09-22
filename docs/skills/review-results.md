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

The initial report target is one explicitly named, SHA-256-versioned UTF-8
artifact captured by the parent. This is not yet a complete exact-Git-change
source bundle. Only the captured target, exposed as `candidate`, can support
line evidence. Full custom-Skill source review therefore remains an acceptance
gap, not something a passing fixture can establish.

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

That is model-free integration evidence, **not** the requested live Reviewer
acceptance. Public cancellation UX, retry-as-new-Reviewer-attempt after a real
runtime failure, exact change-source bundling and live model conformance remain
open. Keep exact commands, observed outcomes and final-head CI in PR #467.
