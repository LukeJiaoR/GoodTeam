"""Normal local run: real Git/worktree and offline Builder/check/Reviewer processes."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from goodteam_os import cli
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.candidate_delivery import CandidateDeliveryService
from goodteam_os.execution.candidate_validation import _read, _write
from goodteam_os.execution.sandbox import SandboxService
from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes, digest
from goodteam_os.operator import AmbiguousOperation, LocalOperatorActions
from goodteam_os.operator_candidate_review import OperatorCandidateReviewConfig
from goodteam_os.workers import WorkerJobStore
from test_automatic_review_entry import admitted, entry as entry_fixture  # noqa: F401
from test_candidate_validation import git, protected
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture
def delivery(request, monkeypatch):
    c = request.getfixturevalue("entry_fixture")
    c.check_calls = c.workspace / "check-calls"
    c.builder_calls = c.workspace / "builder-calls"
    c.argv = [sys.executable, "-c",
        "from pathlib import Path; exec(Path('feature.py').read_text()); assert VALUE == 'fixed'; "
        "Path(" + repr(str(c.check_calls)) + ").open('a').write('check\\n')"]
    script = c.workspace / "offline-builder.py"
    c.builder_script = script
    script.write_text("""import os, sys, subprocess
from pathlib import Path
prompt = sys.stdin.read()
assert 'OLD_CONDITION' in prompt and 'proposed-command' in prompt
Path(os.environ['BUILDER_CALLS']).open('a').write('build\\n')
Path('feature.py').write_text("VALUE = 'fixed'\\n# Built in the assigned worktree\\n")
subprocess.run(['git', 'add', 'feature.py'], check=True)
subprocess.run(['git', 'commit', '-qm', 'Offline Builder candidate'], check=True)
print('Built committed candidate')
""")
    env = {"PATH": os.defpath, "HOME": str(c.workspace / "empty-home"), "BUILDER_CALLS": str(c.builder_calls),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    c.worker_env = env

    def offline(store, job_root, job, spec):
        assert any((protected(c) / name).is_file() for name in
                   ("delivery-plan.json", "operator-candidate-configuration.json"))
        return store._run_cli_worker(job_root, job, command=[sys.executable, str(script)], model="offline",
            preflight={"available": True, "ready": True}, label="Offline fixture", capture="stdout",
            summarize=lambda *_: "Offline returned", worker_env=env, timeout_seconds=5)

    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    c.plan = {"base_sha": c.base, "argv": c.argv, "criteria": [0, 1]}
    return c


def run_args(c, *, first=True):
    return ["execution", "run", "--project-root", str(c.project), "--execution-id", c.execution_id,
        *(["--review-base", c.base, "--criterion", "0", "--criterion", "1", "--", *c.argv] if first else [])]


def run(c, capsys, *, first=True, admission=None):
    assert cli.main(run_args(c, first=first), codex_review_admission=admission) == 0
    return json.loads(capsys.readouterr().out)


def item(c, **metadata):
    return {"id": "operator-fixture", "execution_id": c.execution_id, "project_key": "DEMO", "attempt": 1,
        "repository_identity": "example/project", "source_url": "https://github.com/example/project/issues/1",
        "metadata": {"project_root": str(c.project), "pr_number": 1, "worker_backend": "codex",
                     "task_contract": c.store.load(c.execution_id).task.task_contract.to_dict(), **metadata}}


def operator_config(c, model):
    contract = c.store.load(c.execution_id).task.task_contract.to_dict()
    return OperatorCandidateReviewConfig(contract_digest=digest(canonical_bytes(contract)),
        base_sha=c.plan["base_sha"], argv=tuple(c.plan["argv"]), criteria=tuple(c.plan["criteria"]),
        reviewer_model=model)


def bind_pr(c):
    from goodteam_os.execution.github import GitHubExecutionBindingStore
    store = GitHubExecutionBindingStore(c.project)
    store.write_binding(replace(store.read_binding(c.execution_id), pr_number=1))


def test_normal_cli_runs_builder_check_and_independent_review_then_reopens(delivery, capsys):
    c = delivery
    authority = admitted(c)
    original = c.store.load(c.execution_id).data
    result = run(c, capsys, admission=authority)
    outcome = result["candidate_delivery"]
    assert result["run"]["status"] == "succeeded" and outcome["status"] == "reviewed", result
    evidence = outcome["candidate_validation"]
    head = evidence["candidate"]["head_sha"]
    sandbox = SandboxService(c.project).get_sandbox(c.execution_id)
    assert head == git(Path(sandbox["worktree_path"]), "rev-parse", "HEAD") and head != c.head
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert outcome["review"]["base_sha"] == c.base
    assert evidence["acceptance_status"] == "accepted_by_review_policy"
    assert outcome["review"]["provenance"]["task_skill_context"]["task"] == c.scope["task"]
    assert outcome["review"]["reviewer"] != result["run"]["worker_job_id"]
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n" and len(authority.leases) == 1
    assert c.store.load(c.execution_id).data == original
    assert not list(protected(c).glob("validation-review-*"))
    # Restart requires no head, base, command, criterion, model or receipt copying.
    c.policy_path.unlink()
    again = run(c, capsys, first=False)
    assert again["candidate_delivery"]["status"] == "reviewed"
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n"
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    assert workbench["evidence"]["candidate_validation"]["review"]["receipt_id"] == outcome["review"]["receipt_id"]


def test_run_without_local_options_and_read_surfaces_do_not_execute_saved_check(delivery):
    c = delivery
    authority = admitted(c)
    CandidateDeliveryService(c.project, codex_review_admission=authority).configure(c.execution_id, **c.plan)
    api = ExecutionAPI(c.project, codex_review_admission=authority)
    # This is the existing HTTP run call shape, even with a saved local plan.
    assert "candidate_delivery" not in api.run_execution(c.execution_id)
    api.replay(c.execution_id)
    api.get_evidence(c.execution_id)
    api.get_review_workbench(c.execution_id)
    assert c.builder_calls.read_text() == "build\n" and not c.check_calls.exists() and not c.calls.exists()


@pytest.mark.parametrize("change", ["base", "command", "criteria", "seconds", "model"])
def test_delivery_plan_cannot_change_before_builder_or_on_restart(delivery, capsys, monkeypatch, change):
    c = delivery
    authority = admitted(c)
    service = CandidateDeliveryService(c.project, codex_review_admission=authority)
    saved = service.configure(c.execution_id, **c.plan)
    changed = dict(c.plan)
    if change == "base":
        changed["base_sha"] = c.head
    elif change == "command":
        changed["argv"] = [sys.executable, "-c", "raise AssertionError('unselected command')"]
    elif change == "criteria":
        changed["criteria"] = [0]
    elif change == "seconds":
        changed["seconds"] = 1
    else:
        monkeypatch.setattr(authority, "policy", {**authority.policy, "model": "another-model"})
    with pytest.raises(TaskInputError, match="delivery_plan_changed"):
        ExecutionAPI(c.project, codex_review_admission=authority).run_execution(c.execution_id, candidate_review=changed)
    assert service.configuration(c.execution_id) == saved
    assert not c.builder_calls.exists() and not c.check_calls.exists() and not c.calls.exists()
    if change == "model":
        monkeypatch.setattr(authority, "policy", {**authority.policy, "model": saved["reviewer_model"]})
    assert run(c, capsys, first=False, admission=authority)["candidate_delivery"]["status"] == "reviewed"


def test_missing_policy_or_incomplete_run_input_never_dispatches_builder(delivery, capsys):
    c = delivery
    authority = admitted(c)
    assert cli.main(run_args(c)) == 2
    assert "delivery_reviewer_admission_missing" in capsys.readouterr().err
    incomplete = run_args(c, first=False) + ["--review-base", c.base]
    assert cli.main(incomplete, codex_review_admission=authority) == 2
    assert "explicit check command" in capsys.readouterr().err
    assert not c.builder_calls.exists() and not c.check_calls.exists() and not c.calls.exists()


@pytest.mark.parametrize("change", ["task", "root"])
def test_mismatched_reviewer_policy_blocks_before_builder(delivery, monkeypatch, change):
    c = delivery
    authority = admitted(c)
    if change == "task":
        monkeypatch.setattr(authority, "policy", {**authority.policy,
            "task": {**authority.policy["task"], "logical_id": "another-task"}})
    else:
        monkeypatch.setattr(authority, "root", c.workspace)
    with pytest.raises(TaskInputError, match="delivery_reviewer_task_mismatch"):
        ExecutionAPI(c.project, codex_review_admission=authority).run_execution(
            c.execution_id, candidate_review=c.plan)
    assert not c.builder_calls.exists() and not c.check_calls.exists() and not c.calls.exists()


def test_check_result_survives_interrupted_handoff_without_rerunning(delivery, capsys, monkeypatch):
    c = delivery
    authority = admitted(c)
    from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
    original = AutomaticCandidateReviewService.start
    with monkeypatch.context() as patch:
        patch.setattr(AutomaticCandidateReviewService, "start", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
        assert run(c, capsys, admission=authority)["candidate_delivery"]["status"] == "blocked"
    assert c.check_calls.read_text() == "check\n" and not c.calls.exists()
    assert AutomaticCandidateReviewService.start is original
    assert run(c, capsys, first=False, admission=authority)["candidate_delivery"]["status"] == "reviewed"
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n"


def test_missing_builder_workspace_blocks_until_its_metadata_is_restored(delivery, monkeypatch):
    c = delivery
    authority = admitted(c)
    original = CandidateDeliveryService.advance
    path = c.project / ".goodteam" / "executions" / c.execution_id / "sandbox.json"
    metadata = []
    def lose_metadata(service, execution_id, **kwargs):
        assert SandboxService(c.project).get_sandbox(execution_id)["status"] == "retained_for_pr"
        metadata.append(path.read_bytes())
        path.unlink()
        return original(service, execution_id, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(CandidateDeliveryService, "advance", lose_metadata)
        result = ExecutionAPI(c.project, codex_review_admission=authority).run_execution(
            c.execution_id, candidate_review=c.plan)
    assert result["candidate_delivery"]["status"] == "blocked"
    assert result["candidate_delivery"]["reason"] == "validation_builder_workspace_mismatch"
    assert c.builder_calls.read_text() == "build\n" and not c.check_calls.exists() and not c.calls.exists()
    assert git(c.project, "rev-parse", "HEAD") == c.head
    path.write_bytes(metadata[0])
    resumed = ExecutionAPI(c.project, codex_review_admission=authority).run_execution(
        c.execution_id, resume_candidate_review=True)["candidate_delivery"]
    assert resumed["status"] == "reviewed"
    assert resumed["candidate_validation"]["candidate"]["head_sha"] != c.head
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n"


def test_indeterminate_check_is_not_retried_by_normal_run(delivery, monkeypatch):
    c = delivery
    authority = admitted(c)
    api = ExecutionAPI(c.project, codex_review_admission=authority)
    with monkeypatch.context() as patch:
        patch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                      lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lost transport result")))
        with pytest.raises(RuntimeError):
            api.run_execution(c.execution_id, candidate_review=c.plan)
    result = api.run_execution(c.execution_id, resume_candidate_review=True)
    assert result["candidate_delivery"]["reason"] == "validation_attempt_indeterminate", result
    assert c.builder_calls.read_text() == "build\n" and not c.check_calls.exists() and not c.calls.exists()


def test_findings_use_original_bounded_loop_and_keep_production_admission_blocker(delivery, capsys):
    c = delivery
    result = run(c, capsys, admission=admitted(c, findings=True))
    assert result["candidate_delivery"]["reason"] == "correction_verified_runtime_unavailable", result
    before = CandidateDeliveryService(c.project).configuration(c.execution_id)
    assert run(c, capsys, first=False)["candidate_delivery"]["reason"] == "correction_verified_runtime_unavailable"
    assert CandidateDeliveryService(c.project).configuration(c.execution_id) == before
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n"


def test_normal_run_routes_findings_through_offline_fix_fresh_check_and_review(delivery, monkeypatch):
    from goodteam_os.skills import review_execution
    from test_automatic_candidate_review import CandidateRuntime, admit_correction

    c = delivery
    c.builder_script.write_text(c.builder_script.read_text().replace("VALUE = 'fixed'", "VALUE = 'pending'"))
    c.plan["argv"] = [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(c.check_calls)) +
        ").open('a').write('check\\n'); exec(Path('feature.py').read_text()); assert VALUE == 'fixed'"]
    authority = admitted(c, model="fixture-model")
    runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    admit_correction(monkeypatch)
    fix = c.workspace / "offline-fix.py"
    fix_calls = c.workspace / "fix-calls"
    fix.write_text("""import sys, subprocess
