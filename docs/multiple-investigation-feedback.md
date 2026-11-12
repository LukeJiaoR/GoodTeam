# Return multiple discovered investigations to the existing Manager loop

Manager could already create several follow-ups, including Researcher and
Planner, but the investigation feedback adapter accepted only a one-task batch.
The other review route recognized initial/explicitly-added read-only tasks, not
multiple discovered investigations. A successful Researcher/Planner batch could
therefore remain in review without returning its findings to Manager.

This change reuses the existing batch observation reader and Context consumer.
A pure batch of this grant's discovered read-only tasks is routable under each
original Role permission. The reader still requires every invocation to be
known terminal, correctly attributed to its task/intent/attempt, and every
captured result to have no changed files. Only then does the ordinary receipt
carry a read_only_discovery fact. Unknown results never release execution.

When there is no accepted code-delivery source, the original batch source reader
revalidates that receipt before using the existing investigation permissions.
Mixed/write results retain the original allow_undelivered_feedback requirement.
No new checkbox, grant flag, budget, queue or scheduler is introduced.

The original scheduling-settlement projection recognizes these recorded read-only
results, avoiding an endless unresolved-task wait after Manager has consumed
them. This does not mark tasks done, accept results, satisfy done dependencies
or turn research into verified code delivery. Reports remain review/blocked.
Old frozen receipts without the new fact retain their exact representation;
upgrading cannot silently change an existing pending decision's input digest.

A representative variant reuses the original observation test seams: two
professional Roles return captured read-only results, one Manager judgment
receives both, and unchanged inputs do not cause another paid call. It also
exercises the no-code-delivery source without inventing a delivery receipt.
Terminal evidence/Git/Manager responses are deterministic fixtures, not live
Provider, Pi or complete autonomous-product acceptance. Real acceptance stays
#419. No installation, push, PR, merge or deployment occurred.

Final verification: 14 related tests passed; independent review 10 passed,
including unknown/mixed-write/role/intent/changed-file/report and reread checks.
Ruff passed. Exact final full backend: 514 passed, 32 failed, 5 errors,
21 skipped; the same 37 failing/error identities as the baseline. Full suite
is not green. No frontend changes or new frontend validation in this slice.
