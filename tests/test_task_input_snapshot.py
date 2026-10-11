"""Synthetic task input + real file/worker boundary contracts; no provider calls."""
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os.execution.evidence_manifest import build_evidence_manifest
from goodteam_os.execution.github import GitHubIssueSnapshot, execution_task_from_github_issue
from goodteam_os.execution.orchestrator import ExecutionOrchestrator
from goodteam_os.execution.task_context import render_task_contract
from goodteam_os.execution.task_snapshot import TaskExecutionSnapshot, TaskInputError, canonical_bytes, digest, execution_key
from goodteam_os.execution.task_snapshot_store import TaskSnapshotStore, store_for_project
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.workers import WorkerJobStore


def issue():
    return GitHubIssueSnapshot(repo="example/project", issue_number=1, title="Preserve the original goal",
        body="Fix src/feature.py.\nAcceptance:\n- preserve OLD_CONDITION\n- preserve OLD_CONDITION\nConstraints:\n- no network\nVerification:\n- proposed-command (not permission)",
        url="https://github.com/example/project/issues/1", labels=[], updated_at="source-revision-1")


def task():
    return execution_task_from_github_issue(issue(), clock=lambda: "2026-01-01T00:00:00Z")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    workspace, project, operator = (tmp_path / part for part in ("workspace", "project", "operator"))
    (workspace / "projects").mkdir(parents=True)
    project.mkdir()
    config = workspace / "projects" / "DEMO.yaml"
    config.write_text("name: Demo\n")
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"version": 1, "projects": [{"key": "DEMO", "root": str(project), "config_path": str(config)}]}))
    monkeypatch.setenv("GOODTEAM_PROJECTS_REGISTRY", str(registry))
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(operator))
    monkeypatch.setattr("goodteam_os.workers.default_embedder", lambda: None)
    client = SimpleNamespace(calls=0, value=issue())
    def fetch(_repo, _issue):
        client.calls += 1
        return client.value
    client.fetch_issue = fetch
    orchestrator = ExecutionOrchestrator(project, client, sandbox_enabled=False)
    store = store_for_project(project)
    return SimpleNamespace(workspace=workspace, project=project, operator=operator, registry=registry,
                           client=client, orchestrator=orchestrator, store=store)


def start(ctx, adapter="fake"):
    return ctx.orchestrator.intake_github_issue(repo="example/project", issue_number=1, adapter=adapter)


@pytest.mark.parametrize("mutation", ["source", "criteria", "returned", "task_view"])
def test_snapshot_detaches_every_nested_view(mutation):
    original = task()
    frozen = TaskExecutionSnapshot.capture(original, scope="a" * 64, role="implementation", adapter="fake")
    before = frozen.data, frozen.payload_digest, render_task_contract(frozen)
    if mutation == "source":
        original.task_contract.source_intent["references"].append("later")
    elif mutation == "criteria":
        original.task_contract.acceptance_criteria.append("later")
    elif mutation == "returned":
        frozen.to_dict()["task"]["task_contract"]["constraints"].clear()
    else:
        frozen.task.task_contract.acceptance_criteria.clear()
    assert (frozen.data, frozen.payload_digest, render_task_contract(frozen)) == before


@pytest.mark.parametrize("field,value", [("schema_version", "v99"), ("scope", "bad"), ("contract_revision", True),
                                          ("source_trust", "user_confirmed"), ("context_policy", "unknown")])
def test_bad_envelope_rejected(field, value):
    frozen = TaskExecutionSnapshot.capture(task(), scope="a" * 64, role="implementation", adapter="fake")
    body = frozen.to_dict()
    body[field] = value
    with pytest.raises(TaskInputError):
        TaskExecutionSnapshot(canonical_bytes(body))


