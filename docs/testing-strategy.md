# Testing Strategy

GoodTeam uses more automated tests than a normal CRUD application because it is a
local-first control layer for AI software delivery. The product coordinates
worker backends, GitHub, CI, approval gates, evidence snapshots, provenance, and
project-local memory into an owned workflow ledger. A small contract bug can make
a human approve with stale evidence, write an incorrect audit event, bind a PR to
the wrong execution, or run a worker outside the intended boundary.

The goal of this policy is not to reduce safety coverage. The goal is to make
verification proportional to risk so small changes can use focused tests while
control-plane changes still get full validation.

## Default Principle

Every PR must explain why its verification set is sufficient for the risk it
introduces.

Use focused tests when the changed surface is narrow and low-risk. Use the full
backend suite, frontend build/lint, and live smoke when the change touches
workflow state, approval authority, execution identity, evidence, adapters, or
browser-visible paths.

When unsure, choose the higher tier.

## Verification Tiers

### Tier 0: Docs, comments, and copy-only changes

Examples:

- Documentation prose.
- Comments that do not change generated examples or executable snippets.
- Non-contractual copy edits.

Expected verification:

```bash
git diff --check
```

Optional verification:

- Targeted link or snippet check when the touched document contains commands,
  generated examples, or contract text that must remain accurate.

Full test suites are not required unless the documentation change also changes an
executable contract, command, or agent rule.

### Tier 1: Pure helpers, parsers, and formatters

Examples:

- Pure Python helper with no file-system mutation.
- Frontend parser/formatter extraction.
- Utility refactor that preserves input/output behavior.

