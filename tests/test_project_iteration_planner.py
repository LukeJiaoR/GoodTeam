"""Project-level Manager selection should be replayable, bounded and advisory."""

import pytest

from goodteam_os.project_iteration import (
    latest_iteration_choice, project_iteration_backlog, rank_project_backlog,
    validate_iteration_choice,
)
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def task(store, title, *, priority="medium", dependencies=None):
    return store.create_task(
        title=title, description="A real scoped feature.", role="implementation",
        priority=priority, dependencies=dependencies or [],
        acceptance_criteria=["A test demonstrates the behavior."],
    )


def manager_response(backlog):
    eligible = [candidate for candidate in backlog["candidates"] if candidate["eligible_to_plan"]]
    return {"selection": {
        "selected_task_id": eligible[-1]["task_id"],
        "rationale": "The feature has a verifiable contract and lower integration risk.",
        "compared_task_ids": [item["task_id"] for item in eligible],
    }, "manager_execution": {"job_id": "synthetic-unit-test"}}


def test_manager_can_select_other_than_source_priority_and_persist(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    store = LocalTaskStore(project, "DEMO")
    high = task(store, "High priority", priority="high")
    low = task(store, "Lower risk", priority="low")
    config = tmp_path / "project.yaml"
    config.write_text("key: DEMO", encoding="utf-8")
    before = store.path.read_bytes()
    snapshot = project_iteration_backlog(project, "DEMO")
    calls = []

    def fake_manager(**kwargs):
        calls.append(kwargs)
        return manager_response(kwargs["backlog"])

    first = plan_next_iteration(
        workspace=tmp_path, project_key="DEMO", root=project,
        project_config=config, expected_backlog_version=snapshot["version"],
        confirmed=True, manager_runner=fake_manager,
    )
    assert first["iteration_choice"]["selected_task_id"] == low["id"]
    assert first["iteration_choice"]["selected_task_id"] != high["id"]
    assert first["workflow_started"] is False
    assert first["iteration_choice"]["execution_authorized"] is False
    assert store.path.read_bytes() == before
    assert len(calls) == 1
    assert calls[0]["context"]["backlog"]["project_key"] == "DEMO"

    persisted = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    assert persisted["proposed_task"] is None
    assert persisted["timeline"][-1]["event"] == "project_iteration_choice_recorded"
    assert latest_iteration_choice(persisted, snapshot)["selected_task_id"] == low["id"]

    repeated = plan_next_iteration(
        workspace=tmp_path, project_key="DEMO", root=project,
        project_config=config, expected_backlog_version=snapshot["version"],
        confirmed=True, manager_runner=fake_manager,
    )
    assert repeated["reused"] is True
    assert len(calls) == 1


def test_planning_rejects_unconfirmed_or_stale_backlog(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    store = LocalTaskStore(project, "DEMO")
    created = task(store, "Add a feature")
    snapshot = project_iteration_backlog(project, "DEMO")
    config = tmp_path / "project.yaml"
    with pytest.raises(ValueError, match="Confirm"):
        plan_next_iteration(
            workspace=tmp_path, project_key="DEMO", root=project,
            project_config=config, expected_backlog_version=snapshot["version"],
        )
    store.update_task(created["id"], priority="high")
    with pytest.raises(ValueError, match="changed"):
        plan_next_iteration(
            workspace=tmp_path, project_key="DEMO", root=project,
            project_config=config, expected_backlog_version=snapshot["version"],
            confirmed=True,
        )


def test_models_cannot_pick_missing_or_blocked_tasks():
    snapshot = rank_project_backlog([
        {"id": "T-1", "title": "Ready", "role": "implementation", "status": "todo",
         "priority": "medium", "dependencies": [], "external_refs": []},
        {"id": "T-2", "title": "Blocked", "role": "implementation", "status": "blocked",
         "priority": "critical", "dependencies": [], "external_refs": []},
    ], "T")
    for chosen in ("T-2", "T-404"):
        with pytest.raises(ValueError):
            validate_iteration_choice(snapshot, {
                "selected_task_id": chosen,
                "rationale": "The chosen task seems urgent but is not eligible.",
                "compared_task_ids": [chosen],
            })
    with pytest.raises(ValueError):
        validate_iteration_choice(snapshot, {
            "selected_task_id": "T-1", "rationale": "Too vague",
            "compared_task_ids": ["T-1"],
        })


def test_change_during_model_call_cannot_create_current_recommendation(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    store = LocalTaskStore(root, "DEMO")
    item = task(store, "Scoped feature")
    snapshot = project_iteration_backlog(root, "DEMO")

    def concurrent_manager(**kwargs):
        store.update_task(item["id"], status="blocked")
        return manager_response(kwargs["backlog"])

    with pytest.raises(ValueError, match="changed during planning"):
        plan_next_iteration(
            workspace=tmp_path, project_key="DEMO", root=root,
            project_config=tmp_path / "project.yaml",
            expected_backlog_version=snapshot["version"],
            confirmed=True, manager_runner=concurrent_manager,
        )
    session = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    assert not any(event.get("event") == "project_iteration_choice_recorded" for event in session["timeline"])


def test_workflow_cards_unrelated_to_project_backlog_do_not_invalidate_choice():
    user = {"id": "T-1", "title": "Feature", "role": "implementation",
            "status": "todo", "external_refs": [], "dependencies": []}
    internal = {"id": "T-2", "title": "QA stage", "role": "qa", "status": "todo",
                "external_refs": ["stage:qa"], "dependencies": []}
    version = rank_project_backlog([user, internal], "T")["version"]
    assert rank_project_backlog([user, {**internal, "status": "done"}], "T")["version"] == version


def test_dependency_state_changes_invalidate_choice():
    user = {"id": "T-1", "title": "Feature", "role": "implementation",
            "status": "todo", "external_refs": [], "dependencies": ["T-2"]}
    dependency = {"id": "T-2", "title": "QA stage", "role": "qa", "status": "todo",
                  "external_refs": ["stage:qa"], "dependencies": []}
    first = rank_project_backlog([user, dependency], "T")
    second = rank_project_backlog([user, {**dependency, "status": "done"}], "T")
    assert first["version"] != second["version"]
    assert first["next_candidate"] is None
    assert second["next_candidate"]["task_id"] == "T-1"
