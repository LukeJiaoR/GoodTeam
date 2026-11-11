# Budgeted running-observation advice contract

The existing continuation tick accepts an internal observation_runner injection.
Production supplies none: supported running-period Runtime admission is not wired,
and existing idle-only planning must not be relaxed. Tests supply a mock runner.
This is contract implementation, not a claim that live autonomous observation runs.

With a runner, the same session atomically binds the original grant/current
observation and reserves one existing manager_calls unit before invoking it.
It never increases max_manager_calls. Duplicate/reentrant observations cannot
spend twice. An unresolved reserved/unconfirmed call blocks further consumption;
there is no automatic retry or budget refund. A process crash remains reserved
and requires explicit reconciliation; there is no recovery UI in this increment.
Pending normal planning/discovery is not entered concurrently by this adapter.

The response may only advise wait or needs_attention, with a short rationale and
IDs from the captured invocation evidence. No task changes, control commands,
retry, delivery, reply attribution or additional permission fields are accepted.
Stop/attempt/authority changes make late results stale, without reviving work.
The original Manager conversation receives the labeled advice, not execution.

A future real runner must retain Runtime permission/capacity/isolation checks
and idempotent captured inputs. The current contract's budget reservation alone
is NOT that admission. No production consumer is enabled here.

Pi control acknowledgements and generic session entries do not establish a
reply-to-control relationship. Worker reply attribution therefore remains false;
actual provenance requires supported Runtime correlation, not temporal guessing.
#686/#700/#419, no-seed feedback and real project-loop acceptance remain open.
CI remains paused; no paid model is called by development verification.
