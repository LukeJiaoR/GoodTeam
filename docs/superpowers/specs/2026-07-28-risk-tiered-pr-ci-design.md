# Risk-tiered pull request CI

Status: approved design

Date: 2026-07-28

Scope: pull request change classification, conditional backend and frontend
verification, stable merge gating, manual full CI, and removal of duplicate
post-merge CI

## 1. Summary

GoodTeam will replace its unconditional backend-and-frontend CI with a
repository-owned change classifier. Every pull request will receive a
conservative Tier 0-4 classification and an independent backend/frontend impact
plan. CI will run only the verification required by that plan.

The pull request gate remains mandatory before code enters `main`. The workflow
will no longer run again after the same change is merged to `main`. A manual
`workflow_dispatch` entry point will remain available and will always run the
full backend and frontend checks.

Tier 4 changes will not start a live environment on a GitHub-hosted runner.
They will run all applicable Tier 3 automated checks and require isolated live
smoke evidence in the pull request, as already defined by
`docs/testing-strategy.md`.

## 2. Current state and measured cost

`.github/workflows/ci.yml` currently starts two jobs for every pull request and
every push to `main`:

- backend: install Python development dependencies, run the full backend suite,
  and run ruff;
- frontend: install Node dependencies, build the frontend, and run eslint.

The jobs run in parallel. Recent backend jobs dominate pull request latency,
while frontend jobs add runner usage even when a pull request cannot affect the
frontend.

Historical replay used CI runs from 2026-07-06 through 2026-07-19:

- 49 pull request workflow runs;
- 42 post-merge `main` push workflow runs;
- backend mean 101.9 seconds, range 92-132 seconds;
- frontend mean 29.0 seconds, range 25-36 seconds;
- pull request runner time: 6,418 seconds;
- post-merge runner time: 5,445 seconds;
- total observed runner time: 11,863 seconds, or about 198 minutes.

Replaying the proposed component boundaries across the 49 pull request runs
classified 15 as backend plus frontend, 28 as backend-only, and 6 as docs-only.
Pure component filtering would reduce pull request runner time by 24.5 percent.
Allowing a conservative 12 seconds per run for classification and aggregation
overhead gives a 15.3 percent reduction before any focused Tier 1 or Tier 2 test
savings.

Removing the 42 duplicate `main` push runs and applying the conservative pull
request estimate reduces the historical total from about 198 to 91 runner
minutes, a 54.2 percent reduction. The expected operating range is:

- 15-25 percent less runner time within pull request workflows;
- about 50-55 percent less total runner time after duplicate `main` runs are
  removed;
- 70-90 percent faster feedback for docs-only pull requests;
- little wall-clock improvement for backend-only Tier 3/4 pull requests, because
  the backend remains their critical path;
- additional latency savings for Tier 1/2 changes when focused tests replace the
  full backend suite.

These are historical estimates, not acceptance claims. The first 30 days after
rollout must compare actual job and workflow durations against this baseline.

## 3. Goals

- Keep CI verification proportional to the risk tiers in
  `docs/testing-strategy.md`.
- Derive the minimum tier from changed files without trusting pull request text
  or labels.
- Skip unrelated backend or frontend jobs.
- Preserve full backend coverage for control-plane and security-sensitive code.
- Fail closed when a path is unknown or classification is ambiguous.
- Expose one stable required check for branch rules.
- Eliminate the automatic CI rerun after an already-verified pull request merges.
- Keep a manual full-CI escape hatch for releases, audits, and diagnosis.
- Make the classification reason visible to authors and reviewers.

## 4. Non-goals

- Running isolated live smoke environments on GitHub-hosted runners.
- Letting authors lower a tier with labels, checkboxes, or pull request text.
- Inferring test coverage from Python imports or a dynamic dependency graph.
- Removing tests or weakening Tier 3 control-plane verification.
- Building a broad test matrix across operating systems or runtime versions.
- Treating the tier number alone as a reason to run both technology stacks.

## 5. Trigger and branch policy

The CI workflow will use these triggers:

```yaml
on:
  pull_request:
    branches:
      - main
  workflow_dispatch:
```

