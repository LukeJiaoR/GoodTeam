import json
from pathlib import Path

import pytest

from goodteam_os import smoke, web

from goodteam_os.smoke import (
    SmokeProject,
    classify_smoke_failure,
    ensure_self_worktree,
    ensure_self_worktree_target_is_safe,
    run_shell_command,
    run_smoke_suite,
)



@pytest.fixture
def accept_simulated_reviews(monkeypatch):
    """Explicit synthetic operator input for report/verification tests only."""
    def decide(**kwargs):
        return web.decide_approval(
            **kwargs,
            note="Model-free smoke fixture: accept simulated evidence only to test reports and command results.",
        )
    monkeypatch.setattr(smoke, "decide_approval", decide)


def test_smoke_failure_classifier_uses_stable_categories():
    assert classify_smoke_failure("401 Unauthorized INVALID_API_KEY") == "env"
    assert classify_smoke_failure("worker timeout after 1800 seconds") == "env"
    assert classify_smoke_failure("next_node was blocked") == "product"
    assert classify_smoke_failure("npm run build failed in generated scaffold") == "test"
    assert classify_smoke_failure("better-sqlite3 NODE_MODULE_VERSION mismatch during npm run test") == "test"
    assert classify_smoke_failure("QA release-blocking browser capture failed") == "test"
    assert classify_smoke_failure("something else broke") == "product"


def test_smoke_suite_dry_run_reports_two_cycles_without_mutating_targets(tmp_path):
    result = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops", "self"],
        cycles=2,
        autonomy="full",
        dry_run=True,
        worker_mode="fake",
        repo_root=Path.cwd(),
    )

    assert result["status"] == "planned"
    assert len(result["runs"]) == 4
    assert all(run["status"] == "planned" for run in result["runs"])
    assert not (tmp_path / "projects").exists()
    assert not (tmp_path / "state" / "smoke-runs" / "latest.json").exists()


def test_unattended_smoke_reports_blocked_without_inventing_a_reason(tmp_path, monkeypatch):
    from goodteam_os.state import StateStore

    monkeypatch.setenv("GOODTEAM_CONTENTOPS_ROOT", str(tmp_path / "contentops"))
    result = run_smoke_suite(
        workspace=tmp_path, suites=["contentops"], cycles=1, autonomy="full",
        dry_run=False, worker_mode="fake", repo_root=Path.cwd(),
    )
    assert result["status"] == "blocked"
    run = result["runs"][0]
    assert run["status"] == "blocked"
    assert run["blocked_reason"]["code"] == "approval_reason_required"
    assert run["blocked_reason"]["message"]
    assert run["commands"] == []
    state = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert state.next_node.startswith("await_")
    assert not any(row["action"] == "approval_decided" for row in web.build_action_items(tmp_path, "CONTENTOPS"))
    assert json.loads(Path(result["report_path"]).read_text())["status"] == "blocked"


def test_smoke_suite_fake_mode_generates_reports_tasks_and_worker_ids(tmp_path, monkeypatch, accept_simulated_reviews):
    monkeypatch.setenv("GOODTEAM_CONTENTOPS_ROOT", str(tmp_path / "contentops"))

    result = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
    )

    assert result["status"] == "passed"
    assert result["runs"][0]["project_key"] == "CONTENTOPS"
    assert result["runs"][0]["status"] == "passed"
    assert result["runs"][0]["worker_job_ids"]
    assert result["runs"][0]["commands"]
    assert result["runs"][0]["evidence"]["smoke_report"] == result["report_path"]
    assert result["runs"][0]["evidence"]["worker_result_paths"]
    assert Path(result["report_path"]).is_file()
    persisted_report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert persisted_report["report_path"] == result["report_path"]
    assert persisted_report["runs"][0]["evidence"]["smoke_report"] == result["report_path"]
    task_ledger = tmp_path / "contentops" / ".goodteam" / "tasks" / "tasks.json"
    tasks = json.loads(task_ledger.read_text(encoding="utf-8"))["tasks"]
    smoke_task = next(task for task in tasks if task["title"] == "Run CONTENTOPS smoke cycle")
    assert smoke_task["status"] == "done"
    assert result["report_path"] in smoke_task["artifact_refs"]
    assert all(path in smoke_task["artifact_refs"] for path in result["runs"][0]["evidence"]["worker_result_paths"])


def test_smoke_evidence_only_includes_current_run_worker_results(tmp_path, accept_simulated_reviews):
    def runner(command, *, cwd):
        return {"command": command, "cwd": str(cwd), "exit_code": 0, "stdout": "ok", "stderr": ""}

    project = SmokeProject(
        suite="contentops",
        project_key="CONTENTOPS",
        name="AI Content Studio",
        idea="Build CONTENTOPS",
        delivery_mode="init_project",
        project_config=tmp_path / "projects" / "contentops.local.yaml",
        target_root=tmp_path / "target",
        verification_commands=[{"name": "ok", "command": "echo ok", "cwd": str(tmp_path / "target")}],
        cleanup_policy="keep",
    )
    old_job = tmp_path / "target" / ".goodteam" / "workers" / "jobs" / "job-old"
    old_job.mkdir(parents=True)
    (old_job / "job.json").write_text(json.dumps({"id": "job-old", "run_id": "old-run"}), encoding="utf-8")
    (old_job / "result.md").write_text("old result", encoding="utf-8")

    result = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=runner,
    )

    evidence_paths = result["runs"][0]["evidence"]["worker_result_paths"]
    assert evidence_paths
    assert all("job-old" not in path for path in evidence_paths)


