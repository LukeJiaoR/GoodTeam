# Bounded discovery from failed/mixed batches without a delivered seed

A new grant may explicitly set `discovery.allow_undelivered_feedback: true`
through the existing `start_project_continuation` API. Omitted/false flags
retain the verified-delivery prerequisite and original authorization digest.
No existing grant is edited or expanded. Failed/mixed terminal results use
this flag alone. A new grant may additionally set
`discovery.allow_initial_readonly_feedback: true` to feed initial all-success
Researcher/Planner seed batches into the same observer and Manager outbox.
The additional flag requires `allow_undelivered_feedback: true` and each Role's
existing `allow_investigation` / `allow_planning` permission. Every task must be
an original seed, read-only with no write domains, and have attributable known
terminal evidence and unchanged captured files. Ordinary successful implementation
candidates still wait for review/delivery acceptance. Old grants missing the new
flag retain their previous behavior.

This closes the initial read-only seed routing gap, not actual Runtime admission
or complete real-project acceptance. Reports remain unaccepted observations,
never delivered tasks or dependencies. No empty-project bootstrap is introduced.

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

The initial-success regression runs the production continuation tick through the
shared batch observation, no-delivery source, Manager decision, lost publication
acknowledgement replay and dispatch seam. Researcher and Planner are both covered;
old grants, absent Role permission, unknown termination, changed files, Stop and
budget exhaustion cannot start follow-up work. Model/process/dispatch seams are
fixtures; no live Runtime result is claimed.
