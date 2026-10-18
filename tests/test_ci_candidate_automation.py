"""Execute the deployed metadata controller against a fake GitHub API."""

import json
from pathlib import Path
import subprocess

import pytest
import yaml


AUTOMATION = Path(".github/workflows/candidate-automation.yml")
PUBLICATION = Path(".github/workflows/candidate-published.yml")
BASE = "b" * 40
TARGET = "a" * 40
REPOSITORY = "LukeJiaoR/GoodTeam"
RUN_URL = f"https://github.com/{REPOSITORY}/actions/runs/100"

HARNESS = r"""
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const fixture = input.fixture;
const calls = [];
const notices = [];
const api = (name, response) => async (args) => {
  calls.push({ name, args });
  if (fixture.errors?.includes(name)) throw new Error(`API failed: ${name}`);
  return { data: typeof response === 'function' ? response(args) : response };
};
const github = { rest: {
  actions: {
    getWorkflow: api('getWorkflow', args => ({
      id: args.workflow_id === 'ci.yml' ? 11 : 12,
    })),
    listWorkflowRuns: api('listWorkflowRuns', args => ({
      workflow_runs: args.event === 'workflow_dispatch' ? fixture.candidate_runs : fixture.pr_runs,
    })),
    getWorkflowRun: api('getWorkflowRun', fixture.validation_run),
    listJobsForWorkflowRun: api('listJobsForWorkflowRun', { jobs: fixture.jobs }),
    createWorkflowDispatch: api('createWorkflowDispatch', {}),
    reRunWorkflow: api('reRunWorkflow', {}),
  },
  git: {
    listMatchingRefs: api('listMatchingRefs', fixture.refs),
    getRef: api('getRef', { object: { sha: fixture.current_ref } }),
  },
  repos: {
    getBranch: api('getBranch', { commit: { sha: fixture.base } }),
    compareCommitsWithBasehead: api('compareCommitsWithBasehead', {
      merge_base_commit: { sha: fixture.merge_base },
    }),
    listCommitStatusesForRef: api('listCommitStatusesForRef', fixture.statuses),
  },
  pulls: {
    list: api('listPulls', fixture.pulls),
    get: api('getPull', fixture.current_pull),
  },
} };
github.paginate = async (method, args, mapper) => {
  const response = await method(args);
  return mapper ? mapper(response) : response.data;
};
const summary = { addHeading() { return this; }, addRaw() { return this; }, async write() {} };
const core = { notice: message => notices.push(message), summary };
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
(async () => {
  try {
    await new AsyncFunction('github', 'context', 'core', input.script)(github, fixture.context, core);
    process.stdout.write(JSON.stringify({ calls, notices }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ calls, notices, error: error.message }));
  }
})();
"""


def _workflow(path):
    assert path.exists(), f"Missing automatic candidate CI workflow: {path}"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _fixture():
    validation_run = {
        "id": 100, "workflow_id": 11, "path": ".github/workflows/ci.yml",
        "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
        "head_branch": "main", "head_sha": BASE, "display_title": f"CI {TARGET}",
        "head_repository": {"full_name": REPOSITORY}, "html_url": RUN_URL,
    }
    pull = {
        "number": 577, "state": "open",
        "head": {"sha": TARGET, "ref": "codex/feature", "repo": {"full_name": REPOSITORY}},
        "base": {"ref": "main", "sha": BASE},
    }
    pr_run = {
        "id": 200, "workflow_id": 11, "event": "pull_request", "head_sha": TARGET,
        "head_branch": "codex/feature", "head_repository": {"full_name": REPOSITORY},
        "status": "completed", "conclusion": "failure", "pull_requests": [{"number": 577}],
    }
    jobs = [
        {"name": "Prepare CI mode", "conclusion": "success"},
        *({"name": name, "conclusion": "skipped"} for name in ("classify", "backend", "browser")),
        {"name": "CI / gate", "conclusion": "failure", "started_at": "2026-10-18T01:00:00Z",
         "completed_at": "2026-10-18T01:01:00Z"},
    ]
    return {
        "context": {
            "repo": {"owner": "LukeJiaoR", "repo": "GoodTeam"},
            "ref": "refs/heads/main", "eventName": "workflow_run",
            "payload": {"workflow_run": {
                "workflow_id": 12, "path": ".github/workflows/candidate-published.yml",
                "event": "push", "status": "completed", "conclusion": "success",
                "head_branch": "codex/candidate-pr577-aaaaaaa", "head_sha": TARGET,
                "head_repository": {"full_name": REPOSITORY},
            }},
        },
        "base": BASE, "merge_base": BASE, "current_ref": TARGET,
        "refs": [{"ref": "refs/heads/codex/candidate-pr577-aaaaaaa",
                  "object": {"type": "commit", "sha": TARGET}}],
        "statuses": [], "candidate_runs": [], "pr_runs": [pr_run], "jobs": jobs,
        "validation_run": validation_run, "pulls": [pull], "current_pull": pull,
    }


