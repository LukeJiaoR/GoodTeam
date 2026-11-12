# Recover a lost running-period Manager response (#700)

The existing authorized continuation tick can reconcile an uncertain observation
from its original **completed** local Manager job before considering new evidence.
This closes the case where the job persisted its result but the caller lost the
return value or stopped before saving the judgment. It reuses the original
Manager resource slot, job identity, session receipt and allowance.

Reconciliation reads only the original job, terminal status, captured input and
result through bounded, no-symlink file reads. Job, project, phase, idempotency key
and captured input digest must match the durable receipt; terminal identity must
match the job. Both job and status must say completed. The returned JSON must
pass the same advice/follow-up contract as a normal response.

Publication rechecks the original grant, current attempt, latest observation,
Human context and authority. Completed but outdated advice becomes `stale_result`,
not an executable follow-up. Stop is never reversed. A replaced grant cannot
authorize a model call from the old tick. The original Manager allowance is
neither charged again nor refunded.

Missing, running, failed, corrupt metadata/input or contradictory evidence stays
uncertain. A completed job's readable but invalid answer becomes `invalid_response`,
as on the normal return path; it cannot execute a follow-up.
No heartbeat heuristic, transport retry, new job, queue resend, state reset or
completion assumption is introduced. A valid recovered response can release the
unknown-observation guard, after which the existing scheduler may evaluate new
evidence only within its original authority and remaining budget.

This is not Pi control-to-reply correlation and does not change the Worker Loop.
Local fixture coverage exercises durable job/status/result reads, duplicate
ticks, foreign status/input, symlinks, attempt changes, Stop and grant replacement.
Real Pi/provider execution and full project-loop acceptance remain separate in
#700 / #419; offline fixtures are not proof of those paths.
