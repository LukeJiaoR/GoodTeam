# Project Loop: investigation, feedback and retained question board (#686)

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

## L4: retained Human questions and Manager-owned continuation

Newly captured discovery packets advertise `human_feedback_supported`. Manager
decides which work needs a Human answer and which valuable authorized work can
continue. The existing response accepts `new_task`, `rationale` and an optional
question together:

```json
{
  "new_task": {
    "title": "Verify the existing entry-point behavior",
    "description": "Verify the current entry-point behavior and report its limitations without changing the intended audience.",
    "acceptance_criteria": ["Return the observed entry behavior and remaining uncertainty"],
    "task_type": "investigation",
    "write_domains": [],
    "dependencies": ["PROJECT-1"],
    "source_refs": ["<actual delivery receipt>", "<actual observation receipt>"]
  },
  "rationale": "The audience decision needs Human input; the authorized investigation can establish current behavior meanwhile.",
  "human_question": {
    "question": "Should this entry target internal staff or external API clients?",
    "why_human": "The intended audience is a business choice not established by current evidence.",
    "impact": "Audience-specific changes need this answer; observing current behavior can continue."
  }
}
```

The example references must be replaced with supplied receipts, and an
investigation still requires the original investigation permission and an
available Researcher. This extends the normal response, not the execution path.
The host enforces the existing grant, paths, budget, task identity, execution
isolation and review boundaries. It does not compute question-to-task blocking
rules, infer dependencies or choose priorities. Routine facts that authorized
Workers can obtain should not be escalated to Human.

| Manager output | Effect through the existing continuation |
| --- | --- |
| `new_task` plus `human_question` | Persist the question and queue the selected work in the same decision. |
| `new_task: null` plus a new question | Ask and choose no work in this turn; retain the grant for new feedback. |
| `new_task: null, wait_for_human: true` | Explicitly wait for an already retained question, with a rationale. |
| Plain `new_task: null` | End this grant with `discovery_no_followup`, even if historical questions remain; this is not whole-project completion. |

The existing session's `human_questions` map retains each exact planning packet,
selection, execution identity and host-generated `human-...` ID. There is no
global pending-question gate. In-flight batches continue to be reconciled;
their results, later deliveries and answers can all prompt a new judgment. A
Manager-chosen wait has `reason: awaiting_human_decision`; unchanged feedback
does not buy another model call. Asking is an output, not a self-triggering event.
The existing notice displays outstanding questions alongside the current batch's
review/quality/delivery handoff, without hiding that actual execution state.
No separate question store, scheduler or dialogue engine is introduced.

The packet includes `remaining_budget`. Manager calls, task creation and batches
keep their own existing limits: exhausted task capacity does not suppress an
otherwise authorized judgment about a new answer. Manager can use that turn to
wait or end; the host still refuses a task that exceeds the remaining resources.

The **Work page question board** offers a direct answer on each current open
question. It uses the existing project message endpoint and exact question ID.
The composer also accepts the command displayed in the handoff:

```text
/answer human-<host-generated-id> Internal staff only; keep existing access limits.
```

This explicit identifier avoids treating arbitrary chat, a late reply or a generic
"continue" as consent to whichever question happens to be current. Several
questions may remain open and each answer names its own ID. This does not claim
natural-language reply routing. Board answers always use project-level Context,
including when the user is viewing an old run. Historical-task messages,
attachments and runtime retries keep their existing unrelated meaning.

The handler validates the current grant, target/task authorization, question
identity and effective configuration, then atomically records the answer receipt,
original user message and visible acknowledgement. It does not require an idle
project or equality with the question's old HEAD, backlog or investigation files.
Unrelated progress does not invalidate the question. The handler performs **no
model call or Worker dispatch**. The existing active-continuation service
consumes the answer when its current execution can safely advance, using a new
packet with the latest Context. The owner does not need another Start/continue
command; the original service must be running. GET does not advance it.

The answer is contextual information, not a new grant, completed task or verified
technical fact. Manager chooses which answers are relevant to its task sources;
the full decision packet retains all captured replies. A subsequent proposal must
still satisfy the original sources, paths, dependencies, role, budget and review rules.
An answer requesting extra permissions cannot change those limits; use the normal
separately confirmed authorization/change flow. A changed goal/grant, revoked
authority or Stop still rejects new actions; a reply cannot revive a finished
grant. Manager judges whether a reply still applies to the current project and
what work is now useful, rather than restoring the plan from the question's date.

Packets expose unanswered `human_questions`, all retained `human_feedback`, new
`human_answers`, and historical `investigation_history`. Historical observations
retain their original revision and uncertainty; they are not recertified as
current truth. The existing `discovery_consumed` receipt ledger coalesces captured
delivery, investigation and answer feedback in one decision. Consumption records
that the feedback entered a judgment, not that Manager accepted it or made every
answer a task dependency. New answers that
arrive after a decision was captured remain available to the following judgment.
If an old null decision settles after a new answer arrived, that unconsumed
receipt keeps the same grant active for the next service tick, within its
remaining Manager budget. It does not silently end before that answer can enter
current Context. Stop still takes precedence.

