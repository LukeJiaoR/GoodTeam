# Discover a bounded follow-up from a delivered project outcome

The Work page offers an explicit **outcome discovery** option inside the existing
project continuation form. The default remains a fixed existing task list. Only
the additional grant lets Manager propose a new task after the approved inventory
has been delivered.

This slice discovers at most **one new follow-up per accepted delivery**. Existing
task selection and the isolated batch pool keep their current parallel capacity;
the one-task limit applies only to each discovery response. Across later accepted
deliveries, the same grant can discover more tasks until its finite limits stop it.

## What the owner authorizes

Start with at least one existing seed task, then explicitly confirm:

- A concrete project objective of 20–2000 characters.
- One to twelve project-relative allowed write domains. The follow-up must stay
  inside these paths; metadata paths, glob patterns and parent traversal are not
  accepted. These are verified task scopes, not a claim of OS-level sandboxing.
- A total of 1–50 new tasks under this grant.
- A total of 1–100 Manager planning calls, including initial existing-task
  selection and outcome discovery. Reserving a call consumes its allowance even
  if its outcome later becomes uncertain.
- The existing maximum batch count and configured model charges.

The original seed IDs, seed intent digests, displayed backlog version, objective,
paths, runtime authority and budgets are frozen separately from the growing
effective task scope. Replaying the original authorization after a new task was
created still checks the original seeds; it cannot widen the grant or reset counts.

## What Manager actually receives

The host reads the current project HEAD and verifies the original delivered task
intents, receipt identity, effective dispatch/job results, retained candidate,
landed ancestry, clean project content and unchanged delivered files. If candidate
quality was configured, its original passed check and protected independent-review
receipts must still match. A legacy delivery without configured quality remains
explicitly `not_configured` / `human_attested`; delivery does not invent machine
verification.

This creates a frozen `project_outcome_discovery_packet.v1` containing:

- The exact verified delivery, task criteria, candidate/integration and current HEAD.
- The original discovery grant, authorized delivered dependencies and full backlog
  context so existing work remains visible.
- Bound repository-understanding evidence when already available.
- Existing role-memory pickup and its provenance, as supplemental context.
- The exact delivery/check/review references the proposed follow-up may cite.

The host writes deterministic accepted-delivery facts to the existing
`RoleMemoryStore` WAL, using a stable receipt source and execution provenance.
Its normal knowledge retrieval continues to exclude execution-provenance records.
The verified outcome is passed explicitly; a Worker narrative is never relabeled
as learned truth to bypass memory filtering. Replays do not duplicate the fact.

The existing read-only Manager Worker consumes this packet. Its job identity binds
the complete frozen packet digest, so an unchanged backlog cannot reuse an answer
about another result, revision or grant. The host rechecks current authority,
source HEAD/content, immutable delivery/quality and backlog before and after the
call. Supplemental memory stays the captured packet; a Manager's legitimate new
provenance event does not silently replace the original context.

## From a proposed task to the same execution path

The response may contain one bounded task, with explicit acceptance criteria,
allowed write domains, authorized delivered dependencies and actual receipt
references. The original delivery receipt is required. The host fixes the role to
`implementation`, priority to `medium` and policy to `workspace_write`; model
output cannot select argv, a runtime, merge permission or approval state.

The existing continuation session retains one planning/publication outbox. A
create-once identity binds the grant and input packet. Within the LocalTaskStore
lock, replay checks exact task intent and preserves current task progress,
including `done`. A new identity with an equivalent normalized business intent
cannot duplicate an existing card, including a completed one, or automatically
adopt an unapproved existing task. This is exact deduplication, not a semantic
similarity engine.

The original active authorization is checked under the same session mutation
that admits task creation. If writing the task succeeds but publishing its ID
fails, the next service tick adopts only that exact create-once card. It does not
spend another Manager call or overwrite the card. The retained choice then enters
the ordinary Task Assistant queue and existing batch adapter; no second scheduler,
execution ledger or Manager dispatcher is introduced.

Checks, independent review and delivery remain the existing boundaries:

1. Execute the new task in an isolated Worker workspace.
2. If quality is enabled for this batch, explicitly configure its check and runtime
   authorities using [candidate quality](project-batch-quality.md).
3. Review the final candidate, land it through the authorized Git process and use
   the existing delivery confirmation. Discovery never merges or deploys.
4. The existing service can then observe that new outcome and discover again within
   the remaining grant. A failed attempt uses [batch recovery](project-batch-recovery.md).

## Stopping and incomplete evidence

Stop revokes future planning/admission without deleting retained evidence. An
already reserved Manager call may finish, but its late result cannot create or
execute a task after revocation. Runtime/Team changes, changed source or task
scope, invalid references, out-of-scope advice and uncertain model outcomes block
continuation while retaining the original reservation and counts.

Batch or Manager budget exhaustion is `approved_iteration_budget_used`; new-task
budget exhaustion is `discovery_task_budget_used`. Manager may instead return
`new_task: null` with a concrete rationale. This records `discovery_no_followup`
and ends this grant's active turn. It does **not** claim that the whole project or
objective has been conclusively completed. The Work page displays the actual
stopping reason and the last proposal's source receipt.

## Existing HTTP action

Use the existing `start_project_continuation` action, adding the optional object:

```json
{
  "action": "start_project_continuation",
  "idempotency_key": "owner-project-iteration-grant",
  "expected_backlog_version": "sha256:DISPLAYED_BACKLOG_DIGEST",
  "task_ids": ["DEMO-1"],
  "max_batches": 3,
  "confirmed": true,
  "billing_acknowledged": true,
  "discovery": {
    "objective": "Improve the documented import flow using its verified delivered results.",
    "allowed_write_domains": ["src/importer", "tests/importer", "docs/importer"],
    "max_new_tasks": 2,
    "max_planning_calls": 3
  }
}
```

Omitting `discovery` keeps the original fixed-list behavior. There is no extra
per-follow-up confirmation inside this preapproved scope. The session exposes the
unchanged `seed_task_ids`, effective `task_ids`, `discovered_task_ids`, creation and
planning counters, and `discovery_last` with its delivery receipt and rationale.
GET/read routes do not plan, append memory, create tasks or advance execution.

## Verification boundary

`tests/test_project_outcome_discovery.py` is one representative two-round local
integration case. Offline CLI fixtures pass through the production Manager
planning interface. A is chosen, executed, checked, independently reviewed and
actually landed. B is absent from the initial ledger and is derived from A's real
candidate SHA; it is then created once, executed, checked, reviewed and landed
through the same path. The case includes task-create/session-publication loss,
original-seed replay, scope rejection, Stop and budget exhaustion.

Those processes and Git operations are real local operations; the model responses
are deterministic protocol fixtures. This verifies wiring and authorization
behavior, not a paid model's prioritization quality or complete project-autonomy
acceptance. The real product evidence remains in #419. Automatic GitHub CI stays
paused, and historical plans/acceptance records are preserved.
