from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from goodteam_os import workers
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from goodteam_os.workflow_workspace import (
    WorkflowWorkspaceError,
    adapter_requires_workflow_workspace,
    collect_workflow_workspace,
    context_for_job,
    prepare_workflow_workspace,
    workspace_for_workflow,
)


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", *arguments], cwd=root,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "Tests",
             "GIT_AUTHOR_EMAIL": "tests@goodteam.invalid", "GIT_COMMITTER_NAME": "Tests",
             "GIT_COMMITTER_EMAIL": "tests@goodteam.invalid"},
    ).strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "code.txt").write_bytes(b"committed\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def test_sequential_public_workers_share_frozen_raw_baseline_and_leave_source_intact(source, tmp_path, monkeypatch):
    (source / "code.txt").write_bytes(b"staged\n")
    git(source, "add", "code.txt")
    (source / "code.txt").write_bytes(b"raw dirty\r\n\xff")
    (source / "human.bin").write_bytes(b"\x00input")
    original_head, original_index = git(source, "rev-parse", "HEAD"), (source / ".git/index").read_bytes()
    binding = prepare_workflow_workspace(source, project_key="DEMO", run_id="run-one")
    task_root = workspace_for_workflow(source, binding, project_key="DEMO", run_id="run-one")
    assert (task_root / "code.txt").read_bytes() == b"raw dirty\r\n\xff"
    assert (task_root / "human.bin").read_bytes() == b"\x00input"
    assert (task_root / ".git/index").read_bytes() == original_index
    script = tmp_path / "local_worker.py"
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "role=json.loads(Path(sys.argv[1]).read_text())['role']\n"
        "p=Path('sequence.txt')\nprior=p.read_text() if p.exists() else ''\n"
        "assert prior == {'ui':'', 'implementation':'ui\\n', 'qa':'ui\\nimplementation\\n'}[role]\n"
        "p.write_text(prior + role + '\\n')\n"
        "print('# Worker Result\\n\\nStatus: pass\\n\\n' + str(Path.cwd()))\n",
        encoding="utf-8",
    )
    spec = replace(workers.CODEX_BACKEND, name="workflow-local", which_name="workflow-local",
                   default_path=lambda: sys.executable, isolated_home=False, capture="stdout",
                   build_command=lambda binary, model, root, result: [binary, str(script), str(result.parent / "job.json")])
    monkeypatch.setitem(workers.CLI_BACKENDS, "workflow-local", spec)
    store = workers.WorkerJobStore(source)
    (source / "code.txt").write_bytes(b"human continued editing\n")
    for role in ("ui", "implementation", "qa"):
        job = store.create_job(run_id="run-one", project_key="DEMO", role=role, adapter="workflow-local",
                               task="Local fixture only", context="", workflow_workspace=binding)
        result = store.run_job(job["id"])
        assert result["status"] == "completed"
        assert result["runtime_cwd"] == str(task_root)
        assert result["runtime_workspace_mode"] == "workflow_isolated"
        assert result["workspace_cwd_fallback"] is False
        assert (store.root / job["id"] / "job.json").is_file()
    task_head, task_index = git(task_root, "rev-parse", "HEAD"), (task_root / ".git/index").read_bytes()
    evidence = collect_workflow_workspace(source, binding, project_key="DEMO", run_id="run-one", candidate=True)
    assert evidence["attribution_scope"] == "workflow_task"
    assert evidence["changed_files"] == ["sequence.txt"]
    assert evidence["integration"]["status"] == "succeeded"
    candidate = Path(evidence["integration"]["candidate_path"])
    assert (candidate / "sequence.txt").read_text() == "ui\nimplementation\nqa\n"
    assert (candidate / "code.txt").read_bytes() == b"raw dirty\r\n\xff"
    assert git(task_root, "rev-parse", "HEAD") == task_head
    assert (task_root / ".git/index").read_bytes() == task_index
    assert git(source, "rev-parse", "HEAD") == original_head
    assert (source / ".git/index").read_bytes() == original_index
    assert (source / "code.txt").read_bytes() == b"human continued editing\n"
    assert not (source / "sequence.txt").exists()


