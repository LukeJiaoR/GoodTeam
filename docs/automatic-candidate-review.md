# Automatic exact-candidate review

The trusted application controller connects existing candidate checks to the
registered independent Reviewer and consumes its retained report. It can return
`reviewed` without writing a human attestation or waiting for a mandatory final
human acceptance step. It retains the original execution, task input and fixed
review policy across bounded correction rounds.

This is a review-policy judgment, not independent proof that every task
requirement is correct. It does not add runtime permission, publish code, merge,
deploy, or write verified experience into memory. Production external correction
executors remain blocked by the existing [correction admission boundary](correction-worker-attempts.md).
An admitted Reviewer is not an admitted coding Worker.

## Trusted application entry points

First run the explicitly selected local candidate check described in
[candidate check receipts](candidate-check-receipts.md). Its original command and
criterion mapping are the only command inputs reused during correction. Issue
text, a review finding and mutable project configuration cannot supply commands.

Prepare the coordinator through `ExecutionAPI`:

```python
from pathlib import Path
from goodteam_os.execution.api import ExecutionAPI

api = ExecutionAPI(Path("/path/to/registered/project"))
prepared = api.start_automatic_review(
    "EXECUTION_ID",
    base_sha="FULL_EXPLICIT_BASE_COMMIT_ID",
    receipt_id="EXACT_VALIDATION_RECEIPT_ID",
    head_sha="FULL_CHECKED_HEAD_COMMIT_ID",
    reviewer_adapter="codex",
    reviewer_model="ALREADY_ADMITTED_MODEL",
    max_rounds=3,
    wall_seconds=900,
    fix_seconds=120,
)
result = api.advance_automatic_review("EXECUTION_ID")
```

`start_automatic_review` captures the exact caller-selected base, check receipt,
head, original task and review policy. It prepares input but does not launch a
Reviewer. The example adapter/model are caller choices, not a runtime grant; the
existing authority must admit their execution. `advance_automatic_review` is an
explicit synchronous trusted-application call that drives the already-authorized
producer/consumer sequence until accepted, unresolved, stopped or blocked. No new
HTTP command executor or browser dispatch control is provided. Reading the
Workbench or exporting it does not dispatch any work.

Defaults are three correction rounds, a 900-second coordinator wall budget and
120 seconds per correction attempt. Limits are five rounds, 3,600 coordinator
seconds and 600 seconds per correction attempt. A correction attempt must fit
its remaining loop budget. `acknowledge_external_cost=True` on `advance` is a
separate acknowledgement used by the existing correction Worker boundary; it
cannot make an unsupported external executor available and is not a dollar cap.

`api.stop_automatic_review("EXECUTION_ID")` records a stop for subsequent
coordinator admission/consumption. It does not promise immediate cancellation of
an already running Reviewer. The coordinator wall budget gates new admissions
and consumption of late results. The Reviewer process keeps its separately
admitted runtime deadline, so it can finish after the coordinator deadline; that
late result cannot become accepted or trigger correction. This is not a global
hard process wall-time cap.

## Producer and deterministic policy

The controller creates a separate registered `role:code_review` job, locked to
`goodteam.code-review-protocol.v1`, with `execution_policy=read_only`,
`session_policy=new` and no reused builder session. Its immutable context carries
the complete original task snapshot, exact validation plan and receipt, original
constraints and frozen policy. The Git review target binds the supplied base and
current head. A later candidate does not silently rebase the review onto whatever
`main` now points to.

The Reviewer supplies advisory findings, criterion checks and source evidence.
It cannot approve the candidate, modify sources or advance the gate itself. The
controller consumes the existing protected `ReviewResultStore` result and applies
`candidate_review_policy.v1`:

- An unknown host check is `inconclusive`.
- A failed host check or any critical/high/medium/low finding produces
  `request_changes`, preserving actionable feedback.
