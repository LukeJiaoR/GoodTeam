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

## Local CLI entry and resumption

The normal `execution run` entry now connects Builder completion to checks and
independent review. Provide the trusted local check and original base once:

```bash
goodteam execution run \
  --project-root /path/to/registered/project --execution-id EXECUTION_ID \
  --review-base FULL_ORIGINAL_BASE_COMMIT_ID --criterion 0 --criterion 1 \
  --trusted-codex-review-policy /private/existing-task-policy.json \
  -- /path/to/venv/bin/python -m pytest tests/test_feature.py
```

Before dispatch, this freezes `execution_candidate_delivery.v1` against the
original task snapshot: base, command, criterion mapping, check time limit, Reviewer
adapter and model. After a successful Builder return it resolves the committed
candidate from that execution's retained worktree, runs the check there, passes
the exact receipt to the existing registered Reviewer, and drives the bounded
correction controller. The Builder must leave a clean committed candidate;
delivery does not create an unreviewed commit or accept dirty output. The
original checkout remains separate from the candidate worktree. Worktree
location, Git administration backlinks, repository and exclusive branch identity
are checked before use.

Resume through the same normal entry without copying head or receipt data:

```bash
goodteam execution run \
  --project-root /path/to/registered/project --execution-id EXECUTION_ID \
  --trusted-codex-review-policy /private/existing-task-policy.json
```

The saved plan cannot change on restart. Completed Builder/check/Reviewer
operations are reused; an indeterminate check or provider outcome blocks instead
of being retried. A terminal review can be reopened without the policy file.
The JSON `run.status` describes the Builder; `candidate_delivery` describes the
actual check/review outcome and retained evidence. A successful command dispatch
or Builder return does not establish delivery acceptance. With no saved or
explicit check plan the legacy Builder-only operation remains usable, but its
delivery evidence stays unknown. No command is inferred from issue text,
verification suggestions, Worker output or project configuration.

Embedded callers use
`ExecutionAPI(root, codex_review_admission=authority).run_execution(execution_id,
candidate_review={"base_sha": base, "argv": command, "criteria": [0, 1]})`.
Subsequent local calls use `resume_candidate_review=True`. Ordinary HTTP run
requests pass neither local option and cannot run even a saved check plan;
evidence reads and Workbench exports remain read-only.

`LocalOperatorActions` uses the existing typed
`candidate_reviews={operator_item_id: OperatorCandidateReviewConfig(...)}` and
`codex_review_admission` startup composition. The managed CLI wiring is described
in [Operator plan review](operator-plan-review.md). Its normal execute action
freezes the selection before Builder dispatch and checks the returned candidate.
Publication uses that exact checked commit, and the normal queue invokes the
independent Reviewer once PR/CI is ready, so CI waiting does not consume the
Reviewer budget. A missing plan stops before Builder dispatch; neither queue
metadata nor a repository config can authorize a command. Publication re-reads
the completed check receipt and never creates a summary commit. Approval and
merge require current passed checks and a protected machine-review receipt;
green CI and an empty legacy blockers list cannot pass that gate. Source
hydration preserves the original snapshot and pauses on changed requirements
for explicit reconciliation. Corrected candidates use their validated commit ID
on the existing execution branch. Production correction admission remains closed,
and no legacy generic fix Worker is used as a fallback. CI failures or human
feedback that lack a bound correction receipt remain unresolved.

This delivery plan is a local execution/check selection, not a versioned Planner
artifact or evidence of human plan approval. Real provider, Planner-policy,
Telegram, merge and source-task-update acceptance for the entire #347 workflow
remain separate from the offline integration evidence for this slice.

Run the explicitly selected local candidate check and independent review in one
invocation. Supply the original exact base once and an existing task-bound
Reviewer policy:

