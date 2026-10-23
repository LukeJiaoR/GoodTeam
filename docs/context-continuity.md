# Bounded conversation and task continuity

The project Manager, task workflow and Worker handoff share explicit, versioned
context projections. They reuse the existing session and `AgileDevState`
authority; there is no second editable TaskState, extra model call, automatic
approval, expanded retry budget or background execution loop.

## Conversation compaction

Every session mutation persists `conversation_continuity.v1` under the existing
session lock and atomic replacement. The full exact timeline remains unchanged.
The projection hashes its source, counts older events, and retains at most 20
source-linked historical excerpts. Opening context and the selected task's
historical statements take priority, then recent historical statements and
transitions. Unselected older events and truncated text are explicitly counted.

Manager inputs use the same helper in task intake and running/reference-task
conversation. They receive at most 40 recent events, the historical projection,
and up to eight deterministic query matches from older omitted events. Each
excerpt is capped at 600 characters and includes source index, message ID,
run, actor and SHA-256. Query recall supports word matching and CJK bigrams; it
does not perform semantic interpretation. Reference-task context excludes other
run-bound messages while retaining project-level discussion.

Accepted running-task messages retain their run identity before runtime/model
inspection. A failed or interrupted request therefore stays attached to its
original task on retry; if that task has ended or been replaced, the existing
read-only historical route is used rather than silently retargeting the message
or turning it into new-task intake.

This is extractive compaction, not a guarantee of perfect semantic recall. The
model is told that excerpts may omit facts and that old statements are not
current requirements, approval or execution evidence. New task drafts still
resolve only exact user IDs from the existing intake boundary; summary/recall
never widens that authority. Missing, stale or edited cached summaries are
rebuilt from the full source in memory; reads do not repair or rewrite files.

## Persistent task state and handoff

`task_continuity.v1` is a bounded derivative of the existing workflow fields:

- Project/run identity and SHA-256 of the complete relevant source fields
- Recorded objective, labelled with its workflow-state origin; the original
  contract and delivery standards stay exclusively with the existing verified
  task-input reader rather than being reconstructed from mutable fields
- Current/next node and the saved transition identity/status
- Approval states, recorded change feedback and blockers
- Artifact references, without claiming successful validation or production
- Existing task-lock identity, labelled as an unverified reference until the
  established task-input reader verifies its source

Current-state and checkpoint writes refresh this projection within their same
write; old checkpoints retain their original view. Legacy and stale/corrupt
derived copies are rebuilt from authoritative fields on read. Truncation is
field-labelled and never deletes original fields. No live Team configuration,
other project's state or unconfirmed discussion is substituted for a task
contract.

Manager run snapshots and all existing workflow-role context builders use the
same projection. Each new role input receives a source-bound read-only handoff;
role-specific human messages retain their advisory label. Existing job creation
captures it in the attempt's context. Later state changes do not rewrite an
already prepared attempt or claim to interrupt/replan a running Worker. Existing
memory retrieval, correction invalidation and memory writeback are unchanged.

## Verification boundary

This is Tier 3 control-plane plus Tier 4 browser-visible work. Deterministic
tests cover long history, exact sources, truncation, query recall, source edits,
duplicates, historical scope, legacy/stale cache reads, state/checkpoint
refresh, fresh-process recovery and immutable cross-role materialized inputs.
Existing intake, approval, workflow, goal continuation and memory suites remain
required regressions. Separate UI receipt/reading checks use isolated fake
Workers and require a real local browser; unit/DOM tests alone do not satisfy
that browser gate.

No paid or real-model execution is needed for these checks. They do not complete
the deferred real multi-task/multi-project acceptance, provider memory-read
verification, shared Team, Skill lifecycle or mobile scopes.