def test_recovery_is_lookup_only_and_checks_original_task_source_and_workspace(source, tmp_path):
    binding = prepare_workflow_workspace(source, project_key="DEMO", run_id="recover")
    root = workspace_for_workflow(source, binding, project_key="DEMO", run_id="recover")
    (root / "code.txt").write_bytes(b"partial task output\n")
    (source / "new-human.txt").write_text("after start", encoding="utf-8")
    assert workspace_for_workflow(source, binding, project_key="DEMO", run_id="recover") == root
    assert not (root / "new-human.txt").exists()
    for values in ({"run_id": "another"}, {"project_key": "OTHER"}, {"source_root": tmp_path}):
        arguments = {"source_root": source, "project_key": "DEMO", "run_id": "recover", **values}
        with pytest.raises(WorkflowWorkspaceError):
            workspace_for_workflow(binding=binding, **arguments)
    artifact = dispatch_artifact_root(source, binding["execution_id"])
    shutil.rmtree(artifact)
    with pytest.raises(WorkflowWorkspaceError, match="recapture"):
        workspace_for_workflow(source, binding, project_key="DEMO", run_id="recover")
    assert not artifact.exists()
    with pytest.raises(WorkflowWorkspaceError, match="cannot recapture"):
        workspace_for_workflow(source, {}, project_key="DEMO", run_id="old-run")


@pytest.mark.parametrize("state", ["non-git", "unborn", "broken-head"])
def test_invalid_git_baselines_fail_before_runtime(tmp_path, state):
    root = tmp_path / "source"
    root.mkdir()
    (root / "input.txt").write_text("preserved", encoding="utf-8")
    if state != "non-git":
        git(root, "init", "-q")
    if state == "broken-head":
        (root / ".git/HEAD").write_text("invalid head", encoding="utf-8")
    with pytest.raises(WorkflowWorkspaceError):
        prepare_workflow_workspace(root, project_key="DEMO", run_id=state)
    assert (root / "input.txt").read_text() == "preserved"


def test_worker_context_cannot_fallback_after_binding_or_runtime_changes(source, monkeypatch):
    binding = prepare_workflow_workspace(source, project_key="DEMO", run_id="bound")
    store = workers.WorkerJobStore(source)
    job = store.create_job(run_id="bound", project_key="DEMO", role="implementation", adapter="codex",
                           task="Never invoke provider", context="", workflow_workspace=binding)
    monkeypatch.setattr(store, "_run_cli_backend", lambda *_args: pytest.fail("Runtime must not be invoked"))
    path = store.root / job["id"] / "job.json"
    for context in ({}, {**job["sandbox_context"], "job_id": "another-job"},
                    {**job["sandbox_context"], "workspace_path": str(source)}):
        path.write_text(json.dumps({**job, "sandbox_context": context}), encoding="utf-8")
        with pytest.raises(WorkflowWorkspaceError):
            store.run_job(job["id"])
    path.write_text(json.dumps(job), encoding="utf-8")
    (dispatch_artifact_root(source, binding["execution_id"]) / "baseline.json").unlink()
    with pytest.raises(WorkflowWorkspaceError):
        store.run_job(job["id"])


def test_reviewer_replay_cannot_upgrade_or_change_original_baseline(source):
    store = workers.WorkerJobStore(source)
    original = dict(run_id="replay", project_key="DEMO", role="code_review", adapter="fake",
                    task="fixture", context="", idempotency_key="review-one")
    legacy, _ = store.create_or_reuse_job(**original)
    binding = prepare_workflow_workspace(source, project_key="DEMO", run_id="replay")
    with pytest.raises(WorkflowWorkspaceError, match="different workflow baseline"):
        store.create_or_reuse_job(**original, workflow_workspace=binding)
    bounded, created = store.create_or_reuse_job(**{**original, "idempotency_key": "review-two"}, workflow_workspace=binding)
    assert created
    replayed, created = store.create_or_reuse_job(**{**original, "idempotency_key": "review-two"}, workflow_workspace=binding)
    assert not created and replayed["id"] == bounded["id"]
    assert legacy["id"] != bounded["id"]


def test_shared_task_workers_are_serialized_for_all_runtime_adapters(source, monkeypatch):
    import threading
    import time

    binding = prepare_workflow_workspace(source, project_key="DEMO", run_id="serial")
    store = workers.WorkerJobStore(source)
    jobs = [store.create_job(run_id="serial", project_key="DEMO", role="implementation", adapter="codex",
                             task="fixture", context="", workflow_workspace=binding) for _ in range(2)]
    active, maximum = 0, 0
    guard = threading.Lock()

    def local_runtime(*_args, **_kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.1)
        with guard:
            active -= 1
        return {"status": "completed"}

    monkeypatch.setattr(store, "_run_job", local_runtime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert [result["status"] for result in pool.map(store.run_job, [job["id"] for job in jobs])] == ["completed", "completed"]
    assert maximum == 1
    assert context_for_job(source, binding, project_key="DEMO", run_id="serial", job_id=jobs[0]["id"])["sandbox_id"] == "workflow-task"
    assert all(adapter_requires_workflow_workspace(adapter) for adapter in ("codex", "pi", "agy", "gemini", "claude"))
    assert not any(adapter_requires_workflow_workspace(adapter) for adapter in ("fake", "noop", "human"))