- Approval requires a passed host check, no blocking findings and exactly one
  checked entry for every original criterion ID, each with source evidence.
  Missing, duplicate, unchecked or otherwise unresolved coverage is
  `inconclusive`. Repeated criterion text keeps separate criterion IDs.

A report's `checked` label and evidence references are a review judgment, not
independent verification of semantic truth. The command-to-criterion mapping also
remains operator supplied; success is not a proof that every criterion was tested.
The detailed criterion coverage therefore retains its separate unknown semantic
acceptance even when the candidate review policy is satisfied.

On `request_changes`, the same coordinator may start the existing bounded
correction loop, prepare a distinct retained worktree/Worker, and reuse the
original command and criterion mapping after its committed result. It invokes
only an already admitted correction runtime. When production admission is
blocked, it returns the concrete blocker instead of changing adapters, reusing
the original Worker or relaxing permission. Each subsequent candidate needs a
fresh check receipt and its own registered Reviewer report.

## Receipts and visible result

A consumed result produces `candidate_machine_review.v1` with producer
`automatic_candidate_review_controller`. It binds:

- Original `snapshot` reference and current `candidate`
- Exact `validation_receipt_id`, frozen `base_sha` and `policy`
- Controller decision (`approve`, `request_changes`, `inconclusive`) and reason
- Registered Reviewer provenance: job, task lock, captured input/context
  references, exact Git target, methods, runtime observation and report digest

`ExecutionAPI.get_evidence()` and the existing Workbench expose this through
`candidate_validation.review`, with `review_status=machine_recorded`. The
protected human attestation file remains separate as `human_review`. When both
exist, the machine receipt is the effective policy decision and the optional
human record remains visible independently. No reviewer label is fabricated to
make the automatic path look human-approved.

A current passed check plus machine approval yields
`acceptance_status=accepted_by_review_policy`. The correction loop terminates as
`reviewed`, reason `candidate_review_policy_satisfied`. Optional human approval
on a passed check yields `human_recorded`; it cannot override failed/unknown
checks or a machine decision. `semantic_verification` remains
`judgment_not_independently_verified`, and the broader verification field remains
`unverified`. These labels describe evidence limits, not a required new manual
acceptance gate.

The current-result projection joins the original snapshot, exact receipt,
candidate and machine target base/head. Stale sources or mixed observations
cannot retain current acceptance; a changed check receipt at the same head is
also a different result. The page/text show check and review receipts, machine
producer, base, runtime provenance and any optional human record separately.
They do not promote an old legacy ledger `approve` or `completed` status into
current delivery evidence. Static HTML exports stay read-only.

## Recovery and verification limits

The coordinator reuses the stable per-execution lock and the existing Reviewer
request/attempt/result stores. Registered request comparison, source identity,
protected task lock and immutable report checks prevent a different task,
receipt, head, base, job or report from being attached to the candidate. Review
results are consumed only after current input/source checks and budget checks.
An existing unknown attempt is not treated as never started. Repeated exact
operations reuse the retained evidence; missing or conflicting proof blocks
progress rather than resetting history or dispatching a duplicate process.

Original correction round, no-progress and stop checks still apply. No source
probe or local evidence store is an OS sandbox, trusted external clock or defense
against unrestricted same-UID mutation/complete rollback. Individual durable
publications are not one cross-store transaction. Evidence loss, unsupported
runtime or unknown transport outcomes require inspection.

Tests exercise actual temporary Git commits/worktrees, explicit local checks,
registered read-only/new-session Reviewer requests and the existing result store.
A test-only offline subprocess authority supplies real protocol reports, and a
test-only correction capability performs an offline fix. The path then rechecks
and consumes a second independently registered report, without calling the
human-attestation API. Projection tests verify reopen, receipt mismatch,
failed/unknown checks, optional human provenance and static exports. This is
local integration evidence, not a live provider run or proof of production
correction admission. Browser launch and live-provider validation are separate
checks and must be reported separately when performed.
