# Reuse the original queue while a sibling occupies a write domain

An already-authorized follow-up can be queued while another batch of the same
continuation still owns an overlapping write domain. This is a temporary
resource conflict, not a reason to block the entire continuation.

The admission gate first validates the original attribution, unchanged intent,
distinct tasks, same grant, and both batches' individual write scopes. Only the
combined collision gets a typed `ProjectBatchResourceBusy` outcome. Unknown
write domains, conflicting writers within one selected batch, duplicate tasks,
foreign grants, recovery and unknown invocation outcomes retain their original
fail-closed behavior.

The continuation atomically keeps its exact pending task/proposal/revision
identity and marks `awaiting_batch_resources`. It neither calls Manager again
nor adds another batch reservation or spends another budget unit. Retries use
the same queue entry and original admission gate. It cannot update a stopped,
replaced, changed-authority or changed-cursor continuation with a late wait.

Tests use original Manager choice, queue, task ledger, batch claim and real
background threads. C waits while A executes, then starts once after A has known
termination. Stop during waiting prevents C from starting. The execution and
terminal subprocess evidence are offline fixtures, not real Pi/Provider or
Trusted Git acceptance. No lock bypass, new scheduler, permission expansion,
installation, paid call, push, merge or deployment occurred.

Final validation: 12 focused checks passed; independent review reported 8
passes and no blocker. Ruff passed. Full backend: 522 passed / 32 failed /
5 errors / 21 skipped. The 37 failure/error identities exactly match the
520-pass sibling-feedback baseline. The full suite is not green; real runtime
and trusted Git acceptance remain explicit limitations. No frontend changed.