def test_nonfinite_and_oversized_input_cannot_commit(ctx):
    original = task()
    original.task_contract.metadata["bad"] = float("nan")
    with pytest.raises(TaskInputError):
        TaskExecutionSnapshot.capture(original, scope=ctx.store.scope, role="implementation", adapter="fake")
    original = task()
    original.task_contract.metadata["large"] = "x" * 195000
    frozen = TaskExecutionSnapshot.capture(original, scope=ctx.store.scope, role="implementation", adapter="fake")
    with pytest.raises(TaskInputError, match="context_size_limit"):
        ctx.store.commit(frozen)
    assert not ctx.store.root.exists()


def test_intake_reuses_original_without_current_issue_fetch_or_mutable_ledger(ctx):
    run = start(ctx)
    captured = ctx.store.load(run.execution_id)
    ctx.client.value = replace(issue(), title="CHANGED_TITLE", body="CHANGED_BODY")
    original_task = ctx.orchestrator.ledger.read_task(run.execution_id)
    ctx.orchestrator.ledger.create_task(replace(original_task, body="FORGED_LEDGER"))
    second = start(ctx)
    assert second == run
    assert ctx.client.calls == 1
    job = ctx.orchestrator.worker_store.list_jobs()[0]
    root = ctx.orchestrator.worker_store.root / run.worker_job_id
    for name in ("task.md", "context.md", "constraints.md", "expected_artifacts.json"):
        (root / name).write_text("FORGED_PROTOCOL")
    prompt = ctx.orchestrator.worker_store._worker_prompt(root, job)
    assert "OLD_CONDITION" in prompt
    assert "FORGED" not in prompt and "CHANGED" not in prompt
    assert ctx.orchestrator.worker_store.admit_task_input(run.worker_job_id) is ctx.orchestrator.worker_store.admit_task_input(run.worker_job_id)
    assert ctx.store.load(run.execution_id).data == captured.data


def test_same_execution_different_adapter_cannot_reuse(ctx):
    start(ctx)
    with pytest.raises(TaskInputError, match="operation_conflict"):
        start(ctx, "noop")


def test_capture_commit_precedes_binding_and_recovers_without_refetch(ctx, monkeypatch):
    original_write = ctx.orchestrator.binding_store.write_binding
    def crash(binding):
        assert ctx.store.load(binding.execution_id) is not None
        raise OSError("synthetic crash before binding")
    monkeypatch.setattr(ctx.orchestrator.binding_store, "write_binding", crash)
    with pytest.raises(OSError):
        start(ctx)
    assert ctx.orchestrator.worker_store.list_jobs() == []
    ctx.client.value = replace(issue(), title="NEW ISSUE")
    monkeypatch.setattr(ctx.orchestrator.binding_store, "write_binding", original_write)
    run = start(ctx)
    assert ctx.client.calls == 1
    assert ctx.store.load(run.execution_id).task.title == "Preserve the original goal"


def test_capture_failure_never_publishes_binding_or_dispatches(ctx, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("synthetic disk error")
    monkeypatch.setattr("goodteam_os.execution.task_snapshot_store.atomic_write", fail)
    with pytest.raises(TaskInputError, match="store_unavailable"):
        start(ctx)
    assert ctx.orchestrator.binding_store.existing_for_issue("example/project", 1) is None
    assert ctx.orchestrator.worker_store.list_jobs() == []


@pytest.mark.parametrize("change", ["reference", "run_id", "execution_id", "id", "role", "adapter"])
def test_mutable_job_cannot_downgrade_or_substitute(ctx, change, monkeypatch):
    run = start(ctx)
    root = ctx.orchestrator.worker_store.root / run.worker_job_id
    job = json.loads((root / "job.json").read_text())
    if change == "reference":
        job.pop("task_input_snapshot")
    else:
        job[change] = "changed"
    (root / "job.json").write_text(json.dumps(job))
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError):
        ctx.orchestrator.run_execution(run.execution_id)
    assert calls == []


@pytest.mark.parametrize("loss", ["snapshot", "required", "ready", "registry"])
def test_missing_authority_blocks_even_fake_adapter(ctx, loss, monkeypatch):
    run = start(ctx)
    root = ctx.store.root / execution_key(run.execution_id)
    target = {"snapshot": root / "snapshot.json", "required": root / "required.json",
              "ready": root / "job-ready.json", "registry": ctx.registry}[loss]
    target.unlink()
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError):
        WorkerJobStore(ctx.project).run_job(run.worker_job_id)
    assert calls == []


