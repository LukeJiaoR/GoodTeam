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

## Manager assignment and result return

Project outcome: a concrete user request reaches the appropriate Worker with
its original scope, and actual results return for the Manager's next judgment.
The observed gap was chat classification ending at a draft or the Manager
reading README itself. The smallest change adds a queue action and report
publication to existing services, without changing the execution owner.

Project import only registers the directory and records its identity. It does
not classify the repository, create an inventory, call a model or start a task.
Opening or polling the conversation also does not start inspection or turn old
inventory artifacts into new replies. The `project_understanding` intent and
its pending-inspection/resume and template-report path have been removed.
Existing inspection messages remain readable as history.

Project-question routing follows the evidence needed, not whether the user used
a question mark. For example, “这个项目是干嘛的” is a ready read-only Researcher
task when supplied evidence does not already explain the project. The Manager
uses the same `queue_task` action; it must not read README/source files itself to
turn missing research into a `project_question` answer. Questions already answered
by supplied evidence or retained Worker reports remain ordinary conversation.
A bounded Git-presence fact retains its existing single-command inspection path.

Research and repository-analysis deliverables use ordinary `work_request`
intake and its existing task confirmation/queue. The Manager coordinates the
team's Researcher instead of doing broad research during classification.
Research methods belong to loaded task Skills; this change adds no special
research dispatcher or replacement inspection workflow. Shared explicit
readiness APIs and task-start checks retain their identity and approval gates.
The chat Manager can now return a callable `task_action` with `kind: queue_task`,
a dispatchable Team role, execution policy, declared write paths and acceptance
criteria. It may also set `worker_id` to the exact configured Worker shown in
the Team Context. The host validates that this Worker belongs to the requested
role and is dispatchable, then carries that target through the original task,
queue, isolated job and result. An unavailable explicit target is reported; it
is never silently replaced by the role's default Worker. Calls without a target
retain the existing role-based selection. Runtime, model and prompt settings
come from the selected Worker's configuration, not model-authored commands.
The host binds the selected exact user messages to one original
LocalTaskStore card and one existing queue item. A source-bound retry reuses that
card; a changed assignment fails instead of creating another task. Existing drafts
retain their revision flow. Read-only advice and history never call this action.
Skills can instruct the Manager to use this same action; Skill text cannot grant
execution or review authority.

The conversation displays the original task, role, read/write scope and criteria
before the existing `start_queued_task_batch` confirmation. This initial confirmation
retains model-charge and readiness gates. The ordinary isolated `dispatch_tracks`
pool resolves the configured Worker and captures only the participating roles'
task methods using existing paths;
there is no research runner or second task state machine. Batch settlement returns
actual readable Worker reports, limitations and outstanding independent review to
the conversation, with idempotent publication per batch/recovery attempt. Source
reports remain separately inspectable. Worker completion never marks delivery done.

Named Worker assignments use the existing queued batch execution path, including
a one-item batch. Single-draft Workflows currently resolve by Role and do not
carry an exact Worker target; selection, confirmation and start reject named
assignments rather than silently switching to the default Worker. The original
queued task remains available for batch execution. Previously edited drafts
that still retain a named assignment are also rejected at start; they are not
silently converted into default-Worker execution. Ordinary unnamed drafts keep
their existing single-draft Workflow behavior.

The task's explicit Worker target is part of its existing execution intent.
Queue confirmation, continuation, quality checks and recovery retain that target;
old tasks without a target keep their original intent representation. Worker
reports and conversation result metadata include the actual Worker ID and name.
The bounded implementation/investigation discovery path also accepts an optional
Worker target from its captured Team capability. This does not add new roles or
permissions to an old discovery grant; automatic Planner result reentry remains
part of #696.

Role base Skills use the existing many-to-many bindings: each role can select
several methods, and a method can serve several roles. `required` and `on_demand`
belong to each binding. Only this task's roles are captured for a new direct
dispatch; another role's custom methods cannot opt this task into Skill execution.
An existing task still uses its original captured subjects and methods on replay.
The current default binding retains legacy behavior; explicit base-method
packages and ordinary custom-Skill Runtime delivery remain infra work under
#438/#445. A saved binding is not evidence that a method executed.

In the database-backed Manager dialogue, `advance_continuation` calls the existing
continuation adapter only for the exact current active grant. It runs after the
conversation lock is released and passes no model-chosen task, command, budget or
confirmation. Stopped grants and repeated messages cannot spend another attempt.
The existing continuation service still owns scope, budgets, recovery and gates.

This is a connection of assignment, execution and return paths, not acceptance of
autonomous multi-round research. New chat work does not inherit an unrelated
project grant. Without an applicable grant, its first execution still needs the
existing confirmation. Arbitrary partial research results are not yet automatically
converted into bounded follow-up tasks or independent research acceptance. Existing
continuation can proceed only where its approved inventory and delivery/recovery
contracts permit it. A Worker report is not proof that the full user goal is met.

This is a partial implementation toward #686. The bounded Git-presence exception
remains, and prompt routing does not prove tool-level isolation. The full delegated
investigation → renewed project decision loop remains unaccepted under #419.
The Decisions API proposal in #686 is documentation only; no API dependency or
paid decision invocation is introduced here.

Tier 3 validation: `test_manager_assignment.py` exercises original source binding,
explicit confirmation, isolated Research subprocess execution, visible results and
idempotent replay without delivery promotion. `test_manager_dialogue.py` covers
active-grant follow-up outside the conversation lock and refusal after Stop.
`test_task_assistant.py` and the project Manager browser regression cover passive
import and ordinary research intake. The existing
`test_project_iteration_continuation.py` remains the representative pick → execute
→ verified delivery → pick a different task contract. These fixture checks do not
establish real-model research quality or the complete #686 loop.

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
