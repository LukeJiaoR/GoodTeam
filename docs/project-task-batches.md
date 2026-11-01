# Running a project task batch

Manager compares existing project cards and proposes an iteration. In Work,
choose **Add selected tasks to existing queue**, inspect the selected work,
then **Confirm and run selected tasks (uses model)**. Confirmation authorizes
the configured Workers for exactly those queued revisions. It does not
authorize merging or deploying their output.

The Task Assistant uses the existing `dispatch_tracks` pool, with at most ten
simultaneous Workers and queued overflow. Tasks can share a Worker role; each
task has its own track and Worker job identity. Concurrent writers need
explicit disjoint `write_domains`. Each Worker runs in a retained Git copy;
captured changes are checked against the approved paths before fan-in. A
read-only task retains the existing read-only runtime policy and must produce
no repository changes. A batch cannot overlap a live Workflow or bounded Goal.

The existing queue saves confirmation and runtime inputs before execution.
The original project cards receive the batch/dispatch/job IDs. Retrying the
same queued revisions reads the existing dispatch request and result receipts;
it never creates a new execution identity. Interrupted Workers remain blocked
until their original outcome can be reconciled. Retained output is preserved.

Successful tasks move to **review**. Failed or unverifiable tasks move to
**blocked**. A partial failure or file collision blocks batch integration;
successful sibling outputs remain inspectable. The source checkout is never
the Worker cwd or integration target. The candidate path and commit in the
batch result identify the retained fan-in for independent review and explicit
delivery confirmation. Worker success alone never marks project work done.

The API action is `start_queued_task_batch` on the existing project Task
Assistant actions endpoint. It takes `proposal_ids`, an `expected_revisions`
object mapping each proposal ID to its revision, and `confirmed: true`. HTTP
execution runs in the background. Authorized project continuation calls the
same `run_queued_iteration_batch` service in the background so Stop stays responsive. The session's batch
metadata is a confirmation/result projection; the existing task ledger and
dispatch receipts remain authoritative for work and execution respectively.

Protected Skill execution keeps its existing protocol: a project batch refuses
it when ordinary per-task Git isolation cannot be established. Legacy Workflow,
Goal, Operator and Direct Dispatch history remains readable. GitHub automatic
CI remains paused; batch execution does not enable or dispatch it.

## Local verification

`tests/test_project_iteration_execution.py` runs eleven credential-free CLI
subprocess fixtures through Manager choice, the queue, the real dispatch pool,
isolated Git copies, fan-in, original-card settlement and idempotent replay.
It observes ten concurrent processes, the queued eleventh, and separate task
results for the same role. A representative scope-violation check proves an
out-of-scope captured change cannot become a successful candidate. These
fixtures verify orchestration, not the quality of a paid model's coding work.