def test_missing_snapshot_proof_is_not_repaired_from_project_task(ctx):
    run = start(ctx)
    (ctx.store.root / execution_key(run.execution_id) / "snapshot.json").unlink()
    with pytest.raises(TaskInputError, match="snapshot_missing"):
        start(ctx)
    manifest = build_evidence_manifest(ctx.orchestrator.ledger, RoleMemoryStore(ctx.project), run.execution_id)
    assert manifest["task_input"]["status"] == "unavailable"
    assert manifest["ledger_events"]


def test_fake_success_does_not_verify_duplicate_acceptance_criteria(ctx):
    run = start(ctx)
    ctx.orchestrator.run_execution(run.execution_id)
    manifest = build_evidence_manifest(ctx.orchestrator.ledger, RoleMemoryStore(ctx.project), run.execution_id)
    coverage = manifest["task_input"]["acceptance_coverage"]
    assert len(coverage) == 2
    assert coverage[0]["text"] == coverage[1]["text"]
    assert coverage[0]["criterion_id"] != coverage[1]["criterion_id"]
    assert {item["status"] for item in coverage} == {"unknown"}
    assert manifest["task_input"]["verification"] == "unverified"
    assert manifest["task_input"]["receipts"] == []


def test_real_cli_send_boundary_uses_frozen_input_and_bound_receipt(ctx, monkeypatch):
    import goodteam_os.workers as workers
    run = start(ctx, "claude")
    captured_input = []
    class Process:
        pid = 1234
        returncode = 0
        def __init__(self, command, **_kwargs):
            self.command = command
        def communicate(self, input, timeout):
            captured_input.append(input or self.command[-1])
            return "Status: pass (synthetic transport)", ""
    monkeypatch.setattr(workers.subprocess, "Popen", Process)
    monkeypatch.setattr(workers, "which", lambda _binary: "synthetic-cli")
    monkeypatch.setattr(workers, "_cli_preflight", lambda *_args: {"claude_available": True})
    monkeypatch.setattr(workers, "_cli_runtime_evidence", lambda **_kwargs: {})
    monkeypatch.setattr(WorkerJobStore, "_worker_env", lambda *_args: None)
    result = ctx.orchestrator.run_execution(run.execution_id)
    assert result.status.value == "succeeded"
    assert len(captured_input) == 1 and "OLD_CONDITION" in captured_input[0]
    snapshot = ctx.store.load(run.execution_id)
    receipts = ctx.store.receipts(snapshot)
    assert [item["stage"] for item in receipts] == ["worker_input_prepared", "transport_attempt"]
    assert {item["input_digest"] for item in receipts} == {digest(captured_input[0].encode())}
    assert {item["snapshot_digest"] for item in receipts} == {snapshot.payload_digest}
    assert {item["provider_receipt"] for item in receipts} == {"unknown"}


def _commit_in_process(workspace, project, data, queue):
    store = TaskSnapshotStore(workspace=Path(workspace), project_key="DEMO", worker_root=Path(project))
    try:
        queue.put(store.commit(TaskExecutionSnapshot(data)).payload_digest)
    except Exception as exc:
        queue.put(type(exc).__name__ + ":" + str(exc))