def test_smoke_suite_failure_creates_blocker_task(tmp_path, accept_simulated_reviews):
    def failing_runner(command, *, cwd):
        return {"command": command, "cwd": str(cwd), "exit_code": 1, "stdout": "", "stderr": "npm run build failed"}

    project = SmokeProject(
        suite="contentops",
        project_key="CONTENTOPS",
        name="AI Content Studio",
        idea="Build CONTENTOPS",
        delivery_mode="init_project",
        project_config=tmp_path / "projects" / "contentops.local.yaml",
        target_root=tmp_path / "target",
        verification_commands=[{"name": "build", "command": "npm run build", "cwd": str(tmp_path / "target")}],
        cleanup_policy="keep",
    )

    result = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=failing_runner,
    )

    assert result["status"] == "failed"
    assert result["runs"][0]["classification"] == "test"
    task_ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"
    tasks = json.loads(task_ledger.read_text(encoding="utf-8"))["tasks"]
    assert any(task["title"] == "Smoke failure: CONTENTOPS cycle 1" for task in tasks)
    smoke_task = next(task for task in tasks if task["title"] == "Run CONTENTOPS smoke cycle")
    assert smoke_task["status"] == "blocked"
    assert smoke_task["priority"] == "high"


def test_smoke_failure_updates_existing_backlog_task_for_same_cycle(tmp_path, accept_simulated_reviews):
    calls = {"count": 0}

    def failing_runner(command, *, cwd):
        calls["count"] += 1
        message = "npm run test failed" if calls["count"] == 1 else "npm run build failed"
        return {"command": command, "cwd": str(cwd), "exit_code": 1, "stdout": "", "stderr": message}

    project = SmokeProject(
        suite="contentops",
        project_key="CONTENTOPS",
        name="AI Content Studio",
        idea="Build CONTENTOPS",
        delivery_mode="init_project",
        project_config=tmp_path / "projects" / "contentops.local.yaml",
        target_root=tmp_path / "target",
        verification_commands=[{"name": "verify", "command": "npm run build", "cwd": str(tmp_path / "target")}],
        cleanup_policy="keep",
    )

    first = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=failing_runner,
    )
    second = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=failing_runner,
    )

    task_ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"
    failure_tasks = [
        task
        for task in json.loads(task_ledger.read_text(encoding="utf-8"))["tasks"]
        if task["title"] == "Smoke failure: CONTENTOPS cycle 1"
    ]
    assert first["runs"][0]["next_task"] == second["runs"][0]["next_task"]
    assert len(failure_tasks) == 1
    assert failure_tasks[0]["status"] == "todo"
    assert "npm run build failed" in failure_tasks[0]["description"]


def test_smoke_cycle_rerun_refreshes_acceptance_criteria_after_success(tmp_path, accept_simulated_reviews):
    calls = {"count": 0}

    def flaky_runner(command, *, cwd):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"command": command, "cwd": str(cwd), "exit_code": 1, "stdout": "", "stderr": "npm run build failed"}
        return {"command": command, "cwd": str(cwd), "exit_code": 0, "stdout": "ok", "stderr": ""}

    project = SmokeProject(
        suite="contentops",
        project_key="CONTENTOPS",
        name="AI Content Studio",
        idea="Build CONTENTOPS",
        delivery_mode="init_project",
        project_config=tmp_path / "projects" / "contentops.local.yaml",
        target_root=tmp_path / "target",
        verification_commands=[{"name": "build", "command": "npm run build", "cwd": str(tmp_path / "target")}],
        cleanup_policy="keep",
    )

    first = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=flaky_runner,
    )
    second = run_smoke_suite(
        workspace=tmp_path,
        suites=["contentops"],
        cycles=1,
        autonomy="full",
        dry_run=False,
        worker_mode="fake",
        repo_root=Path.cwd(),
        registry={"contentops": project},
        command_runner=flaky_runner,
    )

    task_ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"
    tasks = json.loads(task_ledger.read_text(encoding="utf-8"))["tasks"]
    smoke_task = next(task for task in tasks if task["title"] == "Run CONTENTOPS smoke cycle")
    assert first["status"] == "failed"
    assert second["status"] == "passed"
    assert smoke_task["status"] == "done"
    assert smoke_task["acceptance_criteria"] == [
        "Smoke report latest.json exists.",
        "Verification commands and exit codes are recorded.",
        "Worker job ids or protocol evidence are recorded.",
    ]


def test_shell_command_runner_uses_login_shell_path(tmp_path, monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, *, cwd, capture_output, text, check):
        calls.append({"command": command, "cwd": cwd, "capture_output": capture_output, "text": text, "check": check})
        return Completed()

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr("goodteam_os.smoke.subprocess.run", fake_run)

    result = run_shell_command("node -p process.version", tmp_path)

    assert result["exit_code"] == 0
    assert calls == [
        {
            "command": ["/bin/zsh", "-lc", "node -p process.version"],
            "cwd": tmp_path,
            "capture_output": True,
            "text": True,
            "check": False,
        }
    ]


def test_self_worktree_target_must_not_be_inside_current_repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    try:
        ensure_self_worktree_target_is_safe(repo_root, repo_root / "nested")
    except ValueError as exc:
        assert "outside the current repo" in str(exc)
    else:
        raise AssertionError("Expected nested self worktree target to be rejected")


def test_ensure_self_worktree_uses_git_worktree_outside_repo(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    target = tmp_path / "goodteam-self"
    calls = []

    def fake_run(command, *, capture_output, text, check):
        calls.append(command)
        target.mkdir(parents=True)
        return object()

    monkeypatch.setattr("goodteam_os.smoke.subprocess.run", fake_run)

    ensure_self_worktree(repo_root, target)

    assert calls == [["git", "-C", str(repo_root), "worktree", "add", "--detach", str(target), "HEAD"]]
