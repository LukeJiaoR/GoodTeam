from pathlib import Path

from goodteam_os.staging import (
    deployment_backend_config,
    run_staging_backend,
    staging_backend_enabled,
)


def _write_project_config(tmp_path: Path, *, include_smoke: bool = True) -> Path:
    project_config = tmp_path / "contentops.local.yaml"
    lines = [
        "name: AI Content Studio",
        "key: CONTENTOPS",
        "repos:",
        f"  docs: {tmp_path / 'docs'}",
        "deployment_backend:",
        "  mode: ssh_compose",
        "  staging_ssh_target: contentops-staging",
        "  staging_remote_dir: /srv/contentops",
        "  staging_git_ref: main",
        "  staging_public_url: https://staging.contentops.example.com",
        "  staging_deploy_command: git fetch --all && git checkout main && docker compose up -d --build",
        "  staging_healthcheck_command: docker compose ps && curl -fsS http://127.0.0.1:8080/health",
    ]
    if include_smoke:
        lines.append("  staging_smoke_command: curl -fsS https://staging.contentops.example.com/health")
    project_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return project_config


def _write_project_config_with_acceptance(tmp_path: Path) -> Path:
    project_config = _write_project_config(tmp_path)
    content = project_config.read_text(encoding="utf-8")
    project_config.write_text(
        content
        + "\n".join(
            [
                "delivery_contract:",
                "  required: true",
                "  minimum_evidence: functional",
                "  fatal_output_patterns:",
                "    migrate_missing: migrate: executable file not found",
                "    missing_relation: relation .* does not exist",
                "  staging_acceptance:",
                "    migrations:",
                "      type: ssh_command",
                "      command: docker compose exec -T backend ./migrate status",
                "    critical-user-journey:",
                "      type: local_command",
                "      command: ./scripts/e2e-critical.sh https://staging.contentops.example.com",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return project_config


def test_deployment_backend_config_reads_ssh_compose_settings(tmp_path):
    (tmp_path / "docs").mkdir()
    config = deployment_backend_config(_write_project_config(tmp_path))

    assert config["mode"] == "ssh_compose"
    assert config["staging_ssh_target"] == "contentops-staging"
    assert config["staging_remote_dir"] == "/srv/contentops"
    assert config["staging_git_ref"] == "main"
    assert config["staging_public_url"] == "https://staging.contentops.example.com"
    assert config["staging_smoke_command"] == "curl -fsS https://staging.contentops.example.com/health"


def test_delivery_contract_config_reads_functional_acceptance_contract(tmp_path):
    (tmp_path / "docs").mkdir()
    config = deployment_backend_config(_write_project_config_with_acceptance(tmp_path))

    assert config["delivery_contract"]["required"] is True
    assert config["delivery_contract"]["minimum_evidence"] == "functional"
    assert "migrate: executable file not found" in config["delivery_contract"]["fatal_output_patterns"]
    assert config["delivery_contract"]["staging_acceptance"] == [
        {
            "id": "migrations",
            "type": "ssh_command",
            "command": "docker compose exec -T backend ./migrate status",
        },
        {
            "id": "critical-user-journey",
            "type": "local_command",
            "command": "./scripts/e2e-critical.sh https://staging.contentops.example.com",
        },
    ]


def test_staging_backend_enabled_only_for_existing_project_with_ssh_compose(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path)

    assert staging_backend_enabled(source_type="existing_project", project_config=project_config) is True
    assert staging_backend_enabled(source_type="new_project", project_config=project_config) is False
    assert staging_backend_enabled(source_type="existing_project", project_config=tmp_path / "missing.local.yaml") is False


def test_run_staging_backend_runs_deploy_healthcheck_and_smoke(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append({"command": command, "cwd": str(cwd), "timeout": timeout})
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "passed"
    assert [step["label"] for step in result["steps"]] == ["deploy", "healthcheck", "smoke"]
    assert [step["timeout"] for step in calls] == [1800, 300, 300]
    assert all(call["command"][0:3] == ["ssh", "-o", "BatchMode=yes"] for call in calls)
    assert "cd /srv/contentops && git fetch --all" in calls[0]["command"][-1]
    assert "cd /srv/contentops && docker compose ps" in calls[1]["command"][-1]


def test_run_staging_backend_blocks_when_contract_required_but_functional_acceptance_missing(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path)
    content = project_config.read_text(encoding="utf-8")
    project_config.write_text(
        content
        + "\n".join(
            [
                "delivery_contract:",
                "  required: true",
                "  minimum_evidence: functional",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "failed"
    assert result["failed_step"] == "acceptance"
    assert result["evidence_level"] == "infrastructure"
    assert result["evidence_sufficient"] is False
    assert result["evidence_gap"] == "functional validation missing"


def test_run_staging_backend_runs_project_acceptance_contract_after_infra_smoke(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config_with_acceptance(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append({"command": command, "cwd": str(cwd), "timeout": timeout})
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "passed"
    assert result["evidence_level"] == "functional"
    assert result["evidence_sufficient"] is True
    assert [step["label"] for step in result["steps"]] == [
        "deploy",
        "healthcheck",
        "smoke",
        "acceptance:migrations",
        "acceptance:critical-user-journey",
    ]
    assert calls[3]["command"][0:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "cd /srv/contentops && docker compose exec -T backend ./migrate status" in calls[3]["command"][-1]
    assert calls[4]["command"] == [
        "/bin/sh",
        "-lc",
        "./scripts/e2e-critical.sh https://staging.contentops.example.com",
    ]


def test_run_staging_backend_fails_on_fatal_output_pattern_even_with_zero_exit(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config_with_acceptance(tmp_path)

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        stdout = "migrate: executable file not found" if "docker compose up" in command[-1] else "ok"
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "failed"
    assert result["failed_step"] == "deploy"
    assert result["steps"][0]["fatal_pattern"] == "migrate: executable file not found"
    assert result["evidence_sufficient"] is False


def test_run_staging_backend_stops_after_failed_deploy(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append(command)
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 1,
            "stdout": "",
            "stderr": "git pull failed",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "failed"
    assert result["failed_step"] == "deploy"
    assert len(calls) == 1


def test_run_staging_backend_skips_optional_smoke_command(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path, include_smoke=False)
    calls: list[dict[str, object]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append({"command": command, "timeout": timeout})
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "passed"
    assert [step["label"] for step in result["steps"]] == ["deploy", "healthcheck"]
    assert len(calls) == 2


def test_run_staging_backend_records_healthcheck_failure(tmp_path):
    (tmp_path / "docs").mkdir()
    project_config = _write_project_config(tmp_path)
    calls = {"count": 0}

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> dict[str, object]:
        calls["count"] += 1
        exit_code = 1 if calls["count"] == 2 else 0
        return {
            "command": command,
            "cwd": str(cwd),
            "timeout": timeout,
            "exit_code": exit_code,
            "stdout": "",
            "stderr": "health failed" if exit_code else "",
        }

    result = run_staging_backend(project_config=project_config, command_runner=fake_runner)

    assert result["status"] == "failed"
    assert result["failed_step"] == "healthcheck"
    assert [step["label"] for step in result["steps"]] == ["deploy", "healthcheck"]
    assert calls["count"] == 2
