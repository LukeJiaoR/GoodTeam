# Keep remaining authorized work visible after one delivery

Outcome: a prior accepted delivery does not end a grant that still has blocked
or otherwise unresolved authorized tasks. The existing loop can reconsider
changed authorized task/dependency facts without buying duplicate judgments.

The prior local candidate was reproduced marking the grant `completed` after a
Manager returned null for a delivered first task while a second authorized task
was still blocked. With the same delivery receipt already consumed, later
original task facts were not a new input either.

For newly captured packets whose existing grant permits backlog judgment, the
all already-authorized task/dependency facts are now attached as `authorized_backlog_context`.
This separate non-delivery receipt enters the original consumed-input ledger and
is revalidated before and after judgment. Replaying the original publication excludes only this pending decision’s own creation references, not earlier authorized follow-ups. The verified delivery remains the
verified delivery; no acceptance is synthesized or replaced. No timestamps,
question publication or unrelated backlog cards enter this wake receipt.

The packet also records unresolved authorized task IDs, including authorized
follow-up tasks. It uses the existing scheduling-settlement rule for observed
read-only results, which is not an engineering-delivery claim. A null judgment
with unresolved tasks remains active with `backlog_judgment_waiting` and the
existing backlog waiting marker. Ready original tasks then use the existing
selection/queue path; unanswered questions do not create a new global gate.
Canceled work is neither delivered nor made eligible.

Old grants without backlog authority and already-frozen pending packets retain
their original contracts. Unknown pending invocations are not replaced with a
new packet, refunded or retried. The existing budget and authorization digest
remain authoritative. There is no new state machine, dispatcher, permission or
model call outside the original loop.

## Evidence boundary

The representative regression uses real task/session/grant storage and the
original continuation/discovery/queue code. Manager output and the prior delivery
are fixtures; no real Git acceptance or Worker execution is claimed. It covers
null-with-unresolved work, unchanged facts, changed task and dependency facts,
ready work entering the original batch boundary, and dependency drift during the
call refusing publication while preserving the reserved decision.

Base: local running-Human-board candidate, archive SHA256
`a76b4d9b173b93e5baa2baad96d574733d14446d543cd53ae2e519ccd94cfe0c`.
Apply this delta only after that candidate chain. No push, merge, real Provider/Pi
call, deployment or security change is performed. Full acceptance remains #419.

Final backend: 32 failed, 461 passed, 20 skipped, 5 errors in 76.25s (0:01:16). All 37 failure/error IDs exactly match the base (457 passed). Independent reviewer ran 12 targeted cases and the real-ledger publication-receipt probe; no remaining blocking finding. Ruff passed. Frontend is unchanged in this slice.
