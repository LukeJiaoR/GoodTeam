
import pytest

from goodteam_os.project_reviewer_profiles import normalize_reviewer_profile_config
from goodteam_os.projects import check_project_target, load_project_target, update_worker_backend_config


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


def test_load_project_target_reads_reviewer_profile_lists(tmp_path):
    config = tmp_path / "project.local.yaml"
    config.write_text(
        """
name: Real Product
key: REAL
reviewer_profiles:
  implementation:
    - correctness-security
    - architecture-maintainability
  release:
    - product-operator
""",
        encoding="utf-8",
    )

    target = load_project_target(config)

    assert target["reviewer_profiles"]["implementation"] == [
        "correctness-security",
        "architecture-maintainability",
    ]
    assert target["reviewer_profiles"]["release"] == ["product-operator"]


def test_check_project_target_reports_repo_and_cli_status():
    target = {
        "repos": {"docs": "/tmp/does-not-exist"},
        "workers": {"backend": "definitely-not-a-real-cli"},
    }

    report = check_project_target(target)

    assert report["repos"]["docs"]["exists"] is False
    assert report["workers"]["backend"]["available"] is False


def test_reviewer_profile_config_is_optional():
    assert normalize_reviewer_profile_config({}) == {}

    report = check_project_target({"worker_backend": {"mode": "fake"}})

    assert report["reviewer_profiles"] == {
        "configured": {},
        "profiles": [],
        "errors": [],
        "warnings": [],
    }
    assert report["reviewer_dispatch"]["mode"] == "single"
    assert report["reviewer_dispatch"]["warnings"] == []
    assert report["reviewer_dispatch"]["errors"] == []


def test_reviewer_profile_config_accepts_explicit_empty_phases():
    target = {"reviewer_profiles": {"implementation": [], "release": "[]"}}

    assert normalize_reviewer_profile_config(target) == {"implementation": (), "release": ()}


def test_reviewer_profile_config_dedupes_ids_and_accepts_comma_strings():
    target = {
        "reviewer_profiles": {
            "implementation": [
                "correctness-security",
                "architecture-maintainability",
                "correctness-security",
            ],
            "release": "product-operator, correctness-security",
        }
    }

    assert normalize_reviewer_profile_config(target) == {
        "implementation": ("correctness-security", "architecture-maintainability"),
        "release": ("product-operator", "correctness-security"),
    }


def test_reviewer_profile_config_rejects_unknown_profile_ids_clearly():
    target = {"reviewer_profiles": {"implementation": ["missing-reviewer"]}}

    with pytest.raises(ValueError, match="Unknown reviewer profile 'missing-reviewer'"):
        normalize_reviewer_profile_config(target)

    report = check_project_target(target)
    assert report["reviewer_profiles"]["errors"] == ["Unknown reviewer profile 'missing-reviewer'"]


def test_reviewer_profile_config_rejects_unknown_phases_clearly():
    target = {"reviewer_profiles": {"design": ["correctness-security"]}}

    with pytest.raises(ValueError, match="Unknown reviewer profile phase 'design'"):
        normalize_reviewer_profile_config(target)


def test_check_project_target_reports_reviewer_adapter_evidence_without_worker_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_worker_backend_doctor(mode):
        calls.append(mode)
        if mode == "codex":
            return {
                "mode": "codex",
                "kind": "cli",
                "command": "codex",
                "command_path": "",
                "available": False,
                "ready": False,
                "hint": "Codex CLI not found.",
            }
        return {"mode": mode, "kind": "builtin", "ready": True, "hint": "ready"}

    import goodteam_os.dispatch as dispatch

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("reviewer profile config must not dispatch worker jobs")

    monkeypatch.setattr(dispatch, "dispatch_tracks", fail_dispatch)
    monkeypatch.setattr("goodteam_os.projects.worker_backend_doctor", fake_worker_backend_doctor)
    target = {
        "worker_backend": {"mode": "fake"},
        "reviewer_profiles": {"implementation": ["correctness-security"]},
    }

    report = check_project_target(target)

    assert report["worker_backends_preflight"] == {
        "fake": {"mode": "fake", "kind": "builtin", "ready": True, "hint": "ready"}
    }
    assert "codex" not in report["worker_backends_preflight"]
    reviewer_report = report["reviewer_profiles"]
    assert reviewer_report["configured"] == {"implementation": ["correctness-security"]}
    [profile] = reviewer_report["profiles"]
    assert profile["phase"] == "implementation"
    assert profile["profile_id"] == "correctness-security"
    assert profile["adapter"] == "codex"
    assert profile["available"] is False
    assert profile["ready"] is False
    assert profile["fallback_policy"] == "manual_review_required_on_missing_evidence"
    assert profile["evidence"]["command"] == "codex"
    assert profile["evidence"]["ready"] is False
    [warning] = reviewer_report["warnings"]
    assert warning["profile_id"] == "correctness-security"
    assert warning["adapter"] == "codex"
    assert warning["fallback_policy"] == "manual_review_required_on_missing_evidence"
    assert warning["evidence"]["command"] == "codex"
    assert "not ready" in warning["message"]
    assert calls == ["fake", "fake", "codex"]
    assert not (tmp_path / ".goodteam").exists()


