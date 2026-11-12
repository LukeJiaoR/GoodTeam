# Known terminal mixed-batch feedback

Incremental #686 items 2 and 4. The original continuation now captures an
attributable batch containing blocked tasks when every invocation is proven
terminal. The same Manager discovery outbox receives each successful companion,
known failure, partial text or missing report. Captured write results remain
unaccepted output. An observation is not review, delivery, permission to retry,
or proof that the overall project succeeded.

## Existing boundaries retained

- Original continuation authority, task intents, run/job/dispatch ownership,
  protected result verification and report bytes are checked before and after
  Manager judgment. No unknown/live invocation is released or reissued.
- A same-continuation verified-delivery seed is still required. All-successful
  review batches retain the existing delivery path; this increment targets
  blocked/mixed feedback, not replacing acceptance.
- Receipt history is keyed by original batch plus effective dispatch. Original
  task and batch states remain unchanged. Inputs are consumed only on durable
  decision/publication, so publication replay does not issue another model call.
- Manager may select a new scoped diagnostic or other authorized follow-up via
  the existing queue. Dependencies must still name verified delivered tasks.
  The host does not automatically retry the old invocation or mark it done.
- A no-followup decision leaves the continuation blocked while any historical
  observed tasks remain unresolved in the current ledger, including after a
  later diagnostic returns. Explicit cancellation or verified delivery resolves
  that status. A Human question retains the existing waiting behavior.

## Context freshness

Reports retain their original source commits; current decision revision is
separate. New discovery packets freeze the grant's task intents and execution /
acceptance facts. Unrelated tasks outside that grant no longer invalidate a
reserved decision solely by changing the global backlog version. Changes to
covered tasks, source delivery, authority or pending report still block stale
publication. Older already-reserved packets retain their old revalidation rules.
The current backlog and execution availability are checked again by the original
publication/queue/dispatch path; this does not authorize concurrent unknown work.

## Remaining limits

After feedback releases its pending pointer, explicit failed-task recovery can
now reattach that same blocked continuation when the retained observation still
matches the original batch, effective dispatch, report digest, authority, task
intents and proposal revisions. This still requires explicit retry confirmation,
charge acknowledgement and `resume_continuation`; observation never starts a
retry itself. Another pending batch or frozen/unjudged input cannot be displaced.
Old recovery command replay does not reactivate a subsequent Stop. History is
retained, and later attempt observations use their own dispatch identity.

No-seed feedback decisions, running-job management, and real Runtime/combined
Loop evidence remain open work. This is not full #686 or #419 acceptance.
`tests/test_feedback_recovery_handoff.py` checks the production recovery handoff
with fixture stores/terminal proofs/dispatch, not a paid or native Runtime run.

`tests/test_project_batch_feedback.py` supplies one deterministic contract with
mixed results, publication interruption, no duplicate call/start, unknown/changed
result rejection, late no-followup resolution and unrelated-versus-authorized
Context changes. Storage, model, dispatch, and accepted-delivery seams are
fixtures, not evidence of paid Provider or isolated Runtime execution. CI stays
paused; this change neither deploys nor makes paid model calls.
