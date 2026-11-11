# Planner through the existing discovery and feedback path

New continuation authorizations may explicitly include `discovery.allow_planning:
true` in the existing `start_project_continuation` action. Omitted flags remain
omitted: an existing implementation or investigation grant does not gain Planner
permission on upgrade or replay. This development change does not modify any
user's current grant, acknowledge charges, or start a Runtime.

When the authorized Planner capability is available, Manager may select
`task_type: planning`. The host maps it to `role: planner`, `read_only`, no write
domains, through the existing create-once task ledger and queue. Direct
implementation remains possible, and planning is never a mandatory phase.
Unavailable Planner or unsupported Skill Runtime retains the existing rejection;
no fallback Manager or legacy worker impersonates Planner.

Settled Planner output uses the same read-only observation and discovery outbox
as Researcher. Successful output, failed partial output, missing text and empty
text retain their original task/job/attempt and report source revision. Planner
receipts include `role: planner`; old Researcher receipts stay byte-compatible.
The next Manager input combines these receipts with current Context and HEAD.
Plans are untrusted professional advice, not authority or accepted delivery.
Follow-up must cite the receipt but cannot depend on an unaccepted Planner card.
No plan is automatically expanded into an execution batch.

Verification uses the real authorization API/session and task ledger plus the
shared report-to-decision fixture, covering replay, Stop, unknown termination,
partial failures and source/current-HEAD separation. It is not live Planner
Runtime acceptance. #445/#440 limits remain, CI stays paused, and no paid model
was called. Discovery still requires a verified seed delivery: no-seed continuation
and #419 real continuous project acceptance remain outstanding. #696 stays open
until real Planner→Manager execution is verified under supported conditions.