```bash
goodteam execution validate-candidate \
  --project-root /path/to/registered/project \
  --execution-id EXECUTION_ID --head FULL_CHECKED_HEAD_COMMIT_ID \
  --operation-id candidate-check-1 --criterion 0 --criterion 1 \
  --review-base FULL_ORIGINAL_BASE_COMMIT_ID \
  --trusted-codex-review-policy /private/existing-task-policy.json \
  -- python -m pytest tests/test_feature.py
```

The CLI passes the returned host-check receipt and head directly into automatic
review, then advances the chain. There is no receipt-copying step or mandatory
human attestation. Reviewer adapter/model come from the already composed
Trusted/Local Codex policy. `--local-codex-policy` selects the existing local
policy route instead; these flags do not create a policy, broaden task scope,
change native configuration, or grant runtime admission. An embedded Operator
can pass the same existing typed `codex_review_admission` to `cli.main` instead
of passing a policy path. Other authority implementations retain the explicit
`ExecutionAPI.start_automatic_review` selection interface below.

The policy must already authorize the exact registered task identity
`direct_task / PROJECT_KEY / candidate-review:<original snapshot payload digest>`
and Worker root. A policy for a workflow review is not interchangeable. No policy
is inferred from the issue, Worker output, project settings or persisted PASS
markers. Existing version, task, model, revocation and runtime-budget checks are
unchanged. An unavailable or mismatched runtime returns its real blocker.

`--review-base` must be a full exact commit SHA. GoodTeam does not guess it from
current `main`, `HEAD`, sandbox defaults or an issue. The original command and
criterion mapping remain explicit local Operator inputs; issue text and review
findings cannot supply commands. Without an automatic start or composed Reviewer,
`validate-candidate` still supports the standalone check-only path described in
[candidate check receipts](candidate-check-receipts.md).

Continue an existing automatic task without re-entering its base, command,
criteria, head, receipt, model or budgets:

```bash
goodteam execution continue-automatic-review \
  --project-root /path/to/registered/project --execution-id EXECUTION_ID \
  --trusted-codex-review-policy /private/existing-task-policy.json

goodteam execution stop-automatic-review \
  --project-root /path/to/registered/project --execution-id EXECUTION_ID
```

Continuation reads the frozen inputs and retained results. It cannot change the
original model or reset the budget; repeating a completed check/review does not
invoke it again. A terminal recorded result can be reopened or continued without
recomposing an admission. Missing or unknown outcomes remain blocked, rather than
retrying a provider invocation. Production correction executors remain unadmitted;
`--acknowledge-external-cost` cannot remove that blocker.

The Workbench displays automatic mode and the actual next step even after a
correction journal exists. Its optional human record remains available, but
supervised start/prepare controls are not shown for automatic tasks. Reading or
exporting the Workbench never dispatches work. No HTTP command executor was added.

## Trusted application entry points

`ExecutionAPI(project_root, codex_review_admission=existing_authority)` carries
the same typed startup dependency into the automatic Reviewer. Its local-entry
method `validate_and_review_candidate(execution_id, review_base=exact_base,
operation_id=..., head_sha=..., argv=..., criteria=...)` combines validation,
start and advance as above.

Callers that already hold a receipt can still use the separate methods:

```python
from pathlib import Path
from goodteam_os.execution.api import ExecutionAPI

# existing_authority is the already admitted, exact-task startup dependency.
api = ExecutionAPI(Path("/path/to/registered/project"), codex_review_admission=existing_authority)
api.start_automatic_review(
    "EXECUTION_ID", base_sha="FULL_EXPLICIT_BASE_COMMIT_ID",
    receipt_id="EXACT_VALIDATION_RECEIPT_ID", head_sha="FULL_CHECKED_HEAD_COMMIT_ID",
    reviewer_adapter="codex", reviewer_model="ALREADY_ADMITTED_MODEL",
    max_rounds=3, wall_seconds=900, fix_seconds=120,
)
result = api.advance_automatic_review("EXECUTION_ID")
```

`start_automatic_review` freezes input without launching a Reviewer. `advance`
drives the already-authorized producer/consumer sequence until accepted,
unresolved, stopped or blocked. Adapter/model selection is not a runtime grant.

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
