# Explicit project-chat Worker control

This #700 increment connects the original project chat to the existing supported
Pi persistent-session control protocol. It is an explicit user command, not a
new model-selected action or autonomous supervision scheduler:

```
/worker JOB_ID steer Additional information within the original task
/worker JOB_ID follow_up A question for this invocation
/worker JOB_ID abort Explicit reason to request termination
```

The exact existing job must be in this project's current invocation records and
current dispatch attempt. Instruction controls currently require a confirmed
project task batch; legacy workflow-only instruction control remains unsupported. Historical jobs, other projects, stale task references
and unsupported Runtimes are rejected. Ended continuation authority cannot be
revived by steer/follow_up; an explicit abort can still ask a live process to stop.
Pi's original runtime/input admission and liveness checks remain intact. In
particular, `skill_runtime_not_connected` is not downgraded into ordinary access.

The existing assistant request ledger binds project/message ID to exact command
bytes and a stable control ID. A duplicate does not resend; changing its job or
text under the same ID is rejected. Runtime waits happen outside the conversation
lock. Final scope/Stop admission and fast enqueue are serialized with the
original session mutation; a Stop recorded first rejects new instructions, while
a previously admitted control may finish recording its acknowledgement without
reviving Stop. A failure/interruption with uncertain delivery is not automatically retried.
A new user message is a new explicit command, not a replay of the old request.

Acknowledgements are preserved in the original conversation and both ordinary
and running-task Manager Context. `queued` means saved, `rpc_accepted` means
Runtime accepted the request; neither proves Worker execution, reply, termination
or task completion. Stop is not reactivated by a late acknowledgement. This
increment does not poll or refresh stored acknowledgements automatically.

## Remaining scope

This does not yet implement autonomous Manager selection of control actions,
automatic wakeup/judgment after a Runtime reply, or attribution of later Worker
messages to the individual question. Those remain #700 work. Running-project
judgment (#686 item 1), richer follow-up decisions (item 7), no-seed feedback and
real continuous-project/Runtime acceptance remain open. No CI activation,
production deployment, paid model calls or security setting changes are included.

`tests/test_manager_worker_control.py` exercises the real chat routing and
control service with fixture store/Runtime responses: exact job, duplicate and
changed identity, wrong scope, old attempt, Stop, unsupported backend, queued
receipt and protected-input refusal. It is not a real Pi/model execution test.