from pathlib import Path
prompt = sys.stdin.read()
assert 'OLD_CONDITION' in prompt and 'Fix VALUE' in prompt
Path('feature.py').write_text("VALUE = 'fixed'\\n")
subprocess.run(['git', 'add', 'feature.py'], check=True)
subprocess.run(['git', 'commit', '-qm', 'Offline correction'], check=True)
""" + "Path(" + repr(str(fix_calls)) + ").open('a').write('fix\\n')\n")
    initial = WorkerJobStore._run_cli_backend
    def offline(store, job_root, job, spec):
        if not job_root.name.startswith("job-correction-"):
            return initial(store, job_root, job, spec)
        return store._run_cli_worker(job_root, job, command=[sys.executable, str(fix)], model="offline",
            preflight={"available": True, "ready": True}, label="Offline correction", capture="stdout",
            summarize=lambda *_: "Offline fixed", worker_env=c.worker_env, timeout_seconds=5)
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", offline)
    result = ExecutionAPI(c.project, codex_review_admission=authority).run_execution(c.execution_id,
        candidate_review=c.plan, acknowledge_external_cost=True)
    outcome = result["candidate_delivery"]
    assert outcome["status"] == "reviewed", outcome
    assert outcome["acceptance_status"] == "accepted_by_review_policy"
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\ncheck\n"
    assert fix_calls.read_text() == "fix\n" and len(runtime.records) == 2
    assert runtime.records[0]["target"]["head_sha"] != runtime.records[1]["target"]["head_sha"]
    assert outcome["review"]["base_sha"] == c.base
    assert not list(protected(c).glob("validation-review-*"))
    assert ExecutionAPI(c.project).run_execution(c.execution_id, resume_candidate_review=True)["candidate_delivery"]["status"] == "reviewed"
    assert len(runtime.records) == 2 and fix_calls.read_text() == "fix\n"


@pytest.mark.parametrize("kind", ["missing_coverage", "unknown_check", "builder_failure"])
def test_normal_run_does_not_accept_incomplete_coverage_unknown_check_or_failed_builder(delivery, monkeypatch, kind):
    from goodteam_os.skills import review_execution
    from test_automatic_candidate_review import CandidateRuntime

    c = delivery
    authority = admitted(c, model="fixture-model")
    runtime = CandidateRuntime(c.workspace)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    if kind == "missing_coverage":
        def transform(report):
            report["checks"].pop()
            return report
        runtime.transform = transform
    elif kind == "unknown_check":
        c.plan["argv"] = [sys.executable, "-c", "import time; time.sleep(10)"]
        c.plan["seconds"] = 1
    else:
        c.builder_script.write_text("raise SystemExit(7)\n")
    result = ExecutionAPI(c.project, codex_review_admission=authority).run_execution(c.execution_id, candidate_review=c.plan)
    outcome = result["candidate_delivery"]
    assert outcome["status"] in {"inconclusive", "blocked"}, outcome
    assert outcome.get("acceptance_status", "unknown") == "unknown"
    assert not list(protected(c).glob("validation-review-*"))
    assert len(runtime.records) == (0 if kind == "builder_failure" else 1)
    assert CandidateDeliveryService(c.project).review_for_head(c.execution_id, c.head)["recommendation"] == "escalate"


def test_operator_execute_uses_same_normal_handoff_and_retains_original_contract(delivery, monkeypatch):
    c = delivery
    authority = admitted(c)
    actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator,
        candidate_reviews={item(c)["id"]: operator_config(c, authority.policy["model"])},
        codex_review_admission=authority)
    monkeypatch.setattr(actions, "_client", lambda _: c.client)
    original = c.client.value
    snapshot = c.store.load(c.execution_id)
    c.client.value = replace(c.client.value, title="Later issue", body="CHANGED criteria and commands")
    result = actions._execute(item(c))
    assert result["status"] == "succeeded", result
    assert result["candidate_check_status"] == "passed"
    assert result["task_snapshot"] == snapshot.reference
    assert c.store.load(c.execution_id).data == snapshot.data
    assert not c.calls.exists()  # Operator review starts after the PR/CI phase.
    with pytest.raises(AmbiguousOperation, match="source contract changed"):
        actions._hydrate(item(c))
    c.client.value = original
    hydrated = actions._hydrate(item(c))
    assert hydrated["task_contract"] == snapshot.task.task_contract.to_dict()
    bind_pr(c)
    current = item(c, **result)
    monkeypatch.setattr(actions, "_client", lambda _: SimpleNamespace(fetch_pr=lambda *_: {"head_sha": result["head_sha"]}))
    assert actions._review(current)["recommendation"] == "approve"
    monkeypatch.setattr(actions, "_client", lambda _: c.client)
    assert actions._execute(item(c))["status"] == "succeeded"
    # The queue's execute result can be lost before it learns the execution ID.
    pending = item(c)
    pending["execution_id"] = ""
    assert actions.reconcile("execute", pending, {"state": "intent"})["status"] == "succeeded"
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n" and c.calls.read_text() == "review\n"


def test_operator_missing_plan_cannot_infer_check_from_issue_or_queue(delivery, monkeypatch):
    c = delivery
    actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator)
    monkeypatch.setattr(actions, "_client", lambda _: c.client)
    with pytest.raises(TaskInputError, match="operator_candidate_configuration_required"):
        actions._execute(item(c, candidate_review=c.plan))
    assert not c.builder_calls.exists() and not c.check_calls.exists() and not c.calls.exists()


def test_operator_publishes_checked_head_before_review_without_source_commit(delivery, monkeypatch):
    from goodteam_os.execution.github import GitHubExecutionBindingStore
    import subprocess

    c = delivery
    authority = admitted(c)
    actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator,
        candidate_reviews={item(c)["id"]: operator_config(c, authority.policy["model"])},
        codex_review_admission=authority)
    monkeypatch.setattr(actions, "_client", lambda _: c.client)
    completed = actions._execute(item(c))
    head, branch = completed["head_sha"], completed["branch_name"]
    assert completed["candidate_check_status"] == "passed" and not c.calls.exists()
    with pytest.raises(ValueError, match="exact-SHA publication"):
        ExecutionAPI(c.project, github_client=c.client).create_or_reuse_pr(c.execution_id)
    binding = GitHubExecutionBindingStore(c.project).read_binding(c.execution_id)

    class Client:
        create_calls = 0
        def create_pr(self, **kwargs):
            assert kwargs["branch_name"] == branch
            self.create_calls += 1
            return replace(binding, pr_number=1)
        def fetch_pr(self, *_):
            return {"head_sha": head, "headRefName": branch, "number": 1}

    client = Client()
    monkeypatch.setattr(actions, "_client", lambda _: client)
    original = subprocess.run
    pushes = []
    def capture(argv, **kwargs):
        if argv[:2] == ["git", "push"]:
            pushes.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, **kwargs)
    monkeypatch.setattr("goodteam_os.operator.subprocess.run", capture)
    current = item(c, **completed)
    published = actions._ensure_pr(current)
    assert published["head_sha"] == head and client.create_calls == 1
    assert pushes == [["git", "push", "origin", head + ":refs/heads/" + branch]]
    assert not c.calls.exists() and git(c.project, "rev-parse", "HEAD") == c.head
    current["metadata"].update(published)
    assert actions._review(current)["recommendation"] == "approve"
    assert actions._ensure_pr(current)["head_sha"] == head and client.create_calls == 1
    assert c.builder_calls.read_text() == "build\n" and c.check_calls.read_text() == "check\n"
    assert c.calls.read_text() == "review\n"


@pytest.mark.parametrize("change", ["head", "dirty", "redirect", "receipt", "none"])
def test_operator_revalidates_protected_receipts_and_rejects_empty_blocker_bypass(delivery, capsys, monkeypatch, change):
    c = delivery
    bind_pr(c)
    if change != "none":
        result = run(c, capsys, admission=admitted(c))
        head = result["candidate_delivery"]["candidate_validation"]["candidate"]["head_sha"]
    else:
        head = c.head
    actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator)

    class Client:
        merge_calls = 0
        def fetch_pr(self, *_):
            return {"head_sha": head}
        def merge_pr(self, *a, **k):
            self.merge_calls += 1
            return {"merged": True, "sha": "merged"}

    client = Client()
    monkeypatch.setattr(actions, "_client", lambda _: client)
    monkeypatch.setattr(actions, "observe_ci", lambda _: {"status": "passed", "head_sha": head})
    current = item(c, head_sha=head, decision={"head_sha": head},
                   review={"head_sha": head, "blockers": [], "recommendation": "approve"})
    if change != "none":
        current["metadata"]["review"] = actions._review(current)
        assert current["metadata"]["review"]["recommendation"] == "approve"
    if change == "head":
        head = c.head
        current["metadata"]["head_sha"] = head
        current["metadata"]["decision"]["head_sha"] = head
        current["metadata"]["review"]["head_sha"] = head
    elif change == "dirty":
        sandbox = SandboxService(c.project).get_sandbox(c.execution_id)
        (Path(sandbox["worktree_path"]) / "feature.py").write_text("unreviewed edits\n")
    elif change == "redirect":
        path = c.project / ".goodteam" / "executions" / c.execution_id / "sandbox.json"
        data = json.loads(path.read_text())
        data.update(workspace_path=str(c.project), worktree_path=str(c.project))
        path.write_text(json.dumps(data))
    elif change == "receipt":
        selected = _read(protected(c) / "validation-current.json")
        path = protected(c) / ("validation-result-" + selected["operation_key"] + ".json")
        data = _read(path)
        data["receipt_id"] = "validation:forged"
        _write(path, data)
    assert actions._review(current)["recommendation"] == "escalate"
    with pytest.raises(AmbiguousOperation, match="independent.*review|plan check"):
        actions._merge(current)
    assert client.merge_calls == 0


def test_operator_publishes_only_validated_commit_and_accepts_bound_review(delivery, capsys, monkeypatch):
    c = delivery
    result = run(c, capsys, admission=admitted(c))
    head = result["candidate_delivery"]["candidate_validation"]["candidate"]["head_sha"]
    bind_pr(c)
    actions = LocalOperatorActions(workspace=c.workspace, state_dir=c.operator)
    class Client:
        merge_calls = 0
        create_calls = 0
        observed_head = head
        def create_pr(self, **kwargs):
            from goodteam_os.execution.models import GitHubBinding
            self.create_calls += 1
            return GitHubBinding(repo=kwargs["repo"], issue_number=1, pr_number=1,
                branch_name=kwargs["branch_name"], execution_id=kwargs["execution_id"])
        def fetch_pr(self, *_):
            return {"head_sha": self.observed_head, "headRefName": result["run"]["branch_name"], "number": 1}
        def merge_pr(self, *a, **kwargs):
            assert kwargs["head_sha"] == head
            self.merge_calls += 1
            return {"merged": True, "sha": "fixture-merge"}
    client = Client()
    monkeypatch.setattr(actions, "_client", lambda _: client)
    monkeypatch.setattr(actions, "observe_ci", lambda _: {"status": "passed", "head_sha": head})
    current = item(c, head_sha=head, branch_name=result["run"]["branch_name"], decision={"decision": "approve", "head_sha": head})
    current["metadata"]["review"] = actions._review(current)
    assert current["metadata"]["review"]["recommendation"] == "approve"
    pushes = []
    import subprocess
    original = subprocess.run
    def capture(argv, **kwargs):
        if argv[:2] == ["git", "push"]:
            pushes.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, **kwargs)
    monkeypatch.setattr("goodteam_os.operator.subprocess.run", capture)
    actions._publish_sandbox(current, c.execution_id)
    assert pushes == [["git", "push", "origin", head + ":refs/heads/" + result["run"]["branch_name"]]]
    assert actions._merge(current)["merged"] is True and client.merge_calls == 1
    # First PR binding creates no summary commit and preserves the reviewed SHA.
    from goodteam_os.execution.github import GitHubExecutionBindingStore
    store = GitHubExecutionBindingStore(c.project)
    store.write_binding(replace(store.read_binding(c.execution_id), pr_number=None))
    client.observed_head = c.head
    with pytest.raises(ValueError, match="no longer matches"):
        actions._ensure_pr(current)
    assert client.create_calls == 1
    assert not [event for event in ExecutionAPI(c.project).ledger_store.read_events(c.execution_id)
                if event.payload.get("transition") == "pr_opened"]
    client.observed_head = head
    assert actions._ensure_pr(current)["head_sha"] == head
    assert actions._ensure_pr(current)["head_sha"] == head and client.create_calls == 1
    assert git(c.project, "rev-parse", "HEAD") == c.head
    with pytest.raises(ValueError, match="exact-SHA publication"):
        ExecutionAPI(c.project, github_client=client).create_or_reuse_pr(c.execution_id)
    current["metadata"]["branch_name"] = "main"
    with pytest.raises(AmbiguousOperation, match="publication target"):
        actions._publish_sandbox(current, c.execution_id)
    current["metadata"]["pr_number"] = 2
    assert actions._review(current)["reason"] == "delivery_pr_binding_mismatch"
    assert len(pushes) == 4
