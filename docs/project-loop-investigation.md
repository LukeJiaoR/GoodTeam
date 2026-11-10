# Project Loop: investigation, feedback and bound Human decisions (#686)

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

The existing continuation tick handles a settled single investigation created
by this grant, including the verified terminal-failure slice described below. It binds the original task intent, confirmed batch and
read-only track to the retained dispatch and exact Worker report. Before releasing
its pending scheduling slot, it requires:

- original project, grant, authority, task, batch, dispatch and job identities;
- existing recovery evidence showing the original process/result is known, with
  no unknown work or unresolved quality execution;
- an attributable dispatch result and a retained workspace with no changed files;
- the same source HEAD and a clean source checkout;
- report bytes read from the bound job path, not a model-supplied path. Missing
  or empty output is recorded explicitly, not replaced with invented findings.
  Malformed UTF-8 or reports larger than 64 KiB block rather than silently losing facts.

The session atomically retains a source-bound observation and pending re-decision
responsibility. It clears only `pending_batch`: the research task and batch stay
`review` or `blocked` as originally recorded, `delivery_verified` stays false, and no Git commit, delivery receipt,
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

## L3 first slice: terminal failure and incomplete report feedback

A failed read-only investigation may re-enter the same Manager decision path if
it belongs to this active grant and the existing recovery service proves the
original Worker ended. `blocked` or an elapsed timeout is not termination proof.
The retained result must still be attributable and its workspace unchanged; an
uncaptured exception, unknown process, changed files or unresolved quality work
keeps the scheduling slot blocked. This slice does not relax those checks.

The observation distinguishes failed execution from report availability:
`worker_status: failed`, `task_status: blocked`, and `report_status: partial`,
`missing` or `empty`. A missing report has `report_digest: null` and empty report
text. An empty file has its actual digest. A successfully terminated Worker with
no readable report also supplies explicit missing/empty feedback, not a finding.
Storage errors, unsafe reads and invalid encoding remain blockers.

Manager can choose a bounded, purposeful follow-up using the original queue or
explain why no useful authorized work follows. It may not infer that an absent
report proves the project is fine, repeat the old job implicitly, or grant itself
environment repair/access rights. New work consumes the same task/batch/call
allowances. No automatic retry service, new failure queue or task-status writer
is introduced. The original failed card and batch remain blocked and visible;
accepted seed results and existing recovery history are untouched.

Successful nonempty L2 observations retain their existing structure and digest.
New failure/empty-output fields are only added when applicable. A report that
appears or changes after capture invalidates the reserved decision rather than
silently replacing its input. Stopped or already-blocked continuation grants are
not reactivated by this upgrade; their normal recovery/authorization remains
explicit. The new path is considered only by an already-active continuation.

## L4 first slice: a bound Human question is a wait, not completion

Newly captured discovery packets advertise `human_feedback_supported`. If a
specific business choice or unavailable fact truly needs the owner, Manager may
return `new_task: null`, its normal `rationale`, and:

```json
{
  "human_question": {
    "question": "Should this entry target internal staff or external API clients?",
    "why_human": "The intended audience is a business choice not established by current evidence."
  }
}
```

This is an addition to the normal response, not a separate API request. The host
rejects simultaneous work plus a question. Existing frozen packets without the
capability keep their original output/prompt contract. Grant normalization and
its scope digest do not change: asking does not authorize any extra Worker work.
Routine facts that authorized Workers can obtain should not be escalated to Human.

The existing continuation session retains the exact planning packet, selection,
execution identity and a host-generated `human-...` question ID. Its grant remains
active with `reason: awaiting_human_decision`; the existing handoff projection
shows `phase: human_required`, the actual question and why it needs the owner.
The original continuation tick returns while no answer exists. Polling/reopening
is passive, and waiting does not buy new Manager calls or create tasks. No extra
question store, scheduler or dialogue engine is introduced.

The first supported response surface is the **existing project message endpoint
and composer**, using the exact command displayed in that handoff:

```text
/answer human-<host-generated-id> Internal staff only; keep existing access limits.
```

This explicit identifier avoids treating arbitrary chat, a late reply or a generic
"continue" as consent to whichever question happens to be current. This slice has
no new dedicated answer button and does not claim natural-language reply routing.
Historical-task messages, attachments and runtime retries cannot answer this
project-level question. They keep their existing unrelated meaning.

