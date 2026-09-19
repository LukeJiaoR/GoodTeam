# Conversational change status

The composer distinguishes ordinary questions from a version-bound change
request. A message during a workflow is read-only discussion, not a queued
change and not a live Worker instruction.

At an actionable current review, **Discuss a task change** moves the exact
unsent text through the existing `startChangeIntent(initialText)` API. The
server validates the project/run/review/artifact/version. It creates a durable
draft only: no model interpretation, decision, retry or Worker dispatch occurs.
An exact saved-draft acknowledgement is required before clearing local input;
errors, uncertain responses and conflicts preserve it. No automatic retry.

The existing single composer then handles multi-turn discussion and ordinary
questions. Sending a question does not approve the pending interpretation.
The original separate Confirm changes action remains the only commit path.

Visible states come from persisted Intent Commit status: discussing,
ready_to_confirm, committing, ambiguous, stale, committed or cancelled. Unknown
values remain unknown. A recorded change decision is not proof of successful
execution or acceptance; inspect the resulting exact-version output and evidence.
The pending status remains visible when switching back to ordinary messages.

No arbitrary mid-worker cancellation, automatic replanning, new change queue,
natural-language approval or cross-task execution control is introduced. When
no exact review capability exists, the UI says so instead of claiming a change
was saved for future execution. Historical work never exposes the new action.

## Verification

Tier 4 frontend handoff over existing Tier 3 server contracts. Added pure
capability/acknowledgement tests, composer handoff/failure/dedup tests and a real
HTTP/fake Worker browser case asserting byte-exact text and unchanged workflow,
Worker jobs and decisions across handoff/reload. Existing confirmation and
conversation tests remain. Local static review and diff checks only under the
CI-only instruction; current-head CI and browser evidence remain required.
No real model or real project workspace was exercised by this implementation.
