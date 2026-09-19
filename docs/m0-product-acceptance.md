# M0 product acceptance: implementation is not delivery

Owner: [#411](https://github.com/LukeJiaoR/GoodTeam/issues/411).
Regression and documentation slice: [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419).
This protocol does not replace either issue or weaken its acceptance criteria.

## Supported acceptance target

Desktop Web; an explicitly configured local Git repository; one verified real
provider/model/reasoning configuration; one bounded coding task at a time. The
user follows Projects → Work → explicit start → execution → review/change → outcome.
Each task's original contract determines whether a PR, merge or deployment is
required. Never reduce that contract at the end to make the task pass.

Optional adapters, mobile, new-project scaffolding and other provider combinations
may exist without being accepted by this protocol. Arbitrary in-flight Worker
steering, automatic replan/change queues, shared Team migration, external Planner
integration and billing are not dependencies for the first bounded delivery proof.

## Evidence matrix

| Capability | Implemented source | Relevant evidence and remaining limit |
| --- | --- | --- |
| Real question, runtime error recovery, versioned task draft and Team confirmation | #421; Task Assistant/session/control plane | Historical isolated real question and draft-start records exist; downstream delivery in those checks used fake Workers. Not full real coding acceptance. |
| Exact review feedback to a modification draft | #422; Intent Commit | Historical real Manager-to-PRD revision handoff exists. The record also retains a scope-expansion finding and unconfirmed correction. Not a finished coding task. |
| Workflow/review recovery and repeated task intake | #427–#429 | Isolated real HTTP, process termination/restart, timeout, concurrent approval and fake-Worker business-loop coverage. External provider side effects are not proven exactly-once. |
| Conversation continuity, message feedback and runtime catalog | #431–#433 | Deterministic contract/browser coverage; metadata is not proof of live account/model compatibility. |
| Research-backed intake, preparation facts, conservative advice and report reads | #426 | Exact project/run/artifact-bound implementation and isolated CI evidence. Report readability and exit code do not establish delivery acceptance. |
| Always-visible task context and scoped history | [#434](https://github.com/LukeJiaoR/GoodTeam/pull/434) | Read-only navigation/projection and focused/browser tests. Use that PR's final head, base, CI and screenshots, not its earlier candidate. |
| Same-composer task-change handoff and status | [#435](https://github.com/LukeJiaoR/GoodTeam/pull/435) | Exact original-text draft handoff and persisted status tests. A draft is not confirmed execution; a recorded decision is not accepted delivery. |
| Repeated useful real coding delivery | #411 | Still requires actual task scorecards and independently inspectable outputs. No PASS count is created by this document. |

Relevant retained records:
[question/runtime checks](plans/2026-09-10-m0-runtime-team-recovery.md),
[PRD feedback handoff](plans/2026-09-10-review-feedback-handoff.md),
[delivery reconciliation](plans/2026-09-19-pr426-readiness.md),
[current context](plans/2026-09-19-current-work-context.md),
[change-status contract](conversation-change-status.md).
These records are evidence of their stated revisions and scopes, not a blanket
assertion that the current installation has been tested with real providers.

## Before a real task

1. Record the selected GoodTeam commit/tree and target repository baseline.
   Inspect current PR dependencies; testing a branch does not mean it is merged
   or deployed. Keep ordinary exact-head CI as a prerequisite, not a substitute.
2. Use an explicitly designated isolated checkout, workspace, project registry,
   state directory and service port. Follow the [live-smoke playbook](live-smoke-playbook.md).
   Do not restart, delete or mutate the user's existing service/workspace.
3. Record allowed repository paths, network destinations, provider/model/reasoning,
   Worker identities and the cost/call budget. Real-provider execution must be
   explicitly allowed for this environment; do not discover or reuse unrelated
   credentials. Fail closed when credentials, configuration or access are absent.
4. Agree the task's exact objective, exclusions, acceptance commands, deliverable
   and merge/deploy boundary before confirming it. Use a genuinely useful task,
   not a scripted success case or a fabricated reviewer defect.
5. Independently identify the reviewer and any required verifier. The Builder
   cannot approve its own output. Record whether independence is actually met.

## Run the existing product, not a rescue script

Enter the goal through Work, add a meaningful constraint in another turn and
confirm the exact resulting draft. At preparation, inspect the actual constraints
and verification commands. Confirm only the intended version and scope.

While a Worker runs, ask about progress. The answer and task bar must refer to
current durable state. Asking or discussing a change must not silently modify
instructions. At an actionable review, transfer a correction through the existing
change draft, inspect the original text and interpretation, then explicitly confirm.
The next attempt must receive the intended constraints and bind its new evidence.

Review the current output independently. Route genuine actionable findings back
through the product; retain the original failed attempt and the fix/re-review
identity. Apply the existing bounded retry policy. If the product cannot perform
the required handoff, report the blockage rather than editing its internal state.

For a recovery case, stop/restart only the isolated service using its documented
operator path at a known safe point. Re-enter Work and check task identity,
unresolved attention, history and current evidence without reconstructing the
mission from raw logs. Do not assume external effects are safe to replay.

At the end, open the primary PR/patch/artifact from the product. Verify the exact
head/hash, actual tests run, independent review, unresolved findings, and the next
human decision. Missing artifacts, stale evidence, simulated output, cancelled
work and partial results must not be presented as accepted completion.

## Compact per-task scorecard

Copy this block into the #411 evidence record, replacing placeholders with actual
observations. Store detailed private evidence in the approved project location;
link only redacted summaries and references in shared issue/PR text.

```text
Task identifier / source issue:
Real work requested and exclusions:
GoodTeam commit/tree; target baseline:
Project / run / proposal revision / attempt IDs:
Provider, model, reasoning, Worker IDs; configuration fingerprint:
Authorized environment, paths, network and cost/call budget:
Actual elapsed time, calls and cost (unknown if not measured):
Required deliverable, tests and merge/deploy boundary:

Intent correct on first pass: yes/no
Unnecessary clarification turns: count
Requirement correction after execution began: yes/no
User interventions before final decision: count + reasons
Hidden/internal-state repair: yes/no
Raw logs needed to understand current status: yes/no
Appropriate Worker selection and useful handoffs: pass/warn/fail + evidence
Independent review: yes/no/not-applicable + reviewer identity
Real review finding → bounded fix → new-head review: evidence/not-observed
Restart/re-entry: evidence/not-tested

Primary output URL/path and exact head/hash:
Verification actually run, results and evidence links:
Unresolved reviewer blockers: count
Original acceptance satisfied: yes/no
Material manual rework: yes/no
False-completion observation: yes/no
Founder delegate-again score: 1–5 + founder's reason (do not infer it)
Disposition: PASS / FAIL / BLOCKED / NOT RUN + reason
Retained failed attempts / focused follow-up issue:
```

A CI fix authored directly through GitHub is not a real GoodTeam-delegated task.
Fake-Worker fixtures, documentation changes, a smoke report and this checklist
must not be counted as five real tasks. A task rescued by internal-state editing
cannot be PASS. Keep failures and unknown measurements visible.

## Completion and escalation

Preserve #411's gate: at least five real tasks across two or more projects; at
least four satisfy their actual contracts without material manual rework; at
least three consecutive tasks need no hidden orchestration repair; no false
completion; one observed meaningful review/fix sequence; useful restart/re-entry;
a sufficiently low median intervention count; and the founder scores delegation
at least 4/5 for at least four tasks. The founder supplies subjective scores.

If review/fix handoffs are the demonstrated blocker, pull a minimal #347 slice.
If repeated project setup is the blocker, pull #409. If runtime/intent/evidence
fails, fix that exact boundary with a regression and repeat the real task. Do not
build those whole epics speculatively or declare this protocol complete because
its supporting PRs merged. Each independently useful implementation step remains
its own PR; shared-file dependencies require combination-head validation.

## Recording current verification

Every implementation PR must identify its final head/base, verification tier,
commands, actual CI result, browser evidence, failed attempts and unexecuted
checks. A passing old head, tests merely present in source, and uninspected
screenshots are not current verification. When validation is CI-owned, do not
run local tests/builds to fill gaps without authorization.

This documentation update does not run a real provider, restart a service,
merge a PR, publish a release or close #411. When the real runtime or authorization
is unavailable, record BLOCKED/NOT RUN with the exact missing prerequisite;
never replace that stage with simulated evidence or a promised background run.
