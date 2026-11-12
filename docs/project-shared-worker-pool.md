# Keep independent approved work moving across batches

The previous project execution gate held workflow_guard for the entire batch,
rejected every other active batch, and treated pending_batch as the only current
batch. A dispatch could run ten Workers internally, but Manager could not use
spare capacity for independent work in another batch. This is a host-level
restriction, not a Pi requirement.

## Existing execution, smaller serialization boundaries

Original project_task_batches remains the only batch ledger. Outstanding batches
are derived from it; pending_batch is a backward-compatible observation and
reservation cursor, rotated across retained identities so a long-running or
unaccepted result cannot hide a sibling. A reserved Manager call freezes its
cursor and executable inventory until reconciled. No second queue is introduced.

Original workflow_guard protects short admission/task claims. Original per-run
reentrant dispatch_workspace_lock protects execution and settlement of that exact
run, preventing duplicate replay without serializing unrelated runs. A busy
short admission preserves its claimed reservation; it is not recorded as Worker
failure. Original task/session atomic writes and original delivery gates remain.

Within one active continuation grant, newly selected tasks may enter another
batch while earlier tasks run. Admission checks every occupied batch's original
identity and task bindings and reuses the existing declared write-domain conflict
checks. Current Workflow/recovery and foreign-grant restrictions remain. Unknown
batch execution is not treated as free capacity or permission to retry.

The Manager still receives authorized project facts, but currently reserved or
conflicting tasks are marked non-executable. The executable subset is frozen in
the original version-5 planning reservation. Restart does not silently replace
that subset or purchase a different decision under the same identity. Original
backlog/context drift checks and Stop remain effective.

## Shared capacity, not ten per batch

Ordinary isolated dispatch jobs share ten resource slots for the same project
repository, across batches/processes. Different projects are NOT a global pool
of ten. Slot occupancy stores original job/run identity, not a task state machine;
original terminal/retirement evidence is the only authority for freeing a started
job. A parent crash, failed label or lost process lock does not prove termination.
Pre-upgrade invocations with evidence of starting also count until original
terminal evidence establishes termination. Explicitly never-started preflight
failures do not occupy capacity.

The original Worker operation is called only after acquiring a slot. Waiting and
post-acquisition checks revalidate Stop, effective authority and original task
intents. A late Stop cannot launch a queued Worker. Availability is advisory;
only the shared atomic claim grants a slot. Protected reviews and non-isolated
legacy invocation paths are unchanged, not claimed as covered by this pool.

## Evidence and limits

The representative Manager test uses original planner, task ledger, queue, batch
claim, background threads and locks. A stays in execution while independent B
enters execution; conflicting C is visible but not executable, repeated ticks do
not duplicate A/B, and Stop prevents new dispatch. The execution operation is an
offline stand-in, not actual Pi/Provider or Git acceptance.

Capacity tests use two groups of eleven real threads and fork/process-exit
recovery: observed simultaneous operations stay at ten, the eleventh uses a
released slot, uncertain orphaned/timeout invocations remain occupied, and late
Stop does not invoke the Worker operation. The original Trusted Git requirement
was not bypassed; unavailable real-Git tests remain failures/skips.

This delivers dynamic admission of a new independent batch, not partial-result
acceptance inside an unfinished batch. Individual invocation resource slots can
be reused on verified termination; task dependencies still require the original
settlement and delivery contracts. Real providers and complete continuous project
acceptance remain #419. No install, paid call, push, merge or deployment occurred.

Final validation: 14 related checks passed; final independent/targeted set eight
passed, including actual short-lock contention, cursor differences and v5 restart
replay without a second Manager budget charge. Ruff passed. Exact final full
backend: 519 passed / 32 failed / 5 errors / 21 skipped. The same 37 failure/error
identities remain as the 514-pass baseline; the full suite is not green. No UI
files changed. The real-provider/Trusted Git limitation remains explicit.
