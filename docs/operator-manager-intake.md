# Operator intake convergence (candidate)

New `goodteam task <issue-url>` and Telegram `/gt task` share the existing
GitHub issue adapter. They retain one source snapshot in the project's Manager
session and submit one normal Task Assistant message. The ordinary Manager
interpretation, task queue, confirmation, dispatch pool, results and continuation
remain authoritative. The adapter neither directly starts Workers nor merges,
deploys or changes the project environment. A project uses one consistent
environment, determined and arranged in advance by Planner. Shared environment
changes go to Planner; no extra Manager approval gate is introduced. This adapter
does not add per-Worker or parallel environment versions. Environment changes are
not implicit input-adapter or arbitrary Worker side effects.

External issue content is explicitly marked untrusted context, not authority.
Source URL, revision, repository, project root and exact snapshot are retained.
Repeated URLs reuse the accepted snapshot and message identity. Failed or
uncertain Manager requests use the normal retry/reconciliation path instead of
silently purchasing another turn. A changed issue is not automatically a new
execution grant.

Historical in-flight, waiting and ambiguous Operator work retains its original
execution identity and recovery path. Unstarted queued items are transferred
only under the old queue lock, after confirming no attempt, execution identity,
operation intent, PR or decision evidence. Repository/root and captured source
revision must still agree. The old item becomes `transferred` with a Manager
receipt, not `completed` business work; failed migration never falls back to
starting the old Builder. A changed source or uncertain history requires explicit
reconciliation. Repeating a transferred URL reaches the Manager source adapter.

Intake captures source receipts without calling a model. The existing Goal /
project continuation service consumes pending inputs through normal Manager
request accounting; it adds no scheduler thread or Worker pool. Each project
error is isolated and service Stop is checked before subsequent turns. Existing
failed/uncertain requests are not automatically retried. Sources retain each
snapshot digest/version separately. Later versions become read-only context
updates rather than additional execution requests, while the first intake uses
the ordinary task proposal/confirmation route. Exact source history remains
available; the current Manager memory projection includes the latest 20 sources
and discloses omitted entries.

Only AST/static review applies to this local candidate. No test suite, model,
Worker or external service has been invoked; runtime acceptance is not claimed. Full
convergence and project acceptance remain open in #381 and #419 respectively.

Legacy development preflight now checks backend/candidate readiness, not the
existence of CI contexts. Known pre-execution pauses caused solely by the removed
CI prerequisite resume through a fresh preflight receipt; execution/ambiguous
effects are not reset. PR-stage CI observation still resolves current protected
checks and combines them with configured requirements. Branch protection and
merge enforcement are unchanged; this does not fabricate CI success.