def test_two_process_initial_capture_is_single_committed_value(ctx):
    snapshot = TaskExecutionSnapshot.capture(task(), scope=ctx.store.scope, role="implementation", adapter="fake")
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [context.Process(target=_commit_in_process, args=(str(ctx.workspace), str(ctx.project), snapshot.data, queue)) for _ in range(2)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert results == [snapshot.payload_digest] * 2
    assert ctx.store.load(snapshot.execution_id).data == snapshot.data


def test_same_snapshot_different_worker_request_cannot_reuse(ctx):
    from goodteam_os.execution.task_context import task_worker_fields
    run = start(ctx)
    snapshot = ctx.store.load(run.execution_id)
    fields = task_worker_fields(snapshot)
    request = dict(run_id=run.execution_id, execution_id=run.execution_id, role="implementation", adapter="fake",
                   task_source="github:example/project#1", idempotency_key=snapshot.to_dict()["operation_id"], task_input=snapshot, **fields)
    store = ctx.orchestrator.worker_store
    same, created = store.create_or_reuse_job(**request)
    assert same["id"] == run.worker_job_id and not created
    assert store.admit_task_input(run.worker_job_id) is snapshot
    with pytest.raises(TaskInputError, match="operation_conflict"):
        store.create_or_reuse_job(**{**request, "task": "changed"})
    with pytest.raises(TaskInputError, match="job_mismatch"):
        store.create_or_reuse_job(**{**request, "model": "other-model"})


def test_partial_job_materialization_recovers_without_new_identity(ctx, monkeypatch):
    original = TaskSnapshotStore.bind_job
    def crash(*_args, **_kwargs):
        raise TaskInputError("task_input_store_unavailable")
    monkeypatch.setattr(TaskSnapshotStore, "bind_job", crash)
    with pytest.raises(TaskInputError, match="store_unavailable"):
        start(ctx)
    [partial] = ctx.orchestrator.worker_store.list_jobs()
    with pytest.raises(TaskInputError, match="not_ready"):
        ctx.orchestrator.worker_store.run_job(partial["id"])
    monkeypatch.setattr(TaskSnapshotStore, "bind_job", original)
    run = start(ctx)
    assert run.worker_job_id == partial["id"]
    assert len(ctx.orchestrator.worker_store.list_jobs()) == 1
    assert ctx.client.calls == 1


def test_lost_transport_ack_never_automatically_invokes_again(ctx, monkeypatch):
    run = start(ctx, "claude")
    job = ctx.orchestrator.worker_store.list_jobs()[0]
    root = ctx.orchestrator.worker_store.root / run.worker_job_id
    prompt = ctx.orchestrator.worker_store._worker_prompt(root, job)
    ctx.orchestrator.worker_store._input_receipt(root, job, prompt, stage="transport_attempt")
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", lambda *_args: calls.append(True))
    assert start(ctx, "claude").worker_job_id == run.worker_job_id
    with pytest.raises(TaskInputError, match="transport_indeterminate"):
        ctx.orchestrator.run_execution(run.execution_id)
    assert calls == []


def test_actual_local_python_transport_echoes_exact_input_digest(ctx, monkeypatch):
    import sys
    import goodteam_os.workers as workers
    from dataclasses import replace as change
    script = "import sys,hashlib,json; print(json.dumps({'synthetic':True,'received_sha256':hashlib.sha256(sys.stdin.buffer.read()).hexdigest()}))"
    spec = change(workers.CLI_BACKENDS["claude"], name="offline-test", which_name="offline-test", auth_env=(),
                  prompt_via="stdin", build_command=lambda *_args: [sys.executable, "-c", script])
    monkeypatch.setitem(workers.CLI_BACKENDS, "offline-test", spec)
    monkeypatch.setattr(workers, "which", lambda _binary: sys.executable)
    monkeypatch.setattr(workers, "_cli_preflight", lambda *_args: {"offline-test_available": True})
    monkeypatch.setattr(workers, "_cli_runtime_evidence", lambda **_kwargs: {})
    monkeypatch.setattr(WorkerJobStore, "_worker_env", lambda *_args: {})
    run = start(ctx, "offline-test")
    result = ctx.orchestrator.run_execution(run.execution_id)
    assert result.status.value == "succeeded"
    reply = json.loads((ctx.orchestrator.worker_store.root / run.worker_job_id / "result.md").read_text())
    assert reply["synthetic"] is True
    receipts = ctx.store.receipts(ctx.store.load(run.execution_id))
    assert {item["input_digest"] for item in receipts} == {reply["received_sha256"]}
    before = (ctx.orchestrator.worker_store.root / run.worker_job_id / "result.md").read_bytes()
    assert ctx.orchestrator.run_execution(run.execution_id) == result
    assert (ctx.orchestrator.worker_store.root / run.worker_job_id / "result.md").read_bytes() == before
    assert len(ctx.store.receipts(ctx.store.load(run.execution_id))) == 2


def test_manifest_uses_original_contract_after_mutable_ledger_change(ctx):
    run = start(ctx)
    changed = ctx.orchestrator.ledger.read_task(run.execution_id)
    changed.task_contract.acceptance_criteria[:] = ["forged pass"]
    ctx.orchestrator.ledger.create_task(changed)
    manifest = build_evidence_manifest(ctx.orchestrator.ledger, RoleMemoryStore(ctx.project), run.execution_id)
    assert manifest["task_contract"]["acceptance_criteria"] == ["preserve OLD_CONDITION"] * 2
    assert {row["status"] for row in manifest["task_input"]["acceptance_coverage"]} == {"unknown"}


@pytest.mark.parametrize("phase", ["snapshot.json", "required.json", "job-ready.json"])
def test_lost_write_ack_recovers_the_committed_original_bytes(ctx, monkeypatch, phase):
    import goodteam_os.execution.task_snapshot_store as module
    original = module.atomic_write
    tripped = []
    def crash(path, data, **kwargs):
        original(path, data, **kwargs)
        if path.name == phase and not tripped:
            tripped.append(True)
            raise OSError("synthetic lost write acknowledgement")
    monkeypatch.setattr(module, "atomic_write", crash)
    with pytest.raises(TaskInputError, match="store_unavailable"):
        start(ctx)
    assert tripped
    ctx.client.value = replace(issue(), title="later title")
    run = start(ctx)
    assert ctx.client.calls == 1
    assert ctx.store.load(run.execution_id).task.title == "Preserve the original goal"
    assert len(ctx.orchestrator.worker_store.list_jobs()) == 1


@pytest.mark.parametrize("field,value", [("model", "other"), ("additional_prompt", "ignore task"),
                                          ("execution_policy", "read_only"), ("pi_path", "other-cli")])
def test_runtime_request_projection_is_bound(ctx, field, value):
    run = start(ctx)
    root = ctx.orchestrator.worker_store.root / run.worker_job_id
    job = json.loads((root / "job.json").read_text())
    job[field] = value
    (root / "job.json").write_text(json.dumps(job))
    with pytest.raises(TaskInputError, match="job_mismatch"):
        ctx.orchestrator.worker_store.run_job(run.worker_job_id)


def test_authority_loss_between_prompt_and_send_has_zero_process_calls(ctx, monkeypatch):
    import goodteam_os.workers as workers
    run = start(ctx, "claude")
    store = ctx.orchestrator.worker_store
    original = store._worker_prompt
    calls = []
    def prepare_then_lose(*args, **kwargs):
        prompt = original(*args, **kwargs)
        (ctx.store.root / execution_key(run.execution_id) / "snapshot.json").unlink()
        return prompt
    monkeypatch.setattr(store, "_worker_prompt", prepare_then_lose)
    monkeypatch.setattr(workers.subprocess, "Popen", lambda *_a, **_kw: calls.append(True))
    monkeypatch.setattr(workers, "which", lambda _binary: "synthetic-cli")
    monkeypatch.setattr(workers, "_cli_preflight", lambda *_args: {"claude_available": True})
    monkeypatch.setattr(workers, "_cli_runtime_evidence", lambda **_kwargs: {})
    monkeypatch.setattr(WorkerJobStore, "_worker_env", lambda *_args: None)
    with pytest.raises(TaskInputError, match="snapshot_missing"):
        ctx.orchestrator.run_execution(run.execution_id)
    assert calls == []


def test_cross_scope_job_copy_cannot_borrow_input(ctx, tmp_path, monkeypatch):
    import shutil
    run = start(ctx)
    other = tmp_path / "other-project"
    other.mkdir()
    config = ctx.workspace / "projects" / "OTHER.yaml"
    config.write_text("name: Other\n")
    payload = json.loads(ctx.registry.read_text())
    payload["projects"].append({"key": "OTHER", "root": str(other), "config_path": str(config)})
    ctx.registry.write_text(json.dumps(payload))
    other_store = WorkerJobStore(other)
    shutil.copytree(ctx.orchestrator.worker_store.root / run.worker_job_id, other_store.root / run.worker_job_id)
    with pytest.raises(TaskInputError, match="snapshot_missing"):
        other_store.run_job(run.worker_job_id)


def test_unsafe_operator_root_and_linked_capture_rejected(ctx, monkeypatch):
    run = start(ctx)
    root = ctx.store.root / execution_key(run.execution_id)
    capture = root / "snapshot.json"
    moved = root / "saved.json"
    capture.rename(moved)
    capture.symlink_to(moved.name)
    with pytest.raises(TaskInputError, match="store_unavailable"):
        ctx.store.load(run.execution_id)
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(ctx.project / "operator"))
    with pytest.raises(TaskInputError, match="unsafe_store_root"):
        store_for_project(ctx.project)


