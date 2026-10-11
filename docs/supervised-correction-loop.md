# Supervised correction rounds

The issue-execution Review Workbench connects exact candidate feedback to a
bounded sequence of new candidates and actual local checks. Feedback can come
from an optional human record or the independent Reviewer consumed by the
[automatic candidate review controller](automatic-candidate-review.md). The
controller closes the check → Reviewer → policy decision path without requiring
a fabricated human approval or a final manual-acceptance gate.

The [correction host protocol](correction-worker-attempts.md) provides separate
attempt inputs/worktrees and simulation-only execution. External correction
executor admission remains blocked. A person can use existing coding tools to
fix and commit a candidate; the loop preserves the original task, feedback and
evidence while guarding subsequent rounds. An admitted Reviewer runtime does
not grant a production correction runtime permission.

## User path

1. Run the explicit candidate check described in `candidate-check-receipts.md`.
2. In the existing Review Workbench, record `request_changes` against the shown
   exact candidate and check receipt. The form now binds those identities instead
   of creating only an unbound legacy decision. The reviewer label remains an
   unauthenticated human attestation. This step is optional when the automatic
   controller has already produced a bound machine `request_changes` receipt.
3. Choose **Start supervised correction (up to 3 rounds / 15 minutes)**. The page
   shows the original goal and constraints, feedback, candidate, consumed rounds,
   stop reason and next action. This action starts bookkeeping, not a command or
   a model invocation.
4. Fix the code with the existing tools and commit a new candidate. Explicitly
   invoke `execution validate-candidate` with a fresh operation ID, its new full
   head, the same argv and the same criterion positions. The old Worker job is
   not replayed. Commands are never extracted from issue or reviewer text.
5. Review the new candidate and its fresh receipt, or let the explicitly started
   automatic controller obtain the independent report. Another `request_changes`
   permits the next bounded round. `reject`, exhausted budgets or no progress
   require inspection. A passed check and matching machine `approve` finish at
   **reviewed**, with `acceptance_status=accepted_by_review_policy` and reason
   `candidate_review_policy_satisfied`. Optional human approval of a passed check
   also finishes at **reviewed**, labeled `human_recorded`. Failed or unknown
   checks cannot become accepted through either kind of approval. Machine
   `inconclusive` remains unresolved and cannot start another fix by itself.

`semantic_verification=judgment_not_independently_verified` describes the limit
of the review judgment, not an unfinishable waiting state. A recorded policy
acceptance does not establish independent semantic proof, authorize delivery, or
change the legacy execution ledger's completion status.

The CLI can start the same loop with explicit limits:

```sh
goodteam execution start-correction-loop \
  --project-root /path/to/project --execution-id EXECUTION_ID \
  --head FULL_COMMIT_ID --validation-receipt VALIDATION_RECEIPT_ID \
  --max-rounds 3 --wall-seconds 900
```

Limits cannot exceed five rounds or one hour. Repeating the original start
returns the same loop; it cannot reset its budget or replace feedback. The page
and `execution stop-correction-loop --loop-id LOOP_ID ...` can stop further
rounds. Stop waits for an already admitted local check's bounded process/lock and
does not promise immediate cancellation of that running check. There is no reset
or automatic new-loop action that erases an exhausted history.

## Enforced boundaries

- The loop belongs to the same protected original snapshot and exact initial
  check/review identities. Mutable project-local files do not select its task.
- Every round uses the originally selected command and criterion mapping; a
  weaker/new command cannot masquerade as a recheck. Replanning the verification
  method requires a separate explicit human decision outside this slice.
- A new tree is required before consuming a round. Repeated tree identities stop
  as no progress. Three consecutive identical observed failure signatures
  (including the baseline when it failed) also stop. This is an exact signature
  heuristic, not proof of semantic progress; the hard round/time limits remain.
- The round is durably reserved before the validation started marker and actual
  local call. A definitely unstarted prepared operation can resume once with
  its exact plan, still respecting stop and time limits. A retained call marker
  without a result is indeterminate and cannot automatically rerun.
