# Running observation uses the captured Pi Manager contract

This connects the existing running-observation entry to the previously captured
`manager_pi_methods.v1` Runtime. It does not remove the existing live authorization
check, enable generic read-only Pi Workers, or create a second supervisor.

## Atomic authority at the actual model-input boundary

The original observation reservation, Manager budget and invocation identity are
retained. Starting an idle Pi process is not treated as model execution. After
its exact tool witness and original method input checks, the first model RPC
write runs inside the existing session authorization mutation. That mutation
rechecks Stop/current grant, observation binding, Human input, original job and
`transport_started`. Only one original write can claim that transport start.

The short mutation contains the write, not the long response wait. Authorized
transport writes use nonblocking I/O with a deadline of at most one second
(including abort/control writes), handle short writes fully, and release the
write lock before cleanup. Thus a child that never reads its pipe cannot hold
Stop indefinitely or deadlock cleanup. A partial write or lost ledger commit
acknowledgement closes the original transport; it does not authorize a replacement
prompt. The old pipe/Popen cleanup behavior remains unchanged.

Both WorkerStore and the Pi Runtime reject direct running-observation launches
without the original prompt authorizer. The existing Manager tool allowlist,
startup/restore witness and per-input method checks still apply.

## Original completion recovery

New Manager jobs retain immutable request files, so `job.status=queued` is not
misread as proof that execution never occurred. Their separate terminal record
and original captured input, logical observation identity, effective idempotency
key, context digest and Pi session binding are checked before a completed result
can reconcile a lost response. The original settled operation and prompt identity
remain required; the legacy pipe recovery contract is unchanged. A recovered
result returns to the existing judgment/Human-board route and does not run the
job again. Changed inputs and late Stop retain refusal/stale-result semantics.

## Verification scope

The representative path is the real observation entry, original Manager producer,
Role package capture, WorkerStore, authorization mutation, RPC subprocess and
judgment publication. The executable is a strictly checked local fake Pi and its
model response is a fixture. The method witness listener is also an explicit
fixture; no installed Pi SDK or real Provider acceptance is claimed.

Independent probes cover Stop before the first prompt, send/commit failure,
response wait outside the authority lock, a child that never reads stdin, and
forced 17-byte short writes. Production tests additionally cover normal result,
lost reply recovery and Stop after process startup. All added tests disallow
unexpected live Runtime fallback. No installation, paid model call, permission
change, push, merge or deployment was performed for this slice.

## Frozen verification results

Targeted 42 passed; independent whole-path review 42 passed and final receipt-preservation follow-up 16 passed, no blocking findings. Ruff passed. Full backend: 544 passed, 32 failed, 5 errors, 21 skipped; exact 37 failure/error identifiers match the base (532 passed). These existing failures are not represented as green acceptance. This complete candidate is not published.
