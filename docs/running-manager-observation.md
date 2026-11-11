# Running-period Manager advice consumer

A new continuation discovery grant may explicitly set
`allow_running_observation: true`. Existing grants remain unchanged. The existing
continuation tick then invokes the configured read-only Manager Worker for a
changed, bound running observation, consuming the original Manager-call budget.
It returns only `wait` or `needs_attention` advice, not autonomous Worker control,
reply attribution, a retry, task completion or delivery acceptance.

All existing Manager turns share a project-level nonblocking Runtime slot.
Observation acquires it before reserving budget, so busy consumes no model call.
The session mutation lock is released before Runtime execution so Stop remains
available. Ordinary CLI Manager jobs capture a single attempt, disabling transient
automatic retries. The configured Runtime's existing read-only and Skill gates
remain unchanged; no Reviewer admission is reused or isolation gate disabled.

The reservation stores its original Worker job ID, logical idempotency key and
input digest before launch. A lost reply remains unconfirmed and blocks another
automatic judgment, even if Worker evidence changes. It is not refunded or
retried. Automatic terminal reconciliation of that uncertain job and automatic
Worker control/reply attribution are not implemented by this increment.

Offline tests exercise the production consumer, durable pre-launch binding,
resource contention, once-only accounting, uncertain results, Stop and unchanged
old grants. Transport is simulated. No live model call, isolation acceptance,
CI run or deployment was performed. Full continuous-project acceptance remains
under #419.

Stop and the actual `Popen` are ordered under a short session mutation: Stop
winning forbids spawn; spawn winning records transport start before releasing the
lock. The lock is not held across process communication. This first production
consumer accepts only the ordinary Codex pipe transport; Skill/session/PTY paths
without that handshake remain unavailable. Persisted observation jobs cannot be
launched through an unguarded generic Worker store. A post-spawn failure still
retains the pre-bound invocation and unknown-result block, never a refund.
