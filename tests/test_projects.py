from pathlib import Path

from goodteam_os.projects import check_project_target, load_project_target


def test_load_project_target_reads_eastern_birth_chart_example_config():
    target = load_project_target(Path("projects/eastern-birth-chart.example.yaml"))

    assert target["name"] == "Eastern Birth Chart"
    assert target["key"] == "EBC"
    assert target["repos"]["docs"] == "<absolute-path-to-eastern-birth-chart-docs>"
    assert target["workers"]["backend"] == "codex"
    assert target["workers"]["frontend"] == "gemini"
    assert target["workers"]["product"] == "claude"


def test_check_project_target_reports_repo_and_cli_status():
    target = {
        "repos": {"docs": "/tmp/does-not-exist"},
        "workers": {"backend": "definitely-not-a-real-cli"},
    }

    report = check_project_target(target)

    assert report["repos"]["docs"]["exists"] is False
    assert report["workers"]["backend"]["available"] is False
