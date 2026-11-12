# Mixed write and initial read-only result feedback

Outcome: a mixed batch of write work and initial Research/Planner work can return
known results to Manager when every task's original feedback permissions are
present. The existing observer, Context and decision queue are reused.

The previous routing predicates accepted all-write or all-initial-read-only
batches separately. A mixed batch stayed in review even when both sets of
permissions were explicitly granted. The write-review predicate now partitions
members by their existing execution policies. It requires a nonempty write
subset, all members to have recognized policies, every write member to be review,
and the original undelivered-feedback grant. The read-only subset must also pass
the original initial-read-only predicate: initial seed membership, review state,
matching Research/Planner authorization, initial-read-only feedback permission,
and no write scope. No member inherits another member's permission.

Ordinary LocalTaskStore.create_task omits an empty write_domains field on a
read-only task; create_task_once can retain []. The routing predicate accepts
these two existing representations only. Explicit None, nonempty or malformed
write scope remains rejected. The task records, project_task_intent and grant
digests are not rewritten or normalized; missing intent fields retain their
original representation.

This is only eligibility to inspect results. The original observer still checks
all invocations, known terminal status, captured output and read-only changed
files. Unknown processes, missing ownership and recovery-in-progress boundaries
are not bypassed. Reading a report neither accepts a delivery nor restarts work.
No new permission, UI switch, state machine or dispatcher is added.

## Evidence boundary

Tests retain real local task/grant/session/board state and use explicit terminal,
recovery and model fixtures. They cover permitted mixed Research/Planner, missing
initial or role authorization, unknown outcomes and empty-scope representation.
Independent review additionally exercised the actual observer function and the
existing publication recovery path with terminal/capture/storage seams supplied.
No real Provider/Pi, Worker or Git-acceptance claim is made.

Base: saved question-ready candidate, archive SHA256
`3f6cd928386b8f67744602ba01c6811450c04adcff8917d572bd4a3ad784bee4`.
No remote publication, deployment, install or security change. #419 remains the
real product acceptance record.

Full backend: 32 failed, 477 passed, 20 skipped, 5 errors in 77.16s (0:01:17). All 37 failure/error IDs match the prior full baseline. The increase includes the previous slice’s wait parameter added after its full collection. Final focused new/existing feedback: 12 passed. Independent review found no blocker. Ruff passed; frontend unchanged.
