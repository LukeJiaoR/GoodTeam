"""Run only against a dedicated disposable GOODTEAM_TEST_DATABASE_URL.

The CLI/model responses are offline fixtures, not real-provider acceptance.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from goodteam_os.database import database
from goodteam_os.database_migration import migrate
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.project_readiness import ProjectReadinessStore
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.execution import ExecutionLedger
from goodteam_os.execution.idempotency import execution_id_from_task
from test_dispatch_isolation import repository


@pytest.fixture
def postgres(monkeypatch, tmp_path):
    url = os.environ.get("GOODTEAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("A dedicated disposable PostgreSQL database is required.")
    schema = "gt_test_" + uuid4().hex
    monkeypatch.setenv("GOODTEAM_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("GOODTEAM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GOODTEAM_PROJECTS_REGISTRY", str(tmp_path / "projects-registry.json"))
    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)

    def enable():
        monkeypatch.setenv("GOODTEAM_DATABASE_URL", url)
    return enable


def test_legacy_import_preserves_authority_identity_and_rejects_overwrite(tmp_path, monkeypatch, postgres):
    from test_execution_ledger import _task, _run, _event

    source = repository(tmp_path / "source")
    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    session.append_message("original", "user", "原文和授权不能重置")
    session._mutate(lambda value: {**value, "project_continuation": {
        "id": "original-grant", "status": "stopped", "reason": "owner_stopped",
        "manager_calls": 2, "max_manager_calls": 2, "batches_started": 1, "max_batches": 2}})
    tasks = LocalTaskStore(source, "DEMO")
    task = tasks.create_task(title="Original scope", description="Keep the exact task.", role="implementation")
    goal_store = GoalStore(tmp_path, "DEMO")
    goal = goal_store.create_goal(objective="Original objective", acceptance_criteria=["Verified evidence"],
        allowed_scope=[task["id"]], max_work_units=1, confirmed=True, idempotency_key="original-goal")
    goal = goal_store.stop_goal(goal["id"], expected_revision=goal["revision"], idempotency_key="stop-original")
    state_store = StateStore(tmp_path)
    state_store.write_project_current(create_initial_state("original-run", "DEMO", "Original work"))
    readiness = ProjectReadinessStore(tmp_path)
    readiness.initialize("DEMO", source)
    team = TeamControlPlaneStore(tmp_path, "DEMO")
    team.load_or_migrate(legacy_target={})
    registry = ProjectRegistry()
    registry.register_project(tmp_path / "projects" / "demo.local.yaml", root=source, key="DEMO")
    queue = DurableOperatorQueue(tmp_path / "state")
    queue.enqueue(idempotency_key="original", source_url="local:original", source_revision="original-sha",
                  project_key="DEMO", repository_identity="original-repository")
    queue.set_desired_state("stopped")
    ledger = ExecutionLedger(source)
    execution_id = execution_id_from_task(_task().task_id)
    execution_paths = [ledger.create_task(_task()), ledger.create_run(_run(execution_id)),
                       ledger.append_event(_event("original-event", execution_id))]

    def extra_state():
        return (readiness.read("DEMO"), team.read(), team.read_events(), registry.list_registered_projects(),
                queue.list_items(), queue.summary(), ledger.replay(execution_id))

    before = (session.read(), tasks.list_tasks(), goal_store.get_goal(goal["id"]),
              asdict(state_store.read_project_current("DEMO")), extra_state())
    source_bytes = {path: path.read_bytes() for path in (
        session.path, tasks.path, goal_store.path, state_store.project_state_dir("DEMO") / "current.json",
        readiness._path("DEMO"), team.state_path, team.events_path, registry.registry_path,
        queue.state_path, queue.events_path, *execution_paths)}
    postgres()
    with pytest.raises(ValueError, match="explicit PostgreSQL import"):
        session.read()
    report = migrate(workspace=tmp_path, project_roots=[source], offline_confirmed=True)
    assert report["documents_imported"] == len(source_bytes) - 1 and report["goals_imported"] == 1
    assert source_bytes == {path: path.read_bytes() for path in source_bytes}
    after = (session.read(), tasks.list_tasks(), GoalStore(tmp_path, "DEMO").get_goal(goal["id"]),
             asdict(state_store.read_project_current("DEMO")), extra_state())
    assert before == after
    assert migrate(workspace=tmp_path, project_roots=[source], offline_confirmed=True)["documents_imported"] == 0
    session.append_message("after-import", "user", "PG 中的新消息")
    assert session.path.read_bytes() == source_bytes[session.path]
    assert session.read()["project_continuation"]["status"] == "stopped"
    session.path.write_text('{"unrelated":"changed backup"}')
    with pytest.raises(ValueError, match="source changed"):
        migrate(workspace=tmp_path, project_roots=[source], offline_confirmed=True)
    assert session.read()["timeline"][-1]["content"] == "PG 中的新消息"
    monkeypatch.delenv("GOODTEAM_DATABASE_URL")
    with pytest.raises(ValueError, match="PostgreSQL"):
        session.read()


def test_pg_preparation_keeps_gate_and_directory_identity_checks(tmp_path, monkeypatch, postgres):
    from test_project_readiness_web import (
        synchronous_ready_executor, test_existing_readiness_api_runs_confirms_and_unlocks_real_workflow_bridge,
    )
    from goodteam_os import state_documents

    postgres()
    synchronous_ready_executor.__wrapped__(monkeypatch)
    test_existing_readiness_api_runs_confirms_and_unlocks_real_workflow_bridge(tmp_path, None)
    workspace = tmp_path / "workspace"
    path = state_documents.paths(workspace / "state" / "projects", "*/readiness.json")[0]
    assert not path.exists()
    before = state_documents.read_json(path)
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    store = ProjectReadinessStore(workspace, workspace_fd=descriptor)
    try:
        store.mutate(path.parent.name, lambda value: {**value, "lifecycle": "attention_required"})
        moved = path.parent.with_name(path.parent.name + "-moved")
        path.parent.rename(moved)
        path.parent.mkdir()
        try:
            with pytest.raises(OSError, match="Readiness directory changed"):
                store._commit_anchored_write(path.parent.name)
            store._rollback_anchored_write(path.parent.name)
            assert state_documents.read_json(path) == before
        finally:
            path.parent.rmdir()
            moved.rename(path.parent)
    finally:
        store.close()
        os.close(descriptor)


def _manager(**request):
    snapshot = request["context"].split("Fresh continuation/batch/attention (read-only, never authorization): ", 1)[1].split("\n", 1)[0]
    grant = json.loads(snapshot)["continuation"]
    kind = "confirm_delivery" if "交付" in request["text"] else "explain"
    return {"intent": "project_operation", "response": "当前在等待你审阅并确认交付，额度没有增加。",
            "project_operation": {"kind": kind, "target_id": grant["id"]}}


def _new_process_message(workspace, root, config, message_id, text):
    program = """
