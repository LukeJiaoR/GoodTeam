# Bounded discovery from failed/mixed batches without a delivered seed

A new grant may explicitly set `discovery.allow_undelivered_feedback: true`
through the existing `start_project_continuation` API. Omitted/false flags
retain the verified-delivery prerequisite and original authorization digest.
No existing grant is edited or expanded. This increment is limited to the
existing generic batch observer's blocked/mixed terminal results. An initial
all-success Researcher/Planner seed still lacks this path (the read-only observer
requires discovered_task_ids); successful implementation candidates still need
their original review/delivery acceptance. This is not a complete no-seed Planner
Loop.

When no eligible delivery exists, the shared discovery outbox can instead use
an already captured, revalidated `batch_feedback_pending` receipt. Its packet
has `verified_outcome: null` and an explicit `undelivered_source` with
`delivery_verified: false`, current decision HEAD and original batch receipt.
Invalid delivered evidence is never caught as absent evidence. Original report
bytes, known terminal state, grant/task/job bindings and source revision are
checked by the existing batch observer, again before publication; current HEAD
is frozen separately. The first reservation atomically retains the exact original
observation key as its undelivered feedback anchor. After the immediate trigger
is consumed, Human answers or read-only feedback can reuse that anchor only
after the same original batch evidence is revalidated; arbitrary history is
never chosen as a substitute. Missing feedback or unknown terminal work cannot start
discovery. This does not create tasks from an empty project.

Manager must cite the batch receipt; failed/partial/unaccepted tasks cannot be
dependencies. It may decline or request human input. An independent bounded
follow-up uses the same create-once task, queue, budgets, Stop and replay logic.
No original invocation is retried and no cost is refunded. Historical metadata
named outcome_receipt_id remains a provenance anchor, not a delivery assertion.

Free fixture regressions include Human ask/answer reentry after clearing the
pending batch trigger, retained-anchor source lookup for a subsequent read-only
result, and rejection of changed original evidence. Full second read-only
dispatch/terminal/reentry without delivery is not independently accepted here.
They exercise the shared production tick/observation/
discovery/publication replay path and original authorization API. Dispatch,
terminal evidence and Manager responses in those tests are fixtures, not real
Runtime acceptance. CI remains paused; no paid call, deployment, credential,
permission or #440/#445 isolation change. Full real-project Loop acceptance
remains tracked separately under #419.
