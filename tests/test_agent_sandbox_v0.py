from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun


class FakeClock:
    def __init__(self):
        self.values = [f"2026-07-06T04:{minute:02d}:00Z" for minute in range(20)]
        self.index = 0

    def __call__(self) -> str:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class FakeSandboxGitAdapter:
    def __init__(self, *, fail_create: bool = False):
        self.fail_create = fail_create
        self.created: list[dict[str, object]] = []
        self.removed: list[dict[str, object]] = []

    def create_worktree(
        self,
        *,
        repo_root: Path,
        worktree_path: Path,
        branch_name: str,
        base_ref: str,
    ) -> dict[str, object]:
        self.created.append(
            {
                "repo_root": repo_root,
                "worktree_path": worktree_path,
                "branch_name": branch_name,
                "base_ref": base_ref,
            }
        )
        if self.fail_create:
            raise RuntimeError("worktree failed")
        worktree_path.mkdir(parents=True, exist_ok=True)
        return {"status": "created", "head_ref": base_ref}

    def remove_worktree(self, *, repo_root: Path, worktree_path: Path) -> dict[str, object]:
        self.removed.append({"repo_root": repo_root, "worktree_path": worktree_path})
        return {"status": "removed"}


def test_create_workspace_sandbox_records_metadata_and_ledger(tmp_path):
    execution_id = "exec_sandbox_workspace"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())

    result = api.create_sandbox(
        execution_id,
        claimed_paths=["src/goodteam_os/execution/api.py"],
        mode="workspace",
    )

    metadata_path = tmp_path / ".goodteam" / "executions" / execution_id / "sandbox.json"
    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "active"
    assert result["schema_version"] == "sandbox.v1"
    assert result["execution_id"] == execution_id
    assert result["mode"] == "workspace"
    assert result["workspace_path"] == str(
        tmp_path / ".goodteam" / "sandboxes" / execution_id / "workspace"
    )
    assert result["worktree_path"] == ""
    assert result["branch_name"] == f"sandbox/{execution_id}"
    assert result["claimed_paths"] == ["src/goodteam_os/execution/api.py"]
    assert result["conflict_risk"] is False
    assert metadata_path.is_file()
    assert event.event_type == "status_changed"
    assert event.payload["transition"] == "sandbox_created"
    assert event.payload["source"] == "sandbox"
    assert event.payload["schema_version"] == "sandbox.v1"


def test_worktree_adapter_receives_deterministic_path_and_branch(tmp_path):
    execution_id = "exec_sandbox_worktree"
    _create_run(tmp_path, execution_id)
    adapter = FakeSandboxGitAdapter()
    api = ExecutionAPI(tmp_path, clock=FakeClock(), sandbox_git_adapter=adapter)

    result = api.create_sandbox(execution_id, claimed_paths=["src/a.py"], mode="worktree")

    call = adapter.created[0]
    assert result["status"] == "active"
    assert result["mode"] == "worktree"
    assert result["worktree_path"] == str(
        tmp_path / ".goodteam" / "sandboxes" / execution_id / "worktree"
    )
    assert result["workspace_path"] == result["worktree_path"]
    assert call["repo_root"] == tmp_path
    assert call["worktree_path"] == Path(result["worktree_path"])
    assert call["branch_name"] == f"sandbox/{execution_id}"
    assert call["base_ref"] == "HEAD"


def test_worktree_create_failure_records_event_without_active_metadata(tmp_path):
    execution_id = "exec_sandbox_worktree_fail"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(
        tmp_path,
        clock=FakeClock(),
        sandbox_git_adapter=FakeSandboxGitAdapter(fail_create=True),
    )

    result = api.create_sandbox(execution_id, claimed_paths=["src/a.py"], mode="worktree")

    metadata_path = tmp_path / ".goodteam" / "executions" / execution_id / "sandbox.json"
    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "failed"
    assert result["reason"] == "worktree_create_failed"
    assert not metadata_path.exists()
    assert event.payload["transition"] == "sandbox_create_failed"
    assert event.payload["reason"] == "worktree_create_failed"


def test_destroy_workspace_sandbox_marks_metadata_and_writes_ledger(tmp_path):
    execution_id = "exec_sandbox_destroy_workspace"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id, mode="workspace")

    result = api.destroy_sandbox(execution_id)

    events = _events(tmp_path, execution_id)
    metadata = api.get_sandbox(execution_id)
    assert result["status"] == "destroyed"
    assert result["mode"] == "workspace"
    assert metadata["status"] == "destroyed"
    assert events[-1].payload["transition"] == "sandbox_destroyed"
    assert events[-1].payload["workspace_path"] == result["workspace_path"]