def _validated_fixture():
    fixture = _fixture()
    fixture["context"]["payload"]["workflow_run"] = fixture["validation_run"].copy()
    fixture["statuses"] = [{
        "context": "merge-candidate/validated", "state": "success", "target_url": RUN_URL,
        "created_at": "2026-10-18T02:00:00Z",
    }]
    return fixture


def _execute(fixture):
    workflow = _workflow(AUTOMATION)
    script = workflow["jobs"]["reconcile"]["steps"][0]["with"]["script"]
    result = subprocess.run(
        ["node", "-e", HARNESS], input=json.dumps({"script": script, "fixture": fixture}),
        text=True, capture_output=True, check=True, timeout=10,
    )
    return json.loads(result.stdout)


def _mutations(result):
    return [call for call in result["calls"] if call["name"] in {"createWorkflowDispatch", "reRunWorkflow"}]


def test_candidate_publication_is_automatic_and_read_only():
    workflow = _workflow(PUBLICATION)
    assert workflow["on"] == {"create": "", "push": {"branches": ["codex/candidate-**"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["run-name"] == "Candidate ${{ github.sha }}"
    assert workflow["jobs"]["announce"]["steps"] == [{"run": "true"}]
    assert "refs/heads/codex/candidate-" in workflow["jobs"]["announce"]["if"]
    assert "github.event.ref_type == 'branch'" in workflow["jobs"]["announce"]["if"]


def test_controller_uses_only_trusted_default_branch_metadata():
    workflow = _workflow(AUTOMATION)
    assert workflow["on"] == {"workflow_run": {"workflows": ["Candidate published", "CI"], "types": ["completed"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert "display_title" in workflow["concurrency"]["group"]
    job = workflow["jobs"]["reconcile"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["permissions"] == {
        "contents": "read", "actions": "write", "statuses": "read", "pull-requests": "read",
    }
    assert len(job["steps"]) == 1
    assert job["steps"][0]["uses"] == "actions/github-script@v8"
    text = AUTOMATION.read_text(encoding="utf-8")
    for forbidden in ("actions/checkout@", "secrets", "upload-artifact", "child_process", "require(", "fetch(", "eval("):
        assert forbidden not in text
    assert "${{" not in job["steps"][0]["with"]["script"]


@pytest.mark.parametrize("event", ["push", "create"])
def test_publication_dispatches_main_with_exact_candidate_and_pinned_base(event):
    fixture = _fixture()
    fixture["context"]["payload"]["workflow_run"]["event"] = event
    result = _execute(fixture)
    assert "error" not in result
    assert _mutations(result) == [{"name": "createWorkflowDispatch", "args": {
        "owner": "LukeJiaoR", "repo": "GoodTeam", "workflow_id": 11,
        "ref": "main", "inputs": {"target_sha": TARGET},
    }}]
    comparisons = [call["args"]["basehead"] for call in result["calls"]
                   if call["name"] == "compareCommitsWithBasehead"]
    assert comparisons == [f"{BASE}...{TARGET}"]


@pytest.mark.parametrize("case", [
    "fork", "ordinary_branch", "moved_ref", "missing_ref", "invalid_sha", "stale_base",
    "failed_publication", "wrong_workflow", "wrong_event", "non_default_controller", "same_as_main",
])
def test_publication_rejects_unselected_or_stale_candidates(case):
    fixture = _fixture()
    source = fixture["context"]["payload"]["workflow_run"]
    if case == "fork":
        source["head_repository"]["full_name"] = "other/fork"
    elif case == "ordinary_branch":
        source["head_branch"] = "codex/feature"
    elif case == "moved_ref":
        fixture["current_ref"] = "c" * 40
    elif case == "missing_ref":
        fixture["refs"] = []
    elif case == "invalid_sha":
        source["head_sha"] = "main"
    elif case == "stale_base":
        fixture["merge_base"] = "c" * 40
    elif case == "failed_publication":
        source["conclusion"] = "failure"
    elif case == "wrong_workflow":
        source["workflow_id"] = 999
    elif case == "wrong_event":
        source["event"] = "pull_request"
    elif case == "non_default_controller":
        fixture["context"]["ref"] = "refs/heads/codex/feature"
    elif case == "same_as_main":
        fixture["base"] = TARGET
    assert not _mutations(_execute(fixture))


@pytest.mark.parametrize("state", ["pending", "success", "failure", "error"])
def test_publication_preserves_existing_candidate_results_without_duplicate_dispatch(state):
    fixture = _fixture()
    fixture["statuses"] = [{"context": "merge-candidate/validated", "state": state}]
    assert not _mutations(_execute(fixture))


def test_dispatch_is_not_repeated_before_pending_status_appears():
    fixture = _fixture()
    fixture["candidate_runs"] = [fixture["validation_run"]]
    result = _execute(fixture)
    assert "error" not in result
    assert not _mutations(result)


def test_unknown_dispatch_outcome_is_reported_without_retry_or_fabricated_status():
    fixture = _fixture()
    fixture["errors"] = ["createWorkflowDispatch"]
    result = _execute(fixture)
    assert "createWorkflowDispatch" in result["error"]
    assert len(_mutations(result)) == 1


def test_successful_candidate_automatically_refreshes_only_the_exact_head_metadata_gate():
    result = _execute(_validated_fixture())
    assert "error" not in result
    assert _mutations(result) == [{"name": "reRunWorkflow", "args": {
        "owner": "LukeJiaoR", "repo": "GoodTeam", "run_id": 200,
    }}]


@pytest.mark.parametrize("case", [
    "failed_validation", "foreign_status_url", "different_validation_target", "different_validation_workflow",
    "untrusted_validation_ref", "old_validation_base", "pending_validation", "fork_pull", "moved_pull",
    "changed_base", "old_pr_run", "other_pull_run", "pending_gate", "passed_gate", "failed_backend",
    "legacy_heavy_run", "failed_prepare", "post_validation_gate_failure", "invalid_timestamp", "missing_status",
])
def test_refresh_never_promotes_unverified_stale_or_genuinely_failed_results(case):
    fixture = _validated_fixture()
    if case == "failed_validation":
        fixture["statuses"][0]["state"] = "failure"
    elif case == "foreign_status_url":
        fixture["statuses"][0]["target_url"] = "https://github.com/other/fork/actions/runs/100"
    elif case == "different_validation_target":
        fixture["validation_run"]["display_title"] = f"CI {'c' * 40}"
    elif case == "different_validation_workflow":
        fixture["validation_run"]["workflow_id"] = 999
    elif case == "untrusted_validation_ref":
        fixture["validation_run"]["head_branch"] = "codex/unreviewed"
    elif case == "old_validation_base":
        fixture["validation_run"]["head_sha"] = "c" * 40
    elif case == "pending_validation":
        fixture["validation_run"]["status"] = "in_progress"
    elif case == "fork_pull":
        fixture["current_pull"]["head"]["repo"]["full_name"] = "other/fork"
    elif case == "moved_pull":
        fixture["current_pull"]["head"]["sha"] = "c" * 40
    elif case == "changed_base":
        fixture["current_pull"]["base"]["sha"] = "c" * 40
    elif case == "old_pr_run":
        fixture["pr_runs"][0]["head_sha"] = "c" * 40
    elif case == "other_pull_run":
        fixture["pr_runs"][0]["pull_requests"] = [{"number": 999}]
    elif case == "pending_gate":
        fixture["pr_runs"][0]["status"] = "in_progress"
    elif case == "passed_gate":
        fixture["pr_runs"][0]["conclusion"] = "success"
    elif case == "failed_backend":
        fixture["jobs"][2]["conclusion"] = "failure"
    elif case == "legacy_heavy_run":
        fixture["jobs"][1]["conclusion"] = "success"
    elif case == "failed_prepare":
        fixture["jobs"][0]["conclusion"] = "failure"
    elif case == "post_validation_gate_failure":
        fixture["jobs"][-1]["started_at"] = "2026-10-18T03:00:00Z"
    elif case == "invalid_timestamp":
        fixture["statuses"][0]["created_at"] = "unknown"
    elif case == "missing_status":
        fixture["statuses"] = []
    assert not _mutations(_execute(fixture))


def test_late_pr_gate_completion_after_candidate_result_is_also_reconciled():
    fixture = _validated_fixture()
    fixture["context"]["payload"]["workflow_run"] = {
        **fixture["pr_runs"][0], "path": ".github/workflows/ci.yml",
    }
    fixture["jobs"][-1]["completed_at"] = "2026-10-18T03:00:00Z"
    assert [call["name"] for call in _mutations(_execute(fixture))] == ["reRunWorkflow"]


def test_latest_failure_overrides_an_older_success():
    fixture = _validated_fixture()
    fixture["statuses"].insert(0, {"context": "merge-candidate/validated", "state": "failure"})
    assert not _mutations(_execute(fixture))
