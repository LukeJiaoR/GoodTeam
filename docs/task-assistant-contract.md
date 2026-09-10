# Task Assistant Recovery And Draft Contract

## Execution Boundary

A chat message produces a response, task draft, or Team proposal, not permission
to start a workflow. Work preparation begins only after the human confirms the
visible draft ID and revision. Explicit repository-inspection requests retain the
read-only readiness path. An ambiguous project mode pauses inspection or task
confirmation until the human selects new/existing project.

Mode selection uses the existing `confirm_project_mode` operation. Drafts are
persisted before this gate; selecting a mode does not invoke the chat model again.
Pending inspection records contain the original message identity and execution
evidence and resume through the existing readiness idempotency key. A confirmed
draft whose preparation becomes ready exposes a continuation action bound to the
same revision. It never asks the user to resend the original request.

## Durable Requests

The session stores logical message IDs separately from execution attempts.
Duplicate sends do not repeat model execution. Retrying a failed model call
requires `message_id` and `expected_attempt`; the worker key includes the project,
logical message and attempt. The original message remains unchanged. A nonblocking
per-project conversation lock serializes sends, retries and confirmation actions.

Effective provider/model/reasoning and verification status are projected from the
current configuration. Changing configuration invalidates old verification. A
connection check requires explicit cost consent, runs read-only, and ignores all
returned task/Team commands. Builtin runtimes are labelled simulated. Public
errors use bounded categories, never provider stderr.

An orphaned model/application attempt is interrupted, not automatically replayed.
Legacy interrupted records without saved continuation evidence require inspection
of persisted task, Team and readiness state. This is intentionally fail-closed.

## Task Drafts

- One unconfirmed draft retains a stable ID and monotonically increasing revision.
- Explicit additions append the exact original paragraph and its source message ID.
  Model summaries never replace the human-approved text.
- Other work requests against a draft become a pending change. The human chooses
  append, replace, or keep the current draft. Confirmation is blocked until resolved.
- Editing records the exact user edit and source identity atomically with revision.
- Withdrawal persists cancellation and invalidates all old confirmation buttons.
  A cancellation utterance requests confirmation; it does not claim withdrawal.
- Already confirmed/running contracts cannot be rewritten by a later chat message.
  Running task changes continue to use the existing change-intent flow.

`POST /api/projects/{key}/task-assistant/actions` supports `edit_task`,
`resolve_draft`, `confirm_project_mode`, and `confirm_task` alongside recovery and
Team actions. Draft mutations and confirmation require `proposal_id` and an integer
`expected_revision`; stale/missing revisions fail with HTTP 409. An edit also
requires `text` and `message_id`. Resolution accepts `append`, `replace`, `discard`,
or `withdraw`. Confirmation replay uses the existing single-workflow start claim.

The bounded language rules are conveniences, not an authorization system or a
general semantic diff engine. Ambiguous relations require an explicit UI decision.
All task execution still requires confirmation even if the model misclassifies.

## Verification And Remaining Scope

Tier 4: focused concurrency/replay/revision tests, full backend suite, frontend
tests/lint/build, and isolated browser/live-model acceptance. See
[the execution record](plans/2026-09-10-m0-runtime-team-recovery.md).

These contracts address #412, #413 and #414. They do not constitute release
acceptance #411: five real tasks across two projects and the remaining product
issues are separate gates. No production workspace is used for smoke writes.
