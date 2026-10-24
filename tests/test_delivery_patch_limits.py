"""Focused output-boundary tests; mocked Git is not a trusted-Git integration run."""
from contextlib import nullcontext
from types import SimpleNamespace
import subprocess

import pytest

from goodteam_os import dispatch_workspace as dispatch
from goodteam_os import workflow_workspace as workflow


@pytest.mark.parametrize("returncode", [0, 1])
def test_git_output_limit_distinguishes_success_from_failure(tmp_path, monkeypatch, returncode):
    monkeypatch.setattr(dispatch, "_trusted_git_executable", lambda *args: ("git", "/usr/bin", {}))
    monkeypatch.setattr(dispatch, "_open_verified_executable", lambda *args: None)
    monkeypatch.setattr(dispatch.os, "close", lambda descriptor: None)

    def run(command, **kwargs):
        kwargs["stdout"].write(b"x" * 17)
        kwargs["stderr"].write(b"actual git failure" if returncode else b"")
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(dispatch.subprocess, "run", run)
    with pytest.raises(dispatch.DispatchWorkspaceError) as error:
        dispatch._read_git(tmp_path, ["diff"], max_output_bytes=16)
    assert isinstance(error.value, dispatch.DispatchGitOutputLimitError) == (returncode == 0)
    if returncode:
        assert "actual git failure" in str(error.value)


@pytest.mark.parametrize("failure", ["size", "git", "identity", "parents"])
def test_optional_patch_limit_does_not_hide_integrity_errors(tmp_path, monkeypatch, failure):
    baseline, commit = "a" * 40, "b" * 40
    root = tmp_path / "checkout"
    project, run = "DEMO", "delivery-run"
    result = {"schema": workflow.SCHEMA, "project_key": project, "run_id": run,
              "job_id": workflow.SLOT, "workspace_path": str(root),
              "baseline_commit": baseline, "result_commit": commit, "changed_files": ["safe.txt"]}
    workspace = SimpleNamespace(metadata={"baseline_commit": baseline}, _verify_job=lambda slot: root)
    monkeypatch.setattr(workflow, "_scope", lambda *args: (tmp_path, "fixture"))
    monkeypatch.setattr(workflow, "_reopen", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(workflow, "dispatch_workspace_lock", lambda *args: nullcontext())
    seen_limits = []

    def read(root, arguments, **kwargs):
        command = arguments[0]
        if command == "rev-list":
            return f"{commit} {('c' * 40) if failure == 'parents' else baseline}\n".encode()
        if command == "diff" and "--name-only" in arguments:
            return b"safe.txt\0"
        if command == "diff":
            seen_limits.append(kwargs.get("max_output_bytes", 32 * 1024 * 1024))
            if failure == "git":
                raise dispatch.DispatchWorkspaceError("actual git failure")
            raise dispatch.DispatchGitOutputLimitError("limit")
        if command == "ls-tree":
            return b"100644 blob " + b"c" * 40 + b"\tsafe.txt\0"
        if command == "cat-file":
            return b"5" if "-s" in arguments else b"safe\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(workflow, "_read_git", read)
    if failure == "identity":
        result["project_key"] = "OTHER"
    if failure != "size":
        with pytest.raises(workflow.WorkflowWorkspaceError):
            workflow.read_workflow_delivery(tmp_path, {}, result, project_key=project, run_id=run, max_bytes=16, max_files=32)
    else:
        items = workflow.read_workflow_delivery(tmp_path, {}, result, project_key=project, run_id=run, max_bytes=16, max_files=32)
        assert seen_limits == [16]
        assert items[0]["unavailable_reason"] == "preview_limit"
        assert "data" not in items[0]
        assert items[1]["data"] == b"safe\n"
