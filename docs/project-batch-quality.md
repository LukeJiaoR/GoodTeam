# Checks, independent review and one correction for a project batch

An explicitly configured project batch now uses the existing
`CandidateValidationService`, `AutomaticCandidateReviewService` and
`CorrectionWorkerService`. It keeps the original Task Assistant confirmation,
LocalTaskStore cards, dispatch jobs and retained integration report. It does not
create an Issue, another Builder or a second execution ledger.

The Work page shows the configured candidate's check, independent review,
correction and current version. **Continue checks and review** advances the
same frozen input using authorities configured by the local operator. Delivery
stays unavailable until an actual check passes and the protected independent
review accepts that exact candidate under the existing review policy. A human
checkbox cannot replace either receipt. Passing quality still leaves the tasks
in `review`; the existing separately confirmed landed-version gate owns `done`.

## Configure one existing batch locally

Quality is opt-in for one successful, undelivered batch. Existing saved batches
without quality configuration retain the historical human-review/delivery path.
Enabling quality writes an independent required marker in the existing protected
execution-input store. Removing a session projection or losing the configuration
cannot revert that batch to human-only acceptance.

1. Read the existing batch and its zero-based criterion map:

   ```bash
   goodteam batch-quality status --workspace /path/to/workspace \
     --project-key DEMO --run-id task-batch-FULL_DIGEST
   ```

2. Explicitly choose the check command and its criterion mapping. For example,
   for a batch whose relevant criteria are 0 and 1:

   ```bash
   goodteam batch-quality prepare --workspace /path/to/workspace \
     --project-key DEMO --run-id task-batch-FULL_DIGEST \
     --reviewer-model YOUR_REVIEW_MODEL --fix-model YOUR_FIX_MODEL \
     --criterion 0 --criterion 1 --seconds 60 --wall-seconds 900 --fix-seconds 120 \
     -- /absolute/path/to/python -m pytest -q tests/test_requested_behavior.py
   ```

   Preparation freezes the actual batch task intents, original result references,
   candidate, check argv, criterion mapping, model selections and budgets. Every
   task needs explicit write domains and acceptance criteria. This first slice
   accepts implementation batches; read-only tasks do not acquire write authority.
   Preparation runs no check, model or correction. It returns `input_digest`,
   `execution_id` and the exact `reviewer_task` needed by the existing runtime
   policy. Criteria describe what the operator selected for the check; that
   mapping alone is not proof of semantic coverage.

3. Configure an existing task-bound local or trusted Codex Reviewer policy for
   that displayed task identity and the original project root. Then advance:

   ```bash
   goodteam batch-quality advance --workspace /path/to/workspace \
     --project-key DEMO --run-id task-batch-FULL_DIGEST \
     --expected-input-digest FULL_INPUT_DIGEST --acknowledge-external-cost \
     --trusted-codex-review-policy /private/path/reviewer-policy.json
   ```

   `--local-codex-policy` is the alternative existing local Reviewer authority.
   The check runs against the exact retained candidate. A read-only Reviewer in
   a fresh session receives the original task contract, exact base/head and
   observed check receipt. Its protected report must assess every criterion with
   source evidence. Missing coverage, failed checks or unresolved findings do
   not authorize delivery.

## One bounded correction, with its existing authority

The existing automatic review service may prepare **one** correction round.
The original tasks and successful Worker results remain unchanged. The report
retains the separate correction job, its exact input and isolated worktree.
Both original and corrected candidates remain inspectable.

Native correction remains closed by default. A Reviewer policy and a cost
acknowledgement cannot grant correction permissions. If correction is required,
status exposes `correction_input` and `correction_workspace` and explains which
existing exact-attempt policy is missing. Configure that policy using the
existing `TrustedCodexCorrectionAdmission` contract, then repeat the same
advance command with:

```bash
--trusted-codex-correction-policy /private/path/exact-correction-policy.json
```

The policy must name the displayed original project root, exact correction input,
worktree, native executable/version, requested fix model, deadline and one
invocation. The native policy protections are unchanged. The Reviewer policy
must separately allow the initial review and the fresh review after correction.
No command or policy file is minted from a model answer or an HTTP request.

The host seals the stopped correction's result, verifies its ancestry and actual
changed paths against the original authorized batch scope, repeats the frozen
check and obtains a new-head independent review. Unknown outcomes, no progress,
budget exhaustion or scope violations retain evidence and stop. Repeating an
advance does not re-run a completed check, Reviewer or correction transport.

The host imports the verified original candidate's objects under a dedicated
`refs/goodteam/batch-quality/` reference so the existing Reviewer and correction
worktree services can resolve exact commits. This does not move the source
checkout's HEAD, index or work files. The original dispatch report is immutable;
the final effective candidate is referenced by the quality receipt.

## Work page and service startup

The same already-configured path is available from the Work page when the server
starts with the existing Reviewer policy, and, if needed, the prepared correction
policy:

```bash
goodteam serve --workspace /path/to/workspace \
  --trusted-codex-review-policy /private/path/reviewer-policy.json \
  --trusted-codex-correction-policy /private/path/exact-correction-policy.json
```

The action `advance_project_batch_quality` accepts only `run_id`,
`expected_input_digest`, `confirmed: true` and `billing_acknowledged: true`.
It cannot select argv, a source path, a model or a runtime authority. Missing or
expired authority produces a retained, actionable reason. Reading the existing
batch report includes the quality receipts without invoking a Worker or changing
the correction clock. After acceptance, the delivery form uses the final reviewed
candidate, including a correction when present, rather than the original fan-in.

This configuration applies to **this batch only**. Project continuation does not
inherit command permissions for a later batch. Each later quality input needs its
own explicit configuration and runtime admission. The existing continuation
service still starts the next authorized batch after delivery; no scheduler or
unbounded correction loop is added. Automatic GitHub CI remains paused.

## Local evidence

`tests/test_project_iteration_quality.py` keeps one representative two-task
scenario with real subprocess Workers, Git isolation/fan-in, a failing check,
protected independent review, a separately admitted correction, host sealing,
the same passing check, new-head review and receipt-bound delivery/replay. It
also verifies that GET/evidence reads do not change protected files or launch
processes and that HTTP cannot supply a check command.

The test compiles tiny offline transports with `cc` so the production rejection
of script wrappers remains intact. Manager decisions and native CLI responses
are explicitly labeled protocol fixtures. No paid provider, real-model quality,
native sandbox confinement or whole-project autonomy is established by this
test; real product acceptance remains in #419.
