import json
import shlex
import sys

from goodteam_os.dispatch import run_track_verify
from goodteam_os.staging import _run_ssh_command
from goodteam_os.smoke import run_shell_command
from goodteam_os.full_smoke import _run_text
from goodteam_os.execution.sandbox_policy import command_env
from goodteam_os.web import _run_readonly_command


def test_project_commands_do_not_inherit_control_plane_database_configuration(tmp_path, monkeypatch):
    names = ("GOODTEAM_DATABASE_URL", "GOODTEAM_TEST_DATABASE_URL", "GOODTEAM_DATABASE_SCHEMA")
    for name in names:
        monkeypatch.setenv(name, "private-sentinel")
    monkeypatch.setenv("GOODTEAM_TEST_VISIBLE", "kept")
    probe = tmp_path / "probe.py"
    probe.write_text("import json, os\nprint(json.dumps({key: os.environ.get(key) for key in "
                     + repr((*names, "GOODTEAM_TEST_VISIBLE")) + "}))\n")
    command = [sys.executable, str(probe)]
    outputs = [
        run_track_verify(shlex.join(command), cwd=tmp_path)["stdout"],
        _run_ssh_command(command, cwd=tmp_path, timeout=10)["stdout"],
        run_shell_command(shlex.join(command), cwd=tmp_path)["stdout"],
        _run_text(command, cwd=tmp_path),
        _run_readonly_command(command, tmp_path, []).stdout,
    ]
    expected = dict.fromkeys(names) | {"GOODTEAM_TEST_VISIBLE": "kept"}
    assert [json.loads(value) for value in outputs] == [expected] * len(outputs)
    sandbox_env, redacted, secrets = command_env({"GOODTEAM_DATABASE_URL": "explicit-private-sentinel"})
    assert all(name not in sandbox_env for name in names)
    assert "GOODTEAM_DATABASE_URL" in redacted and "explicit-private-sentinel" in secrets