import json, sys
from pathlib import Path
from goodteam_os.task_assistant import TaskAssistantService
def manager(**request):
    data=request['context'].split('Fresh continuation/batch/attention (read-only, never authorization): ',1)[1].split('\\n',1)[0]
    grant=json.loads(data)['continuation']
    return {'intent':'project_operation','response':'正在等待当前批次的交付确认。',
            'project_operation':{'kind':'explain','target_id':grant['id']}}
service=TaskAssistantService(Path(sys.argv[1]),project_config=Path(sys.argv[3]),manager_turn=manager)
result=service.send_message(project_key='DEMO',root=Path(sys.argv[2]),message_id=sys.argv[4],text=sys.argv[5])
print(json.dumps({'status':result['session']['assistant_requests'][sys.argv[4]]['status']}))
"""
    completed = subprocess.run([sys.executable, "-c", program, str(workspace), str(root), str(config), message_id, text],
                               capture_output=True, text=True, timeout=30, check=True)
    return json.loads(completed.stdout)


def test_pg_handoff_survives_process_restart_and_advances_two_distinct_iterations(tmp_path, monkeypatch, postgres):
    from test_project_iteration_continuation import test_verified_delivery_automatically_replans_next_scoped_batch_and_survives_replay

    postgres()

    def delivery_handoff(workspace, root, config, request):
        service = TaskAssistantService(workspace, project_config=config, manager_turn=_manager)
        result = service.send_message(project_key="DEMO", root=root, text="确认这批交付", message_id="handoff")
        assert result["session"]["assistant_requests"]["handoff"]["status"] == "succeeded"
        assert "确认你已审阅" in result["session"]["timeline"][-1]["content"]
        before = TaskAssistantSessionStore(workspace, "DEMO").read()["project_continuation"]
        assert _new_process_message(workspace, root, config, "question", "为什么在这里等待？")["status"] == "succeeded"
        assert TaskAssistantSessionStore(workspace, "DEMO").read()["project_continuation"] == before
        assert not (workspace / "state" / "projects" / "DEMO" / "readiness.json").exists()
        assert _new_process_message(workspace, root, config, "confirm", "确认交付")["status"] == "succeeded"
        # HTTP/message replay returns the existing result without a second effect.
        service.send_message(project_key="DEMO", root=root, text="确认交付", message_id="confirm")
        session = TaskAssistantSessionStore(workspace, "DEMO").read()
        deliveries = [item for item in session["timeline"] if item.get("event") == "project_batch_delivery_verified"]
        assert len(deliveries) == 1
        assert deliveries[0]["details"]["candidate_commit"] == request["candidate_commit"]
        assert session["project_continuation"]["manager_calls"] == before["manager_calls"]
        with database().connection() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM checkpoints").fetchone()["n"] > 0

    test_verified_delivery_automatically_replans_next_scoped_batch_and_survives_replay(
        tmp_path, monkeypatch, delivery_handoff=delivery_handoff)


def test_stop_while_waiting_invalidates_old_confirmation(tmp_path, monkeypatch, postgres):
    from goodteam_os import manager_dialogue, project_iteration_continuation as continuation
    from goodteam_os.projects import write_project_config

    postgres()
    source = repository(tmp_path / "source")
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(source), worker_mode="fake")
    session = TaskAssistantSessionStore(tmp_path, "DEMO")
    session._mutate(lambda value: {**value, "project_continuation": {
        "id": "grant", "status": "active", "reason": "awaiting_verified_delivery",
        "manager_calls": 1, "max_manager_calls": 2, "pending_batch": {"run_id": "task-batch-one"}},
        "project_task_batches": {"task-batch-one": {"status": "review", "expected_continuation_id": "grant",
            "project_root": str(source.resolve()), "delivery_receipt": None}}})
    monkeypatch.setattr(manager_dialogue, "current_context", lambda store, root: {
        "continuation": store.read()["project_continuation"], "attention": None,
        "batch": {"expected_continuation_id": "grant"}, "integration": {"candidate_commit": "a" * 40},
        "landed_commit": "b" * 40, "version": store.read()["project_continuation"]["status"]})
    monkeypatch.setattr(continuation, "_verify_landed_batch", lambda **kwargs: ({}, None))
    monkeypatch.setattr("goodteam_os.project_iteration_quality.accepted_batch_quality", lambda **kwargs: None)
    effects = []
    monkeypatch.setattr(continuation, "accept_project_batch_delivery", lambda **kwargs: effects.append(kwargs))
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=_manager)
    service.send_message(project_key="DEMO", root=source, text="确认这批交付", message_id="prepare")
    continuation.stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id="grant")
    service.send_message(project_key="DEMO", root=source, text="确认交付", message_id="stale-confirm")
    assert not effects
    grant = session.read()["project_continuation"]
    assert grant["status"] == "stopped" and grant["manager_calls"] == 1
    assert "状态已经变化" in session.read()["timeline"][-1]["content"]


def test_pg_goal_ledger_runs_existing_workflow_and_derives_followup(tmp_path, monkeypatch, postgres):
    from test_goal_continuation import setup, test_actual_worker_result_drives_manager_and_two_real_workflow_runs

    postgres()
    test_actual_worker_result_drives_manager_and_two_real_workflow_runs(setup.__wrapped__(tmp_path), monkeypatch)


def test_pg_model_failure_retries_existing_message_identity(tmp_path, monkeypatch, postgres):
    from test_assistant_recovery_api import test_api_failure_reload_retry_and_replay

    postgres()
    test_api_failure_reload_retry_and_replay(tmp_path, monkeypatch)

    # Retrying an earlier task question cannot interpret it as permission for
    # a newly active run or its current continuation.
    from goodteam_os.manager_execution import ManagerExecutionError
    from goodteam_os.projects import write_project_config

    source = repository(tmp_path / "history-source")
    config = write_project_config(tmp_path / "history-projects", "HISTORY", "History", repo_path=str(source), worker_mode="fake")
    calls = []

    def manager(**request):
        calls.append(request)
        if len(calls) == 1:
            raise ManagerExecutionError("Fixture model unavailable")
        return {"intent": "ambiguous", "response": "The earlier run is read-only."}

    states = StateStore(tmp_path)
    states.write_project_current(create_initial_state("original-history-run", "HISTORY", "Original task"))
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=manager)
    with pytest.raises(ManagerExecutionError):
        service.send_message(project_key="HISTORY", root=source, text="Explain the current task", message_id="old-question")
    states.archive_project_current("HISTORY", "completed")
    states.write_project_current(create_initial_state("new-history-run", "HISTORY", "A different task"))
    service.retry_message(project_key="HISTORY", root=source, message_id="old-question", expected_attempt=1)
    assert '"kind": "task_reference"' in calls[-1]["context"]
    assert '"run_id": "original-history-run"' in calls[-1]["context"]
    session = TaskAssistantSessionStore(tmp_path, "HISTORY").read()
    assert session["timeline"][-1]["run_id"] == "original-history-run"
    assert states.read_project_current("HISTORY").run_id == "new-history-run"


def test_fresh_pg_state_rejects_missing_configuration_after_restart(tmp_path, postgres):
    postgres()
    workspace, goal_workspace, source = (tmp_path / name for name in ("workspace", "goals-only", "source"))
    session = TaskAssistantSessionStore(workspace, "DEMO")
    session._mutate(lambda value: {**value, "project_continuation": {
        "id": "ended", "status": "stopped", "manager_calls": 2, "max_manager_calls": 2}})
    task = LocalTaskStore(source, "DEMO").create_task(title="Preserved", description="Original scope", role="implementation")
    goals = GoalStore(goal_workspace, "DEMO")
    goal = goals.create_goal(objective="Preserved", acceptance_criteria=["Verified"], allowed_scope=[task["id"]],
        max_work_units=1, confirmed=True, idempotency_key="original")
    goals.stop_goal(goal["id"], expected_revision=goal["revision"], idempotency_key="stop")
    program = """
