# Recover or abandon an undelivered project batch

The Work page now exposes an explicit recovery action for a confirmed batch
whose Worker failed. It uses the original Task Assistant queue, LocalTaskStore
cards and `dispatch_tracks` pool. A recovery command is retained with the batch's
confirmation; it is not another task ledger or scheduler.

## Retry only observed failures

The batch evidence link shows the current dispatch and a versioned `recovery`
projection. A retry is available only for listed task IDs with an original,
durable Worker return. Real CLI Workers need an integer process exit code in
their matching original job/status records. A stale heartbeat, timeout without
an observed exit, missing status, or an outer dispatch error is insufficient.
An unknown or still-running Worker keeps the project execution slot and cannot
be replaced or abandoned through this action.

Confirm the displayed failed-task scope and additional Worker charges. Successful
siblings retain their original jobs, result commits, receipts and workspaces.
The selected failures receive new job identities with the original failed-job
lineage and observed failure summary. Every retry starts from the same retained
batch baseline, even if the source checkout has since changed. The composite
result records each job's own `dispatch_run_id`, then integrates those results
into a fresh candidate. The original report is never rewritten.

Use the same command identity when a response is lost. Replay repairs unfinished
task projection and resumes only that admitted attempt; it never allocates a
replacement attempt or repeats the continuation consent. Once transport has
been reserved, an uncertain outcome remains blocked. Existing terminal jobs may
reconstitute a missing report without starting new Workers.

## Resume the existing continuation, or stop here

The retry form can separately resume the **same blocked continuation** when it
still owns this batch. Its original task intents, Team/model authority, batch
limit and used Manager/batch counts remain unchanged. The retry's additional
cost is explicitly acknowledged; it does not reset iteration budgets. A stopped
or replaced authorization cannot be resumed by this flag or by replaying an old
request. An already admitted attempt may finish after Stop, but no later batch
is authorized by that completion.

After the recovered batch succeeds, the existing quality and landed-delivery
gates still apply. Quality preparation binds every effective result's actual
source dispatch/job/commit. Old check or review receipts cannot certify a new
candidate. Quality remains opt-in per successful batch as described in
[project-batch-quality.md](project-batch-quality.md); this action neither selects
a check command nor grants Reviewer/correction authority. An existing protected
quality requirement cannot be reset by recovery. A candidate is `done` only
after its exact reviewed version has actually landed and delivery is confirmed.
Only then can an active continuation select its next authorized batch.

## Explicitly abandon ended, undelivered work

Abandon cancels the original undelivered task cards, closes their queue entries
and clears/stops a matching pending continuation. It preserves the reports,
partial candidates and all Worker evidence. Successful sibling output remains
inspectable; abandoning does not claim it was delivered. No runtime configuration
or model charge is needed to abandon terminal work, so changed Team/model
settings cannot prevent cancellation.

This is unavailable while any original Worker, quality command, Reviewer or
correction has an unknown execution outcome. Protected terminal receipts are
checked separately from product labels; a prepared Reviewer may already own a
runtime route and is not assumed stopped. A quality configuration with no started
runtime can be abandoned. Invalid output from a proven stopped Worker can
prevent reuse while still allowing abandonment. Integration conflicts with all
Workers stopped can likewise be abandoned; this feature does not invent an
integration fixer. Abandoned batches retain evidence but cannot advance quality
or accept delivery.

## Existing HTTP surface

`GET /api/projects/{key}/iteration-batches/{run_id}` returns the current report
and recovery projection. During an active dispatch it returns the known batch
identity and unknown-outcome reason, even before the report exists. Reading this
view does not launch Workers, create Git objects or rewrite evidence.

`POST /api/projects/{key}/task-assistant/actions` accepts:

```json
{
  "action": "retry_project_batch",
  "run_id": "task-batch-FULL_DIGEST",
  "expected_recovery_version": "sha256:CURRENT_EVIDENCE_DIGEST",
  "idempotency_key": "owner-retry-command",
  "retry_task_ids": ["DEMO-2"],
  "confirmed": true,
  "billing_acknowledged": true,
  "resume_continuation": false
}
```

When resuming, send `resume_continuation: true` and the exact displayed
`continuation_id`. Retry returns HTTP 202 with the original `run_id`, new
`attempt_id`, source `dispatch_run_id` and current session. For abandonment use
`action: "abandon_project_batch"` with only run ID, evidence version, command
identity and confirmation; it returns HTTP 200. Stale evidence, changed task
scope and unknown outcomes return HTTP 409 without admitting new work.

## Local evidence

The representative test `tests/test_project_iteration_recovery.py` uses real
offline CLI processes and real Git isolation: A succeeds, B exits 7, only B is
retried, their combined candidate passes a real check and protected independent
review, delivery unlocks C, then C's terminal failure is explicitly abandoned.
The same scenario checks live/unknown refusal, idempotent retry, immutable
successful-sibling evidence and that replay after Stop does not revive the
continuation. The Manager and native model responses are protocol fixtures;
this is not a paid-provider acceptance run. Automatic GitHub CI stays paused.
