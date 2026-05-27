from pathlib import Path

from goodteam_os.projects import check_project_target, load_project_target


def test_load_project_target_reads_project_config(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "project.local.yaml"
    config.write_text(
        f"""
name: Real Product
key: REAL
model_profile: balanced
delivery_mode: multi_service
role_model_overrides:
  architect: claude:opusplan
repos:
  docs: {docs}
task_backend:
  mode: local
  sync:
worker_backend:
  mode: manual
workers:
  backend: codex
  frontend: agy
  product: claude
providers:
  deepseek:
    type: openai-compatible
    api_key_env: DEEPSEEK_API_KEY
""",
        encoding="utf-8",
    )

    target = load_project_target(config)

    assert target["name"] == "Real Product"
    assert target["key"] == "REAL"
    assert target["repos"]["docs"] == str(docs)
    assert target["task_backend"]["mode"] == "local"
    assert "sync" in target["task_backend"]
    assert "plane" not in target["task_backend"]
    assert target["worker_backend"]["mode"] == "manual"
    assert target["workers"]["backend"] == "codex"
    assert target["workers"]["frontend"] == "agy"
    assert target["workers"]["product"] == "claude"
    assert target["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert target["model_profile"] == "balanced"
    assert target["delivery_mode"] == "multi_service"
    assert target["role_model_overrides"]["architect"] == "claude:opusplan"


def test_check_project_target_reports_repo_and_cli_status():
    target = {
        "repos": {"docs": "/tmp/does-not-exist"},
        "workers": {"backend": "definitely-not-a-real-cli"},
    }

    report = check_project_target(target)

    assert report["repos"]["docs"]["exists"] is False
    assert report["workers"]["backend"]["available"] is False


def test_check_project_target_reports_deepseek_provider_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    target = {
        "providers": {
            "deepseek": {
                "type": "openai-compatible",
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com",
            }
        }
    }

    report = check_project_target(target)

    assert report["providers"]["deepseek"]["configured"] is True
    assert report["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "test-key-not-real" not in str(report)


def test_check_project_target_reports_deepseek_provider_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "# local secrets",
                "export DEEPSEEK_API_KEY='test-key-not-real'",
                "IGNORED_WITHOUT_LOOKUP=also-secret",
            ]
        ),
        encoding="utf-8",
    )
    target = {
        "providers": {
            "deepseek": {
                "type": "openai-compatible",
                "api_key_env": "DEEPSEEK_API_KEY",
                "env_file": str(env_file),
                "base_url": "https://api.deepseek.com",
            }
        }
    }

    report = check_project_target(target)

    assert report["providers"]["deepseek"]["configured"] is True
    assert report["providers"]["deepseek"]["env_file"] == str(env_file)
    assert "test-key-not-real" not in str(report)
    assert "also-secret" not in str(report)


def test_check_project_target_reports_ssh_compose_deployment_backend(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.projects.which", lambda command: "/usr/bin/ssh" if command == "ssh" else None)
    target = {
        "repos": {"docs": str(docs)},
        "deployment_backend": {
            "mode": "ssh_compose",
            "staging_ssh_target": "contentops-staging",
            "staging_remote_dir": "/srv/contentops",
            "staging_deploy_command": "docker compose up -d --build",
            "staging_healthcheck_command": "curl -fsS http://127.0.0.1:8080/health",
            "staging_public_url": "https://staging.contentops.example.com",
        },
    }

    report = check_project_target(target)

    assert report["deployment_backend"]["mode"] == "ssh_compose"
    assert report["deployment_backend"]["configured"] is True
    assert report["deployment_backend"]["missing_fields"] == []
    assert report["deployment_backend"]["ssh_available"] is True
    assert report["deployment_backend"]["staging_public_url"] == "https://staging.contentops.example.com"


def test_check_project_target_reports_delivery_contract_evidence_gap(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.projects.which", lambda command: "/usr/bin/ssh" if command == "ssh" else None)
    target = {
        "repos": {"docs": str(docs)},
        "deployment_backend": {
            "mode": "ssh_compose",
            "staging_ssh_target": "contentops-staging",
            "staging_remote_dir": "/srv/contentops",
            "staging_deploy_command": "docker compose up -d --build",
            "staging_healthcheck_command": "curl -fsS http://127.0.0.1:8080/health",
        },
        "delivery_contract": {
            "required": True,
            "minimum_evidence": "functional",
        },
    }

    report = check_project_target(target)

    assert report["delivery_contract"]["required"] is True
    assert report["delivery_contract"]["minimum_evidence"] == "functional"
    assert report["delivery_contract"]["configured"] is False
    assert report["delivery_contract"]["evidence_level"] == "infrastructure"
    assert report["delivery_contract"]["missing_fields"] == ["staging_acceptance"]


def test_check_project_target_reports_configured_functional_delivery_contract(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.projects.which", lambda command: "/usr/bin/ssh" if command == "ssh" else None)
    target = {
        "repos": {"docs": str(docs)},
        "deployment_backend": {
            "mode": "ssh_compose",
            "staging_ssh_target": "contentops-staging",
            "staging_remote_dir": "/srv/contentops",
            "staging_deploy_command": "docker compose up -d --build",
            "staging_healthcheck_command": "curl -fsS http://127.0.0.1:8080/health",
        },
        "delivery_contract": {
            "required": True,
            "minimum_evidence": "functional",
            "fatal_output_patterns": ["migrate: executable file not found"],
            "staging_acceptance": [
                {
                    "id": "critical-user-journey",
                    "type": "local_command",
                    "command": "./scripts/e2e-critical.sh",
                }
            ],
        },
    }

    report = check_project_target(target)

    assert report["delivery_contract"]["configured"] is True
    assert report["delivery_contract"]["evidence_level"] == "functional"
    assert report["delivery_contract"]["acceptance_count"] == 1
    assert report["delivery_contract"]["fatal_output_patterns_count"] == 1


def test_check_project_target_reports_missing_ssh_compose_fields(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.projects.which", lambda command: None)
    target = {
        "repos": {"docs": str(docs)},
        "deployment_backend": {
            "mode": "ssh_compose",
            "staging_ssh_target": "contentops-staging",
        },
    }

    report = check_project_target(target)

    assert report["deployment_backend"]["configured"] is False
    assert report["deployment_backend"]["ssh_available"] is False
    assert report["deployment_backend"]["missing_fields"] == [
        "staging_remote_dir",
        "staging_deploy_command",
        "staging_healthcheck_command",
    ]