def test_destroy_worktree_sandbox_uses_git_adapter_remove(tmp_path):
    execution_id = "exec_sandbox_destroy_worktree"
    _create_run(tmp_path, execution_id)
    adapter = FakeSandboxGitAdapter()
    api = ExecutionAPI(tmp_path, clock=FakeClock(), sandbox_git_adapter=adapter)
    sandbox = api.create_sandbox(execution_id, mode="worktree")

    result = api.destroy_sandbox(execution_id)

    assert result["status"] == "destroyed"
    assert result["mode"] == "worktree"
    assert adapter.removed == [
        {
            "repo_root": tmp_path,
            "worktree_path": Path(sandbox["worktree_path"]),
        }
    ]
    assert _events(tmp_path, execution_id)[-1].payload["transition"] == "sandbox_destroyed"


def test_destroy_sandbox_is_idempotent_for_already_destroyed_metadata(tmp_path):
    execution_id = "exec_sandbox_destroy_idempotent"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)
    first = api.destroy_sandbox(execution_id)
    before = _events(tmp_path, execution_id)

    second = api.destroy_sandbox(execution_id)

    assert first["status"] == "destroyed"
    assert second["status"] == "already_destroyed"
    assert second["sandbox_id"] == first["sandbox_id"]
    assert _events(tmp_path, execution_id) == before


def test_path_overlap_is_advisory_and_records_conflict_event(tmp_path):
    first_id = "exec_sandbox_first"
    second_id = "exec_sandbox_second"
    _create_run(tmp_path, first_id)
    _create_run(tmp_path, second_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(first_id, claimed_paths=["src/goodteam_os/execution/api.py"])

    result = api.create_sandbox(second_id, claimed_paths=["src/goodteam_os/execution"])

    transitions = [event.payload.get("transition") for event in _events(tmp_path, second_id)]
    assert result["status"] == "active"
    assert result["conflict_risk"] is True
    assert result["conflict_risks"][0]["execution_id"] == first_id
    assert result["conflict_risks"][0]["path"] == "src/goodteam_os/execution"
    assert transitions == ["sandbox_conflict_risk_detected", "sandbox_created"]


@pytest.mark.parametrize(
    ("argv", "reason"),
    [
        (["sh", "-c", "echo unsafe"], "shell_command_rejected"),
        (["bash", "-c", "echo unsafe"], "shell_command_rejected"),
        (["python", "-c", "print('a')", "&&", "python", "-V"], "chained_command_rejected"),
        (["python", "-c", "print('a');print('b')"], "chained_command_rejected"),
        (["python", "-c", "print('a')|cat"], "chained_command_rejected"),
        (["gh", "pr", "view"], "unknown_command"),
    ],
)
def test_command_policy_rejects_unsafe_commands_and_writes_ledger(tmp_path, argv, reason):
    execution_id = "exec_sandbox_rejected"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)

    result = api.run_sandbox_command(execution_id, argv)

    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "rejected"
    assert result["reason"] == reason
    assert event.payload["transition"] == "sandbox_command_rejected"
    assert event.payload["reason"] == reason
    assert event.payload["argv"] == argv


def test_command_policy_rejects_shell_string_and_cwd_outside_sandbox(tmp_path):
    execution_id = "exec_sandbox_cwd"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)

    shell_result = api.run_sandbox_command(execution_id, "python -V")
    cwd_result = api.run_sandbox_command(execution_id, [sys.executable, "-V"], cwd=tmp_path)

    assert shell_result["status"] == "rejected"
    assert shell_result["reason"] == "shell_string_rejected"
    assert cwd_result["status"] == "rejected"
    assert cwd_result["reason"] == "cwd_outside_sandbox"


def test_rejected_command_scrubs_sensitive_argv_and_env(tmp_path):
    execution_id = "exec_sandbox_rejected_secret"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    api.create_sandbox(execution_id)

    result = api.run_sandbox_command(
        execution_id,
        ["unknown-tool", "ghp_rejected_secret"],
        env={"GH_TOKEN": "ghp_rejected_secret"},
    )

    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "rejected"
    assert result["argv"] == ["unknown-tool", "[REDACTED]"]
    assert "ghp_rejected_secret" not in str(event.to_dict())
    assert "GH_TOKEN" in event.payload["redacted_env_keys"]


