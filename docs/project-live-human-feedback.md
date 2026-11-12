# Human answers reach Manager while sibling Workers continue

The original discovery question board already retained a Human answer, but the
continuation's batch-observation cursor could select another running batch and
prevent that answer from reaching the original discovery judgment. This made
Human interaction wait for unrelated Worker completion.

When an answered original discovery question has an unconsumed receipt, the
continuation now locally yields its running-batch cursor to the existing
Manager discovery path. It does not remove, cancel or finish the original batch.
The existing source, intent, permission and billing checks still govern the
judgment and any follow-up. Approved tasks use the existing ledger and queue;
write conflicts use the preceding exact-reservation wait behavior.

This does not take over an unclaimed queued task, a reserved planning/discovery
call, or an uncertain running-observation judgment. Questions originating from
running observation do not acquire a new discovery source through this route.
No role methods or execution capabilities are broadened.

Regression coverage uses the original feedback reader, question/answer schema,
Manager decision validation and task-publication logic. While A remains running,
B's answer can lead either to no follow-up or to an already-authorized Researcher
C. Model output, answer submission and queue/Worker boundary include offline
fixtures; this is not actual Pi/Provider/Trusted Git acceptance. The independent
probe reproduces the old stuck state (one Manager call) and the fixed consumption
(two calls for the distinct question and answer).

No installation, paid call, push, merge or deployment occurred.

Final verification: 13 related checks passed; independent review passed all
8 scenarios (two actual routing paths and six priority/source guards), with no
blocker. Ruff passed. Full backend: 523 passed / 32 failed / 5 errors / 21 skipped.
All 37 failure/error identities match the preceding 522-pass conflict-wait
baseline; the complete suite is not green. No frontend files changed.
