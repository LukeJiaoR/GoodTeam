import json
from pathlib import Path

from goodteam_os.smoke import (
    SmokeProject,
    build_smoke_project_registry,
    classify_smoke_failure,
    ensure_self_worktree,
    ensure_self_worktree_target_is_safe,
    run_shell_command,
    run_smoke_suite,
    write_smoke_report,
)
from goodteam_os.web import build_smoke_run_items, render_dashboard


def test_smoke_registry_defines_contentops_and_self_projects(tmp_path):
    registry = build_smoke_project_registry(workspace=tmp_path, repo_root=Path.cwd())

    assert set(registry) == {"contentops", "self"}
    assert registry["contentops"].project_key == "CONTENTOPS"
    assert registry["contentops"].delivery_mode == "init_project"
    assert registry["self"].project_key == "GOODTEAM-SELF"
    assert any("pytest tests -q" in command["command"] for command in registry["self"].verification_commands)
    assert registry["self"].cleanup_policy == "temporary_worktree"


def test_contentops_registry_uses_existing_target_verification_commands(tmp_path, monkeypatch):
    target = tmp_path / "contentops"
    target.mkdir()
    (target / "package.json").write_text('{"scripts":{"test":"echo test","build":"echo build"}}', encoding="utf-8")
    monkeypatch.setenv("GOODTEAM_CONTENTOPS_ROOT", str(target))

    registry = build_smoke_project_registry(workspace=tmp_path / "workspace", repo_root=Path.cwd())

    commands = [item["command"] for item in registry["contentops"].verification_commands]
    assert commands == ["npm run test", "npm run build"]
    assert registry["contentops"].target_root == target


def test_smoke_failure_classifier_uses_stable_categories():
    assert classify_smoke_failure("401 Unauthorized INVALID_API_KEY") == "auth/provider"
    assert classify_smoke_failure("worker timeout after 1800 seconds") == "worker timeout"
    assert classify_smoke_failure("next_node was blocked") == "GoodTeam workflow"
    assert classify_smoke_failure("npm run build failed in generated scaffold") == "target scaffold"
    assert classify_smoke_failure("better-sqlite3 NODE_MODULE_VERSION mismatch during npm run test") == "target scaffold"
    assert classify_smoke_failure("QA release-blocking browser capture failed") == "QA/browser/UI"
    assert classify_smoke_failure("something else broke") == "unknown"


def test_write_smoke_report_persists_machine_readable_json(tmp_path):
    report = {
        "status": "passed",
        "suite": ["contentops"],
        "cycles": 1,
        "runs": [{"project_key": "CONTENTOPS", "cycle": 1, "status": "passed"}],
    }

    path = write_smoke_report(tmp_path, report)

    assert path == tmp_path / "state" / "smoke-runs" / "latest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["runs"][0]["project_key"] == "CONTENTOPS"


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


def test_smoke_suite_fake_mode_generates_reports_tasks_and_worker_ids(tmp_path, monkeypatch):
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
    assert Path(result["report_path"]).is_file()


def test_smoke_suite_failure_creates_blocker_task(tmp_path):
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
    assert result["runs"][0]["classification"] == "target scaffold"
    task_ledger = tmp_path / "target" / ".goodteam" / "tasks" / "tasks.json"
    assert "Smoke failure: CONTENTOPS cycle 1" in task_ledger.read_text(encoding="utf-8")


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


def test_dashboard_renders_recent_smoke_runs(tmp_path):
    write_smoke_report(
        tmp_path,
        {
            "status": "failed",
            "suite": ["contentops"],
            "cycles": 1,
            "runs": [
                {
                    "project_key": "CONTENTOPS",
                    "suite": "contentops",
                    "cycle": 1,
                    "status": "failed",
                    "classification": "target scaffold",
                    "next_task": "GT-1",
                }
            ],
        },
    )

    items = build_smoke_run_items(tmp_path)
    html = render_dashboard(None, {"workspace": str(tmp_path), "project_key": "CONTENTOPS", "project_name": "AI Content Studio"})

    assert items[0]["classification"] == "target scaffold"
    assert "Smoke Runs" in html
    assert "target scaffold" in html
