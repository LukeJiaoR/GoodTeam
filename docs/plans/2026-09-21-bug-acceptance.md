# Bug acceptance, 2026-09-21

## Scope and source

Close the remaining Team proposal UX gap in #414 and recheck the merged intake,
evidence and feedback changes in #416, #425 and #415. Implementation and the real
smoke started from `648419ffa8f678d373815c7ba286a2d516e828e2`. The concurrent #456
package-store change is independent and is integrated before submission; the PR
records final head/base and combination checks. No generated OpenWiki page changed.

The original checkout's `workers.py` and `test_workers.py` changes are preserved.
Work is in the separate `codex/bug-acceptance-414` worktree.

## Team confirmation contract

- Project command effects on a copy through the existing control-plane handlers;
  persist Worker identity, before/after values and cascading changes with the
  proposal's original Team revision. Preview generation writes no Team state,
  configuration or decision ledger.
- Known ambiguous names produce role/name/ID candidates before either builtin or
  real-model routing can choose a Worker. Exact-ID follow-ups still require a
  separate confirmation. Active-task conversation retains the same guard.
- Deterministic clarification does not claim that a real model was verified.
- Stale proposals retain their original preview, show a recovery state, and can
  be dismissed. Confirmation still fails under the existing revision lock.
- Team-page action names include role/name/ID. The confirmation UI displays
  changes and never sends confirmation while a matching preview is absent.

## Verification

Tier 4 with Tier 3 authorization, revision, runtime verification and session
projection contracts. On the initial implementation baseline:

- Focused backend: `PYTHONPATH=src .venv/bin/python -m pytest
  tests/test_assistant_team_confirmation.py tests/test_team_control_plane.py
  tests/test_assistant_conversation.py tests/test_intake_team_dispatch.py -q`:
  **73 passed**.
- Full backend: `PYTHONPATH=src .venv/bin/python -m pytest tests -q --durations=5`:
  **2,280 passed**, 14 existing warnings, 466.69 seconds.
- `npm test --prefix frontend`: **201 Vitest and 6 Node passed**.
- `npm run test:e2e --prefix frontend`: **14 passed**, retries disabled, fresh
  temporary workspaces, real HTTP/persisted state and fake Workers.
- After completing stale-status presentation: focused Work/Assistant/context
  tests **44 passed**; rebuilt and reran `team-confirmation.spec.js`: **1 passed**.
- Ruff, frontend lint/build and `git diff --check` passed. The existing bundle
  size advisory remains.

The new browser case checks candidate clarification, exact target and effects,
zero changes before confirmation, reload, dismissal, denied stale/dismissed
confirmation, replay without an extra decision, and desktop/390px/360px layouts.
Screenshots were inspected; initial mobile screenshots showed the conversation
rather than the proposal, so evidence capture now scrolls the confirmation into
the viewport and captures the proposal itself.

Initial focused failures were two test assumptions: a cascade event correctly
uses `actor=system`, and an old authorization fixture used the now-ambiguous
`Builder A` name. The corrected assertions retain cascade provenance and test
model authorization with an exact ID; new cases independently reject ambiguity.
One extra focused invocation named a nonexistent feedback test file and collected
no tests; it is not a passing result. No production assertion was weakened.

## Isolated real intake and recovery

Environment: temporary synthetic Python Git repository, separate GoodTeam
workspace/home/registry/state, service `127.0.0.1:51163`. The existing user service
and previously paused real acceptance environment were not touched. Source files
in the synthetic target remained byte-identical (`git diff` empty).

Runtime: only Research was changed through Team UI to `codex / gpt-5.6-sol / low`,
Worker `research-a`; other roles and the Manager used fake. Budget: at most three
provider calls for intake-only assessment; **two actual provider calls** completed,
exit code 0. Cost was not reported. No coding, deployment or external adapter
operation was authorized by the smoke.

Run: `task-b1baaf0f3a2c43d7b71612d99992428a`.

1. Two task paragraphs survived draft revision 2 and appeared exactly in the
   actual run's `idea`. Empty discovered constraints were displayed as an explicit
   empty state rather than claimed to have been found.
2. Job `job-dba35d2c-e3f8-4520-ab22-5e79091d1192` dispatched `project_intake` through
   `research-a` with the configured provider/model/reasoning. The resulting
   assessment correctly described `tasks.py`, `list_titles()`, the exact test
   result expected by the fixture, and the no-dependency constraint; it explicitly
   said tests had not been executed.
3. Disabled Research in Team. Review rationale transferred verbatim through
   Request changes to Work, survived reload, was interpreted and explicitly
   confirmed. The same run produced an honest placeholder with no new Worker job.
   Work and Review recommended `caution` for missing evidence, not approval.
4. Re-enabled Research and explicitly reactivated its paused Worker through Team.
   Another bound Request changes/interpret/confirm produced job
   `job-aa790e8c-92e4-4922-8ad8-7d603c16af91` in the same run. Its task input included
   the exact feedback; the revised brief captured Unicode-casefold substring
   matching, title-string results and empty-query behavior without changing code.
5. The final takeover artifact was
   `sha256:c51833c28f97ed7ac2124232b000122a5e7a80b0115b709648f000373ed0c1b1`.
   Stopping/restarting only the isolated service preserved run, artifact hash,
   caution recommendation and two jobs; GET/reload did not dispatch another job.

Both real assessments remained awaiting human takeover review. Missing independent
review evidence was disclosed; no approval or successful code delivery was inferred.
The isolated service and browser task space were stopped after evidence capture.

Durable local evidence: `output/bug-acceptance-2026-09-21/` in the original
checkout. It contains full/focused test reports, screenshots, HTTP state snapshots,
runtime/job summary and SHA-256 manifest. Detailed synthetic project and Worker
artifacts remain in the named temporary environment; no output directory was deleted.

## Issue disposition

| Issue | Result |
| --- | --- |
| #414 | Remaining implementation delivered by this PR; close after merge and current-head CI/review. |
| #425 | Real intake, disabled gate and same-run recovery verified; legacy per-role behavior also covered by full backend. Eligible to close. |
| #415 | Exact Review → Work → reload → explicit confirmation → real Research input verified; conflict/replay protections covered by full backend. Eligible to close independently of #411. |
| #416 | Conservative recommendations and actual/empty preparation facts verified. Keep open: Work's advisory approval still says `Approve and continue` and submits an empty note, so explicit informed-override wording/reason remains unfulfilled. |
| #412 | Existing runtime/recovery regression passes; this smoke did not repeat a real Assistant failure→configuration fix→original-message retry. Keep open pending that final acceptance. |
| #413 | Current two-paragraph draft/run fidelity and regressions pass; this smoke used fake Manager classification. Do not present it as fresh real-Manager acceptance. |
| #420 | Work proposal widths passed; the complete Team mobile/keyboard/device acceptance remains separate. |
| #424 | The previously failing read-only test passed in this full run (16.60 s); a pass does not explain the original Linux failure. Keep open. |

No new PASS is added to #411's five real coding tasks across two projects. This
record demonstrates bounded intake and review handoff, not completed coding delivery.
