# M0 Runtime Recovery And Team Confirmation

## Scope And Priority

1. P0 #412: make the first Assistant request recoverable when the configured runtime fails.
2. P0 #414: require explicit user confirmation for conversational Team mutations.
3. Next: #413 task-draft convergence, then the remaining M0 end-to-end acceptance gate #411.

Branch: `codex/m0-runtime-recovery`. The user authorized continuing the fixes,
committing, pushing and creating a PR on 2026-09-10. No merge, dependency/CI
change, or modification of active real project data is authorized by this batch.

## Continuation: Draft Contract And Mode Recovery

- [x] Implement #413 stable drafts, raw-message append, explicit ambiguous-change
  choice, edit/withdraw, and revision-bound confirmation.
- [x] Defer work preparation until task confirmation. Persist the draft before
  requiring a project mode, so mode selection needs no repeated model call.
  Resume explicitly requested inspection after mode selection using saved intent.
- [x] Add focused stale/replay/reload/cancel tests and desktop/mobile acceptance.
- [x] Run Tier 4 verification and review the complete diff.
- [ ] Commit, push, and create PR (explicitly authorized; no automatic merge).

This supersedes the earlier auto-preparation approach: a model interpretation
alone must not start task preparation. Existing interrupted records without a
saved interpretation remain fail-closed; they cannot be safely reconstructed.

### Final Continuation Evidence

- Full backend: `python -m pytest tests -q --tb=short` -> **2117 passed in 462.48s**.
  The later concurrent edit/confirm test and mode-saved-before-inspection-failure
  test, including its final attention projection fix, passed in the **102-test**
  focused Assistant/session/Team/manager run. The full count predates those two tests.
  A final focused run including `tests/test_web.py` passed **324 tests in 6.91s**.
- Frontend: `npm test --prefix frontend` -> **264 Vitest + 6 Node tests passed**;
  `npm run lint --prefix frontend` and `npm run build --prefix frontend` passed.
  The existing large-bundle advisory and Node localStorage warning remain.
- `ruff check src/goodteam_os/ tests/` and `git diff --check` passed.
- M0DRAFT: exact two-message draft retained after reload; revision 1 -> 2 -> 3
  for a pending change -> 4 after explicit replacement -> 5 after edit -> 6 after
  withdrawal. Old confirmation returned 409. No workflow started.
- M0NEW: explicit new-project selection, task confirmation and continuation
  started `task-9801e208709c4285a7dd7c8b34f816dc` with the exact draft request.
- M0LIVE: two real `codex / gpt-5.5 / high` read-only chat turns retained search,
  regression tests, and no-new-dependencies paragraphs under one revision-2 draft.
  Selecting existing-project mode created no additional chat request. Revision 1
  confirmation returned 409. After preparation confirmations, the exact text
  reached `task-972bffa2fa564dfb940ad4bce3201c66`; replay returned 200 with **one run**.
  Downstream delivery workers were fake; this is not a real-delivery release test.
- M0RECOVER: a real read-only inspection turn returned a repository-bound artifact
  naming Python and `app.py`, verifiable against the isolated target. The prior
  task draft and Team configuration were not changed.
- Desktop 1440x900 and mobile 390x844, English and Chinese, were exercised with
  Ego Browser. No horizontal page overflow was observed. Screenshots are in
  `/private/tmp/goodteam-m0-smoke-XwwFJe/`, including `draft-mode-mobile-en.png`,
  `draft-change-mobile-zh.png`, `live-draft-desktop-zh.png`,
  `live-started-desktop-zh.png`, and `readonly-answer-mobile-zh.png`.
- Restarting the isolated server preserved the M0LIVE run, exact request and two
  logical chat requests. The final preview remains at
  `http://127.0.0.1:21910/projects/M0LIVE/work`.

The two follow-up implementation blockers below are addressed by this continuation.
The PR should reference #412/#413/#414 without automatically closing their product
acceptance checklists. Richer Team before/after previews and named disambiguation
guidance remain iteration items under #414. #411 still requires five real delivery
tasks across two projects; no claim of overall M0 release readiness is made.

## Minimum Product Contract

- Show effective provider, model, reasoning depth and verification status.
- A connection check requires cost consent, makes one read-only model call and
  ignores any task or Team commands in the response. Builtin runtimes are
  labelled simulated, never verified as a real model.
- Persist the original message and request attempt history. HTTP replay does not
  create a second message or repeat model execution. Retry preserves the original
  text and uses a distinct, project-scoped Worker execution identity.
- Serialize conversational operations per project. A dead in-flight operation is
  marked interrupted; uncertain application effects are not automatically retried.
- Keep diagnostics to fixed error categories; do not expose raw provider stderr.
- Treat every conversational Team command as a proposal. Model-generated
  `team_authorization` never grants permission to execute.
- Bind proposals to the Team revision observed before model interpretation.
  Check this revision inside the Team command lock, after idempotent replay lookup.
- Show exact proposed commands, targets and parameters before confirmation.
  Reject legacy proposals without a revision; allow failed proposals to be dismissed.