def test_command_runner_scrubs_secrets_bounds_output_and_records_nonzero(tmp_path):
    execution_id = "exec_sandbox_command"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    sandbox = api.create_sandbox(execution_id)

    result = api.run_sandbox_command(
        execution_id,
        [
            sys.executable,
            "-c",
            (
                "import os\n"
                "import sys\n"
                "print(os.environ.get('GH_TOKEN'))\n"
                "print('x' * 5000)\n"
                "print(os.environ.get('AWS_SECRET_ACCESS_KEY'), file=sys.stderr)\n"
                "sys.exit(7)"
            ),
        ],
        cwd=Path(sandbox["workspace_path"]),
        env={
            "GH_TOKEN": "ghp_real_secret",
            "AWS_SECRET_ACCESS_KEY": "aws_real_secret",
            "SAFE_VALUE": "ok",
        },
    )

    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "completed"
    assert result["exit_code"] == 7
    assert "ghp_real_secret" not in result["stdout_excerpt"]
    assert "aws_real_secret" not in result["stderr_excerpt"]
    assert "[REDACTED]" in result["stdout_excerpt"]
    assert "[REDACTED]" in result["stderr_excerpt"]
    assert len(result["stdout_excerpt"]) <= 1200
    assert event.payload["transition"] == "sandbox_command_executed"
    assert event.payload["exit_code"] == 7
    assert {"AWS_SECRET_ACCESS_KEY", "GH_TOKEN"} <= set(event.payload["redacted_env_keys"])
    assert "ghp_real_secret" not in str(event.to_dict())
    assert api.get_sandbox(execution_id)["status"] == "active"


def test_command_timeout_writes_failed_ledger_event(tmp_path, monkeypatch):
    execution_id = "exec_sandbox_timeout"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    sandbox = api.create_sandbox(execution_id)

    def fail_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args", args[0] if args else []),
            timeout=1,
            output="stdout ghp_timeout_secret",
            stderr="stderr ghp_timeout_secret",
        )

    monkeypatch.setattr("goodteam_os.execution.sandbox.subprocess.run", fail_timeout)

    result = api.run_sandbox_command(
        execution_id,
        [sys.executable, "-V"],
        cwd=Path(sandbox["workspace_path"]),
        env={"GH_TOKEN": "ghp_timeout_secret"},
    )

    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "failed"
    assert result["reason"] == "timeout"
    assert "[REDACTED]" in result["stdout_excerpt"]
    assert "[REDACTED]" in result["stderr_excerpt"]
    assert event.payload["transition"] == "sandbox_command_failed"
    assert "ghp_timeout_secret" not in str(event.to_dict())
    assert api.get_sandbox(execution_id)["status"] == "active"


def test_command_spawn_failure_writes_failed_ledger_event(tmp_path, monkeypatch):
    execution_id = "exec_sandbox_spawn_failed"
    _create_run(tmp_path, execution_id)
    api = ExecutionAPI(tmp_path, clock=FakeClock())
    sandbox = api.create_sandbox(execution_id)

    def fail_spawn(*args, **kwargs):
        raise OSError("spawn failed with sk_spawn_secret")

    monkeypatch.setattr("goodteam_os.execution.sandbox.subprocess.run", fail_spawn)

    result = api.run_sandbox_command(
        execution_id,
        [sys.executable, "-V", "sk_spawn_secret"],
        cwd=Path(sandbox["workspace_path"]),
        env={"OPENAI_API_KEY": "sk_spawn_secret"},
    )

    event = _events(tmp_path, execution_id)[-1]
    assert result["status"] == "failed"
    assert result["reason"] == "spawn_failed"
    assert result["argv"] == [sys.executable, "-V", "[REDACTED]"]
    assert "[REDACTED]" in result["error_excerpt"]
    assert event.payload["transition"] == "sandbox_command_failed"
    assert "sk_spawn_secret" not in str(event.to_dict())
    assert api.get_sandbox(execution_id)["status"] == "active"


def _create_run(tmp_path: Path, execution_id: str) -> None:
    ExecutionLedger(tmp_path).create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id=f"task_{execution_id}",
            worker_job_id=f"worker_{execution_id}",
            adapter="fake",
            status="succeeded",
            branch_name=f"goodteam/{execution_id}",
            pr_number=None,
            created_at="2026-07-06T04:00:00Z",
            updated_at="2026-07-06T04:01:00Z",
        )
    )


def _events(tmp_path: Path, execution_id: str):
    return ExecutionLedger(tmp_path).read_events(execution_id)
