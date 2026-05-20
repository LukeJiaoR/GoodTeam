import json
import subprocess
import sys


def test_cli_doctor_reports_project_target():
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "doctor",
        "--project-config",
        "projects/eastern-birth-chart.local.yaml",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["project"]["key"] == "EBC"
    assert payload["repos"]["docs"]["path"].endswith("/eastern-birth-chart-docs")
    assert "codex" in payload["workers"]["backend"]["command"]
