# Bind Telegram decisions to displayed review material

## Problem and scope

An undecided Telegram session retained its project and gate but did not pass its
run or artifact version to the shared approval boundary. A reply to an old card
could therefore approve a replacement run at the same gate. This predates #486;
the note-type and blocked-result fixes in that PR did not close this gap.

This change is limited to existing workflow approval adapters. Operator-task
approvals keep their separate exact-head contract. It does not change rationale
policy, CI gates, runner permissions, or production deployment.

## Contract

- Notification creation records the project, run, review gate, artifact key and
  SHA-256 version of the actual bytes read for that card. A concurrently changed
  or unavailable artifact cannot acquire the current version by default.
- Text commands, v1 decision callbacks and legacy callbacks forward the stored
  target to the existing guarded server boundary. The same project, chat and
  card message must match the persisted session; a polling default project is
  only a fallback and does not override an explicit selected card.
- Missing, superseded or internally inconsistent bindings fail closed. Current
  run/material checks remain in the shared decision boundary, so an intervening
  state change cannot be hidden by the adapter's earlier read.
- When material changes, refreshing creates a new session/message and retires
  the old one. Reusing its message ID would allow a queued old reply to approve
  unseen replacement material. Unchanged material can keep the current card.
- Rejected decisions do not consume chooser intentions, mark sessions decided,
  or write review-note artifacts before the shared boundary accepts a decision.

## Existing cards and recovery

Previously persisted cards have no reliable artifact-version binding. They are
not automatically migrated by looking up the newest artifact. Their mutation
attempts are rejected with existing stale-review guidance. Use current Review,
or request `/gt approvals` to display a newly bound card before replying with a
reason. This command now reads the gate's artifact when no explicit path was
provided, using the same configured/fix-profile gate resolver as web Review
(including code_review -> implementation_handoff and qa -> qa_report). Old
cards may still be readable but are not authority for a new run.

Button-only approvals continue to obey the shared positive-recommendation/reason
policy; this change does not manufacture approval rationale. Synthetic reasons
and mocked Telegram transports are test-only.

## Verification scope

The focused suite uses actual temporary state, artifact hashes, notification
construction, guarded decisions and ledger readback. Cases cover old run/same
gate, same-run material replacement, inconsistent target identity, cross-chat or
message callbacks, missing migration bindings, chooser rejection without writes,
refresh/new-card recovery, a concurrent replacement before the shared decision,
and current approve/reject/request-changes text decisions with exact audit and
no duplicate ledger decision.

Fix-profile regression cases cover first display without an explicit artifact
path and a real `/gt approvals` refresh of an existing automatically sent card.
Unchanged code-review and QA artifacts retain their session, message and exact
version binding, and a subsequent text decision reaches the guarded boundary.

Existing adapter unit tests explicitly carry structural bindings while spying
on decision calls. Those spies are not treated as shared-boundary acceptance.
The new callback forwarding tests separately verify all decision actions pass
exact expected fields before session/feedback writes.

This is model-free local evidence, not real Telegram delivery or provider-run
acceptance. Full local backend and remote exact-SHA CI results must be reported
separately with their actual environment limits.
