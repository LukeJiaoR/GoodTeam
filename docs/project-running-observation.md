# Running observation candidate on the original continuation

The existing authorized continuation tick now observes its own confirmed running
batch after original authority/scope checks. It stores one latest semantic
snapshot on that same grant, binding grant, authority, batch and dispatch attempt.
There is no separate execution ledger, dispatcher or timer.

Observation changes are candidate inputs only. Heartbeat timestamps and control
acks do not trigger a new candidate; changes in scoped job state/local Runtime
health do. The latest receipt is bounded and replaces the previous snapshot, not
an append-only audit. A race with Stop, recovery or grant replacement prevents
publication to the wrong attempt. Normal and running Manager conversations can
read it with explicit current-binding and remaining-budget labels.

No Manager call is scheduled, reserved or spent by this increment. No Worker
control is sent. Budget exhaustion does not kill already-running work. An eventual
automatic consumer must reserve from the existing Manager-call budget, revalidate
authority/current attempt and run through supported resource admission. Existing
idle-only planning is unchanged, not repurposed as a running-period dispatcher.

This is a narrow wakeup-input contract for #686 and #700, NOT an implemented
autonomous wakeup. Real Worker reply attribution, structured observation decisions,
automatic model consumption, no-seed feedback and the #419 continuous path remain
unverified/unimplemented. A saved candidate or RPC ack never means Worker reply or
task completion. Tests use local session persistence and controlled tick fixtures;
no paid Runtime is called. CI stays paused.
