# Human-confirmed later work in the existing project grant

The original project action endpoint and continuation panel now accept an
explicit additive confirmation for existing todo task cards. Manager discussion
and assignment still only prepare original unconfirmed tasks. There is no
Manager action that grants the confirmation, no new queue and no new scheduler.

The UI lists the chosen tasks, role/policy, paths, description and acceptance
summary. Selection or displayed backlog revision changes invalidate confirmation.
One submission identity is retained for retries. The backend binds the current
grant, displayed backlog version and exact task intents, and records its receipt
in that same grant and original timeline under the original operation locks.

The original seed/authorization digest, current pending batch, consumed feedback,
Stop, runtime authority and all budget counters/limits remain unchanged. A fixed
scope grant retains original seed IDs so replaying its original start request
continues to match. Added work uses the existing planner and batch adapter at the
next eligible scheduling point; confirmation itself does not dispatch anything.

The action rejects stopped/replaced grants, exhausted allowances, changed
runtime authority or task evidence, internal/already-started/already-authorized
tasks, recovery-only grants and unresolved reserved Manager decisions. Unknown
Manager decisions must first be reconciled; they are not silently discarded.
Successful confirmation replay after Stop is read-only. Existing execution
admission still prevents duplicate/conflicting dispatch.

Explicit additions do not increase discovery paths, search permissions, model
allowances or automatic feedback capabilities. Added read-only tasks can use the
original initial-read-only feedback route only with a valid additive receipt and
all original role and feedback permissions. Merely adding a task never grants
those permissions. The original receipt digest and captured task intent provide
attribution without changing old seed identity or invalidating old results.

Tests exercise original task/session/queue state and Manager tick with offline
Manager and batch fixtures. There is no real Provider/Pi invocation, installation,
paid search, push, merge, deployment or complete project acceptance. #419 remains
the real acceptance record. Pending Manager admission and true Runtime behavior
are not proven by UI or fixture success.

Verification: focused backend 3 passed; independent review 8 passed (including
that new representative test), related UI 10 passed. Targeted Ruff/ESLint and
production build passed, with the existing bundle-size warning. Full backend:
513 passed / 32 failed / 5 errors / 21 skipped, exactly the same 37 failing/error
IDs as baseline (512 passed). Full UI: 775 passed / 50 failed, exactly the same
50 failing IDs as baseline (774 passed). Neither full suite is green.

Independent review found the initial read-only feedback provenance omission;
this was fixed before final review. Final review found no remaining blocker.
The source retains one primary new backend regression and one UI regression;
independent negative probes are archived as validation evidence, not duplicated
into the lean routine test suite.

## Corrected live-execution lock integration

The earlier candidate mistakenly acquired workflow_guard, which the original
batch executor holds for the entire Worker run. A pending-status fixture missed
that real execution would reject additions as busy. This candidate removes only
that execution-lock acquisition: the original grant guard and short atomic
session mutation still serialize grant changes with continuation and Stop.
It does not change batch execution locking, admission or task dispatch.
The primary representative test now holds the real workflow_guard while adding
scope, and passes. Combined focused/negative checks: eight passed. The prior
full-suite comparison above predates this two-line lock correction; it is not
claimed as a new full run. No live Provider/Pi execution was used.