Expected verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest <focused tests> -q
.venv/bin/ruff check <touched python files or tests>
git diff --check
```

For frontend-only helpers:

```bash
npm run lint --prefix frontend
npm run build --prefix frontend
```

Full backend suite is optional unless the helper is shared by approval,
execution, ledger, worker dispatch, project discovery, or evidence paths.

### Tier 2: API, web contract, and state projection changes

Examples:

- `/api/*` route payload shape.
- Dashboard state projection.
- Read-only workflow or project-state view.
- Frontend component consuming an existing API shape.

Expected verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest <focused web/api tests> -q
.venv/bin/ruff check src/goodteam_os/ tests/
git diff --check
```

Add frontend validation when the payload is consumed by React:

```bash
npm run lint --prefix frontend
npm run build --prefix frontend
```

Run the full backend suite before merge when the route or projection touches
state mutation, approval state, project discovery, path resolution, or workflow
advancement.

### Tier 3: Control-plane core changes

Examples:

- Execution models, deterministic IDs, idempotency keys, attempts, branch names,
  PR binding, or CI binding.
- Ledger writes, action events, audit metadata, approval decisions, review
  policies, evidence snapshots, reviewer provenance, or multi-reviewer
  projection.
- Worker dispatch boundaries, retry semantics, server actions, same-origin
  policy, or security-sensitive adapter contracts.

Expected verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest <focused subsystem tests> -q
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/
git diff --check
```

Add frontend validation when the control-plane payload is rendered or acted on by
React:

```bash
npm run lint --prefix frontend
npm run build --prefix frontend
```

Tier 3 PRs should not rely on passing CI alone. The PR body should name the
specific contract that the focused tests protect.

### Tier 4: End-to-end, adapter, and live-path changes

Examples:

- Real workflow advancement.
- Worker dispatch or retry execution path.
- Telegram approval loop.
- Browser-visible dashboard routing or layout behavior.
- Project-root resolution or file-picker paths.
- CI/provenance ingestion.
- Anything where earlier incidents showed unit tests can pass while the live path
  fails.

Expected verification:

- All relevant Tier 3 validation when control-plane state is touched.
- A live smoke or Playwright smoke against an isolated workspace.
- Explicit evidence of observed behavior, not only pass counts.
- Explicit note that the smoke did not run against real project configs.

Example smoke wording:

```text
Live smoke: ran serve through scripts/with_server.py against isolated
workspace/tmp-demo; opened /?project=DEMO; approval card rendered translated
labels and current evidence; no browser console errors observed.
```

Never run mutating smoke commands against an active real project workspace.

## Contract Areas And Suggested Focused Tests

Use this table as a starting point, not a substitute for judgment.

| Contract area | Typical tests | Default tier |
| --- | --- | --- |
| CLI and project init | `tests/test_cli.py`, `tests/test_init.py`, `tests/test_cli_doctor.py` | Tier 2 |
| Project discovery and path resolution | `tests/test_projects.py`, `tests/test_web.py` focused cases | Tier 2 or 4 |
| Workflow graph/state | `tests/test_state.py`, `tests/test_workflow_table.py`, `tests/test_graph_sample.py` | Tier 2 or 3 |
| Approval decisions and gates | `tests/test_review_gate.py`, `tests/test_web.py`, `tests/test_fix_approval_reviews.py` | Tier 3 |
| Review policy/provenance | `tests/test_review_policy.py`, `tests/test_review_provenance.py`, `tests/test_multi_reviewer_projection.py` | Tier 3 |
| Reviewer dispatch/evidence | `tests/test_reviewer_profiles.py`, `tests/test_reviewer_dispatch.py`, `tests/test_reviewer_evidence_snapshot.py` | Tier 3 |
| Execution identity/idempotency | `tests/test_execution_models.py`, `tests/test_execution_idempotency.py`, `tests/test_execution_pr_binding.py` | Tier 3 |
| Execution orchestration/API | `tests/test_execution_orchestrator.py`, `tests/test_execution_api.py`, `tests/test_execution_e2e_contract.py` | Tier 3 or 4 |
| CI/GitHub binding | `tests/test_execution_ci_binding.py`, `tests/test_execution_github_contract.py` | Tier 3 |
| Worker backends | `tests/test_workers.py` | Tier 3 or 4 |
| Telegram adapter | `tests/test_telegram.py`, `tests/test_telegram_review.py` | Tier 3 or 4 |
| Memory and cross-project context | `tests/test_memory.py`, `tests/test_cross_project_memory.py` | Tier 2 or 3 |
| Failure pattern routing/readiness | `tests/test_failure_patterns.py`, `tests/test_workflow_table.py` focused cases | Tier 2 or 3 |
| Frontend contracts | frontend lint/build plus relevant backend web/API focused tests | Tier 2 or 4 |

## PR Author Checklist

Every PR should include:

1. The selected verification tier.
2. The changed contract area.
3. The commands that were run, including pass counts when available.
4. Any tests intentionally skipped and why that is safe.
5. Live-smoke evidence for Tier 4 work.

Good example:

```text
Verification tier: Tier 3 control-plane core.
Changed contract: approval_decided action payload now includes reviewer snapshot.
Focused tests: test_reviewer_evidence_snapshot.py + review/web regression set.
Full validation: pytest tests -q -> 914 passed; ruff clean.
Frontend: not run; no React code or consumed API shape changed.
```

Bad example:

```text
Tests passed.
```

## Reviewer Checklist

Reviewers should challenge a PR when:

- The selected tier is lower than the changed contract area.
- Approval, ledger, execution identity, evidence, or worker-dispatch code lacks a
  full backend run.
- Browser-visible or adapter-path work lacks live smoke evidence.
- A PR claims docs-only but changes command examples, agent rules, or executable
  contracts.
- The PR body omits skipped validations that would normally apply.

Reviewer feedback should cite the matrix rather than inventing ad hoc rules.

## CI Policy

Pull request CI derives a minimum Tier 0-4 risk classification from changed
files. Authors cannot lower the tier with PR text, checkboxes, or labels. The
classifier also computes backend and frontend impact separately, so a high-risk
backend-only change does not run unrelated frontend checks.

The automated contract is:

- Tier 0 documentation runs repository hygiene only.
- Tier 1 bounded helpers and tests run fixed focused targets.
- Tier 2 ordinary application contracts run maintained contract test groups.
- Tier 3 control-plane, security, dependency, build, and CI-authority changes run
  the applicable full backend and/or frontend checks.
- Every change affecting either stack also runs the `browser` job on the guarded
  `goodteam-ci` runner. It builds the frontend and runs the real HTTP/browser
  acceptance suite once against a fresh temporary workspace with fake Workers.
  Frontend unit tests and lint also run in this job when the frontend is affected,
  sharing the same dependency install and build.
  Tier 4 changes require reviewing this evidence and any additional smoke needed
  for adapters outside that suite.

Unknown paths, empty diffs, unsafe test target names, and ambiguous ownership
fail closed to Tier 3 full backend and frontend verification. Changes to the
classifier or workflow also run both full stacks.

`CI / gate` is the stable required status check. It succeeds only when
classification passes, the backend job matches its planned mode, and the browser
job (including planned frontend checks) succeeds. Browser acceptance may be skipped only
when both stacks are unaffected. A manual `workflow_dispatch` bypasses
changed-file selection and always runs full backend, frontend, and browser verification.

New commits cancel obsolete runs for the same pull request. The workflow also
covers stacked pull requests targeting another feature branch.
The workflow runs before merge on pull requests and does not rerun after merge
to `main`. This is safe only while the repository ruleset requires pull requests
for `main` and requires `CI / gate`; the workflow and required check must exist
on the default branch before that ruleset is enabled.

## Cleanup Guidance

Remove tests only with evidence of low value:

- repeated runtime cost,
- flaky behavior,
- duplicated coverage,
- assertion of implementation detail instead of contract,
- or obsolete product behavior.

Every cleanup should name the contract that remains protected after removal or
consolidation. Do not maintain unit tests for UI presentation: static rendering,
copy, translation catalogs, CSS values, layout, component locations, and visual
disclosure/focus details belong in browser acceptance or visual review. Component
tests are still appropriate for business behavior such as exact request payloads,
explicit confirmation, version binding, duplicate submission prevention, failed
input preservation, and stale response/project/run isolation.

The September 2026 cleanup removes presentation-only suites and presentation
cases from mixed suites. It also removes a hard-coded package-version smoke test,
duplicate model-default assertions, a same-object JSON serialization comparison,
and CI/documentation substring checks. Package imports remain exercised by the
backend suite; the model routing matrix remains covered by `test_models.py`.
Classifier behavior and an executable gate test protect CI selection and reject
failed or cancelled jobs and unexpected skips. The guarded runner and read-only
permissions retain explicit checks.

The subsequent [full backend test-value audit](audit/backend-test-value-2026-09-21.md)
records a disposition and supporting evidence for all 2,235 baseline pytest cases,
including retained cases, known limitations, and the replacements for changed tests.

## Business-loop Browser Acceptance

`frontend/e2e/business-loop.spec.js` exercises project import, task drafting,
existing-project detection, explicit task confirmation, command and constraint preparation, and
fake Worker execution. It approves takeover/PRD/RFC, requests changes at the security
gate, confirms the change discussion, reapproves RFC/security/release, completes
the run, and submits and starts a second task in the same project.

Assertions compare the UI and real API with persisted current state, approval
operation IDs, proposal history, and the archived first run. The old run must
remain read-only after the second task starts. Existing navigation/import tests
use independent projects and no longer depend on test ordering. No business API
is mocked. The shared browser fixture substitutes an empty Google Fonts stylesheet
so business acceptance uses fallback fonts without relying on the public CDN;
browser console and unhandled-error checks remain enabled.
Each CI invocation uses a unique temporary directory and loopback
port; the server refuses reuse and cleans only its owned directory on shutdown.

The runner must provide the Chromium system libraries. CI downloads the pinned
Playwright browser into the runner user cache with `npx playwright install chromium`;
it does not require sudo or change system packages. Missing libraries fail the job.

On the single runner, browser acceptance precedes the remaining frontend checks
in the same job and the backend job so setup or workflow failures surface first.
The final gate still requires every planned stack to pass. CI runs
`npm run test:e2e` with retries disabled. Repetition is available on demand via
`npm run test:e2e -- --repeat-each=2` for flake investigation. The
`browser-acceptance-<run>-<attempt>` artifact contains the HTML/JSON report,
business-state/ledger snapshots, and screenshots/traces on failure. This is
evidence for the built-in fake workflow; it does not verify real model output,
deployment, GitHub or Telegram adapters. Staging deployment is disabled in the
fixture's default configuration.

The initial implementation used CI-only validation at the user's request.
Subsequent changes follow the verification tiers above. Review the CI run for
the exact PR head rather than reusing a prior PR's result.

## Recovery Acceptance

The browser CI job first runs `tests/test_workflow_http_recovery.py` with a
dedicated pytest temporary root, then runs Playwright. Both must pass before
the remaining frontend checks, backend job, and final gate can succeed. The CI
backend command excludes `tests/test_workflow_http_recovery.py` because the
required browser job already ran it with dedicated evidence. An ordinary local
`pytest tests -q` still includes recovery; no tests are globally deselected.

| Scenario | Observed contract |
| --- | --- |
| Kill after decision persistence, before ledger append | Restart exposes an interrupted transition; explicit Retry publishes the original operation ID once and advances to release review. |
| Kill after ledger append, before execution | Restart retains the original decision; GET does not dispatch work; Retry and repeated bound approval cannot add a second decision/execution. |
| Two server processes share one workspace | A live holder prevents another process from approving or retrying; GET preserves the live transition; a repeated completed approval replays. |
| Worker subprocess timeout | The existing timeout handler produces failed evidence and a blocked workflow; GET does not retry; explicit Retry keeps failed job evidence and the original approval. |
| Two browser tabs approve the same evidence | Both requests reach the real API; exactly one implementation job and one security decision result; refreshed tabs cannot approve the old gate. |

`tests/fixtures/workflow_recovery_server.py` hosts the real HTTP server in a
separate process. Its fault barriers and fake-only Worker guard exist only in
test code. All setup and workflow advancement use HTTP; tests never seed an
approval state. Process termination targets only that fixture's child. The
timeout uses a bounded Python sleeper through the existing Worker subprocess
handler, with no provider/real Worker invocation. The tab test delays forwarding
both requests until both have arrived; it never replaces an API response.

Workspaces, registry and Operator state all use temporary paths. CI uploads
`http-recovery-<run>-<attempt>` with JUnit results, server logs, current state,
approval logs and fake Worker evidence for 1 day on success or 3 days after
failure/cancellation, using the job status at upload time. Playwright retains its
separate browser artifact with concurrency state snapshots under the same policy.

These cases verify the recorded interruption boundaries and explicit retry
contract. They do not promise exactly-once external side effects after an
arbitrary provider process crash, and do not exercise real GitHub, Telegram,
deployment or model adapters.