def test_check_project_target_reports_per_role_worker_backends(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    target = {
        "worker_backend": {"mode": "codex"},
        "workers": {"implementation": "anthropic", "qa": "codex", "ui": ""},
    }

    report = check_project_target(target)

    # ui has no explicit backend, so it resolves to the global default.
    assert report["role_worker_backends"] == {
        "implementation": "anthropic",
        "qa": "codex",
        "ui": "codex",
    }
    # every distinct backend referenced gets its own readiness report.
    preflight = report["worker_backends_preflight"]
    assert set(preflight) == {"anthropic", "codex"}
    assert preflight["anthropic"]["command"] == "claude"
    assert preflight["codex"]["command"] == "codex"


def test_update_worker_backend_config_preserves_unrelated_blocks(tmp_path):
    config = tmp_path / "project.local.yaml"
    config.write_text(
        """# local machine config
name: Real Product
key: REAL
repos:
  docs: /tmp/docs
  backend: /tmp/backend
worker_backend:
  mode: fake
  old_child: should disappear
workers:
  implementation: fake
  qa: fake
providers:
  deepseek:
    type: openai-compatible
    base_url: https://api.deepseek.com/v1
delivery_contract:
  required: true
  staging_acceptance:
    smoke:
      type: local_command
      command: npm run smoke
""",
        encoding="utf-8",
    )
    before = config.read_text(encoding="utf-8")
    repos_block = before[before.index("repos:") : before.index("worker_backend:")]
    providers_and_delivery = before[before.index("providers:") :]

    update_worker_backend_config(
        config,
        mode="codex",
        workers={"implementation": "anthropic", "qa": "codex"},
    )

    after = config.read_text(encoding="utf-8")
    target = load_project_target(config)
    assert target["worker_backend"]["mode"] == "codex"
    assert target["workers"] == {"implementation": "anthropic", "qa": "codex"}
    assert repos_block in after
    assert providers_and_delivery in after
    assert "old_child" not in after


def test_update_worker_backend_config_omitted_role_removes_override(tmp_path):
    config = tmp_path / "project.local.yaml"
    config.write_text(
        """name: Real Product
key: REAL
worker_backend:
  mode: fake
workers:
  implementation: anthropic
  qa: codex
""",
        encoding="utf-8",
    )

    update_worker_backend_config(config, mode="codex", workers={"qa": "codex"})

    target = load_project_target(config)
    assert target["worker_backend"]["mode"] == "codex"
    assert target["workers"] == {"qa": "codex"}


def test_update_worker_backend_config_empty_workers_removes_block(tmp_path):
    config = tmp_path / "project.local.yaml"
    config.write_text(
        """name: Real Product
key: REAL
worker_backend:
  mode: anthropic
workers:
  implementation: anthropic
  qa: codex
task_backend:
  mode: local
""",
        encoding="utf-8",
    )

    update_worker_backend_config(config, mode="human", workers={})

    after = config.read_text(encoding="utf-8")
    target = load_project_target(config)
    assert target["worker_backend"]["mode"] == "human"
    assert "workers:" not in after
    assert "task_backend:\n  mode: local\n" in after
    assert "workers" not in target


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
