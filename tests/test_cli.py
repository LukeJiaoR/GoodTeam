import json
import subprocess
import sys


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
    assert (tmp_path / "state" / "current.json").exists()


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
        "architect=claude:opusplan",
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["model_profile"] == "cheap"
    assert payload["role_models"]["planner"]["primary"] == "deepseek:deepseek-chat"
    assert payload["role_models"]["architect"]["primary"] == "claude:opusplan"


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
    assert payload["role_models"]["architect"]["primary"] == "claude:opusplan"
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "artifacts").exists()
