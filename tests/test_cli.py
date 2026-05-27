import json
import subprocess
import sys

from goodteam_os.tasks import LocalTaskStore


def test_cli_start_reaches_prd_gate(tmp_path):
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--workspace",
        str(tmp_path),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["project_key"] == "GTO"
    assert payload["next_node"] == "await_prd_approval"
    assert "prd" in payload["artifacts"]
    assert (tmp_path / "state" / "projects" / "GTO" / "current.json").exists()


def test_cli_smoke_dry_run_outputs_planned_report(tmp_path):
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "smoke",
        "--workspace",
        str(tmp_path),
        "--suite",
        "contentops,self",
        "--cycles",
        "2",
        "--autonomy",
        "full",
        "--worker-mode",
        "fake",
        "--dry-run",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["status"] == "planned"
    assert len(payload["runs"]) == 4
    assert not (tmp_path / "state" / "smoke-runs" / "latest.json").exists()


def test_cli_smoke_failure_exits_nonzero_and_writes_report(tmp_path, monkeypatch):
    from goodteam_os import cli

    def fake_run_smoke_suite(**kwargs):
        return {
            "status": "failed",
            "report_path": str(tmp_path / "state" / "smoke-runs" / "latest.json"),
            "runs": [{"project_key": "CONTENTOPS", "status": "failed", "classification": "target scaffold"}],
        }

    monkeypatch.setattr(cli, "run_smoke_suite", fake_run_smoke_suite)

    assert cli.main(["smoke", "--workspace", str(tmp_path), "--suite", "contentops", "--cycles", "1"]) == 1


def test_cli_board_outputs_task_board_json(tmp_path, capsys):
    project_root = tmp_path / "target"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    store.create_task(title="Run CONTENTOPS smoke", description="", role="qa", priority="high")

    from goodteam_os import cli

    result = cli.main(["board", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["counts"]["backlog"] == 1
    assert payload["columns"]["backlog"][0]["title"] == "Run CONTENTOPS smoke"


def test_cli_workers_outputs_worker_job_status_json(tmp_path, capsys):
    from goodteam_os import cli
    from goodteam_os.workers import WorkerJobStore

    project_root = tmp_path / "target"
    project_root.mkdir()
    store = WorkerJobStore(project_root)
    job = store.create_job(run_id="run-1", role="qa", adapter="human", task="Verify", context="Context")
    store.run_job(job["id"])

    result = cli.main(["workers", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["project_root"] == str(project_root)
    assert payload["jobs"][0]["id"] == job["id"]
    assert payload["jobs"][0]["status"] == "needs_review"


def test_cli_start_accepts_model_profile_and_role_override(tmp_path):
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--workspace",
        str(tmp_path),
        "--model-profile",
        "cheap",
        "--role-model",
        "architect=codex:gpt-5.5",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["model_profile"] == "cheap"
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.5"
    assert payload["role_models"]["architect"]["primary"] == "codex:gpt-5.5"


def test_cli_start_accepts_delivery_mode(tmp_path):
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build a production-grade AI content platform",
        "--workspace",
        str(tmp_path),
        "--delivery-mode",
        "multi_service",
        "--dry-run",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["delivery_mode"] == "multi_service"


def test_cli_start_uses_project_model_profile_and_role_overrides(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: cheap",
                "delivery_mode: init_project",
                "role_model_overrides:",
                "  frontend: codex:gpt-5.3-codex",
            ]
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--workspace",
        str(tmp_path),
        "--project-key",
        "CONTENTOPS",
        "--model-profile",
        "",
        "--project-config",
        str(project_config),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "init_project"
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.5"
    assert payload["role_models"]["frontend"]["primary"] == "codex:gpt-5.3-codex"


def test_cli_start_project_model_profile_wins_when_cli_profile_omitted(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: cheap",
            ]
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--workspace",
        str(tmp_path),
        "--project-key",
        "CONTENTOPS",
        "--project-config",
        str(project_config),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["model_profile"] == "cheap"


def test_cli_start_dry_run_does_not_write_state_or_artifacts(tmp_path):
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--workspace",
        str(tmp_path),
        "--model-profile",
        "balanced",
        "--dry-run",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["dry_run"] is True
    assert payload["next_node"] == "await_prd_approval"
    assert payload["planned_artifacts"]["prd"].endswith("/prd/prd.md")
    assert payload["role_models"]["architect"]["primary"] == "codex:gpt-5.5"
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "artifacts").exists()


def test_cli_start_can_adopt_existing_project(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "codex.md").write_text(
        "# Memory\n- The product gathers AI usage research and turns it into platform-specific posts.\n",
        encoding="utf-8",
    )
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Take over the AI content studio publishing workflow",
        "--workspace",
        str(tmp_path),
        "--project-key",
        "CONTENTOPS",
        "--source-type",
        "existing_project",
        "--project-config",
        str(project_config),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["source_type"] == "existing_project"
    assert payload["next_node"] == "await_takeover_approval"
    assert "takeover" in payload["artifacts"]
    assert "prd" not in payload["artifacts"]


def test_cli_start_existing_project_requires_project_config():
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "start",
        "Build AI Team OS",
        "--dry-run",
        "--source-type",
        "existing_project",
    ]

    completed = subprocess.run(command, capture_output=True, text=True)

    assert completed.returncode == 1
    assert "Project config is required" in completed.stderr
