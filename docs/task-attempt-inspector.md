# Inspect a workflow attempt

Tracked by #532 under #379 and #397. This bounded slice follows the functional
UX research and the shared workspace in #531. Beautiful UI's
[Task Rows and Context Cards](https://www.beautifului.dev/) inform compact
execution rows and source-first details; they do not supply execution evidence.

An existing Work execution assignment with a server-selected current attempt
opens a read-only side panel. It keeps the conversation mounted and shows the
task-start baseline, the saved attempt's status/activity, and versioned delivery
and review records for the same workflow run. Closing returns to the trigger.

## Identity and evidence

`resolveAttemptInspection` requires the exact workflow mission project/run,
state run, and delivery-summary project/run. Both the assignment ID and its
`current_attempt_id` must resolve uniquely. The attempt must occur exactly once
in the server-filtered `delivery_summary.attempts`; another assignment using the
same attempt is ambiguous. Optional Worker details use only a unique matching
ID, rejecting contradictory scope, role, or Skill task identity.

The frontend never chooses the latest attempt by role, timestamp or ordering.
The saved assignment state and attempt status are displayed separately. A
completed assignment does not rewrite a failed attempt record. Missing optional
Worker details, including on historical runs, do not erase the bound attempt.

The panel preserves three distinct scopes:

- Task-start input and methods use the existing bounded input-context reader.
  A task-start capture is not the full actual input of a later attempt, and
  recorded methods do not establish model receipt or adherence.
- The attempt record supplies identity, saved status, timestamps and optional
  saved job summary/heartbeat. A heartbeat is not completion or measured progress.
- Delivery artifacts are bound to project/run/key/content version. They have no
  producer-job binding, so they are labelled as records for the run rather than
  outputs of the selected attempt. Protected Reviewer evidence retains its own
  validation and run-level label; the panel never exposes review mutation mode.

Ordinary Worker memory observations are included on the exact server-bound
`delivery_summary.attempts` row. The panel distinguishes captured pickup input,
host preparation, host call entry and WAL writeback observations. Provider
receipt and actual reading stay unknown. The panel does not substitute a role's
latest retrieval, a search match, mutable status citations, or another attempt's
receipts. Missing legacy history and protected task-input/correction/Skill
protocols remain unknown; damaged retained proofs are unavailable. See
[Worker memory observations](worker-memory-observations.md) for source and
truncation boundaries.

## Navigation and invalidation

Selection is pinned to route project/run, assignment and attempt. A new current
attempt makes the selected view unavailable rather than silently retargeting
it. Missing/duplicate identities and stale snapshots similarly unmount readable
content. Existing input, artifact and review readers then abort pending requests.
Project or run navigation clears selection, including A→B→A with colliding IDs.

The panel lives outside the mutation-disabled fieldset, so its close control
remains usable when the snapshot becomes stale. Viewing or closing it does not
send a message, retry a job, approve work or replace the composer draft. The
existing native dialog handles modal focus and Escape; the existing mobile
execution tab continues to own its visibility.

## Verification boundary

Tests exercise the real Work execution-row entry and nested readers: current and
historical records, route changes, new attempts, duplicate IDs, stale snapshots,
late responses, draft preservation and focus return. Focused/full frontend
tests, lint, production build and independent final-source review are recorded
in the PR. These are DOM/source/build checks, not actual-browser pixel or real
model Runtime acceptance. The read-only memory observation extension adds no
execution permission, CI workflow, dependency or deployment. #409/#420 remain parked;
#411/#418/#419 retain their real-task and browser gates.
