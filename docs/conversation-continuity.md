# Conversation continuity

Work keeps one composer across ordinary questions and version-bound Request changes discussions.

## Pending and running work

A pending draft blocks draft mutation/confirmation, not messages. Follow-up messages use a
conversation-only path with the pending exact request, revision, pending change and recent
conversation as context. The existing append/replace/keep/withdraw actions remain the only
way to resolve that pending change.

Once a task is confirmed or running, questions read a server-owned snapshot of the current
run, blockers, approvals, artifact references and persisted change discussion. They do not
reopen the original draft, start another task, run preparation, retry a worker or decide a
review. Assistant replies identify their run/proposal scope. Completed/rejected/archived
proposals still use the existing archive-and-next-task path.

Project-wide Team requests retain the existing proposal/explicit-confirmation service during
a run. Task-only preferences and messages while resolving a pending task draft cannot mutate
Team. Model-provided authorization is never execution authority.

## One input, explicit scope

Request changes opens the existing durable Intent Commit and focuses the bottom composer.
The composer shows its change-discussion context. Sending adds the exact new paragraph to the
existing request; earlier constraints are not replaced by a model summary. A further correction
to a ready interpretation first invokes Keep discussing, then the existing discuss endpoint.
It never invokes Confirm changes or an approval endpoint.

The Message control temporarily uses that same composer for read-only questions about the
current work. The change interpretation stays pending. Ordinary and change-specific unsent
text are kept separate by intent identity. Errors preserve input; duplicate submissions are
blocked; stale/ambiguous/committing intents never receive new discussion mutations. Reload
restores durable intent text and the existing exact-target confirmation boundary. Unsaved
keystrokes remain browser-local and are not promised to survive reload.

## Deliberate limits

This change does not implement arbitrary mid-worker cancellation, automatic replanning,
parallel task intake or natural-language approval. Running-task scope changes still go through
a current review's Request changes/Intent Commit boundary. Discussing an adjustment before
that boundary does not change the worker's instructions. Fake/builtin responses display the
recorded state; they are not evidence of real-model comprehension or verified delivery.

## Verification

This is Tier 4 browser-visible behavior with Tier 3 message/state boundaries. Focused coverage:
`tests/test_assistant_conversation.py`, the existing draft/execution regression tests,
`ConversationComposer.test.jsx`, `conversationRouting.test.js`, and the Work business-loop
browser acceptance. Full backend, frontend lint/build/tests and isolated browser evidence are
required before merge. No real project workspace or deployment is used by this change.
