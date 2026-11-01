# Continue a project across delivered batches

The Work page now has **Automatic project iteration** beside the Manager's
iteration suggestion. Select the displayed existing task scope, set a maximum
number of batches, and confirm the configured provider charges. The same limit
also bounds Manager planning calls in the default fixed-task mode. An optional
[outcome discovery grant](project-outcome-discovery.md) has its own explicit total
Manager-call limit and can add bounded follow-ups. One batch uses the existing isolated pool,
with up to ten concurrent Workers; each authorized task can be claimed once.

The authorization is retained in the existing Task Assistant session. The
existing continuation service resumes it on server restart. LocalTaskStore
remains the only project task ledger, and dispatch reports/workspaces remain
the execution evidence. Viewing the Work page or report never starts a Worker.

## Delivery and the next iteration

1. Manager compares only tasks in the approved scope, using a fresh complete
   project backlog as context. Dependencies must already be delivered.
2. The choice enters the ordinary Assistant queue and the same batch adapter
   used by a manually confirmed queue selection. Readiness, method admission,
   actual write scope and isolation checks still apply.
3. Completed Workers leave original task cards in `review`. Open **Inspect
   execution and verification records** to inspect the actual jobs, verification
   results, changed paths and integrated candidate. A successful process is not
   an independent review; `verify.skipped` remains visible when no command ran.
   A batch explicitly configured for [candidate quality](project-batch-quality.md)
   additionally requires a passed real check and exact-candidate independent
   review; its original result report and any bounded correction stay inspectable.
4. Independently review the candidate and acceptance evidence. Integrate the
   reviewed candidate commit from the displayed candidate directory into the
   project repository using the normal authorized Git workflow. This feature
   does not merge or deploy on the user's behalf.
   For a quality-enabled batch, use its final reviewed candidate, which can be a
   separately recorded correction of the initial integration.
5. In **Confirm landed delivery**, enter the full current project HEAD and
   confirm the review. GoodTeam verifies the retained candidate and Worker
   identities, candidate ancestry in current HEAD, clean non-metadata project
   files, and matching candidate/landed blob identities for every changed path.
   A squash/cherry-pick without the retained candidate in its ancestry cannot
   satisfy this proof. Changed task scope also prevents acceptance.
6. Accepted cards become `done` with a bound delivery receipt. On its next
   service tick, GoodTeam automatically makes a fresh Manager choice and starts
   the next approved batch. No per-task Pick or Start click is needed.

**Stop future iterations** revokes subsequent planning/dispatch. An already
started batch retains its existing Worker controls and evidence. It can still
be reviewed and delivered without reactivating the stopped authorization.
After delivery settles, a new explicit authorization can select new scope.
Stop is durable even while a Manager call or Worker is running. A Manager call
already admitted may finish, but its late result cannot reactivate continuation
or admit a new batch after revocation.

Without outcome discovery, the service stops when the approved tasks are delivered, the batch/model-call
budget is used, scope/configuration changes, an execution is blocked, or no
approved task is ready. That default mode does not create new backlog tasks or expand the
approved inventory. The explicit discovery mode freezes its original seed list
separately and records each new task admitted within the additional grant.
Uncertain outcomes retain exact queue/dispatch identities
and are not replaced with a newly launched batch. Repeated delivery requests
return the same historical receipt even after a later batch advances HEAD.
For a blocked batch, use [failed-task recovery or abandonment](project-batch-recovery.md);
recovery never resets the continuation's original budgets or revives Stop.

## Existing API

Use `POST /api/projects/{key}/task-assistant/actions` with:

- `start_project_continuation`: `task_ids`, `max_batches` (1–50), stable
  `idempotency_key`, the displayed `expected_backlog_version`, `confirmed: true`,
  `billing_acknowledged: true`.
  The optional `discovery` object is documented in [outcome discovery](project-outcome-discovery.md);
  omitting it retains the fixed-task contract.
- `stop_project_continuation`: the current `continuation_id`. This remains
  available even when the target directory/configuration cannot be read.
- `accept_project_batch_delivery`: `run_id`, `candidate_commit`, `landed_commit`,
  `reviewed: true`, `confirmed: true`. Both commits must be exact full hashes.

Read an existing batch report at
`GET /api/projects/{key}/iteration-batches/{task-batch-id}`. The ordinary Work
response contains `task_assistant_session.project_continuation` and
`project_task_batches` for status and review presentation.

## Local evidence

`tests/test_project_iteration_continuation.py` retains one representative
two-batch scenario through the public action handlers and existing service tick.
It uses actual local subprocesses, isolated Git repositories, fan-in and landed
commits, with an offline provider executable and synthetic Manager choices.
It is evidence of the execution/continuation wiring, not real-model quality or
whole-project autonomy acceptance. That product record remains in #419.
Automatic GitHub CI remains paused.