- Use exact worker IDs or a unique name match. Never select the first of several
  identically named workers.

## Previous Batch Record

- [x] Reproduce the original defects with failing tests.
- [x] Implement compatible session extensions, retry/check API actions and UI controls.
- [x] Add concurrency, interruption, duplicate delivery, stale proposal and API tests.
- [x] Focused backend verification: 81 passed, including API and application-phase failure checks.
- [x] Frontend verification: 258 unit tests and 6 Node tests passed; lint/build passed.
- [x] Full backend regression: 2097 passed in 456.29s; the later application-failure test also passed in the 81-test focused run.
- [x] Isolated desktop/mobile live acceptance, including confirmation and dismissal, completed; evidence recorded.

## Verification Gate

Tier 4: execution identity, retries, authorization, persistence, API projections
and browser-visible controls. Run focused tests, the full backend suite, Ruff,
frontend tests/lint/build, `git diff --check`, and isolated live acceptance.

The available test environment is
`/private/tmp/goodteam-product-acceptance-rQeil6/venv/bin/python`.
The isolated smoke workspace is
`/private/tmp/goodteam-m0-smoke-XwwFJe/workspace`, with its own `GOODTEAM_HOME`
and project registry. No smoke writes target the GoodTeam source project.

Observed so far: a real `codex / gpt-5.4` request failed with the bounded
`model_unavailable` diagnostic; the failure survived navigation. The existing
Team runtime dialog changed only the isolated Assistant to `gpt-5.5`, and a
consented real read-only connection check succeeded without creating a task.

On a second fixture with project mode explicitly confirmed, the original failed
request retried successfully under `gpt-5.5`: one user message, one proposal,
attempt states `failed -> succeeded`, and no workflow run. Replaying the retry
created no Worker job. A real model-generated QA-disable proposal left QA enabled
until the user clicked confirmation; repeated confirmation preserved revision 4
and one applied-decision event. Cross-origin confirmation was rejected with 403.

Desktop (1440 x 900) and mobile (390 x 844, English and Chinese) screenshots are
under `/private/tmp/goodteam-m0-smoke-XwwFJe/`; `live-evidence.json` records the
identity and replay assertions. No horizontal page overflow was observed at 390px.
The real model subsequently proposed re-enabling QA; dismissing that proposal
kept QA disabled and Team revision at 4. The browser task was closed, while the
isolated server remains available at `http://127.0.0.1:21910/projects/M0RECOVER/work`.

Final commands: `python -m pytest tests -q --tb=short` (2097 passed), focused
Assistant/Team/manager suites (81 passed), `ruff check src/goodteam_os/ tests/`,
`npm test --prefix frontend` (258 Vitest + 6 Node tests), frontend lint/build,
and `git diff --check`. Final timeline-copy changes also passed the 49 related
frontend tests. Build retains the pre-existing large-bundle warning; no dependency
or bundling changes were made.

Batch status: #412/#414 minimum contracts implemented and locally verified.
Overall M0 release acceptance is still blocked by the follow-up issues below.
Issues were not closed remotely, and no commit or push was performed.

## Follow-Up Issues Addressed In This Continuation

### P0: Project Mode Confirmation Is Missing From The Assistant Path

- Scope: existing onboarding gap discovered during #412 live acceptance; blocks #411 sign-off.
- Reproduce: import a directory with only a README and ambiguous mode, then send
  a work request. `ProjectReadinessExecutor.start` rejects the unconfirmed mode.
- Actual: applying the model reply is interrupted before a task proposal is saved.
  This batch correctly retains the request and does not label it a connection failure.
- Minimum next fix: show a new/existing project choice in the Assistant, call the
  existing mode-confirmation API, then resume the saved request without another
  model call or duplicated user message. Never guess the mode from ambiguous evidence.
- Acceptance: both mode choices work after reload, interrupted state is reconciled,
  and no workflow starts before explicit task confirmation.

### P1: Acknowledgements Can Be Misclassified As Work Requests

- Scope: task-draft convergence #413.
- Observed: the real model classified a message asking only for acknowledgement,
  explicitly excluding work, as `work_request`.
- Minimum next fix: preserve intent evidence and stage a reviewable draft without
  auto-running preparation for non-work messages; improve the response contract
  and regression set. Keep the existing explicit task-start gate.
- Acceptance: acknowledgement, explanation, task revision and new task cases are
  separated; uncertain intent asks a question without starting preparation.

## Review And Handoff

Local contract review covered authorization provenance, revision-lock ordering,
duplicate deliveries, stale attempts, interrupted application, exact-message
preservation and translated action visibility. No independent subagent review
was run: the user-allowed reviewer models are unavailable in the current agent tool.
An independent review remains a pre-merge recommendation, not a claim of this batch.

## Deliberate Limits

This batch does not make unsupported models usable, silently substitute models,
provide a general semantic diff engine, or prove the entire delivery loop.
An interrupted application-phase request requires checking the timeline, task
proposal and Team state before a new request; generic automated reconciliation is deferred.
The next iteration can add bounded attempt-history retention and richer diagnostic
links without changing the logical-message/attempt separation.