There will be no `push: main` trigger. `workflow_dispatch` always selects full
backend and frontend verification and does not use changed-file classification.

Removing post-merge CI is safe only when GitHub prevents direct changes to
`main`. The repository currently reports `main` as unprotected. Rollout must
therefore include a repository ruleset that:

- requires changes to enter `main` through a pull request;
- requires the stable `CI / gate` status check;
- prevents bypass except for an explicitly retained emergency administrator
  path;
- applies to merge commits and squash merges.

The workflow must land on `main` before `CI / gate` is made required. The branch
ruleset is then enabled immediately. The change is not considered operationally
complete until the ruleset is verified through the GitHub API.

## 6. Classifier architecture

Add a small repository-owned Python module under `scripts/ci/` and focused unit
tests under `tests/`. The classifier accepts the changed-file name and status
list and returns structured JSON. It must not call GitHub or depend on runner
environment variables internally.

The workflow obtains the pull request base and head SHAs, checks out enough
history to compare them, and supplies a NUL-safe `git diff --name-status` result
to the classifier. Rename and deletion status must be retained; classification
must consider both sides of a rename.

The output contract is:

```json
{
  "tier": 3,
  "backend": "full",
  "frontend": "none",
  "backend_test_targets": [],
  "frontend_test_targets": [],
  "live_smoke_evidence_required": false,
  "reasons": [
    "src/goodteam_os/execution/ledger.py matches control-plane core"
  ]
}
```

Supported execution modes are `none`, `focused`, and `full`. The final tier is
the maximum tier assigned to any changed path. Backend and frontend modes are
merged independently, taking the strongest mode required by any path.

The classifier has no manual downgrade input. Unknown paths, malformed input,
empty diffs, classification exceptions, and ambiguous rename/delete cases
produce Tier 3 with full backend and frontend verification.

## 7. Classification policy

### Tier 0: non-executable documentation

Examples:

- `docs/**`;
- `openwiki/**`;
- root Markdown documentation;
- `.github/pull_request_template.md`;
- comments or copy-only files explicitly listed by the classifier.

Tier 0 runs repository hygiene only. Executable examples and agent policy remain
subject to reviewer judgment, but do not justify full product suites by
themselves.

### Tier 1: explicitly bounded helpers and tests

Tier 1 is allowlist-only. Initial candidates include:

- a non-sensitive changed backend test and its directly named test target;
- pure frontend utilities, formatters, locale catalogs, and their tests;
- pure backend helpers only after an explicit source-to-test mapping is added.

Files do not become Tier 1 merely because they look small. Sensitive execution,
approval, evidence, worker, and adapter tests retain the tier of their contract
area even in tests-only pull requests.

### Tier 2: ordinary application contracts

Examples:

- CLI behavior and project initialization;
- read-only project discovery and state projection;
- memory, messages, resources, and ordinary API payloads;
- frontend data helpers and non-control-plane API clients.

Tier 2 backend paths map to named contract test groups rather than arbitrary
test discovery. The initial groups follow the contract-area table in
`docs/testing-strategy.md`. A path without a maintained mapping escalates to
Tier 3.

### Tier 3: control plane, security, and build authority

Examples:

- `src/goodteam_os/execution/**`;
- workflow state, graph, ledger, execution identity, approval, review policy,
  evidence, provenance, worker dispatch, and operator merge authority;
- security, sandbox, same-origin, and server mutation boundaries;
- `.github/workflows/**`, `pyproject.toml`, frontend dependency manifests, and
  build or release scripts;
- classifier implementation and classifier rules.

Tier 3 requires the full backend suite when backend/control-plane behavior is
affected. Frontend checks are added only when the changed paths affect the
frontend, frontend build inputs, or a cross-stack contract.

### Tier 4: live and browser-visible paths

Examples:

- browser-visible routes, layouts, and interactive workflows;
- Telegram and external adapter loops;
- project-root resolution and native file-picking paths;
- smoke harnesses and real workflow advancement paths.

