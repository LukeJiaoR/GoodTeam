# Compose existing running-project intake with Role/Loop work

Base: full loop-visibility candidate, archive SHA256
`0fd1da8024050048b26ad97dce3c930f5431760249f2919744907746080dcc10`.

The latest combined tree retained the old active-Goal intake check. This was
already fixed by pushed commit `1765ce7ddd14a3f9250118abd4f9618397cf283e`.
GitHub comparison against main `602ad62674d3c8d66e0235db56eabdf62f0b8f0b`
confirmed one commit/two files. Both exact upstream files are now composed;
this repairs an integration omission, not new feature development.

Manager may save later user work in the original task ledger/unconfirmed queue
while a Goal runs. Intake does not confirm proposals, expand grants or start
Workers. Execution/resource admission and explicit confirmation remain intact.

Original intake and Role/Loop representative tests: two passed. Independent
representative probe: one passed using real GoalStore/task ledger/session and
original batch claim/runtime tracks. Active A rejects B execution. After A is
stopped through its original stop service, unconfirmed B still rejects; explicit
confirmation reaches execution boundary with the original task/proposal identity.
Stopping A is not accepted delivery. The probe stops before actual Worker launch;
no live Manager classification, Pi, Provider or complete Loop acceptance claimed.

No new permissions, budgets, installation, push, PR, merge or deployment.
Explicit amendment of current grant scope is not implemented; a new chat task
cannot silently enlarge an existing grant. Whole-project acceptance remains #419.

Full backend: baseline 511 passed / 32 failed / 5 errors / 21 skipped;
candidate 512 passed / 32 failed / 5 errors / 21 skipped. The same 37
failing/error IDs remain. Targeted Ruff passed. This is not a green full suite.