Repeated submission of the same message returns its receipt. Reusing an ID with
different text is rejected. Question persistence leaves the original discovery
outbox intact, so an interrupted combined action reuses its decision, task
creation reference and queue identity. Replay never clears an answer already
recorded. Frozen decisions still undergo their original input/authorization
checks before dispatch; recording an answer is distinct from executing old advice.
Stop is never undone by reply/replay, and budgets are not replenished.

### Question board: retained issues and explicit Manager conclusions

The Work page projects `question_board` from the original `human_questions`
records. The board groups pending answers, answered issues that still need
handling, and closed issues. Each card shows the question, why Human input is
needed, optional Manager-described impact, the answer and any closing reason.
`considered` means an answer entered a decision; it does not mean the underlying
issue was resolved. Answering while other work runs is allowed by the same
authorization checks as the existing message endpoint. A failed send keeps its
text and retry identity. Refreshing or opening the board has no execution effect.

New Manager packets include the project's current and historical questions.
In the same decision as `new_task`, a new question, a wait or a stopping reason,
Manager can return:

```json
{
  "new_task": null,
  "rationale": "The owner confirmed the intended audience; the authorized scope is settled.",
  "question_updates": [
    {
      "question_id": "<an exact ID captured in question_board.items>",
      "status": "resolved",
      "reason": "The confirmed audience answers this decision; the implementation has its own task acceptance."
    }
  ]
}
```

`resolved` records a settled issue; `not_needed` records why an answer is no longer
needed. The host validates the captured question identity and retains the
Manager's reason and original decision evidence. No answer, receipt consumption,
unrelated progress or new grant automatically closes a question. Closing an issue
does not mark a task done or accept a Worker result. `impact` is explanatory text,
not a second dependency or blocking-rule system. These outputs are applied through
the original discovery outbox; replay does not append another conclusion event,
spend a model turn or duplicate task publication. Frozen older decisions keep
their original response contract.

When a new grant replaces one with questions, the original grant is retained in
the same session's `project_continuation_history`. Its unresolved and closed
questions remain visible. Current Manager decisions may update their conclusions
using current Context; the original record retains which current grant made that
decision. Its old status, answers, tasks, permissions and budgets stay unchanged.
Historical questions cannot accept new answers under an ended grant. Replaying
an older authorization command returns that retained original grant without replacing or restarting the current
one. The board reads the latest project session even while viewing historical run
details; it introduces no separate question store or scheduler.

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
The L4 slice supports explicit question/answer continuation, not arbitrary
natural-language approval or automatic goal/permission changes. More expressive
stopping choices beyond the bounded action contract remain open. This does not
independently accept the research task or claim that the full user goal is met. Manager tool-level isolation,
UI opt-in and real-model two-delivery acceptance remain unverified/open.

## Validation boundary

`test_project_discovery_investigation.py` retains the L1 pure protocol contract.
`test_project_investigation_feedback.py` adds one representative result-to-decision
case using explicit in-memory storage, terminal-evidence and model/dispatch
fixtures, extended in place for terminal failures and missing/empty reports. It
covers the production continuation/observation/discovery connection,
queue-publication interruption/replay, no delivery promotion, changed report,
Stop during decision, exhausted allowance, unknown termination, file mutation,
failed partial/missing/empty output, failure-preserving follow-up or stop,
unattributable output, invalid UTF-8 and source/dependency restrictions.
These fixtures are not real database durability, Worker/process isolation, actual
Git checks, independent review or live model evidence. Run the normal tests in a
complete checkout plus the existing outcome/assignment/continuation integration
cases and required control-plane checks before claiming those properties. The
normal `pytest` entry now exercises complete production-module imports with the
declared dependencies installed. Earlier source-subset and AST-subset runs are
historical evidence only; the PR records the actual commands, version, results
and environment limitations of each validation run.

All product results remain under #419. CI stays paused. This code does not start
StockFlow, enable production services, call paid models or restore old Stop/grants.

The same representative fixture covers L4 via the complete production message
entry: question plus work, publication and answer acknowledgement loss, batch
progress and review while questions remain open, answers during execution,
coalesced replies with current Context, explicit waiting versus no-followup,
question history, exhausted budget and Stop. It preserves the distinction
between unrelated Context progress and changed authorization. Full imports do
not change the fixture boundary: no PostgreSQL restart, browser or real-model
quality claim follows from these tests.

`test_project_question_board.py` exercises the board through real file-backed
session/task stores and authorization/answer handlers with explicit Manager
decision fixtures: answers remain open after consumption, explicit conclusions
retain evidence, replay is stable, and grant replacement preserves history that
current Manager decisions can explicitly conclude without reviving old authority.
The existing result-to-decision fixture also closes questions while
queuing work. Frontend tests exercise the current board inside the conversation,
answer failure/retry and answering from a historical-run view. These establish
the bounded application contract, not live-model judgment or full project Loop
acceptance.
