# Operator plan-compliance gate

The GitHub-issue Operator keeps its execution, PR, bounded fix rounds and final
approval flow. Candidate identity and green CI do not mean the task is satisfied.
This slice connects its gate to the existing automatic candidate-review service;
it does not create another plan or Reviewer protocol.

The normal Operator entry can now prepare the candidate check and frozen review
configuration after the original Builder succeeds. Trusted application startup
must provide `candidate_reviews={operator_item_id: OperatorCandidateReviewConfig(...)}`
to `OperatorService` or `LocalOperatorActions`, together with any already admitted
`codex_review_admission`. The immutable config binds the full hydrated task-contract
SHA-256 digest, explicit exact base, check argv, criterion indices, check timeout
and Reviewer adapter/model. Selection grants no runtime permission. Neither
issue text, Worker output, queue metadata nor mutable project settings can create
this startup dependency. Default CLI/service construction without it stops at
preflight with `operator_candidate_configuration_required`. The normal background
startup CLI can now supply it explicitly; Python embedding is not required.

## Explicit CLI setup

Use an already authorized, task-bound Reviewer policy for the exact original
execution. These commands do not create that policy, grant new native access or
make an unavailable route supported. The selected model must match the policy.

Prepare the queue item before permitting new starts. `prepare-review` captures the
original input through existing intake and persists its queued Builder, without
dispatching it. It prints the exact snapshot-bound Reviewer task identity and
Worker root for the existing policy admission process; repeated calls retain the
original snapshot even if the source changes. It does not grant runtime authority.
A new task cannot have an exact policy until this identity is prepared. Then print a configuration
from the exact source contract. `review-config` reads that issue (or checks the
frozen original contract for an existing execution); it does not run the supplied
command, dispatch a Worker, create a review policy or change the task's state.
The command, criterion indices, original base and model are explicit arguments.
Changed issue requirements after this preview invalidate the configuration.

```sh
goodteam queue drain
goodteam task enqueue https://github.com/OWNER/REPO/issues/NUMBER --workspace /workspace
# Copy the returned item ID and prepare its stable original identity.
goodteam queue prepare-review --item-id QUEUE_ITEM_ID --workspace /workspace
# Obtain an already authorized policy for the printed reviewer_task and worker_root.
# Stop here if the existing admission process cannot authorize this exact task.
umask 077
goodteam queue review-config --item-id QUEUE_ITEM_ID --workspace /workspace \
  --review-base FULL_ORIGINAL_BASE_SHA --criterion 0 --criterion 1 \
  --reviewer-model EXACT_POLICY_MODEL --seconds 30 \
  -- /absolute/python -m pytest tests/test_feature.py \
  > /private/operator-candidate.json
goodteam restart --workspace /workspace \
  --operator-review-config /private/operator-candidate.json \
  --trusted-codex-review-policy /private/existing-task-policy.json
```

The existing `--local-codex-policy` may be selected instead, subject to its
unchanged admission restrictions. Choose exactly one policy. Review the generated
JSON before startup. Configuration and policy files must be private owner files,
not symlinks; keep the configuration outside all selected Worker project roots.
No credential belongs in command arguments or this configuration.

`restart` validates before stopping the current service, pins the configuration's
byte digest, and passes its path/hash and the existing policy path through
`supervise` to the Operator component only. Web and Telegram gain no Reviewer
authority. Child startup rechecks the digest, schema, model and private-file
boundary; editing the configuration while that startup is in flight or while a
component restarts blocks it rather than silently selecting new commands.
Configuration choices remain frozen per task when execution starts.

If the exact task had paused before any Builder operation solely because this
configuration was missing, an explicit matching startup configuration resumes its
preflight under a fresh setup operation identity, without consuming a fix attempt. Other pauses, started/unknown Builder
outcomes and changed contracts do not automatically resume. Existing workflow,
CI, review and Telegram decisions remain required; this startup command does not
approve or merge anything by itself.

## Execution and recovery

The configuration is durably frozen against the original task snapshot before
Builder dispatch. After success the Operator checks the original retained Builder
worktree and records the actual check receipt. On reaching the review phase after
PR/CI, it starts the existing automatic review service with the frozen choices.
Waiting for PR/CI does not consume the review/correction wall budget. A previously
started or partially started review retains its original clock on every replay;
an expired budget is not reset. Independent review runs at that phase. Command failure
is preserved as failed evidence, never rewritten as a successful check. The
explicit standalone entry in [automatic candidate review](automatic-candidate-review.md)
remains available.

If the queue result was not published, recovery derives the original execution ID
and proceeds only from its retained successful Builder. It never repeats intake
or an unknown Builder. A stable initial-check operation reuses its receipt without
running twice; changed config/head conflicts, and a started check without a result
is indeterminate. Recovery can finish review configuration after an interruption
between check completion and review start, including a partially recorded start.
Lost frozen configuration is not
reconstructed after Builder completion. Missing setup, unavailable admission or
unresolved evidence stays blocked/unknown, with its reason retained in the queue.
This does not complete autonomous #347 delivery or open production correction.

## Review and correction

The existing registered read-only/new-session Reviewer produces a protected
report. Its controller checks every original criterion ID separately, including
repeated criterion text, source-matched evidence, actual check receipt and
blocking findings. The Operator consumes that result rather than treating a
Worker's successful exit or an `approve` string as completion. Before merging it
revalidates the protected receipt against the current original input, candidate
and check. A valid assessment may accept the same head without a new commit.

Unmet requirements enter the existing bounded correction coordinator. Production
correction admission remains closed; the real blocker is reported without trying
a legacy Worker or expanding permissions. Existing admitted simulations still
run, recheck and stop under the same no-progress/round/time limits. If an admitted
correction genuinely reaches a checked/reviewed candidate, the Operator publishes
that validated worktree to the existing branch/PR. Human suggestions alone do
not fabricate a machine finding or grant a correction runtime.

Incomplete evidence pauses as `unknown` for evidence work, without declaring
product failure or sending an approval card. Automatic evidence gathering and
resumption beyond the existing controller remain outside this slice. Only real
scope, risk or budget decisions should be escalated as user decisions.

Fix hydration compares requirements rather than issue timestamps, labels or
comment references. Metadata-only updates can proceed while the original
contract, snapshot and source revision stay frozen. Actual requirement changes
need explicit reconciliation. This snapshot is captured external input, not a
new claim that the user approved a Planner artifact.

## Evidence limits and verification

Matching source references does not independently prove a model's interpretation
or a runtime claim. The existing policy preserves inconclusive/not-run checks
and labels acceptance as a review-policy judgment. Real runtime proof needs
actual check evidence; a source quote is not a test run. This slice neither
creates new provider access nor broadens correction, security or merge authority.

Tests exercise real WorkerJobStore registration, protected Reviewer results and
Git candidates through an offline subprocess authority, plus actual built-in
noop correction/no-progress behavior. They cover altered/missing reports,
criterion coverage, same-head replay, merge revalidation and source changes.
These are local integration checks, not a live provider or full product trial.
