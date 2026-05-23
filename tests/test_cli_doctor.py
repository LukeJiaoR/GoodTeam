import json
import subprocess
import sys


def test_cli_doctor_reports_project_target(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: Real Product\nkey: REAL\nrepos:\n  docs: {docs}\nworkers:\n  backend: codex\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "doctor",
        "--project-config",
        str(project_config),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["project"]["key"] == "REAL"
    assert payload["repos"]["docs"]["path"] == str(docs)
    assert "codex" in payload["workers"]["backend"]["command"]


def test_cli_doctor_requires_project_config():
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "doctor",
    ]

    completed = subprocess.run(command, capture_output=True, text=True)

    assert completed.returncode == 2
    assert "--project-config" in completed.stderr