- The same stable per-execution lock serializes concurrent checks, so another
  candidate cannot consume a second round before the current one is reviewed.
  Validation admission failures do not replace the current evidence pointer.
- The wall budget is an admission deadline. A check's requested timeout must fit
  the remaining budget; after expiry or an observed clock rollback, no new check
  is admitted. Observed timestamps are retained under the same lock; expiry,
  backward time and other terminal stops are durably latched, including when
  first observed by an evidence read. Restarting the service or a later clock
  correction cannot revive them. This is not a trusted external clock or proof
  against rollback of all retained evidence. The bounded process observer still
  controls each call.
- Starting this bookkeeping loop does not authorize or launch provider calls.
  The separate automatic controller can dispatch through the existing registered
  read-only/new-session Reviewer authority when explicitly advanced. External
  correction executors remain blocked until separately admitted; an external
  cost acknowledgement cannot admit them. Costs are unknown, and neither loop
  enforces a dollar cap on arbitrary code the Operator separately invokes.

## Evidence, UI and recovery

The evidence API exposes `correction_loop`, and the same existing Workbench HTML
and text show its state and next action. `candidate_validation.review_status`
distinguishes `machine_recorded` from `operator_recorded`; preserved
`human_review` evidence never impersonates the machine producer. The current
result joins the original snapshot, current candidate, validation receipt and
review provenance. Separate observations that disagree cannot produce acceptance,
including two different check receipts at the same commit. Bound feedback forms reload fresh state
on success and suppress duplicate submissions. An uncertain response asks the
person to refresh and inspect the preserved outcome before retrying. Static HTML
exports remain read-only. Start, stop and human-attestation POST routes require
`application/json` and the exact HTTP scheme, hostname and port of the configured
dashboard listener. Loopback aliases do not make two different browser origins
equivalent. A matching attacker-controlled Host/Origin pair is not a configured
authority. Wildcard listeners accept local authorities for these protected routes;
remote use requires an explicit configured hostname. No proxy-forwarded origin is
implicitly trusted. Explicit non-browser JSON clients may omit Origin, but a
cross-site browser fetch marker is rejected. This compatibility path is not
authentication or proof of human approval. The routes cannot run commands or a Worker.

Missing one loop proof, missing historical receipts or an independent round
reservation that contradicts a rolled-back journal becomes `needs_human`; it
never initializes a clean new budget. Cached checks return historical receipts
without moving an active loop's current-evidence pointer backwards. Each round's
commit-once reservation precedes the journal and call marker; interruption before
journal publication needs inspection, never an extra call or budget reset. A
durable halt preceding a lost journal acknowledgement still means stopped.
The state, clock watermark, required marker, reservations and halt are atomic
files under the existing Operator root, not a multi-file database transaction.
An interruption during initial publication may require inspection rather than
automatic reconstruction. Replacement/rollback of all protected
proof, unrestricted same-UID modification, ignored dependencies and concurrent
edit-and-revert are outside this application's guarantees.

The old Work workflow's fix planner and issue execution are different identities.
This feature does not attach the nearest Work run or claim their contracts have
already been unified. It also does not reopen or redispatch a captured Worker
whose prior transport may have run. The separate correction-attempt contract requires retained return evidence, a
new bounded identity, and its own supported executor admission. It never changes
the original job status to redispatch it.

## Verification

Synthetic registered projects exercise actual Git edits/commits and actual local
Python checks through request-changes, correction and fresh bound review. The
automatic-controller tests also use the real registered Reviewer result store,
with an offline subprocess authority, to consume a findings report, run a
separately test-admitted correction, recheck and consume a second report without
calling the human-attestation API.
Tests also cover unchanged candidates, repeated failures, round/time limits,
concurrent contenders, prepared/call interruption, proof loss, stop and stale
sources. Actual dashboard handler requests and generated-page JavaScript DOM
checks verify bound requests, duplicate suppression and uncertain-response text.
These are not evidence of a live model, production correction admission, or
independently verified semantic correctness. A machine judgment may satisfy the
captured candidate review policy; neither that judgment, a successful process,
a human label nor a final workflow status authorizes merge, deployment or
verified memory write-back.