def test_unregistered_legacy_stays_compatible_and_unverified(tmp_path):
    project = tmp_path / "legacy"
    project.mkdir()
    client = SimpleNamespace(fetch_issue=lambda *_args: issue())
    orchestrator = ExecutionOrchestrator(project, client, sandbox_enabled=False)
    run = orchestrator.intake_github_issue(repo="example/project", issue_number=1, adapter="fake")
    assert not run.worker_job_id.startswith("job-input-")
    orchestrator.run_execution(run.execution_id)
    manifest = build_evidence_manifest(orchestrator.ledger, RoleMemoryStore(project), run.execution_id)
    assert manifest["task_input"]["status"] == "legacy_unverified"
    assert manifest["task_input"]["acceptance_coverage"] == []


def test_lost_job_ready_after_attempt_cannot_erase_result_or_repair_as_new(ctx):
    run = start(ctx, "claude")
    job = ctx.orchestrator.worker_store.list_jobs()[0]
    root = ctx.orchestrator.worker_store.root / run.worker_job_id
    prompt = ctx.orchestrator.worker_store._worker_prompt(root, job)
    ctx.orchestrator.worker_store._input_receipt(root, job, prompt, stage="transport_attempt")
    (root / "result.md").write_text("Existing result must survive ambiguous recovery")
    (ctx.store.root / execution_key(run.execution_id) / "job-ready.json").unlink()
    from goodteam_os.execution.task_context import task_worker_fields
    snapshot = ctx.store.load(run.execution_id)
    with pytest.raises(TaskInputError, match="transport_indeterminate"):
        ctx.orchestrator.worker_store.create_job(
            run_id=run.execution_id, execution_id=run.execution_id, role="implementation", adapter="claude",
            task_source="github:example/project#1", idempotency_key=snapshot.to_dict()["operation_id"],
            task_input=snapshot, **task_worker_fields(snapshot),
        )
    assert (root / "result.md").read_text() == "Existing result must survive ambiguous recovery"


