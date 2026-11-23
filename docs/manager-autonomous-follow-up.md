# Bounded Manager progress questions (#700)

New project-scope starts in Work include running-period observations and one
bounded Worker progress question. Manager decides whether to use them. This uses
the same project settings and usage records. The `discovery.worker_follow_up_limit`
field can still set a total of 1–10 for callers configuring the API. Existing
grants retain their original observation and follow-up settings.

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
