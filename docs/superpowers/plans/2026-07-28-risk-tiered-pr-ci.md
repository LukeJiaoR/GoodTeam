# Risk-Tiered Pull Request CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unconditional pull request CI with a fail-closed Tier 0-4 changed-file classifier, conditional backend/frontend verification, and one stable merge gate.

**Architecture:** A standard-library Python classifier under `scripts/ci/` owns path-to-risk and path-to-verification mappings. GitHub Actions checks out full history, asks the classifier for a JSON plan, conditionally runs backend/frontend jobs, and aggregates their results in `CI / gate`; manual dispatch always requests both full jobs.

**Tech Stack:** Python 3.12, pytest, ruff, GitHub Actions YAML, Bash, React/Vite npm scripts

---

## File map

- Create `scripts/ci/classify_changes.py`: pure classification model, git diff adapter, GitHub output writer, and Markdown summary writer.
- Create `tests/test_ci_change_classifier.py`: unit and historical changed-file regression coverage.
- Create `tests/test_ci_workflow.py`: source-contract checks for triggers, conditional jobs, manual full mode, and stable aggregation.
- Modify `.github/workflows/ci.yml`: classification, conditional verification, stable gate, PR/manual triggers.
- Modify `docs/testing-strategy.md`: replace the unconditional-CI policy with the approved risk-tiered contract.

### Task 1: Lock the classifier contract with failing tests

**Files:**
- Create: `tests/test_ci_change_classifier.py`
- Create: `scripts/ci/classify_changes.py`

- [ ] **Step 1: Write tests for the plan shape and maximum-risk merge**

Create tests that import `classify_changes`, construct `Change(status, paths)`, and assert the returned `Plan` fields. The first cases are:

```python
def test_docs_only_is_tier_zero_without_product_jobs():
    plan = classify_changes([Change("M", ("docs/testing-strategy.md",))])
    assert plan.tier == 0
    assert plan.backend == "none"
    assert plan.frontend == "none"


def test_mixed_changes_take_highest_tier_and_union_stack_impact():
    plan = classify_changes([
        Change("M", ("src/goodteam_os/execution/ledger.py",)),
        Change("M", ("frontend/src/components/ApprovalBanner.jsx",)),
    ])
    assert plan.tier == 4
    assert plan.backend == "full"
    assert plan.frontend == "full"
    assert plan.live_smoke_evidence_required is True
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_change_classifier.py -q
```

Expected: collection fails because `scripts.ci.classify_changes` does not exist.

- [ ] **Step 3: Add the minimal data model and conservative classifier**

Implement immutable `Change` and `Plan` dataclasses, mode ranking, per-path decisions, and deterministic merging. Use this public shape:

```python
@dataclass(frozen=True)
class Change:
    status: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    tier: int
    backend: str
    frontend: str
    backend_test_targets: tuple[str, ...]
    frontend_test_targets: tuple[str, ...]
    live_smoke_evidence_required: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_changes(changes: Sequence[Change]) -> Plan:
    if not changes:
        return fail_closed_plan("empty changed-file set")
    decisions = [classify_path(path) for change in changes for path in change.paths]
    return merge_decisions(decisions)
```

Rules must be ordered from narrowest authority paths to broader product paths.
Unknown paths return Tier 3 with both stacks full. `docs/**`, `openwiki/**`, root
Markdown, and the PR template return Tier 0 with both stacks `none`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the same focused pytest command. Expected: all initial classifier tests pass.

- [ ] **Step 5: Commit the classifier foundation**

```bash
git add scripts/ci/classify_changes.py tests/test_ci_change_classifier.py
git commit -m "feat(ci): add risk-tier change classifier"
```

### Task 2: Add risk mappings, diff parsing, and historical regressions

**Files:**
- Modify: `scripts/ci/classify_changes.py`
- Modify: `tests/test_ci_change_classifier.py`

- [ ] **Step 1: Add failing tests for all approved risk boundaries**

Add parameterized cases for:

```python
@pytest.mark.parametrize(("path", "tier", "backend", "frontend", "smoke"), [
    ("tests/test_memory.py", 1, "focused", "none", False),
    ("src/goodteam_os/memory.py", 2, "focused", "none", False),
    ("src/goodteam_os/execution/orchestrator.py", 3, "full", "none", False),
    ("src/goodteam_os/workers.py", 3, "full", "none", False),
    ("src/goodteam_os/telegram.py", 4, "full", "none", True),
    ("frontend/src/utils/time.js", 2, "focused", "full", False),
    ("frontend/src/features/projects/ProjectHomePage.jsx", 4, "focused", "full", True),
    (".github/workflows/ci.yml", 3, "full", "full", False),
    ("frontend/package-lock.json", 3, "none", "full", False),
    ("unowned/new-system.conf", 3, "full", "full", False),
])
def test_path_policy(path, tier, backend, frontend, smoke):
    plan = classify_changes([Change("M", (path,))])
    assert (plan.tier, plan.backend, plan.frontend) == (tier, backend, frontend)
    assert plan.live_smoke_evidence_required is smoke
```

