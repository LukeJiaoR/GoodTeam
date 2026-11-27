import json
import os
import subprocess
import sys
from textwrap import dedent

from goodteam_os.workers import discover_installed_agents


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


def test_codebuddy_model_discovery_uses_a_disposable_native_session(tmp_path, monkeypatch):
    cli = tmp_path / "codebuddy"
    cli.write_text(f"#!{sys.executable}\n" + dedent("""\
        import json
        import os
        from pathlib import Path
        import sys

        assert "--no-session-persistence" in sys.argv
        for line in sys.stdin:
            request = json.loads(line)
            if request["method"] == "initialize":
                result = {"protocolVersion": 1}
            else:
                assert request["method"] == "session/new"
                assert request["params"] == {"cwd": str(Path.cwd()), "mcpServers": []}
                assert Path(os.environ["CODEBUDDY_CONFIG_DIR"]).parent == Path.cwd()
                result = {"sessionId": "catalog", "models": {
                    "currentModelId": "alternate",
                    "availableModels": [{"modelId": "standard"}, {"modelId": "alternate"}],
                }}
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        """))
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)

    agent, = discover_installed_agents("codebuddy")

    assert agent["backend"] == "codebuddy"
    assert agent["models_source"] == "live"
    assert agent["models"] == ["standard", "alternate"]
    assert agent["model_default"] == "alternate"