@pytest.mark.parametrize("missing_registry", [False, True])
def test_caller_execution_identity_cannot_route_to_an_unrelated_legacy_job(ctx, monkeypatch, missing_registry):
    run = start(ctx)
    legacy = ctx.orchestrator.worker_store.create_job(run_id="other", role="implementation", adapter="fake", task="other", context="other")
    ctx.orchestrator.ledger.create_run(replace(run, worker_job_id=legacy["id"]))
    if missing_registry:
        ctx.registry.unlink()
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError):
        ctx.orchestrator.run_execution(run.execution_id)
    assert calls == []


def test_renamed_job_and_legacy_creation_cannot_reuse_captured_identity(ctx, monkeypatch):
    run = start(ctx)
    store = ctx.orchestrator.worker_store
    with pytest.raises(TaskInputError, match="job_mismatch"):
        store.create_job(run_id=run.execution_id, role="implementation", adapter="fake", task="other", context="other")
    root = store.root / run.worker_job_id
    payload = json.loads((root / "job.json").read_text())
    payload.pop("task_input_snapshot")
    payload["id"] = "job-legacy-rename"
    (root / "job.json").write_text(json.dumps(payload))
    root.rename(store.root / payload["id"])
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError, match="job_mismatch"):
        store.run_job(payload["id"])
    assert calls == []