Also cover NUL-delimited `git diff --name-status -z` additions, deletions, and
renames, plus deterministic JSON output and these historical changed-file sets:

```python
HISTORICAL_CASES = [
    (["docs/superpowers/specs/2026-07-16-goodteam-product-frontend-redesign-design.md"], 0, "none", "none"),
    (["src/goodteam_os/telegram.py", "src/goodteam_os/workers.py", "tests/test_telegram_runtime_controls.py"], 4, "full", "none"),
    (["frontend/package-lock.json", "frontend/src/app/AppRouter.jsx", "src/goodteam_os/project_import.py", "tests/test_web.py"], 4, "full", "full"),
]
```

- [ ] **Step 2: Run the expanded tests and verify RED**

Expected: failures identify missing mappings and diff parsing.

- [ ] **Step 3: Implement the complete mapping and CLI**

Add these implementations around the path-policy functions from Task 1:

```python
def parse_name_status(data: bytes) -> tuple[Change, ...]:
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise ValueError("incomplete git name-status record")
        paths = tuple(field.decode("utf-8") for field in fields[index:index + path_count])
        changes.append(Change(status=status, paths=paths))
        index += path_count
    return tuple(changes)


def changes_between(base: str, head: str) -> tuple[Change, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-status", "-z", f"{base}...{head}"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return parse_name_status(result.stdout)


def full_plan(reason: str = "manual full verification") -> Plan:
    return Plan(3, "full", "full", (), (), False, (reason,))


def write_github_outputs(path: Path, plan: Plan) -> None:
    values = {
        "tier": str(plan.tier),
        "backend": plan.backend,
        "frontend": plan.frontend,
        "backend_test_targets": " ".join(plan.backend_test_targets),
        "frontend_test_targets": " ".join(plan.frontend_test_targets),
        "live_smoke_evidence_required": str(plan.live_smoke_evidence_required).lower(),
        "plan_json": json.dumps(plan.as_dict(), separators=(",", ":"), sort_keys=True),
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def append_summary(path: Path, plan: Plan) -> None:
    rows = [
        "## CI change classification",
        "",
        f"- Tier: **{plan.tier}**",
        f"- Backend: `{plan.backend}`",
        f"- Frontend: `{plan.frontend}`",
        f"- Live smoke evidence required: `{str(plan.live_smoke_evidence_required).lower()}`",
        "",
        "### Reasons",
        *[f"- {reason}" for reason in plan.reasons],
        "",
    ]
    with path.open("a", encoding="utf-8") as summary:
        summary.write("\n".join(rows))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--full", action="store_true")
    source.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    if args.full:
        plan = full_plan()
    else:
        if not args.head:
            parser.error("--head is required with --base")
        plan = classify_changes(changes_between(args.base, args.head))
    print(json.dumps(plan.as_dict(), sort_keys=True))
    if args.github_output:
        write_github_outputs(args.github_output, plan)
    if args.summary:
        append_summary(args.summary, plan)
    return 0
```

GitHub outputs include `tier`, `backend`, `frontend`, space-separated fixed test
targets, `live_smoke_evidence_required`, and compact `plan_json`.

- [ ] **Step 4: Run classifier tests and ruff**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_change_classifier.py -q
.venv/bin/ruff check scripts/ci/classify_changes.py tests/test_ci_change_classifier.py
```

Expected: all pass.

- [ ] **Step 5: Commit the complete classifier**

```bash
git add scripts/ci/classify_changes.py tests/test_ci_change_classifier.py
git commit -m "test(ci): cover tier classification regressions"
```

### Task 3: Replace unconditional workflow execution

**Files:**
- Create: `tests/test_ci_workflow.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write a failing workflow source-contract test**

Read `.github/workflows/ci.yml` as text and assert:

