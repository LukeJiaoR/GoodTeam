# Bounded Manager progress questions (#700)

The existing project-continuation authorization form can opt into running-period
Manager observations, then separately authorize 1–10 total Worker follow-ups.
Both are off by default. The explicit `discovery.worker_follow_up_limit` field is
the opt-in and is included in the original authorization digest. An omitted
field grants no controls; existing grants never upgrade. Changing any form
option invalidates its confirmation. Stop/settle/re-authorize rules are unchanged.
The form discloses both Manager allowance usage and additional Worker model cost.

The existing tick consumes an optional Manager `follow_up` proposal through the
original `WorkerJobStore.session_control` queue, targeting the exact current Pi
job and dispatch attempt. This is a progress question within the original task,
not a new assignment. No steer, abort, wider scope or completion action is added.
Before enqueue, the original grant, task intents, current batch/attempt, Human
snapshot, Runtime input admission and live process are checked again.

Each reservation is durable before enqueue and consumes one follow-up allowance.
Each job can receive at most one automatic question, including failed/unknown
attempts. A crash or lost response never causes an automatic resend or refund.
Later ticks may read the same persisted control identity and acknowledgement,
including controls from older observation snapshots. Reads do not wait for an
acknowledgement and do not enqueue. Other tasks remain subject to their own
scope, resource and allowance checks.

Manager Context distinguishes `queued`, `unconfirmed`, `rpc_accepted` and failure.
`worker_reply_attributed` and `execution_completed` stay false: an acknowledgement
is not a reply. The ordinary Pi event protocol has no universal control request
ID. Stable job/session/entry output provenance and exact reply correlation remain
separate work; this increment does not guess them from the latest assistant text.

Offline tests cover real session storage and Pi queue identity, lost enqueue
responses, duplicate suppression, old grants, altered scope, foreign targets,
and UI confirmation invalidation. Manager responses are fixtures; no live model,
Pi binary, paid call, production deployment or full-loop acceptance is claimed.
