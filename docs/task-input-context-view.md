# Task input and method inspection

Work offers an on-demand, read-only view for an explicitly selected current or
historical **workflow run**. It does not derive an issue execution, Manager turn
or operator task from a workflow run ID. Project conversation and unsent input
remain mounted while the view is opened and closed.

`GET /api/projects/{project}/runs/{run}/input-context` resolves the existing
project configuration and current/archive state, then reads the registered
immutable task lock using the existing task identity and lock checks. The
response is `goodteam.task-input-context.v1`, with the exact project and run.

## What each source means

- `locked` / `task_start_lock`: original user-facing request fields, capture
  time, captured Team revision, lock identity and per-role method references
  come from the registered task-start capture. The captured producer workspace
  and Worker root must match the resolved project. Current Team settings never
  replace these fields.
- `legacy_unverified` / `workflow_state`: no carried lock reference is present
  and no known registered capture is found. Only the existing workflow record is
  shown. This is not evidence that the task was never captured, that the model
  received these fields, or that its current contents are the original input.
- `unavailable`: an expected capture is missing, incomplete, corrupt or belongs
  to a different identity/root. A known registered task with a stripped state
  reference is also unavailable. No mutable workflow or Team input is substituted.

As elsewhere in the file-backed control plane, loss of every independent
authority/marker cannot be distinguished from unverified legacy state. This
view does not create a new trust or recovery protocol.

The whitelist contains `exact_request`, `source_type`, `delivery_mode` and
`delivery_standards`. Extra private context, producer paths, full prompts,
Role contracts, method bodies, and unrestricted captured JSON are not returned.
Missing fields stay empty rather than being reconstructed from conversation.

Method diagnostics describe the capture at that time. A method reference does
not establish current runtime admission, availability, input delivery, reading
or compliance. The existing task-bound Reviewer view supplies its independently
validated execution and resource-read records; command-output matching still
does not establish model comprehension. No new review, retry, approval or
execution control is added by the input inspector.

## Read behavior and recovery

The read uses the registered committed lock and its original scope; it does not
capture, repair task metadata, resolve a fresh method set, initialize availability
or acquire pins. Both Store constructors and lock opens use the existing non-creating filesystem
primitives, so a disappearing directory or lock remains missing. Surviving
immutable objects prevent a lost index/reference from becoming legacy input.
Errors contain
bounded error codes rather than private paths or exception text. A retry only
reads the same selected task again.

The frontend rejects response schema/project/run/kind mismatches and late
responses from a previous selection. Missing input and loading are distinct;
opening a historical task cannot silently show the current task's input.

## Validation scope

Related tests use real registered method packages, original task captures,
current and archived state, changed Team versions, missing authority and
stripped references. Both the dashboard handler and product HTTP server are
exercised with isolated synthetic projects. These reads do not run a model or
perform a task mutation. Frontend interaction tests cover the user entry point
and interrupted navigation. Commands and final results are recorded in the PR.

Browser visual/keyboard layout acceptance and a real model's complete input/use
are not established by HTTP or DOM tests. The independent native correction
runtime blocker is unchanged. No CI workflow is added.