Tier 4 runs all applicable Tier 3 automation for the affected stacks. It also
sets `live_smoke_evidence_required` so the job summary and gate report the manual
evidence requirement. It does not launch a live environment in GitHub Actions.

## 8. Verification execution

Every pull request runs a change-classification job and `git diff --check`.
Conditional jobs then apply the classifier plan:

- backend `focused`: install Python dependencies, run named focused tests, and
  run ruff on the applicable Python surface;
- backend `full`: run `PYTHONPATH=src python -m pytest tests -q` and
  `ruff check src/goodteam_os/ tests/`;
- frontend `focused`: install locked Node dependencies and run the selected
  frontend test plus the applicable lint and build contracts;
- frontend `full`: install locked Node dependencies, run lint, and build;
- frontend source changes that are covered by Python source-contract assertions
  also request the mapped `tests/test_web.py` focused group.

The first implementation must preserve the current frontend build and lint
checks. Adding `npm test` to full frontend CI requires a separate policy decision
after its runtime and stability are measured; CI reduction must not silently
expand into an unrelated frontend testing policy change.

## 9. Stable merge gate

`CI / gate` is an always-present aggregation job. It depends on classification,
backend, and frontend results and fails when:

- classification failed;
- a required conditional job failed or was unexpectedly cancelled;
- a planned job did not run;
- repository hygiene failed.

Legitimate job skips are successful only when the classifier selected `none`
for that stack. The gate writes a summary containing:

- computed tier;
- changed paths and matched reasons;
- backend and frontend modes;
- focused test targets;
- checks run and intentionally skipped;
- whether isolated live smoke evidence is required.

The gate does not attempt to decide whether prose in a pull request constitutes
valid smoke evidence. That remains a human review requirement.

## 10. Failure handling and safety

- Unknown files fail closed to Tier 3 full backend and frontend.
- Changes to the classifier, workflow, dependency manifests, or release scripts
  run full CI.
- Cross-area pull requests take the maximum tier and union of affected stacks.
- Renames and deletions are classified using old and new ownership.
- Fork pull requests use only read permissions and no repository secrets.
- No third-party changed-file action is required; the repository owns the
  classification contract.
- A manual full run remains available when reviewers distrust a classification.

## 11. Testing strategy

Classifier unit tests must cover at least:

- docs-only Tier 0;
- backend tests-only focused execution;
- frontend-only execution without unrelated full backend;
- ordinary Tier 2 backend contract groups;
- execution, ledger, approval, evidence, and worker Tier 3 escalation;
- browser and adapter Tier 4 evidence signaling;
- mixed backend/frontend maximum-tier merging;
- workflow, dependency, and classifier self-change escalation;
- unknown paths and empty input failing closed;
- additions, modifications, deletions, and renames;
- stable deterministic JSON output.

Historical regression fixtures will encode representative changed-file sets from
recent pull requests, including docs-only, backend-only control-plane, and mixed
frontend/backend changes. They validate classification decisions without calling
GitHub.

Implementation verification is Tier 3 because it changes CI authority:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_change_classifier.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/ scripts/ci/
git diff --check
```

The workflow must also be exercised on a pull request with at least one docs-only
commit and one classifier self-change commit. The resulting Actions summaries
are acceptance evidence.

## 12. Rollout and observability

Rollout order:

1. Land the classifier, tests, conditional jobs, stable gate, manual full run,
   documentation update, and removal of `push: main`.
2. Confirm `CI / gate` appears and succeeds on the implementation pull request.
3. Enable the `main` ruleset requiring pull requests and `CI / gate`.
4. Query the ruleset/branch policy through the GitHub API and verify direct
   changes cannot bypass the pull request gate.
5. Observe the first 30 days of runs and compare them with the historical
   baseline.

Track:

- number of runs per tier;
- runner seconds by job and tier;
- pull request wall-clock duration;
- frequency and reason for fail-closed Tier 3 fallback;
- manual full-run frequency;
- any post-merge regression attributable to missed classification.

The target is at least 45 percent lower total runner time without a missed
required contract. A missed classification is a release-blocking defect and its
path must be escalated before further CI reduction.
