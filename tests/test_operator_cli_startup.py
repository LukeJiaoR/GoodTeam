"""Explicit user CLI -> supervisor -> Operator config; offline authority only."""
from dataclasses import asdict, replace
import json
import sys
from types import SimpleNamespace

import pytest

from goodteam_os import cli, service, supervisor
from goodteam_os.operator import LocalOperatorActions, OperatorService
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.operator_startup import SCHEMA, load_config, startup_arguments
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills.models import SkillError
from test_codex_trusted import FixtureTrustedAdmission, native, policy
from test_operator_candidate_entry import entry  # noqa: F401
from test_candidate_validation import candidate  # noqa: F401
from test_candidate_validation import git
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture
def startup(request, monkeypatch):
    c = request.getfixturevalue("entry")
    c.config_path = c.workspace / "operator-config.json"
    c.config_path.write_text(json.dumps({"schema_version": SCHEMA, "items": {c.item["id"]: asdict(c.config)}}))
    c.config_path.chmod(0o600)
    bridge = SimpleNamespace(root=c.workspace, repo=c.project)
    host = native(bridge)
    scope = {"task": {"kind": "direct_task", "project_key": "DEMO",
                       "logical_id": "candidate-review:" + c.store.load(c.execution_id).payload_digest}}
    c.policy_path = policy(bridge, host, scope, model=c.config.reviewer_model)
    monkeypatch.setattr("goodteam_os.skills.codex_trusted.TrustedCodexReviewAdmission", FixtureTrustedAdmission)
    c.flags = ["--operator-review-config", str(c.config_path), "--trusted-codex-review-policy", str(c.policy_path)]
    return c


