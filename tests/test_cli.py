import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_cli_failure_patterns_outputs_routing_readiness_json(tmp_path, capsys):
    from goodteam_os import cli
    from goodteam_os.failure_patterns import FailurePatternLibrary

    project_root = tmp_path / "target"
    project_root.mkdir()
    library = FailurePatternLibrary(project_root)
    for index in range(2):
        library.record_failure(
            failure_class="network",
            summary="503 Service Unavailable from registry.npmmirror.com",
            disposition="escalate_human",
            role="implementation",
            node="implementation",
            source=f"worker_failure:run-{index}:implementation:implementation",
        )

    result = cli.main(["failure-patterns", "--project-root", str(project_root), "--min-hits", "2"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["candidate_count"] == 1
    assert payload["routing_application"] == "deferred"
    assert payload["candidate_patterns"][0]["classifier_hint"] == "network"


def test_cli_failure_patterns_accepts_routing_quality_thresholds(tmp_path, capsys):
    from goodteam_os import cli
    from goodteam_os.failure_patterns import FailurePatternLibrary

    project_root = tmp_path / "target"
    project_root.mkdir()
    library = FailurePatternLibrary(project_root)
    for index in range(2):
        library.record_failure(
            failure_class="network",
            summary="503 Service Unavailable from registry.npmmirror.com",
            disposition="retry_same_role",
            role="implementation",
            node="implementation",
            source=f"worker_failure:run-{index}:implementation:implementation",
        )
    pattern = library.list_patterns()[0]
    for index in range(2):
        library.record_routing_outcome(
            signature=str(pattern["signature"]),
            applied=True,
            disposition="retry_same_role",
            deterministic_disposition="block",
            reason="canary_allowed",
            outcome="confirmed",
            source=f"triage_decided:run-{index}:implementation",
        )

    result = cli.main(
        [
            "failure-patterns",
            "--project-root",
            str(project_root),
            "--min-hits",
            "1",
            "--min-confirmed",
            "2",
            "--max-false-positive-rate",
            "0.5",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["routing_quality_gate"]["min_confirmed"] == 2
    assert payload["routing_quality_gate"]["max_false_positive_rate"] == 0.5
    assert payload["routing_quality_gate"]["status"] == "satisfied_for_review"


def test_cli_failure_patterns_outputs_llm_triage_readiness_json(tmp_path, capsys):
    from goodteam_os import cli
    from goodteam_os.failure_patterns import FailurePatternLibrary

    project_root = tmp_path / "target"
    project_root.mkdir()
    library = FailurePatternLibrary(project_root)
    for index in range(2):
        library.record_failure(
            failure_class="unknown",
            summary=f"Worker returned unclear failure envelope {index}",
            disposition="block",
            role="implementation",
            node="implementation",
            source=f"worker_failure:run-unknown-{index}:implementation:implementation",
        )

    result = cli.main(
        [
            "failure-patterns",
            "--project-root",
            str(project_root),
            "--min-hits",
            "1",
            "--min-llm-triage-samples",
            "2",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["llm_triage_readiness"]["status"] == "ready_for_shadow_triage"
    assert payload["llm_triage_readiness"]["min_samples"] == 2
    assert payload["llm_triage_readiness"]["model_invocation"] == "none"


def test_cli_memory_append_writes_event_jsonl(tmp_path, capsys):
    from goodteam_os import cli

    project_root = tmp_path / "target"
    project_root.mkdir()

    result = cli.main(
        [
            "memory-append",
            "--project-root",
            str(project_root),
            "--role",
            "release",
            "--horizon",
            "short-term",
            "--text",
            "PR 123 landed with dispatch provenance.",
            "--source",
            "land/pr-123",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    event_path = project_root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

    assert result == 0
    assert captured.err == ""
    assert payload == {"status": "ok", "role": "release", "horizon": "short-term", "source": "land/pr-123"}
    assert len(events) == 1
    assert events[0]["horizon"] == "short-term"
    assert events[0]["text"] == "PR 123 landed with dispatch provenance."
    assert events[0]["source"] == "land/pr-123"


def test_cli_memory_append_duplicate_exits_zero_without_second_event(tmp_path, capsys):
    from goodteam_os import cli

    project_root = tmp_path / "target"
    project_root.mkdir()
    command = [
        "memory-append",
        "--project-root",
        str(project_root),
        "--role",
        "release",
        "--horizon",
        "mid-term",
        "--text",
        "Record the same release event once.",
        "--source",
        "land/pr-456",
    ]

    assert cli.main(command) == 0
    capsys.readouterr()
    result = cli.main(command)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    event_path = project_root / ".goodteam" / "memory" / "roles" / "release" / "events.jsonl"
    lines = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert result == 0
    assert captured.err == ""
    assert payload["status"] == "deduped"
    assert len(lines) == 1


def test_cli_memory_append_invalid_horizon_exits_nonzero(tmp_path, capsys):
    from goodteam_os import cli

    project_root = tmp_path / "target"
    project_root.mkdir()

    result = cli.main(
        [
            "memory-append",
            "--project-root",
            str(project_root),
            "--role",
            "release",
            "--horizon",
            "forever",
            "--text",
            "Invalid horizon should fail.",
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "Unknown memory horizon: forever" in captured.err


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
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.4"
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
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.4"
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
    assert payload["role_models"]["architect"]["primary"] == "codex:gpt-5.6-sol"
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
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\nworker_backend:\n  mode: fake\n",
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


def test_cli_dispatch_calls_runner_and_returns_zero_for_all_ok(tmp_path, capsys, monkeypatch):
    from goodteam_os import cli

    task_file = tmp_path / "backend.md"
    task_file.write_text("Implement backend.", encoding="utf-8")
    constraints_file = tmp_path / "lane.md"
    constraints_file.write_text("- Stay inside backend.\n", encoding="utf-8")
    research_report = tmp_path / "research.md"
    research_report.write_text("Research evidence.\n", encoding="utf-8")
    project_root = tmp_path / "target"
    project_root.mkdir()
    calls = []

    def fake_dispatch_tracks(**kwargs):
        calls.append(kwargs)
        return {
            "all_ok": True,
            "jobs": [{"role": "backend", "outcome": "ok"}],
            "ok": [{"role": "backend", "outcome": "ok"}],
            "needs_human": [],
        }

    monkeypatch.setattr(cli, "dispatch_tracks", fake_dispatch_tracks)

    result = cli.main(
        [
            "dispatch",
            "--project-root",
            str(project_root),
            "--track",
            f"backend:fake:{task_file}",
            "--verify",
            "backend:echo ok",
            "--constraints-file",
            str(constraints_file),
            "--research-report",
            str(research_report),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["all_ok"] is True
    assert calls[0]["project_root"] == project_root
    assert calls[0]["tracks"][0]["task"] == "Implement backend."
    assert calls[0]["tracks"][0]["task_source"] == str(task_file)
    assert calls[0]["verify_commands"] == {"backend": "echo ok"}
    assert calls[0]["constraints_file"] == constraints_file
    assert calls[0]["research_report_file"] == research_report


@pytest.mark.parametrize("syntax", ["separate", "equals"])
def test_dispatch_track_script_forwards_research_report(tmp_path, syntax):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "dispatch-track.sh"
    script.write_text(Path("scripts/dispatch-track.sh").read_text(encoding="utf-8"), encoding="utf-8")
    executable = tmp_path / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    captured = tmp_path / "argv"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\0' \"$@\" > " + shlex.quote(str(captured)) + "\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    checkout = tmp_path / "isolated checkout"
    checkout.mkdir()
    report = tmp_path / "research report.md"
    report.write_text("Research evidence", encoding="utf-8")
    task = tmp_path / "task.md"
    task.write_text("Read the report", encoding="utf-8")
    option = ["--research-report", str(report)] if syntax == "separate" else [f"--research-report={report}"]

    result = subprocess.run(
        ["bash", str(script), "--name", "audit", "--task", str(task), "--worktree", str(checkout), *option],
        capture_output=True, text=True, timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert captured.read_bytes().decode().split("\0")[:-1] == [
        "-m", "goodteam_os.cli", "dispatch", "--project-root", str(checkout),
        "--track", f"implementation:codex:{task}", "--research-report", str(report),
    ]


def test_cli_dispatch_returns_one_when_any_track_needs_human(tmp_path, capsys, monkeypatch):
    from goodteam_os import cli

    project_root = tmp_path / "target"
    project_root.mkdir()

    monkeypatch.setattr(
        cli,
        "dispatch_tracks",
        lambda **kwargs: {
            "all_ok": False,
            "jobs": [{"role": "frontend", "outcome": "needs_human"}],
            "ok": [],
            "needs_human": [{"role": "frontend", "outcome": "needs_human"}],
        },
    )

    result = cli.main(
        [
            "dispatch",
            "--project-root",
            str(project_root),
            "--track",
            "frontend:fake:Implement frontend.",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["needs_human"][0]["role"] == "frontend"
