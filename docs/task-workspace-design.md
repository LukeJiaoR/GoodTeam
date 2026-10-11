# Task workspace

The Work entry keeps project task navigation, the ongoing conversation, and the
selected task's results together. Its visual direction is informed by
[Beautiful UI](https://www.beautifului.dev/): quiet surfaces, compact task rows,
and a focused composer. The implementation uses GoodTeam's existing components
and state, not the reference site's timer-driven demonstration task lifecycle.

## State and interaction boundaries

- Current and historical workflow runs are top-level task navigation. Outcome
  assignments are steps within the selected task; they are not additional runs.
- Queued requests remain editable drafts until the existing start flow accepts
  them. A saved draft is not a running worker.
- The conversation remains mounted while task navigation and execution details
  are opened. Existing draft, acknowledgement, and duplicate-submit behavior is
  retained.
- Historical views remain read-only. Stale or unknown data does not become a
  completed result or grant mutation capabilities through a visual change.
- Delivery summaries and skill-review reports keep their existing task,
  artifact-version, and receipt bindings. A completed process is not proof that
  every acceptance criterion was verified.
- Automatic candidate review retains its existing automatic protocol. This
  layout does not add a human approval prerequisite or change other workflows'
  server-side decision rules.

## Execution evidence entry

The optional `team_workspace.mission.execution_evidence_href` is projected by
the backend for the current project-bound Operator queue item. It points to the
existing same-origin `/review-workbench/{execution_id}?project_root=...` page.
The queue execution ID, joined run execution/task IDs, task repository, and
available issue number must agree. Both task and run records must exist in the
resolved project root.

Missing, malformed, or mismatched records produce an empty link. Ordinary
workflow missions have no inferred execution link: their `run_id` is never used
as an execution ID. The React surface consumes this field rather than searching
other executions or recomputing candidate acceptance. Link availability means
that the evidence page can be opened, not that the candidate is accepted.

## Verification scope

The read-only projection is tested for current project scoping, malformed and
mismatched records, missing roots, encoded root paths, and repeat-read
immutability. The generated link is passed through the existing Workbench page
handler and checked against the correct execution and task.

Frontend verification covers the persistent task list, mobile task access,
continuous conversation drafts, result bindings, and the evidence URL boundary.
Use the existing continuity and task-flow tests when browser execution is
available. DOM interaction checks and a successful production build do not
replace real browser layout, multi-size, or visual acceptance evidence.