def test_review_config_command_only_reads_source_and_emits_explicit_argv(startup, monkeypatch, capsys):
    c = startup
    before = c.store.load(c.execution_id).data
    monkeypatch.setattr(DurableOperatorQueue, "get", lambda *_: c.item)
    monkeypatch.setattr(LocalOperatorActions, "_hydrate", lambda *_: {"task_contract": c.item["metadata"]["task_contract"]})
    assert cli.main(["queue", "review-config", "--item-id", c.item["id"], "--workspace", str(c.workspace),
        "--criterion", "0", "--criterion", "1", "--review-base", c.config.base_sha,
        "--reviewer-model", c.config.reviewer_model, "--", *c.config.argv]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["items"][c.item["id"]]["argv"] == list(c.config.argv)
    assert output["items"][c.item["id"]]["contract_digest"] == c.config.contract_digest
    assert c.store.load(c.execution_id).data == before
    assert c.orchestrator.ledger.read_run(c.execution_id).status.value == "queued"


def test_cli_restart_passes_only_operator_startup_paths(startup, monkeypatch, capsys):
    c = startup
    captured = {}
    monkeypatch.setattr(cli, "restart_service", lambda **kw: captured.update(kw) or {"running": False})
    monkeypatch.setattr(cli, "_print_service_status", lambda _: None)
    assert cli.main(["restart", "--workspace", str(c.workspace), *c.flags]) == 0
    assert captured["operator_review_config"] == c.config_path
    assert captured["trusted_codex_review_policy"] == c.policy_path
    assert "codex_review_admission" not in captured


def test_service_pins_config_before_restart_and_supervisor_routes_only_to_operator(startup, monkeypatch):
    c = startup
    captured = {}
    monkeypatch.setattr(service, "resolve_frontend_dist", lambda: c.workspace)
    monkeypatch.setattr(service, "_restart_service_unlocked", lambda *a, **kw: captured.update(kw) or {})
    service.restart_service(c.workspace, operator_review_config=c.config_path, trusted_codex_review_policy=c.policy_path)
    args = captured["operator_args"]
    assert "--operator-review-config-sha256" in args
    specs = supervisor._component_specs(workspace=c.workspace, host="127.0.0.1", port=1234,
                                        frontend_dist=c.workspace, state_dir=c.operator, operator_args=args)
    for component in specs:
        assert ("--operator-review-config" in component.command) == (component.name == "operator")
        assert ("--trusted-codex-review-policy" in component.command) == (component.name == "operator")


def component_args(c):
    return ["_operator-component", "--workspace", str(c.workspace), "--state-dir", str(c.operator),
        "--ready-file", str(c.operator / "ready"), "--ready-token", "ready", "--heartbeat-file", str(c.operator / "beat"),
        "--heartbeat-token", "beat", *c.flags]


def test_component_cli_composes_existing_authority_and_typed_config(startup, monkeypatch):
    c = startup
    captured = {}
    monkeypatch.setattr(cli, "run_operator_component", lambda **kw: captured.update(kw) or 0)
    assert cli.main(component_args(c)) == 0
    assert captured["candidate_reviews"][c.item["id"]] == c.config
    assert isinstance(captured["codex_review_admission"], FixtureTrustedAdmission)


def test_component_runner_injects_configuration_into_existing_service(startup, monkeypatch):
    c = startup
    captured = {}
    class Service:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.queue = SimpleNamespace()
        def recover(self):
            captured["recover"] = True
        def tick(self):
            raise StopIteration("finished offline route test")
    monkeypatch.setattr("goodteam_os.operator.OperatorService", Service)
    monkeypatch.setattr(supervisor.signal, "signal", lambda *args: None)
    monkeypatch.setattr(supervisor, "OperatorLivenessHeartbeat", lambda **kw: SimpleNamespace(
        start=lambda: None, stop=lambda: None, progress=lambda *_: None))
    with pytest.raises(StopIteration):
        supervisor.run_operator_component(workspace=c.workspace, state_dir=c.operator,
            ready_file=c.operator / "ready", ready_token="ready", heartbeat_file=c.operator / "beat", heartbeat_token="beat",
            candidate_reviews={c.item["id"]: c.config}, codex_review_admission=FixtureTrustedAdmission(c.policy_path))
    assert captured["candidate_reviews"][c.item["id"]] == c.config and captured["recover"]


@pytest.mark.parametrize("change", ["bytes", "mode", "symlink", "unknown_field", "untyped_command"])
def test_modified_or_untrusted_config_never_restarts_service(startup, monkeypatch, change):
    c = startup
    identity = load_config(c.config_path)[1]
    if change == "bytes":
        c.config_path.write_text(c.config_path.read_text() + " ")
    elif change == "mode":
        c.config_path.chmod(0o644)
    elif change == "symlink":
        original = c.config_path.with_name("original.json")
        c.config_path.rename(original)
        c.config_path.symlink_to(original)
    else:
        data = json.loads(c.config_path.read_text())
        if change == "unknown_field":
            data["items"][c.item["id"]]["allow"] = True
        else:
            data["items"][c.item["id"]]["argv"] = "python -m pytest"
        c.config_path.write_text(json.dumps(data))
    monkeypatch.setattr(cli, "restart_service", lambda **kw: pytest.fail("must not stop/start service"))
    with pytest.raises((TaskInputError, SkillError)):
        cli.main(["restart", *c.flags, "--operator-review-config-sha256", identity])


def test_model_mismatch_and_config_inside_worker_root_are_rejected(startup, monkeypatch):
    c = startup
    monkeypatch.setattr(cli, "restart_service", lambda **kw: pytest.fail("must not restart"))
    data = json.loads(c.config_path.read_text())
    data["items"][c.item["id"]]["reviewer_model"] = "other-model"
    c.config_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="match"):
        cli.main(["restart", *c.flags])
    data["items"][c.item["id"]]["reviewer_model"] = c.config.reviewer_model
    path = c.project / ".goodteam" / "unsafe-config.json"
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="outside"):
        cli.main(["restart", "--operator-review-config", str(path), "--trusted-codex-review-policy", str(c.policy_path)])


