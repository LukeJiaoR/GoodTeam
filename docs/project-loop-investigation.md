# Project-loop investigation: L1 delegation and L2 result feedback (#686)

An explicitly authorized discovery turn may delegate a read-only investigation
when a material fact is missing. The existing continuation can then observe a
settled report, ask Manager to reconsider the same objective and queue its next
bounded action through the same task/Worker path. Manager does not perform the
investigation. This is not a new scheduler, task ledger or automatic acceptance.

## Explicit opt-in, unchanged old grants

The existing `start_project_continuation` request accepts the optional boolean
`discovery.allow_investigation`. It is omitted by default. For example:

```json
{
  "objective": "Use verified import results to identify the next useful improvement.",
  "allowed_write_domains": ["src/import", "tests/import"],
  "max_new_tasks": 3,
  "max_planning_calls": 5,
  "allow_investigation": true
}
```

The surrounding request still specifies and confirms a finite batch allowance.
Investigation, follow-up tasks and Manager decisions consume those original
limits. A seed delivery, investigation and next implementation need sufficient
remaining batches/tasks/calls; this change does not add free turns or reset them.
Read-only investigation uses the same registered repository, configured
Researcher and existing runtime access limits. It does not grant new network,
credential, test-execution, merge or deployment authority.

Omitting the flag preserves the old normalized grant and digest. Old fixed-list
or implementation-only grants do not acquire investigation permission on upgrade
or replay. An explicitly confirmed new grant uses the existing controls when
scope must change. The UI does not expose this experimental flag yet.

## L1: select and delegate, using the original queue

The output remains `new_task` plus `rationale`. Opted-in turns may additionally
use `task_type: investigation`, concrete factual criteria and `write_domains: []`.
The host fixes `research/read_only`; implementation keeps its original role and
allowed write paths. The model cannot choose argv, runtime, model or permissions.
The input reports actual configured Researcher availability; dispatch rechecks it.
No Worker is silently substituted by the Manager.

Create-once publication, the Task Assistant queue, isolated dispatch, scope and
source validation, Stop and budgets retain their original owners. Initial manual
chat research without an applicable grant still needs its normal confirmation.

## L2: report observation is not task acceptance or Git delivery

The existing continuation tick handles a successful settled single investigation
created by this grant. It binds the original task intent, confirmed batch and
read-only track to the retained dispatch and exact Worker report. Before releasing
its pending scheduling slot, it requires:

- original project, grant, authority, task, batch, dispatch and job identities;
- existing recovery evidence showing the original process/result is known, with
  no unknown work or unresolved quality execution;
- an attributable successful output and retained workspace with no changed files;
- the same source HEAD and a clean source checkout;
- a nonempty UTF-8 report read from the bound job path, not a model-supplied path.
  Reports larger than 64 KiB stop explicitly instead of silently truncating facts.

The session atomically retains a source-bound observation and pending re-decision
responsibility. It clears only `pending_batch`: the research task and batch stay
`review`, `delivery_verified` stays false, and no Git commit, delivery receipt,
review approval or task-acceptance result is fabricated. The observation says
`task_acceptance: not_assessed` and `review_status: not_attested_by_observation`.

This distinction allows Manager to reason about a report without pretending it
has been independently verified. Limitations or negative findings inside a
successful report remain untrusted observations. Mandatory technical checks and
independent review for later engineering delivery are unchanged.

The original discovery packet includes the captured report and its receipt
reference while retaining the earlier actual verified delivery separately. A
new follow-up must cite both. It cannot depend on the unaccepted research card
as though that card were done. Manager should pass relevant findings and their
uncertainty in the next task description, not treat report text as instructions
or authorization.

The observation receipt is the new trigger, rather than spending the same seed
delivery again. The existing discovery outbox freezes this exact input before
reserving a Manager call. Report bytes, source, task and authority are rechecked
before/after the decision and publication. A changed report blocks the reserved
turn; no fresh decision is purchased to conceal the change.

After a valid decision, the original create-once/queue/dispatch adapter starts
its next authorized action. A no-follow-up decision retains the existing honest
stopping meaning, not a project-complete claim. Future rounds may pass a previously
observed research card for scheduling, but it remains unaccepted and cannot satisfy
normal done-task dependencies. The observation is not a fresh attestation after
later code changes.

## Replay, Stop and deliberate remaining limits

Report capture and pending judgment are one session write. Publication replay
reuses the original input, task and reserved call; it does not create another
card or spend another planning turn. Stop/replacement is checked in existing
mutation boundaries and again before dispatch. Waiting/read endpoints do not
run models. Unknown termination, missing output, mutated source/receipt or actual
file writes remain blockers, not reasons to launch a competing Worker.

This slice handles successful terminal investigation reports. Failed/blocked
attempts, arbitrary partial executions, Human decision wait/resume and more
expressive stopping choices remain L3/L4. It does not independently accept the
research task or claim that the full user goal is met. Manager tool-level isolation,
UI opt-in and real-model two-delivery acceptance remain unverified/open.

## Validation boundary

`test_project_discovery_investigation.py` retains the L1 pure protocol contract.
`test_project_investigation_feedback.py` adds one representative result-to-decision
case using explicitly in-memory storage, terminal-evidence and model/dispatch
fixtures. It covers the production continuation/observation/discovery connection,
queue-publication interruption/replay, no delivery promotion, changed report,
Stop during decision, exhausted allowance, unknown termination, file mutation,
missing output and source/dependency restrictions.

These fixtures are not real database durability, Worker/process isolation, actual
Git checks, independent review or live model evidence. Run the normal tests in a
complete checkout plus the existing outcome/assignment/continuation integration
cases and required control-plane checks before claiming those properties. The
local AST-subset runner is only supplemental evidence for an environment without
full dependencies; it is not the normal repository test entry point.

All product results remain under #419. CI stays paused. This code does not start
StockFlow, enable production services, call paid models or restore old Stop/grants.