@pytest.mark.parametrize("lost", ["receipts", "dispatch", "receipts_prefix"])
def test_partial_dispatch_history_loss_cannot_allow_another_attempt(ctx, monkeypatch, lost):
    run = start(ctx, "claude")
    store = ctx.orchestrator.worker_store
    job = store.list_jobs()[0]
    prompt = store._worker_prompt(store.root / run.worker_job_id, job)
    store._input_receipt(store.root / run.worker_job_id, job, prompt, stage="transport_attempt")
    root = ctx.store.root / execution_key(run.execution_id)
    if lost == "receipts_prefix":
        receipts = json.loads((root / "receipts.json").read_text())
        (root / "receipts.json").write_bytes(canonical_bytes(receipts[:1]))
    else:
        (root / f"{lost}.json").unlink()
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError, match="transport_indeterminate"):
        WorkerJobStore(ctx.project).run_job(run.worker_job_id)
    assert calls == []
    if lost != "dispatch":
        manifest = build_evidence_manifest(ctx.orchestrator.ledger, RoleMemoryStore(ctx.project), run.execution_id)
        assert manifest["task_input"]["status"] == "partial"
        assert manifest["task_input"]["handoff_status"] == "unavailable"
        assert manifest["task_contract"]["acceptance_criteria"] == ["preserve OLD_CONDITION"] * 2


def _die_after_dispatch_marker(project, job_id, mode):
    import os
    import signal
    import goodteam_os.execution.task_snapshot_store as module
    store = WorkerJobStore(Path(project))
    job = json.loads((store.root / job_id / "job.json").read_text())
    prompt = store._worker_prompt(store.root / job_id, job)
    original = module.atomic_write
    def die(path, data, **kwargs):
        original(path, data, **kwargs)
        if path.name == "dispatch.json":
            if mode == "kill":
                os.kill(os.getpid(), signal.SIGKILL)
            raise OSError("synthetic lost dispatch marker acknowledgement")
    module.atomic_write = die
    try:
        store._input_receipt(store.root / job_id, job, prompt, stage="transport_attempt")
    except TaskInputError:
        if mode != "lost_ack":
            raise


@pytest.mark.parametrize("mode", ["kill", "lost_ack"])
def test_dispatch_marker_before_receipt_crash_is_indeterminate_without_replay(ctx, monkeypatch, mode):
    run = start(ctx, "claude")
    process = multiprocessing.get_context("fork").Process(target=_die_after_dispatch_marker, args=(str(ctx.project), run.worker_job_id, mode))
    process.start()
    process.join(10)
    assert process.exitcode == (-9 if mode == "kill" else 0)
    root = ctx.store.root / execution_key(run.execution_id)
    assert (root / "dispatch.json").is_file()
    assert [row["stage"] for row in json.loads((root / "receipts.json").read_text())] == ["worker_input_prepared"]
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError, match="transport_indeterminate"):
        WorkerJobStore(ctx.project).run_job(run.worker_job_id)
    assert calls == []


