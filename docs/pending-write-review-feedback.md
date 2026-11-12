# Read known write-batch results before delivery acceptance

Outcome: when every write invocation has ended and the tasks await review,
Manager can consume their known results through the original feedback channel
under the existing `allow_undelivered_feedback` authorization. It no longer has
to wait for a task to fail or for the batch to be accepted just to understand
what the completed invocations reported.

The observed gap was in continuation routing: the terminal review/blocked branch
called the existing batch observer only for a blocked task or the separately
opted-in initial read-only case. The observer already supported review batches
and retained all invocation ownership, known-terminal and captured-output checks.

A small routing predicate now admits only nonempty all-review batches whose
members all have the original workspace-write policy, and only when the existing
undelivered-feedback permission is true. Mixed and initial read-only batches keep
their existing authorization routes; this does not grant their separate exception.
No new flag or UI control is added. An eligible route is not evidence: the
original observer, current authority and execution/recovery checks still run.
Unknown invocations or recovery in progress are not released or retried.

Reports remain non-delivery evidence in the existing Context. Task and batch
review states and acceptance/quality gates are untouched. Manager may use the
already-authorized discovery/board actions, but reading a report does not make
a dependency done, approve a candidate, or authorize overlapping new work.

## Verification boundary

The regression uses real local task/grant/session/board storage and original
continuation/discovery logic. Terminal/captured-result and recovery evidence plus
Manager output are explicit fixtures. It verifies authorized all-write results
reach Manager and the original Human board without repeat calls; no permission
or mixed initial read-only source remains waiting; unknown evidence preserves
pending ownership and prevents judgment. Existing batch-observer tests continue
to validate the underlying report/attribution contracts.

No real Git acceptance, Provider/Pi model or Worker run is claimed. Nothing is
pushed, merged, deployed or installed. #419 remains the real acceptance record.
Base: saved local wait-receipt candidate, archive SHA256
`5a03e5d2edd65e86b0882bf0346144d2af342bd428eacfce8c76f29825e23b5f`.

Final full backend: 32 failed, 469 passed, 20 skipped, 5 errors in 78.82s (0:01:18). All 37 failure/error IDs match the previous full baseline. The increase includes one prior-slice focused case that was added after its full run. New/existing feedback focused tests: 6 passed. Independent review found no blocker and checked 9 additional routing boundaries. Ruff passed. Frontend unchanged.
