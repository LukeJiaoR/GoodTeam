"""Actual offline CLI/worktree handoff; no provider credentials or model calls."""
import json
import os
import sys

import pytest

from goodteam_os.execution.candidate_validation import CandidateValidationService
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.correction_input import correction_runtime_capability as production_capability
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.correction_worker import CorrectionWorkerService
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.workers import WorkerJobStore
from test_task_input_snapshot import ctx, start  # noqa: F401
from test_candidate_validation import git


@pytest.fixture
def worker_context(request, monkeypatch):
    c = request.getfixturevalue("ctx")
    # This test-only code composition exercises transport, not a verified live
    # Codex admission. Production external-runtime admission remains closed.
    monkeypatch.setattr("goodteam_os.execution.correction_input.correction_runtime_capability",
        lambda _adapter: {"available": True, "status": "offline_test_composition", "reason": "synthetic_fixture"})
    parameter = getattr(request, "param", "")
    run = start(c, adapter=parameter if parameter in {"fake", "noop"} else "codex")
    c.execution_id = run.execution_id
    git(c.project, "init", "-q")
    (c.project / ".gitignore").write_text(".goodteam/\n")
    (c.project / "feature.py").write_text("VALUE = 'original'\n")
    git(c.project, "add", ".")
    git(c.project, "commit", "-qm", "Synthetic baseline")
    c.head = git(c.project, "rev-parse", "HEAD")
    c.argv = [sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    c.validation = CandidateValidationService(c.project)
    c.first = c.validation.validate(c.execution_id, operation_id="initial-check", head_sha=c.head, argv=c.argv, criteria=[0])
    c.validation.attest_review(c.execution_id, receipt_id=c.first["receipt_id"], head_sha=c.head,
        reviewer="operator", decision="request_changes", reason="Fix VALUE while preserving OLD_CONDITION")
    if getattr(request, "param", "") in {"unresolved", "returned"}:
        original = c.store.load(c.execution_id)
        c.store.record(original, stage="transport_attempt", input_digest="a" * 64)
        if request.param == "returned":
            c.store.record_host_return(original, status="completed", output_digest="b" * 64)
    c.loop = CorrectionLoopService(c.project)
    c.service = CorrectionWorkerService(c.project)
    c.calls = []
    # Test-only executor injection invokes a real Python process through the
    # existing CLI transport, with an explicit empty/synthetic environment.
    script = c.workspace / "offline_worker.py"
    script.write_text("""import hashlib,json,os,subprocess,sys
from pathlib import Path
prompt=sys.stdin.read()
Path(os.environ['FIXTURE_CALLS']).open('a').write('call\\n')
assert 'OLD_CONDITION' in prompt and 'Fix VALUE' in prompt
Path('feature.py').write_text(\"VALUE = 'fixed'\\n\")
subprocess.run(['git','add','feature.py'],check=True)
subprocess.run(['git','commit','-qm','Offline correction'],check=True)
print(json.dumps({'prompt_digest':hashlib.sha256(prompt.encode()).hexdigest(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}))
""")
    c.effects = c.workspace / "calls.txt"
    env = {"PATH": os.defpath, "HOME": str(c.workspace / "empty-home"), "FIXTURE_CALLS": str(c.effects), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
           "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"}
    def offline(store, job_root, job, spec):
        c.calls.append(job_root.name)
        return store._run_cli_worker(job_root, job, command=[sys.executable, str(script)], model="offline-script",
            preflight={"available": True, "ready": True}, label="Offline fixture", capture="stdout",
            summarize=lambda *_: "Offline process returned", worker_env=env, timeout_seconds=5)
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    c.script = script
    if parameter == "original-invoked":
        body = script.read_text()
        script.write_text("print('Original offline task returned')")
        assert c.service.workers.run_job(c.store.load(c.execution_id).job_id)["status"] == "completed"
        script.write_text(body)
        c.calls.clear()
    c.opened = c.loop.start(c.execution_id, receipt_id=c.first["receipt_id"], head_sha=c.head)
    return c


def prepare(c, **overrides):
    args = {"loop_id": c.opened["loop_id"], "receipt_id": c.first["receipt_id"], "head_sha": c.head,
            "operation_id": "repair-1", "max_seconds": 30}
    args.update(overrides)
    return c.service.prepare(c.execution_id, **args)


def test_real_worker_fix_and_same_round_recheck_in_independent_worktree(worker_context):
    c = worker_context
    original = c.store.load(c.execution_id)
    prepared = prepare(c)
    assert c.calls == [] and c.loop.evidence(c.execution_id)["reserved_rounds"] == 1
    assert c.loop.evidence(c.execution_id)["status"] == "awaiting_worker"
    assert prepare(c) == prepared
    job_id = prepared["job_id"]
    with pytest.raises(TaskInputError, match="authorization_required"):
        c.service.run(c.execution_id, job_id)
    result = c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert result["status"] == "completed" and c.calls == [job_id]
    assert c.service.run(c.execution_id, job_id, acknowledge_external_cost=True) == result
    assert c.calls == [job_id]
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert (c.project / "feature.py").read_text() == "VALUE = 'original'\n"
    authority = CorrectionAttemptStore(c.project, job_id)
    value = authority.load()
    worktree = authority.workspace(value)
    head = git(worktree, "rev-parse", "HEAD")
    assert head != c.head and c.store.load(c.execution_id).data == original.data
    checked = c.validation.validate(c.execution_id, operation_id="fixed-check", head_sha=head,
        argv=c.argv, criteria=[0], correction_worker=job_id)
    assert checked["observation"]["status"] == "passed"
    c.validation.attest_review(c.execution_id, receipt_id=checked["receipt_id"], head_sha=head,
        reviewer="operator", decision="approve", reason="Inspect offline candidate")
    assert c.loop.evidence(c.execution_id)["status"] == "reviewed"
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 1
    assert c.loop.evidence(c.execution_id)["acceptance_status"] == "human_recorded"
    rows = authority.receipts(value)
    emitted = json.loads((c.service.workers.root / job_id / "result.md").read_text())
    assert {row["input_digest"] for row in rows} == {emitted["prompt_digest"]}


@pytest.mark.parametrize("worker_context", ["unresolved", "returned"], indirect=True)
def test_original_transport_needs_protected_host_return(worker_context, request):
    c = worker_context
    # A mutable completed status cannot substitute for the host return.
    original_job = c.service.workers.root / c.store.load(c.execution_id).job_id
    (original_job / "status.json").write_text(json.dumps({"status": "completed"}))
    if request.node.callspec.params["worker_context"] == "unresolved":
        with pytest.raises(TaskInputError, match="original_transport_unresolved"):
            prepare(c)
        assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 0
    else:
        assert prepare(c)["status"] == "prepared"
    assert not c.effects.exists()


def test_new_input_views_are_immutable_and_direct_handoff_is_reused(worker_context):
    c = worker_context
    job_id = prepare(c)["job_id"]
    value = c.service.workers.admit_task_input(job_id)
    before = value.data, value.original.data, value.render()
    value.to_dict()["review"]["reason"] = "changed"
    value.original.task.task_contract.acceptance_criteria.clear()
    value.original.to_dict()["task"]["task_contract"]["constraints"].clear()
    assert (value.data, value.original.data, value.render()) == before
    assert c.service.workers.admit_task_input(job_id) is value


def test_prepared_job_cannot_bypass_cli_authorization_or_caller_identity(worker_context):
    c = worker_context
    job_id = prepare(c)["job_id"]
    with pytest.raises(TaskInputError, match="authorization_required"):
        c.service.workers.run_job(job_id)
    with pytest.raises(TaskInputError, match="identity_mismatch"):
        c.service.run("unrelated-execution", job_id, acknowledge_external_cost=True)
    with pytest.raises(TaskInputError, match="validation_input_missing"):
        c.validation.validate(c.execution_id + "-wrong", operation_id="borrow", head_sha=c.head,
            argv=c.argv, criteria=[0], correction_worker=job_id)
    assert c.calls == [] and not c.effects.exists()


@pytest.mark.parametrize("lost", ["input.json", "required.json", "workspace-ready.json", "job-ready.json", "receipts.json"])
def test_partial_attempt_proof_loss_never_dispatches(worker_context, lost):
    c = worker_context
    job_id = prepare(c)["job_id"]
    authority = CorrectionAttemptStore(c.project, job_id)
    path = authority.root / lost
    path.unlink()
    with pytest.raises(ValueError):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert not path.exists() and not c.effects.exists()


def test_worktree_redirect_cannot_change_worker_or_check_scope(worker_context):
    c = worker_context
    job_id = prepare(c)["job_id"]
    authority = CorrectionAttemptStore(c.project, job_id)
    value = authority.load()
    workspace = authority.workspace(value)
    moved = workspace.with_name("preserved-worktree")
    workspace.rename(moved)
    workspace.symlink_to(c.project, target_is_directory=True)
    with pytest.raises(ValueError):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    with pytest.raises(ValueError):
        c.validation.validate(c.execution_id, operation_id="redirected-check", head_sha=c.head,
            argv=c.argv, criteria=[0], correction_worker=job_id)
    assert not c.effects.exists()


def test_lost_result_and_unknown_started_call_cannot_reinvoke(worker_context, monkeypatch):
    c = worker_context
    job_id = prepare(c)["job_id"]
    original = WorkerJobStore.run_job
    def interrupted(*a, **k):
        raise OSError("synthetic interruption after service started marker")
    monkeypatch.setattr(WorkerJobStore, "run_job", interrupted)
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    monkeypatch.setattr(WorkerJobStore, "run_job", original)
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        CorrectionWorkerService(c.project).run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert c.loop.evidence(c.execution_id)["status"] == "awaiting_worker_result"
    for workers in (c.service.workers, WorkerJobStore(c.project)):
        with pytest.raises(TaskInputError, match="outcome_unknown"):
            workers.run_job(job_id)
    assert not c.effects.exists()


def test_return_receipt_loss_cannot_make_finished_attempt_fresh(worker_context):
    c = worker_context
    job_id = prepare(c)["job_id"]
    c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    authority = CorrectionAttemptStore(c.project, job_id)
    (authority.root / "run-result.json").unlink()
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        CorrectionWorkerService(c.project).run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert c.effects.read_text() == "call\n"
    assert not (authority.root / "run-result.json").exists()


def test_stop_before_dispatch_preserves_reserved_budget(worker_context):
    c = worker_context
    job_id = prepare(c)["job_id"]
    c.loop.stop(c.execution_id, loop_id=c.opened["loop_id"])
    with pytest.raises(TaskInputError, match="stopped_by_operator"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 1
    assert not c.effects.exists()


def _run_contender(project, execution, job_id, pipe):
    try:
        pipe.send(CorrectionWorkerService(project).run(execution, job_id, acknowledge_external_cost=True)["status"])
    except TaskInputError as exc:
        pipe.send(exc.code)
    finally:
        pipe.close()


def test_two_processes_cannot_dispatch_same_correction_twice(worker_context):
    import multiprocessing
    c = worker_context
    job_id = prepare(c)["job_id"]
    mp = multiprocessing.get_context("fork")
    pairs = [mp.Pipe(duplex=False) for _ in range(2)]
    processes = [mp.Process(target=_run_contender, args=(c.project, c.execution_id, job_id, send)) for _, send in pairs]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    states = [receive.recv() for receive, _ in pairs]
    assert "completed" in states and set(states) <= {"completed", "correction_worker_outcome_unknown"}
    assert c.effects.read_text() == "call\n"
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 1


def test_actual_dashboard_prepares_only_and_has_no_worker_run_route(worker_context, monkeypatch):
    from test_correction_loop import _dashboard_post_headers
    from goodteam_os.execution.api import ExecutionAPI
    from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
    c = worker_context
    payload = {"project_root": str(c.project), "loop_id": c.opened["loop_id"], "head": c.head,
               "validation_receipt": c.first["receipt_id"], "operation_id": "ui-prepare"}
    path = f"/api/executions/{c.execution_id}/correction/prepare-worker"
    assert _dashboard_post_headers(c.project, monkeypatch, path, payload,
        {"Origin": "http://127.0.0.1:9999"}) == 403
    assert _dashboard_post_headers(c.project, monkeypatch, path, payload,
        {"Origin": "http://127.0.0.1:8788"}) == 200
    assert _dashboard_post_headers(c.project, monkeypatch,
        f"/api/executions/{c.execution_id}/correction/run-worker", payload,
        {"Origin": "http://127.0.0.1:8788"}) == 404
    html = render_review_workbench_html(ExecutionAPI(c.project).get_review_workbench(c.execution_id))
    assert "Retained worktree" in html and "awaiting_worker" in html
    assert not c.effects.exists()


def test_production_codex_admission_is_blocked_even_with_cost_ack_and_ui_explains(worker_context, monkeypatch):
    from goodteam_os.execution.api import ExecutionAPI
    from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
    c = worker_context
    monkeypatch.setattr("goodteam_os.execution.correction_input.correction_runtime_capability", production_capability)
    html = render_review_workbench_html(ExecutionAPI(c.project).get_review_workbench(c.execution_id), decision_actions_enabled=True)
    assert 'id="prepare-correction-worker" disabled' in html
    assert "External correction executors are not yet admitted" in html
    job_id = prepare(c)["job_id"]  # Safe CLI preparation is allowed; no runtime authority follows.
    with pytest.raises(TaskInputError, match="verified_runtime_unavailable"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    with pytest.raises(TaskInputError, match="verified_runtime_unavailable"):
        c.service.workers.run_job(job_id)
    assert "lacks a verified correction admission" in c.loop.evidence(c.execution_id)["next_action"]
    assert not c.effects.exists() and c.calls == []


@pytest.mark.parametrize("worker_context", ["fake", "noop"], indirect=True)
def test_real_cli_builtin_simulation_is_explicit_single_invocation(worker_context, monkeypatch, capsys):
    from goodteam_os.cli import main
    c = worker_context
    monkeypatch.setattr("goodteam_os.execution.correction_input.correction_runtime_capability", production_capability)
    assert main(["execution", "prepare-correction-worker", "--project-root", str(c.project),
        "--execution-id", c.execution_id, "--loop-id", c.opened["loop_id"], "--head", c.head,
        "--validation-receipt", c.first["receipt_id"], "--operation-id", "builtin-attempt"]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["runtime_admission"]["status"] == "simulation_only"
    assert main(["execution", "run-correction-worker", "--project-root", str(c.project),
        "--execution-id", c.execution_id, "--job-id", prepared["job_id"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed" and result["acceptance_status"] == "unknown"
    with pytest.raises(TaskInputError, match="transport_indeterminate"):
        WorkerJobStore(c.project).run_job(prepared["job_id"])
    assert c.calls == [] and not c.effects.exists()


@pytest.mark.parametrize("worker_context", ["original-invoked"], indirect=True)
def test_actual_initial_worker_return_is_retained_before_correction(worker_context):
    c = worker_context
    captured = c.store.load(c.execution_id)
    assert c.store.host_return(captured)["status"] == "completed"
    assert prepare(c)["status"] == "prepared"
    assert c.store.host_return(captured)["observation"] == "host_worker_return"


def test_reused_check_operation_does_not_poison_next_worker_identity(worker_context):
    c = worker_context
    with pytest.raises(TaskInputError, match="operation_conflict"):
        prepare(c, operation_id="initial-check")
    assert prepare(c)["status"] == "prepared"


def test_remaining_wall_budget_is_observed_before_dispatch(worker_context, monkeypatch):
    c = worker_context
    job_id = prepare(c)["job_id"]
    monkeypatch.setattr("goodteam_os.execution.correction_loop.time.time", lambda: c.opened["deadline_at"] + 1)
    with pytest.raises(TaskInputError, match="time_budget_exhausted"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert not c.effects.exists()


@pytest.mark.parametrize("mode", ["timeout", "output", "stop"])
def test_real_transport_is_bounded_and_unknown_cannot_retry(worker_context, mode):
    import threading
    import time
    c = worker_context
    c.script.write_text("import time\nfrom pathlib import Path\n" +
        f"Path({str(c.effects)!r}).write_text('call\\n')\n" +
        ("print('x' * 2000000)\n" if mode == "output" else "time.sleep(10)\n"))
    job_id = prepare(c, max_seconds=1 if mode == "timeout" else 30)["job_id"]
    stopped = []
    def stop():
        for _ in range(100):
            if c.effects.exists():
                stopped.append(c.loop.stop(c.execution_id, loop_id=c.opened["loop_id"]))
                return
            time.sleep(.02)
    stopper = threading.Thread(target=stop) if mode == "stop" else None
    if stopper:
        stopper.start()
    before = time.monotonic()
    if mode == "timeout":
        assert c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)["status"] == "failed"
    else:
        with pytest.raises(TaskInputError, match="outcome_unknown"):
            c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    if stopper:
        stopper.join(5)
        assert stopped
    assert time.monotonic() - before < 6
    assert c.effects.read_text() == "call\n"
    if mode != "timeout":
        with pytest.raises(TaskInputError, match="outcome_unknown"):
            CorrectionWorkerService(c.project).run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert c.effects.read_text() == "call\n"


def test_prepare_materializes_raw_bytes_without_repository_hooks_or_filters(worker_context):
    c = worker_context
    hook = c.project / ".git" / "hooks" / "post-checkout"
    marker = c.workspace / "forbidden-hook"
    hook.write_text(f"#!/bin/sh\necho unexpected > {marker}\n")
    hook.chmod(0o755)
    git(c.project, "config", "filter.evil.smudge", f"sh -c 'echo unexpected > {marker}; cat'")
    # Per-repository attributes affect checkout without changing the committed
    # candidate or authorizing any command from its contents.
    (c.project / ".git" / "info" / "attributes").write_text("*.py filter=evil\n")
    prepared = prepare(c)
    authority = CorrectionAttemptStore(c.project, prepared["job_id"])
    assert (authority.workspace(authority.load()) / "feature.py").read_bytes() == (c.project / "feature.py").read_bytes()
    assert not marker.exists()


def test_new_round_keeps_original_contract_and_uses_prior_attempt_candidate(worker_context):
    c = worker_context
    first_id = prepare(c)["job_id"]
    c.service.run(c.execution_id, first_id, acknowledge_external_cost=True)
    first_store = CorrectionAttemptStore(c.project, first_id)
    first = first_store.load()
    head = git(first_store.workspace(first), "rev-parse", "HEAD")
    checked = c.validation.validate(c.execution_id, operation_id="fixed-check", head_sha=head,
        argv=c.argv, criteria=[0], correction_worker=first_id)
    c.validation.attest_review(c.execution_id, receipt_id=checked["receipt_id"], head_sha=head,
        reviewer="operator", decision="request_changes", reason="Inspect another bounded correction")
    with pytest.raises(TaskInputError, match="operation_conflict"):
        prepare(c, operation_id="fixed-check", receipt_id=checked["receipt_id"], head_sha=head)
    next_attempt = prepare(c, operation_id="repair-2", receipt_id=checked["receipt_id"], head_sha=head)
    second_store = CorrectionAttemptStore(c.project, next_attempt["job_id"])
    second = second_store.load()
    assert second.job_id != first_id and second.original.data == first.original.data
    assert second.to_dict()["candidate"]["head_sha"] == head
    assert git(second_store.workspace(second), "rev-parse", "HEAD") == head
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 2
    assert c.effects.read_text() == "call\n"


def test_pty_cannot_bypass_missing_group_ownership(worker_context, monkeypatch):
    c = worker_context
    job_id = prepare(c)["job_id"]
    def pty(store, job_root, job, spec):
        return store._run_cli_worker(job_root, job, command=[sys.executable, str(c.script)], model="synthetic",
            preflight={}, label="fixture", capture="stdout", summarize=lambda *_: "unused", use_pty=True)
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", pty)
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert not c.effects.exists()


def test_partial_worktree_publication_requires_inspection_not_second_creation(worker_context, monkeypatch):
    from goodteam_os.execution.correction_workspace import CorrectionWorktreeGit
    c = worker_context
    create = CorrectionWorktreeGit.create_worktree
    calls = []
    def interrupted(self, **kwargs):
        calls.append(kwargs["worktree_path"])
        create(self, **kwargs)
        raise OSError("lost acknowledgement after real worktree creation")
    monkeypatch.setattr(CorrectionWorktreeGit, "create_worktree", interrupted)
    with pytest.raises(TaskInputError, match="preparation_unknown"):
        prepare(c)
    monkeypatch.setattr(CorrectionWorktreeGit, "create_worktree", create)
    with pytest.raises(TaskInputError, match="preparation_unknown"):
        prepare(c)
    assert len(calls) == 1 and calls[0].is_dir()
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 1
    assert not c.effects.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-state observation in this offline probe")
def test_successful_parent_exit_still_cleans_background_process_group(worker_context):
    from pathlib import Path
    c = worker_context
    pid_file = c.workspace / "descendant-pid"
    c.script.write_text("import subprocess,sys\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\nprint('parent returned')\n")
    job_id = prepare(c)["job_id"]
    try:
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    except ValueError:
        pass  # An unreaped orphan may conservatively leave termination unknown.
    pid = int(pid_file.read_text())
    state = Path(f"/proc/{pid}/stat")
    assert not state.exists() or state.read_text().split()[2] == "Z"
    with pytest.raises(TaskInputError):
        WorkerJobStore(c.project).run_job(job_id)


@pytest.mark.parametrize("redirect", ["original", "sibling", "head", "branch-alias"])
def test_attempt_git_ownership_cannot_borrow_other_head_or_index(worker_context, redirect):
    c = worker_context
    job_id = prepare(c)["job_id"]
    authority = CorrectionAttemptStore(c.project, job_id)
    workspace = authority.workspace(authority.load())
    original_ref = git(c.project, "symbolic-ref", "HEAD")
    if redirect == "original":
        (workspace / ".git").write_text(f"gitdir: {c.project / '.git'}\n")
    elif redirect == "sibling":
        other = c.workspace / "sibling"
        git(c.project, "worktree", "add", "-b", "sibling", str(other), c.head)
        (workspace / ".git").write_bytes((other / ".git").read_bytes())
    elif redirect == "head":
        git(workspace, "symbolic-ref", "HEAD", original_ref)
    else:
        git(workspace, "symbolic-ref", "refs/heads/sandbox/" + job_id, original_ref)
    with pytest.raises(ValueError):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    with pytest.raises(ValueError):
        c.validation.validate(c.execution_id, operation_id="borrowed-check", head_sha=c.head,
            argv=c.argv, criteria=[0], correction_worker=job_id)
    assert not c.effects.exists() and git(c.project, "rev-parse", "HEAD") == c.head


@pytest.mark.parametrize("removed", ["correction-required.json", "correction-loop.json"])
def test_any_retained_correction_proof_prevents_original_redispatch(worker_context, removed):
    from goodteam_os.execution.task_snapshot import execution_key
    c = worker_context
    prepare(c)
    (c.store.root / execution_key(c.execution_id) / removed).unlink()
    with pytest.raises(TaskInputError, match="correction_active"):
        WorkerJobStore(c.project).run_job(c.store.load(c.execution_id).job_id)
    assert not c.effects.exists()


@pytest.mark.parametrize("target", ["jobs-parent", "executions-parent", "task-symlink", "task-hardlink"])
def test_preexisting_projection_redirect_never_writes_outside_project(worker_context, target):
    from goodteam_os.execution.task_snapshot import execution_key
    c = worker_context
    outside = c.workspace / "outside-projection"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("preserve")
    if target.endswith("parent"):
        path = c.project / (".goodteam/workers/jobs" if target == "jobs-parent" else ".goodteam/executions")
        path.rename(path.with_name(path.name + "-preserved"))
        path.symlink_to(outside, target_is_directory=True)
    else:
        job_root = c.service.workers.root / ("job-correction-" + execution_key(c.execution_id) + "-1")
        job_root.mkdir()
        if target == "task-symlink":
            (job_root / "task.md").symlink_to(sentinel)
        else:
            os.link(sentinel, job_root / "task.md")
    try:
        prepare(c)
    except ValueError:
        pass
    assert sentinel.read_text() == "preserve" and sorted(p.name for p in outside.iterdir()) == ["sentinel"]


def test_fork_cannot_inherit_live_start_lease(worker_context, monkeypatch):
    import multiprocessing
    c = worker_context
    job_id = prepare(c)["job_id"]
    original_run = WorkerJobStore.run_job
    def forked(_workers, _job_id):
        def child():
            try:
                original_run(c.service.workers, job_id)
            except TaskInputError:
                return
            os._exit(91)
        process = multiprocessing.get_context("fork").Process(target=child)
        process.start()
        process.join(5)
        assert process.exitcode == 0
        raise OSError("Synthetic failed parent after inherited child was refused")
    monkeypatch.setattr(WorkerJobStore, "run_job", forked)
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        c.service.run(c.execution_id, job_id, acknowledge_external_cost=True)
    assert not c.effects.exists()
