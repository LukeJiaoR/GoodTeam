# Project Manager and task boundaries

The Task Assistant is a project coordinator. It has no business task assignment.
A conversation can reference a task without becoming that task or controlling its
execution. Worker roles own business tasks; runs and attempts provide execution
state and evidence. This implements issue #450.

## Read contract

`GET /api/state?project=KEY` exposes `project_assistant.v1` in `project_assistant`:

- Stable project identity, response status, runtime and logical assistant requests.
- Project conversation, including durable change discussions from earlier runs.
- Current proposal, explicitly saved future drafts and `can_select_task`.
- Separate assistant activity records for model-call diagnostics.

`team_workspace.v1` continues to describe task/run context, workers, outcomes and
available task operations. Its `mission` is null when no business run is selected;
an idle conversation no longer manufactures a mission. Existing conversation
fields remain as compatibility projections. Historical run endpoints continue to
return read-only task details, not another assistant session.

Manager jobs keep their existing storage and provenance. The canonical
`orchestrator` role identifies assistant activity, which is excluded from business
assignment and delivery calculations. A phase label alone cannot hide a Worker
failure. New business task creation/reassignment rejects `orchestrator`.

## Conversation and navigation

`/projects/KEY/work` stays a project conversation instead of redirecting to the
active run. **Manage tasks** opens the otherwise hidden task/history list in the
execution pane, including saved future drafts. Closing it returns to the composer;
selecting a task or preparing a saved draft closes it automatically. Desktop and
mobile keep the same explicit entry point. The list selects task context and worker/result details. The
same conversation component and composer remain mounted when selecting history;
unsent text and project messages remain available. Historical task activity is
separately inspectable. Historical detail dialogs and inspectors cannot expose
live task mutations.

Messages accept optional `context_run_id`. The backend resolves it only among the
project's current and archived states, before recording or executing a request.
An explicit reference permits a read-only discussion, including after that run
ends. It cannot create tasks, update Team or approve/modify the current run. The
reference is bound to the logical request identity and reused on explicit retry;
reusing a message ID for a different reference is rejected.

Without a selected reference, ordinary project discussion retains the existing
task-intake and Team-confirmation contracts. Running-work questions can discuss
independent ideas without mutating the running contract. Exact-target Request
changes still uses the existing Intent Commit and explicit confirmation.

## Saved future tasks

The composer has an explicit **New task → Save task** mode. It preserves the
user's exact text in `queued_task_proposals` in the existing project session.
Saving never calls a model, performs preparation, starts a run or changes the
active proposal. Multiple future drafts can coexist with one active workflow.

The existing assistant actions endpoint accepts:

| Action | Required fields | Effect |
| --- | --- | --- |
| `queue_task` | `message_id`, `text` | Save a separate draft, up to 8,000 characters. |
| `edit_queued_task` | `proposal_id`, `expected_revision`, `message_id`, `text` | Persist an exact revision. |
| `discard_queued_task` | `proposal_id`, `expected_revision` | Record withdrawal. |
| `select_queued_task` | `proposal_id`, `expected_revision` | Move an available draft into the existing confirmation flow. |

All actions use the existing project conversation lock and atomic session writes.
Duplicate saves/edits with the same identity and content replay without adding a
draft or revision. Identity conflicts and stale revisions fail with HTTP 409.
Selection requires no active workflow and no unresolved current draft. It
archives a terminal proposal only from persisted run evidence, then selects the
future draft without confirming it. A separate **Start this task** action retains
the original readiness, revision, start-claim and recovery checks. An existing
start's reconciliation remains allowed; a different active run blocks startup.

This adds future-work collection, not parallel execution or mid-Worker steering.
Builtin/fake responses are recorded snapshots, not real-model capability evidence.

## Archiving the displayed task (#417)

`POST /api/terminate` (and `/terminate`) requires `project_key` and a nonempty
`expected_run_id` from the displayed active task. Missing or malformed bindings
return HTTP 400 `run_binding_required`. A different or absent active run returns
HTTP 409 `run_binding_mismatch`, with a refresh recovery action. Validation and
archiving share the existing project workflow lock; rejected requests do not
change the replacement run, its pending transition, archives or action ledger.
The trusted Python helper retains its existing unbound calling convention.
Older HTTP callers must send the run binding. A successful archive retains the
existing orchestration-stop semantics; it does not delete the repository or
establish that an external Worker process has stopped.

Work sends the displayed run identity and resets its archive confirmation when
the active run changes. A rejected archive refreshes task state and history.
When state updates fail, the last visible task and evidence stay readable, while
the Work controls (including conversation input) pause until a successful refresh.
The existing current-context Refresh and error recovery remain available outside
that disabled scope. Open mutation panels are hidden while state is stale.
This is a freshness guard, not a new client-side permissions or workflow model.

Regression coverage is in `tests/test_task_cancellation_http.py`, Work page unit
tests and `frontend/e2e/task-cancellation.spec.js`. The real product HTTP checks
use isolated task state and no Workers or models. Browser checks use the existing
fake-Worker fixture: an old tab cannot archive a second task started through the
project conversation, and an explicitly malformed poll pauses actions until a
real refresh. These checks do not close the real-model acceptance issues or prove
the persistent-goal execution loop in #439.

## Implementation and verification

- `assistant_workspace.py` and `conversation_projection.py`: read projections.
- `assistant_queue.py`: explicit saved-task transitions.
- `useProjectConversation.js`: conversation requests and delivery receipts.
- `ProjectConversation`, `ConversationTimeline`, `TaskAttention`, `TaskNavigation`:
  independently owned conversation, task decisions and navigation.

Tier 4, with Tier 3 contracts for request identity and draft transitions. Focused
coverage is in `tests/test_project_assistant.py`, existing assistant/web/workspace
regressions, composer tests and `frontend/e2e/project-manager.spec.js`. The browser
fixture uses temporary workspace, registry and fake Workers. It checks exact saved
text, task termination and explicit second-task startup, composer continuity,
history context, unchanged current state/approvals and browser errors. The broader
business-loop and concurrent-approval acceptance remain required.