The handler validates the original grant, target/task intents, source evidence,
question and effective configuration, then atomically records the answer receipt,
original user message and visible acknowledgement. It performs **no model call or
Worker dispatch**. The existing active-continuation service consumes the answer
on its next tick, including after a process restart or lost HTTP response. The
owner does not need another Start/continue command; the original service must be
running, as for all existing automatic continuation. GET does not advance it.

The answer is contextual information, not a new grant, completed task or verified
technical fact. A subsequent work proposal must cite the actual answer and still
satisfy the original sources, paths, dependencies, role, budget and review rules.
An answer requesting extra permissions cannot change those limits; use the normal
separately confirmed authorization/change flow. If the original target, backlog,
report or authority changed, the answer is rejected rather than silently retargeted.
That stale-question recovery is still explicit in this first slice.

Prior replies remain available to later planning within the same continuation.
An additional question gets a new bound ID; it cannot absorb an old answer.
Repeated submission of the same message returns its receipt without another
planning call. Reusing an ID with different text is rejected. A question publication
whose acknowledgement is lost remains a wait; an answer committed before a crash
remains pending for the original service. Queue-publication replay continues to
reuse the same reserved decision and task. Stop is never undone by reply/replay;
exhausted budgets do not get replenished. Plain `new_task:null` still means no
follow-up, not whole-project completion; broader stopping categories are not
claimed implemented here.

## Replay, Stop and deliberate remaining limits

Report capture and pending judgment are one session write. Publication replay
reuses the original input, task and reserved call; it does not create another
card or spend another planning turn. Stop/replacement is checked in existing
mutation boundaries and again before dispatch. Waiting/read endpoints do not
run models. Unknown termination, unverified output attribution, mutated
source/receipt or actual file writes remain blockers, not reasons to launch a
competing Worker. Missing/empty report text can be feedback only after the same
termination and unchanged-workspace checks.

This L3 slice is restricted to single, grant-discovered read-only investigations.
Failed writing tasks, mixed batches and uncaptured failures keep the original
recovery behavior; general partial-execution handling remains unfinished L3.
The L4 slice below supports explicit question/answer continuation, not arbitrary
natural-language approval or automatic goal/permission changes. More expressive
stopping choices remain open. This does not independently accept the research
task or claim that the full user goal is met. Manager tool-level isolation,
UI opt-in and real-model two-delivery acceptance remain unverified/open.

## Validation boundary

`test_project_discovery_investigation.py` retains the L1 pure protocol contract.
`test_project_investigation_feedback.py` adds one representative result-to-decision
case using explicit in-memory storage, terminal-evidence and model/dispatch
fixtures, extended in place for terminal failures and missing/empty reports. It covers the production continuation/observation/discovery connection,
queue-publication interruption/replay, no delivery promotion, changed report,
Stop during decision, exhausted allowance, unknown termination, file mutation,
failed partial/missing/empty output, failure-preserving follow-up or stop,
unattributable output, invalid UTF-8 and source/dependency restrictions.
The same failed-feedback scenario rejects progress on the preceding L2 code and
reaches re-decision on this candidate under those fixtures.

These fixtures are not real database durability, Worker/process isolation, actual
Git checks, independent review or live model evidence. Run the normal tests in a
complete checkout plus the existing outcome/assignment/continuation integration
cases and required control-plane checks before claiming those properties. The
local source-subset runner imports the complete candidate modules with explicit
dependency substitutes. It is supplemental evidence for an environment without
full dependencies, not the normal repository test entry point or real integration.
Earlier AST-subset results remain historical evidence only.

All product results remain under #419. CI stays paused. This code does not start
StockFlow, enable production services, call paid models or restore old Stop/grants.

The same representative fixture now covers L4 via the complete production message
entry: durable question, passive waiting, bound answer, next-tick re-decision,
question/answer acknowledgement loss, duplicate and stale replies, source/goal
changes, history retention, exhausted budget and Stop. The preceding L3 discovery
rejects the question response in the corresponding RED run. No real PostgreSQL
restart, browser or model quality claim follows from these substituted dependencies.
