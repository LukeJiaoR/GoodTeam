from pathlib import Path

from goodteam_os.projects import check_project_target, load_project_target


def test_load_project_target_reads_eastern_birth_chart_example_config():
    target = load_project_target(Path("projects/eastern-birth-chart.example.yaml"))

    assert target["name"] == "Eastern Birth Chart"
    assert target["key"] == "EBC"
    assert target["repos"]["docs"] == "<absolute-path-to-eastern-birth-chart-docs>"
    assert target["task_backend"]["mode"] == "local"
    assert "sync" in target["task_backend"]
    assert target["task_backend"]["plane"]["api_key_env"] == "PLANE_API_KEY"
    assert "api_key" not in target["task_backend"]["plane"]
    assert target["worker_backend"]["mode"] == "manual"
    assert target["workers"]["backend"] == "codex"
    assert target["workers"]["frontend"] == "agy"
    assert target["workers"]["product"] == "claude"
    assert target["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"


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
