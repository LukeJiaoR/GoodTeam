"""
[INPUT]: 依赖数据库配置边界、真实连接池和隔离子进程。
[OUTPUT]: 验证子进程配置隔离与连接池退出清理。
[POS]: 数据库进程边界回归；不连接真实数据库。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import shlex
import sys
import subprocess
import textwrap

from goodteam_os.dispatch import run_track_verify
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
        run_shell_command(shlex.join(command), cwd=tmp_path)["stdout"],
        _run_text(command, cwd=tmp_path),
        _run_readonly_command(command, tmp_path, []).stdout,
    ]
    expected = dict.fromkeys(names) | {"GOODTEAM_TEST_VISIBLE": "kept"}
    assert [json.loads(value) for value in outputs] == [expected] * len(outputs)
    sandbox_env, redacted, secrets = command_env({"GOODTEAM_DATABASE_URL": "explicit-private-sentinel"})
    assert all(name not in sandbox_env for name in names)
    assert "GOODTEAM_DATABASE_URL" in redacted and "explicit-private-sentinel" in secrets


def test_database_pool_closes_before_interpreter_finalization():
    probe = textwrap.dedent("""\
        import atexit
        from unittest.mock import MagicMock, patch
        from goodteam_os.database import PostgresDatabase

        pools = []
        def verify_closed():
            assert pools and all(pool.closed for pool in pools)
            print("pools closed")

        # atexit 逆序执行；断言必须在连接池清理后执行。
        atexit.register(verify_closed)
        with patch("goodteam_os.database.psycopg.connect", return_value=MagicMock()):
            for schema in ("first", "second"):
                db = PostgresDatabase("postgresql://unused", schema)
                pools.append(db.pool)
        """)
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pools closed"
    assert result.stderr == ""
