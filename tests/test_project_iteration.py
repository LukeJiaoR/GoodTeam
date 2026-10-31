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


def test_iteration_preview_never_creates_task_even_if_manager_misclassifies(tmp_path):
    from test_assistant_drafts import send, setup_service

    service, root, executor, _calls = setup_service(
        tmp_path, turn={"intent": "work_request", "response": "Ready to implement."},
    )
    result = send(service, root,
                  "GoodTeam project iteration preview (read-only).\n"
                  "Evaluate the next backlog candidate, do not execute.")
    assert result["proposed_task"] is None
    assert result["timeline"][-1]["event"] == "project_question_answered"
    assert executor.started == []



def test_parallel_scope_paths_validate_without_trusting_globs_or_parent_traversal():
    from goodteam_os.project_iteration import normalize_write_domains

    assert normalize_write_domains(["src/api/", "frontend/src/"]) == ["src/api", "frontend/src"]
    for invalid in ([], ["."], ["../secret"], ["/etc"], ["src//api"],
                    ["src\\api"], ["src/*"], ["src/api", "SRC/API"],
                    ["C:/repo"], ["src/./api"], ["src/../api"]):
        assert normalize_write_domains(invalid) is None


def test_task_creation_can_declare_parallel_write_scope_or_readonly_policy(tmp_path):
    store = LocalTaskStore(tmp_path, "APP")
    writer = store.create_task(
        title="Independent API change", description="A scoped edit", role="implementation",
        write_domains=["backend/api/"],
    )
    advisor = store.create_task(
        title="Independent architecture review", description="Review only", role="architect",
        execution_policy="read_only",
    )
    assert store.get_task(writer["id"])["write_domains"] == ["backend/api"]
    assert store.get_task(advisor["id"])["execution_policy"] == "read_only"
    projection = rank_project_backlog(store.list_tasks(), "APP")
    assert next(row for row in projection["candidates"] if row["task_id"] == writer["id"])["write_domain_status"] == "declared"
    assert next(row for row in projection["candidates"] if row["task_id"] == advisor["id"])["execution_policy"] == "read_only"


def test_bad_write_scope_cannot_be_saved_or_treated_as_parallel_authority(tmp_path):
    import pytest

    store = LocalTaskStore(tmp_path, "APP")
    with pytest.raises(ValueError, match="Write domains"):
        store.create_task(
            title="Unsafe", description="Invalid declared scope", role="implementation",
            write_domains=["../../secret"],
        )
    record = store.create_task(title="Unknown scope", description="Still plannable", role="implementation")
    projection = rank_project_backlog(store.list_tasks(), "APP")
    candidate = projection["next_candidate"]
    assert candidate["task_id"] == record["id"]
    assert candidate["write_domain_status"] == "unknown"
