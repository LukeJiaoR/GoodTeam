# Recover a committed waiting decision after acknowledgement loss

Outcome: the existing automatic loop keeps waiting, rather than becoming blocked,
when its null judgment was committed successfully but the caller lost the save
acknowledgement. The failure was reproduced on both initial and already-authorized
follow-up tasks; the prior candidate wrote `blocked` despite a committed wait.

The original null-result mutation now retains its exact packet digest in
`discovery_last`. At that same save point only, an OSError triggers a read of the
original session. Recovery requires the same continuation/grant digest, cleared
pending decision, exact packet/trigger/source/status/rationale and all consumed
input receipts. Only then is the already-persisted state returned. No new model
turn, refund, replacement packet, duplicate task or new state machine is added.

This is not a broad "reason is waiting" exception handler. An older saved wait
cannot hide a failure before a new decision commits. A newer changed pending
input fails the exact match. Existing saved Stop/answer state is preserved, not
rewritten into an earlier active state. Older stored decisions without the new
packet field do not acquire fabricated evidence.

Base: saved local delivery-backlog candidate, archive SHA256
`94cfe0e7605fc3aeec7310797aef5164fe12234e143a194dc4e3a78aeef1ce93`.
This is a small dependent delta, not a patch against main.

Tests use the real local ledger/grant/continuation storage with injected Manager
and delivery evidence. A committed write then loses its acknowledgement. The
scenario continues through changed facts and the original batch entry. No real
Provider/Pi, paid call, Git acceptance, production deployment or remote publication
was performed. #419 remains the real product acceptance record.

Backend full suite: 464 passed /32 failed /5 errors /20 skipped; all 37 failure/error IDs match base461. Final focused file: 4 passed, including one extra before-commit counterexample added after full collection. Independent review: no blocker, 11 targeted passes including original reproduction. Ruff passed. Frontend unchanged.