@pytest.mark.parametrize("action", ["steer", "follow_up", "abort"])
def test_pi_incremental_control_rechecks_capture_but_abort_remains_available(ctx, action):
    run = start(ctx, "pi")
    store = ctx.orchestrator.worker_store
    root = store.root / run.worker_job_id
    job = json.loads((root / "job.json").read_text())
    (ctx.store.root / execution_key(run.execution_id) / "snapshot.json").unlink()
    if action != "abort":
        with pytest.raises(TaskInputError, match="snapshot_missing"):
            store.session_control(run.worker_job_id, action, message="extra instruction")
        assert not (root / "pi_control_queue.jsonl").exists()
    calls = []
    runtime = SimpleNamespace(steer=lambda text: calls.append(("steer", text)),
                              follow_up=lambda text: calls.append(("follow_up", text)),
                              abort=lambda: calls.append(("abort", "")),
                              store=SimpleNamespace(append_event=lambda *_args: None))
    (root / "pi_control_queue.jsonl").write_text(json.dumps({"id": "control-synthetic-1234", "action": action,
                                                            "message": "extra instruction"}) + "\n")
    store._process_pi_controls(root, job, runtime)
    assert calls == ([("abort", "")] if action == "abort" else [])


def test_cached_input_cannot_outlive_changed_operator_authority(ctx, tmp_path, monkeypatch):
    run = start(ctx)
    ctx.orchestrator.worker_store.admit_task_input(run.worker_job_id)
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "different-operator"))
    with pytest.raises(TaskInputError, match="scope_mismatch"):
        ctx.orchestrator.worker_store.admit_task_input(run.worker_job_id)


def test_uncaptured_caller_cannot_borrow_another_captured_execution(ctx, monkeypatch):
    run = start(ctx)
    alias = replace(run, execution_id="exec_unrelated_caller")
    ctx.orchestrator.ledger.create_run(alias)
    calls = []
    monkeypatch.setattr(WorkerJobStore, "_complete_fake", lambda *_args: calls.append(True))
    with pytest.raises(TaskInputError, match="job_mismatch"):
        ctx.orchestrator.run_execution(alias.execution_id)
    assert calls == []


def test_other_issue_cannot_borrow_an_existing_capture_through_binding(ctx):
    run = start(ctx)
    binding = ctx.orchestrator.binding_store.read_binding(run.execution_id)
    ctx.orchestrator.binding_store.write_binding(replace(binding, issue_number=2))
    with pytest.raises(TaskInputError, match="job_mismatch"):
        ctx.orchestrator.intake_github_issue(repo="example/project", issue_number=2, adapter="fake")
    assert ctx.client.calls == 1


def test_foreign_github_response_is_rejected_before_capture_or_binding(ctx):
    ctx.client.value = replace(issue(), issue_number=2)
    with pytest.raises(TaskInputError, match="source_mismatch"):
        start(ctx)
    assert not ctx.store.root.exists()
    assert ctx.orchestrator.worker_store.list_jobs() == []
    assert ctx.orchestrator.binding_store.existing_for_issue("example/project", 2) is None


def test_binding_and_run_cannot_jointly_hide_another_captured_execution(ctx):
    run = start(ctx)
    legacy = ctx.orchestrator.worker_store.create_job(run_id="other", role="implementation", adapter="fake", task="other", context="other")
    ctx.orchestrator.ledger.create_run(replace(run, worker_job_id=legacy["id"]))
    binding = ctx.orchestrator.binding_store.read_binding(run.execution_id)
    ctx.orchestrator.binding_store.write_binding(replace(binding, issue_number=2))
    with pytest.raises(TaskInputError, match="job_mismatch"):
        ctx.orchestrator.intake_github_issue(repo="example/project", issue_number=2, adapter="fake")


def test_captured_default_prompt_preserves_exact_legacy_memory_writeback_contract(ctx):
    run = start(ctx)
    store = ctx.orchestrator.worker_store
    captured_job = store.list_jobs()[0]
    captured_prompt = store._worker_prompt(store.root / run.worker_job_id, captured_job)
    legacy = store.create_job(run_id="legacy-output-contract", role="implementation", adapter="fake", task="task", context="context")
    legacy_prompt = store._worker_prompt(store.root / legacy["id"], legacy)
    heading = "## Memory Write-Back Sections"
    assert captured_prompt[captured_prompt.index(heading):] == legacy_prompt[legacy_prompt.index(heading):]
    for name in ("Learned", "Caveats", "Decisions", "Open Questions"):
        assert f"## {name}\n" in captured_prompt