def test_policy_pair_is_explicit_and_exclusive(startup):
    c = startup
    with pytest.raises(ValueError, match="requires"):
        startup_arguments(operator_review_config=c.config_path)
    with pytest.raises(ValueError, match="Choose one"):
        startup_arguments(operator_review_config=c.config_path, local_codex_policy=c.policy_path,
                          trusted_codex_review_policy=c.policy_path)


def test_brand_new_queue_can_prepare_exact_policy_identity_before_any_builder(request, monkeypatch, capsys):
    c = request.getfixturevalue("ctx")  # No pre-created execution or snapshot.
    git(c.project, "init", "-q")
    (c.project / ".gitignore").write_text(".goodteam/\n")
    (c.project / "feature.py").write_text("VALUE = 'base'\n")
    git(c.project, "add", ".")
    git(c.project, "commit", "-qm", "Initial local project")
    base = git(c.project, "rev-parse", "HEAD")
    queue = DurableOperatorQueue(c.operator)
    item, _ = queue.enqueue(idempotency_key="new-source", source_url="https://github.com/example/project/issues/1",
        source_revision="source-revision-1", project_key="DEMO", repository_identity="example/project",
        metadata={"project_root": str(c.project), "worker_backend": "fake", "required_checks": ["CI"],
                  "project_config": str(c.workspace / "projects" / "DEMO.yaml")})
    monkeypatch.setattr(LocalOperatorActions, "_client", lambda *_: c.client)
    assert not list((c.project / ".goodteam" / "executions").glob("*/run.json"))
    prepare = ["queue", "prepare-review", "--item-id", item["id"], "--workspace", str(c.workspace)]
    assert cli.main(prepare) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["builder_status"] == "queued" and not first["runtime_admission_granted"]
    assert first["reviewer_task"]["logical_id"] == "candidate-review:" + first["task_snapshot"]["payload_digest"]
    assert cli.main(prepare) == 0
    assert json.loads(capsys.readouterr().out) == first
    original_source = c.client.value
    c.client.value = replace(original_source, body="Replacement requirements must not replace the original")
    assert cli.main(prepare) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert c.store.load(first["execution_id"]).task.task_contract.acceptance_criteria == ["preserve OLD_CONDITION", "preserve OLD_CONDITION"]
    c.client.value = original_source
    # Obtain a model-free fixture for precisely the printed identity, then use
    # normal configuration CLI and component routing without Python API setup.
    bridge = SimpleNamespace(root=c.workspace, repo=c.project)
    c.policy_path = policy(bridge, native(bridge), {"task": first["reviewer_task"]})
    monkeypatch.setattr("goodteam_os.skills.codex_trusted.TrustedCodexReviewAdmission", FixtureTrustedAdmission)
    assert cli.main(["queue", "review-config", "--item-id", item["id"], "--workspace", str(c.workspace),
        "--review-base", base, "--criterion", "0", "--criterion", "1", "--reviewer-model", "explicit-test-model",
        "--", sys.executable, "-c", "print('offline check')"]) == 0
    c.config_path = c.workspace / "new-task-config.json"
    c.config_path.write_text(capsys.readouterr().out)
    c.config_path.chmod(0o600)
    c.flags = ["--operator-review-config", str(c.config_path), "--trusted-codex-review-policy", str(c.policy_path)]
    captured = {}
    monkeypatch.setattr(cli, "run_operator_component", lambda **kw: captured.update(kw) or 0)
    assert cli.main(component_args(c)) == 0
    app = OperatorService(workspace=c.workspace, state_dir=c.operator,
        candidate_reviews=captured["candidate_reviews"], codex_review_admission=captured["codex_review_admission"])
    for _ in range(4):
        result = app.tick()
    assert result["phase"] == "pr_ready", result
    assert result["metadata"]["candidate_check_status"] == "passed"
    assert c.store.load(first["execution_id"]).reference == first["task_snapshot"]


