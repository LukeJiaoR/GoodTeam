# Supervised correction rounds

The existing issue-execution Review Workbench now connects exact human feedback
to a bounded sequence of new candidates and actual local checks. This is a
supervised review → fix → recheck → review path. It does not yet dispatch a new
autonomous coding Worker. A person uses the existing coding tools to make the
fix and commit it; this loop preserves the original task, feedback and evidence
while guarding the subsequent check rounds.

## User path

1. Run the explicit candidate check described in `candidate-check-receipts.md`.
2. In the existing Review Workbench, record `request_changes` against the shown
   exact candidate and check receipt. The form now binds those identities instead
   of creating only an unbound legacy decision. The reviewer label remains an
   unauthenticated human attestation; this is not task acceptance.
3. Choose **Start supervised correction (up to 3 rounds / 15 minutes)**. The page
   shows the original goal and constraints, feedback, candidate, consumed rounds,
   stop reason and next action. This action starts bookkeeping, not a command or
   a model invocation.
4. Fix the code with the existing tools and commit a new candidate. Explicitly
   invoke `execution validate-candidate` with a fresh operation ID, its new full
   head, the same argv and the same criterion positions. The old Worker job is
   not replayed. Commands are never extracted from issue or reviewer text.
5. Review the new candidate and its fresh receipt. Another `request_changes`
   permits the next bounded round. `reject`, exhausted budgets or no progress
   require human intervention. An `approve` leads to **awaiting human
   acceptance**, with semantic acceptance still unknown.

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
- No automatic provider invocations are authorized or launched. External tool
  costs are unknown, not zero-cost proof, and this loop cannot enforce a dollar
  cap on arbitrary code the Operator separately invokes. A paid/new Worker
  attempt needs its own existing authorization/runtime boundary and human handoff.

## Evidence, UI and recovery

The evidence API exposes `correction_loop`, and the same existing Workbench HTML
and text show its state and next action. Bound feedback forms reload fresh state
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
whose prior transport may have run. That requires an explicit future-attempt
contract rather than a status change here.

## Verification

Synthetic registered projects exercise actual Git edits/commits and actual local
Python checks through request-changes, correction, fresh review and human handoff.
Tests also cover unchanged candidates, repeated failures, round/time limits,
concurrent contenders, prepared/call interruption, proof loss, stop and stale
sources. Actual dashboard handler requests and generated-page JavaScript DOM
checks verify bound requests, duplicate suppression and uncertain-response text.
These are not evidence of a live model, automatic coding success or verified
semantic acceptance. No successful process, human label or final workflow status
authorizes approval, merge, deployment or verified memory write-back.
