# Goal context through the existing task scheduler

New pending Goal work now uses the existing project task ledger, Task Assistant
queue, batch execution, process capacity, worktrees and result services. It does
not start another Workflow or create a separate execution queue. This document
records implementation and remaining acceptance, not a new policy checklist.

## Ownership and recovery

- A Goal work item retains its logical `goal-…-work-…` ID. Its `execution` field
  binds the original task card, exact proposal revisions and derived batch ID.
  The existing Goal transaction/operation log persists that association before
  dispatch. SQLite and PostgreSQL use their existing JSON records; no new tables.
- Binding precedes Goal claim and queue admission. Retries retain the original
  proposal, task intent and batch identity. Existing claimed/running batches go
  through the existing background entry, which observes a live owner and uses
  existing dispatch recovery after interruption. Blocked batches are not silently
  restarted.
- Once bound, Goal projection does not rewrite task status, observations,
  artifacts or annotations. The task/batch owns execution state. Goal views read
  that exact batch instead of the project's single `current.json`.
- Goal Stop revokes subsequent admission and Worker starts, including not-yet-
  started recovery. Already-running Workers retain their existing process
  controls. A Stop during assessment is returned as stopped, not overwritten
  with a new blocked state.
- Worker success remains `review`, not accepted delivery. Goal assessment waits
  for the batch's existing delivery fact, then reads original candidate validation
  receipts and the complete protected review report. It uses the actual batch
  execution ID, original Goal criteria and literal evidence citations, not a
  fabricated Workflow state or a result summary.

## History and remaining scope

Already-active, unbound Goal work remains attached to its original Workflow.
Its original evidence, logical IDs and recovery remain readable; the migration
never requeues it as a new batch. After that work completes, subsequent pending
Goal work enters the common task path.

## New request entry points

`/start`, CLI `start` and bug reports now adapt free-form input into an ordinary
Planner task in the same task ledger/queue/batch path. The old `start_workflow`
function name is retained as an API adapter; it no longer creates `current.json`,
PRD/adoption/fix stages, approval nodes or a separate execution engine.

Task Assistant confirmation preserves an explicitly assigned role and scope.
Unassigned drafts become Planner/read-only tasks instead of silently becoming
implementation work. Exact draft IDs/revisions retain the task and proposal;
existing started Workflow attempts only reconcile their original records.
Captured historical methods remain retained evidence and are not relabeled as
new batch methods. New tasks capture their own methods through the batch path.

The route-wide exclusions based solely on active Workflow/Goal/batch identity,
project-mode selection and planning-idle state have been removed from intake and
planning. Actual task reservations, dependencies, write conflicts and process
capacity remain in the existing scheduler. Historical Workflow controls still
operate their original records, but no new request creates that route.

Direct API callers may supply and reuse `run_id` as their request identity; CLI
provides `--request-id`. A successful response returns `request_id` separately
from the actual batch `run_id`. Sending no request ID is a new request, not a
promise of deduplication after a lost response. Model configuration/profile/role
overrides remain part of the original queued input and reach runtime resolution.

No active frontend used the standalone `startRun` export. The current confirmation
UI already refreshes the project/task view and supports batch controls; new batch
IDs must not be navigated to the historical Workflow-only run detail page.

The smoke harness now observes actual batch/report evidence. Planning or missing
delivery yields `incomplete`, including CLI/background status, rather than
approving an unrelated historical Workflow or claiming product acceptance.

A Planner result is still a plan, not implementation or product delivery. This
entry-point migration does not claim that arbitrary Planner prose is automatically
converted into authorized child tasks. Subsequent assignments use the existing
Manager/Planner task handoff; their complete autonomous loop needs real acceptance.

Protected review retains its existing process ownership, not the ordinary Worker
wrapper. The Goal-to-task adapter preserves existing work descriptions and does
not invent filesystem paths from natural-language allowed scope.

## Required real acceptance (#419)

Only static source review, Python AST parsing and diff checks were performed.
No tests, model runs or dependency installation were executed. Validate:

- fresh Goal -> original task/proposal -> batch -> delivered result -> assessment;
- interruption before binding, after binding, after claim and during Worker start;
- no duplicate execution on lost responses or repeated ticks;
- Stop versus admission, recovery and assessment;
- task digest stability and task-owned status after Goal revisions;
- exact candidate/report evidence and two related iterations;
- old Workflow-backed Goal history and subsequent task-backed work;
- existing project dependencies, worktree source imports and parallel capacity.

Record results in [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419). This
local document does not claim that issue was updated or acceptance passed.

## PR #737 static review follow-up

The Goal assessment now includes the original batch execution artifacts even
when optional batch quality is not configured; absent checks remain unverified.
Assessment retries resolve the original persisted batch binding instead of
requiring a legacy Workflow. Telegram bug submissions identify the actual
Planner task and batch, not a nonexistent FIX_PLAN approval.

The configured installer no longer inherits GoodTeam's injected provider key.
It still reuses host/project environment and HOME access, so ambient system API
keys and readable credential files are not isolated. Restoring a fixed environment
allowlist or synthetic HOME would change the requested host-environment behavior;
this patch does not silently reintroduce either, nor claim the P1 is fully closed.

These changes have static checks only. Real assessment retries, quality-disabled
batch delivery, Telegram submission and installation remain unexecuted acceptance.