def test_wrong_task_policy_blocks_before_builder(startup, monkeypatch):
    c = startup
    data = json.loads(c.policy_path.read_text())
    data["task"]["logical_id"] = "candidate-review:" + "a" * 64
    c.policy_path.write_text(json.dumps(data))
    c.actions.review_runtime_kwargs = {"codex_review_admission": FixtureTrustedAdmission(c.policy_path)}
    monkeypatch.setattr("goodteam_os.execution.orchestrator.ExecutionOrchestrator.run_execution",
                        lambda *_: pytest.fail("wrong Reviewer task must stop before Builder"))
    with pytest.raises(TaskInputError, match="operator_reviewer_task_mismatch"):
        c.actions._execute(c.item)


def test_changed_config_between_cli_validation_and_restart_cannot_stop_service(startup, monkeypatch):
    c = startup
    identity = load_config(c.config_path)[1]
    c.config_path.write_text(c.config_path.read_text() + " ")
    monkeypatch.setattr(service, "_restart_service_unlocked", lambda *a, **k: pytest.fail("must not stop service"))
    with pytest.raises(TaskInputError, match="configuration_changed"):
        service.restart_service(c.workspace, operator_review_config=c.config_path,
            trusted_codex_review_policy=c.policy_path, operator_review_config_sha256=identity)


def test_supplied_configuration_rechecks_preflight_instead_of_replaying_old_missing_result(startup, monkeypatch):
    c = startup
    queue = DurableOperatorQueue(c.operator)
    item, _ = queue.enqueue(idempotency_key="resume-effect", source_url=c.item["source_url"], source_revision="rev",
        project_key="DEMO", repository_identity="example/project", metadata=c.item["metadata"])
    item = queue.claim_next()
    old = f"{item['id']}:{item['attempt']}:preflight:none"
    queue.record_operation_intent(item["id"], old, kind="preflight", payload={})
    queue.record_operation_result(item["id"], old, result={"ready": False, "reason": "operator_candidate_configuration_required"})
    queue.transition(item["id"], status="paused", phase="paused", updates={"last_failure_class": "policy_preflight_block",
        "metadata": {**item["metadata"], "pause_reason": "operator_candidate_configuration_required"}})
    app = OperatorService(workspace=c.workspace, state_dir=c.operator, candidate_reviews={item["id"]: c.config})
    app.recover()
    monkeypatch.setattr(app.actions, "_preflight", lambda _: {"ready": True})
    assert app.tick()["phase"] == "executing"
    current = queue.get(item["id"])
    assert current["operations"][old]["result"]["ready"] is False
    assert current["operations"][old + ":configured"]["result"]["ready"] is True
    assert current["attempt"] == item["attempt"]


@pytest.mark.parametrize("block", ["missing_config", "unknown_execution", "started", "other_reason"])
def test_restart_resumes_only_known_pre_builder_missing_configuration(startup, block):
    c = startup
    queue = DurableOperatorQueue(c.operator)
    item, _ = queue.enqueue(idempotency_key="config-pause", source_url=c.item["source_url"], source_revision="rev",
        project_key="DEMO", repository_identity="example/project", metadata=c.item["metadata"])
    item = queue.claim_next()
    if block == "started":
        queue.record_operation_intent(item["id"], "unknown-execute", kind="execute", payload={})
    queue.transition(item["id"], status="paused", phase="paused", updates={
        "last_failure_class": "policy_preflight_block", "execution_id": "unknown" if block == "unknown_execution" else "",
        "metadata": {**item["metadata"], "pause_reason": "operator_candidate_configuration_required"
                     if block != "other_reason" else "something_else"}})
    app = OperatorService(workspace=c.workspace, state_dir=c.operator, candidate_reviews={item["id"]: c.config})
    resumed = app.recover()
    assert resumed["phase"] == ("preflight" if block == "missing_config" else "paused")
    assert resumed["attempt"] == item["attempt"]  # Setup recovery does not consume fix rounds.
    assert app.recover()["attempt"] == resumed["attempt"]
