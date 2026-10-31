"""First-vertical-slice checks for project-level next-iteration facts."""

from goodteam_os.project_iteration import rank_project_backlog, project_iteration_backlog
from goodteam_os.tasks import LocalTaskStore


def _task(identifier, *, status="todo", priority="medium", dependencies=None,
          external_refs=None, acceptance_criteria=None):
    return {"id": identifier, "title": identifier, "description": "Real work", "role": "implementation",
            "status": status, "priority": priority, "dependencies": dependencies or [],
            "external_refs": external_refs or [], "acceptance_criteria": acceptance_criteria or []}


def test_prioritizes_real_ready_work_and_checks_dependencies():
    tasks = [
        _task("APP-1", priority="high", dependencies=["APP-2"]),
        _task("APP-2", status="done"),
        _task("APP-3", priority="critical"),
        _task("APP-4", priority="high", dependencies=["APP-404"]),
    ]
    result = rank_project_backlog(tasks, "APP")
    assert result["status"] == "candidate_ready"
    assert result["next_candidate"]["task_id"] == "APP-3"
    assert result["execution_authorized"] is False
    assert next(x for x in result["candidates"] if x["task_id"] == "APP-4")["blocked_by"] == ["APP-404"]


def test_excludes_workflow_and_goal_execution_cards():
    tasks = [_task("APP-1", priority="critical", external_refs=["stage:implementation"]),
             _task("APP-2", priority="critical", external_refs=["goal:g:work:w"]),
             _task("APP-3", priority="low")]
    result = rank_project_backlog(tasks, "APP")
    assert [x["task_id"] for x in result["candidates"]] == ["APP-3"]


def test_no_fabricated_work_or_false_eligibility():
    assert rank_project_backlog([], "APP")["status"] == "empty"
    result = rank_project_backlog([_task("APP-1", status="blocked")], "APP")
    assert result["status"] == "blocked"
    assert result["next_candidate"] is None
    assert not result["candidates"][0]["eligible_to_plan"]


def test_revision_is_stable_and_changes_on_meaningful_task_update():
    a, b = _task("APP-1"), _task("APP-2")
    first = rank_project_backlog([a, b], "APP")
    assert first["version"] == rank_project_backlog([b, a], "APP")["version"]
    assert first["version"] != rank_project_backlog([a, {**b, "status": "done"}], "APP")["version"]


def test_unreadable_ledger_is_not_presented_as_empty(tmp_path):
    store = LocalTaskStore(tmp_path, "APP")
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{bad json", encoding="utf-8")
    result = project_iteration_backlog(tmp_path, "APP")
    assert result["status"] == "unavailable"
    assert result["reason"] == "task_ledger_unreadable"
    assert result["next_candidate"] is None


def test_read_only_projection_does_not_create_ledger(tmp_path):
    path = LocalTaskStore(tmp_path, "APP").path
    result = project_iteration_backlog(tmp_path, "APP")
    assert result["status"] == "empty"
    assert not path.exists()


def test_bounded_candidate_list_has_separate_total():
    result = rank_project_backlog([_task(f"APP-{n}") for n in range(10)], "APP", limit=3)
    assert len(result["candidates"]) == 3
    assert result["candidate_count"] == 10
    assert result["truncated"] is True


def test_manager_chat_receives_project_backlog_without_auto_dispatch(tmp_path):
    from test_assistant_drafts import send, setup_service

    service, root, executor, calls = setup_service(
        tmp_path, turn={"intent": "project_question", "response": "I can evaluate the backlog."},
    )
    store = LocalTaskStore(root, "DEMO")
    created = store.create_task(
        title="Fix source issue", description="Cover empty request handling",
        role="implementation", acceptance_criteria=["Empty input returns 400"],
        priority="high",
    )
    session = send(service, root, "What should we tackle next?")
    assert len(calls) == 1
    context = calls[0]["context"]
    assert "Project backlog snapshot" in context
    assert created["id"] in context
    assert "Empty input returns 400" in context
    assert '"execution_authorized": false' in context
    assert session["proposed_task"] is None
    assert executor.started == []