```python
def test_ci_is_pr_or_manual_only_with_stable_gate():
    workflow = Path(".github/workflows/ci.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "name: CI / gate" in workflow
    assert "needs: [classify, backend, frontend]" in workflow
    assert "if: always()" in workflow


def test_ci_jobs_follow_classifier_outputs():
    workflow = Path(".github/workflows/ci.yml").read_text()
    assert "needs.classify.outputs.backend != 'none'" in workflow
    assert "needs.classify.outputs.frontend != 'none'" in workflow
    assert "--full" in workflow
    assert "--base" in workflow and "--head" in workflow
```

- [ ] **Step 2: Run the workflow test and verify RED**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_workflow.py -q
```

Expected: assertions fail against the unconditional workflow.

- [ ] **Step 3: Implement classify, conditional stack jobs, and gate**

Rewrite the workflow with:

```yaml
on:
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  classify:
    outputs:
      tier: ${{ steps.plan.outputs.tier }}
      backend: ${{ steps.plan.outputs.backend }}
      frontend: ${{ steps.plan.outputs.frontend }}
      backend_test_targets: ${{ steps.plan.outputs.backend_test_targets }}
      live_smoke_evidence_required: ${{ steps.plan.outputs.live_smoke_evidence_required }}
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - id: plan
        run: |
          if [[ "${{ github.event_name }}" == "workflow_dispatch" ]]; then
            python3 scripts/ci/classify_changes.py --full --github-output "$GITHUB_OUTPUT" --summary "$GITHUB_STEP_SUMMARY"
          else
            git diff --check "${{ github.event.pull_request.base.sha }}...${{ github.event.pull_request.head.sha }}"
            python3 scripts/ci/classify_changes.py --base "${{ github.event.pull_request.base.sha }}" --head "${{ github.event.pull_request.head.sha }}" --github-output "$GITHUB_OUTPUT" --summary "$GITHUB_STEP_SUMMARY"
          fi
```

Backend and frontend jobs keep their current setup commands. Backend chooses the
focused target list or `tests`; frontend always preserves `npm ci`, build, and
lint. The final gate compares planned modes with `needs.<job>.result` and permits
`skipped` only when the corresponding planned mode is `none`.

- [ ] **Step 4: Run workflow and classifier contract tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_change_classifier.py tests/test_ci_workflow.py -q
git diff --check
```

Expected: all pass.

- [ ] **Step 5: Commit workflow orchestration**

```bash
git add .github/workflows/ci.yml tests/test_ci_workflow.py
git commit -m "ci: run pull request checks by risk tier"
```

### Task 4: Align policy docs and verify the control-plane change

**Files:**
- Modify: `docs/testing-strategy.md`

- [ ] **Step 1: Replace the old unconditional CI Policy section**

Document PR-only automatic classification, the stable gate, fail-closed Tier 3,
Tier 4 manual smoke evidence, manual full dispatch, and the prerequisite `main`
ruleset. Retain the existing verification tier definitions.

- [ ] **Step 2: Run focused verification**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_ci_change_classifier.py tests/test_ci_workflow.py -q
.venv/bin/ruff check scripts/ci/classify_changes.py tests/test_ci_change_classifier.py tests/test_ci_workflow.py
git diff --check
```

Expected: all pass with no ruff or whitespace findings.

- [ ] **Step 3: Run required Tier 3 full backend verification**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os/ tests/ scripts/ci/
```

Expected: full suite passes and ruff is clean.

- [ ] **Step 4: Run current frontend CI commands**

```bash
npm run build --prefix frontend
npm run lint --prefix frontend
```

Expected: both pass.

- [ ] **Step 5: Commit policy alignment**

```bash
git add docs/testing-strategy.md
git commit -m "docs(ci): document risk-tiered pull request policy"
```

### Task 5: Pull request rollout and branch rule activation

**Files:**
- No repository file changes after Task 4.

- [ ] **Step 1: Push the feature branch and open a PR**

The PR reports Tier 3, CI authority as the changed contract, focused/full command
results, and that no live smoke is required.

- [ ] **Step 2: Verify real Actions behavior**

Confirm `classify`, applicable backend/frontend jobs, and `CI / gate` appear. The
self-change must select Tier 3 with full backend and frontend.

- [ ] **Step 3: Merge only after the gate succeeds**

Use the repository's normal squash merge and branch-cleanup policy.

- [ ] **Step 4: Enable and verify the `main` repository ruleset**

Require pull requests and `CI / gate`, then query the GitHub API to confirm the
rule is active. Do not enable the required context before the workflow exists on
`main`.

- [ ] **Step 5: Perform branch/worktree cleanup**

Delete the remote and local feature branch, prune refs, and report remaining
worktrees and untracked artifacts according to
`docs/development-branch-hygiene.md`.