import sys
from pathlib import Path
from goodteam_os.goals import GoalStore, goal_store_exists
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
workspace, goals, source = map(Path, sys.argv[1:])
for read in (lambda: TaskAssistantSessionStore(workspace, 'DEMO').read(),
             lambda: goal_store_exists(goals), lambda: GoalStore(goals, 'DEMO').list_goals(),
             lambda: LocalTaskStore(source, 'DEMO').list_tasks()):
    try:
        read()
    except ValueError as exc:
        assert 'PostgreSQL' in str(exc)
    else:
        raise AssertionError('PG authority silently reopened in file mode')
"""
    env = dict(os.environ)
    env.pop("GOODTEAM_DATABASE_URL")
    restarted = subprocess.run([sys.executable, "-c", program, str(workspace), str(goal_workspace), str(source)],
                               env=env, capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    assert session.read()["project_continuation"]["manager_calls"] == 2
    assert goals.get_goal(goal["id"])["status"] == "stopped"


def test_import_preserves_legacy_fallback_locations_and_approvals(tmp_path, monkeypatch, postgres):
    from goodteam_os.state import ApprovalDecision

    workspace = tmp_path / "workspace"
    source = repository(tmp_path / "source")
    registry = ProjectRegistry()
    origins = {"PARENT": tmp_path, "HOME": Path(os.environ["GOODTEAM_HOME"]), "REGISTERED": tmp_path / "external"}
    expected, source_bytes = {}, {}
    for key, origin in origins.items():
        state = create_initial_state("original-" + key, key, "Preserve the original approval")
        state.approvals["plan"] = ApprovalDecision.APPROVED
        states = StateStore(origin)
        states.write_project_current(state)
        ProjectReadinessStore(origin).initialize(key, source)
        directory = states.project_state_dir(key)
        registry.register_project(workspace / "projects" / (key + ".yaml"), root=source, key=key,
                                  state_path=directory if key == "REGISTERED" else None)
        expected[key] = (asdict(StateStore(workspace).read_project_current(key)), ProjectReadinessStore(workspace).read(key))
        source_bytes.update({path: path.read_bytes() for path in directory.glob("*.json")})
    postgres()
    migrate(workspace=workspace, project_roots=[source], offline_confirmed=True)
    for key in origins:
        assert (asdict(StateStore(workspace).read_project_current(key)), ProjectReadinessStore(workspace).read(key)) == expected[key]
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        anchored = ProjectReadinessStore(workspace, workspace_fd=descriptor)
        assert [anchored.read(key) for key in origins] == [expected[key][1] for key in origins]
    finally:
        os.close(descriptor)
    assert source_bytes == {path: path.read_bytes() for path in source_bytes}
    assert migrate(workspace=workspace, project_roots=[source], offline_confirmed=True)["documents_imported"] == 0
    # The captured original locations survive restart and a changed home setting.
    program = """
import json, sys
from pathlib import Path
from goodteam_os.state import StateStore
store = StateStore(Path(sys.argv[1]))
print(json.dumps([store.read_project_current(key).run_id for key in sys.argv[2:]]))
"""
    env = {**os.environ, "GOODTEAM_HOME": str(tmp_path / "different-home")}
    result = subprocess.run([sys.executable, "-c", program, str(workspace), *origins], env=env,
                            capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(result.stdout) == ["original-" + key for key in origins]


def test_pg_conversation_delivers_reviewed_correction(tmp_path, monkeypatch, postgres):
    from test_project_iteration_quality import test_batch_quality_checks_reviews_corrects_and_delivers_without_reexecution

    postgres()

    def delivery_handoff(workspace, root, config, request):
        store = TaskAssistantSessionStore(workspace, "DEMO")
        def awaiting(session):
            session["project_continuation"] = {"id": "correction-grant", "status": "active",
                "manager_calls": 1, "max_manager_calls": 1, "pending_batch": {"run_id": request["run_id"]}}
            session["project_task_batches"][request["run_id"]]["expected_continuation_id"] = "correction-grant"
            return session
        store._mutate(awaiting)
        service = TaskAssistantService(workspace, project_config=config, manager_turn=_manager)
        result = service.send_message(project_key="DEMO", root=root, text="确认这批交付", message_id="corrected-handoff")
        assert "确认你已审阅" in result["session"]["timeline"][-1]["content"]
        assert request["candidate_commit"] in result["session"]["timeline"][-1]["content"]
        assert _new_process_message(workspace, root, config, "corrected-confirm", "确认交付")["status"] == "succeeded"
        receipt = store.read()["project_task_batches"][request["run_id"]]["delivery_receipt"]
        assert receipt["candidate_commit"] == receipt["quality"]["candidate_commit"] == request["candidate_commit"]

    test_batch_quality_checks_reviews_corrects_and_delivers_without_reexecution(
        tmp_path, monkeypatch, delivery_handoff=delivery_handoff)


def test_pg_terminal_transition_reconciles_original_project_task(tmp_path, monkeypatch, postgres):
    from test_project_iteration_planner import test_parallel_selection_is_persisted_and_reused_without_new_model_call

    postgres()
    test_parallel_selection_is_persisted_and_reused_without_new_model_call(tmp_path, monkeypatch)


def test_pg_idle_goal_cache_revalidates_changed_row_values(tmp_path, postgres):
    from goodteam_os.goals import GoalCorruptState

    postgres()
    store = GoalStore(tmp_path, "DEMO")
    goal = store.create_goal(objective="Keep stopped authority", acceptance_criteria=["Verified"],
        allowed_scope=["src"], max_work_units=1, confirmed=True, idempotency_key="original")
    store.stop_goal(goal["id"], expected_revision=goal["revision"], idempotency_key="stop")
    store.sync_tasks(goal["id"], LocalTaskStore(tmp_path / "source", "DEMO"))
    cache = {}
    assert store.list_continuation_goals(validated_idle=cache) == []
    assert goal["id"] in cache["records"]
    with store._transaction(write=True) as conn:
        conn.execute("UPDATE goals SET data=? WHERE id=?", ("{broken", goal["id"]))
    with pytest.raises(GoalCorruptState):
        store.list_continuation_goals(validated_idle=cache)
    assert cache == {}


def test_pg_failed_event_append_releases_nested_lock_and_rolls_back(tmp_path, postgres):
    from hashlib import sha256
    import psycopg
    from psycopg import sql
    from goodteam_os import state_documents

    postgres()
    queue = DurableOperatorQueue(tmp_path / "state")
    path = state_documents.document_key(queue.events_path)
    with database().connection() as conn:
        conn.execute(sql.SQL("ALTER TABLE state_documents ADD CONSTRAINT reject_event CHECK (path <> {})").format(sql.Literal(path)))
    request = dict(idempotency_key="original", source_url="local:original", source_revision="original-sha",
                   project_key="DEMO", repository_identity="original-repository")
    with pytest.raises(psycopg.Error):
        queue.enqueue(**request)
    assert state_documents._CONNECTION.get() is None
    key = int.from_bytes(sha256(path.encode()).digest()[:8], signed=True)
    with psycopg.connect(os.environ["GOODTEAM_DATABASE_URL"], autocommit=True) as observer:
        assert observer.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
    with database().connection() as conn:
        conn.execute("ALTER TABLE state_documents DROP CONSTRAINT reject_event")
    assert queue.list_items() == []
    assert queue.enqueue(**request)[1] is True
    assert len(queue.list_items()) == len(state_documents.read_events(queue.events_path)) == 1
