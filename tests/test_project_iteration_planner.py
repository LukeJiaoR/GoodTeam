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


def test_active_goal_cannot_be_bypassed_by_iteration_selection(tmp_path):
    from goodteam_os.goals import GoalStore

    root = tmp_path / "project"
    root.mkdir()
    store = LocalTaskStore(root, "DEMO")
    task(store, "Another candidate")
    snapshot = project_iteration_backlog(root, "DEMO")
    GoalStore(tmp_path, "DEMO").create_goal(
        objective="Deliver a bounded goal", acceptance_criteria=["A real test passes"],
        allowed_scope=["docs"], max_work_units=1, confirmed=True,
        idempotency_key="goal-unit-test",
    )
    with pytest.raises(ValueError, match="active Goal"):
        plan_next_iteration(
            workspace=tmp_path, project_key="DEMO", root=root,
            project_config=tmp_path / "project.yaml",
            expected_backlog_version=snapshot["version"],
            confirmed=True, manager_runner=lambda **_: pytest.fail("Model was invoked"),
        )


def _batch_candidate(identifier, *, domains=None, policy="workspace_write", dependencies=None,
                     status="todo"):
    return {
        "id": identifier, "title": "Independent work " + identifier,
        "description": "A real task in its own scope", "role": "implementation",
        "status": status, "priority": "high", "dependencies": dependencies or [],
        "external_refs": [], "write_domains": domains,
        "execution_policy": policy,
    }


def _batch_response(*ids):
    return {
        "selected_task_ids": list(ids), "selected_task_id": ids[0],
        "compared_task_ids": list(ids),
        "rationale": "The selected tasks are independent in the supplied project plan; verify actual worktree isolation.",
    }


@pytest.mark.parametrize("left,right", [
    ("src/api", "src/api"),
    ("src/api", "src/api/routes"),
    ("src/api/routes", "src/api"),
    ("src/API", "SRC/api/routes"),
])
def test_manager_parallel_plan_rejects_overlapping_write_domains(left, right):
    snapshot = rank_project_backlog([
        _batch_candidate("T-1", domains=[left]),
        _batch_candidate("T-2", domains=[right]),
    ], "T")
    with pytest.raises(ValueError, match="write-domain collision"):
        validate_iteration_choice(snapshot, _batch_response("T-1", "T-2"))


def test_parallel_selection_is_persisted_and_reused_without_new_model_call(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ledger = LocalTaskStore(project, "DEMO")
    task_a = ledger.create_task(
        title="Write one service", description="Implement service A",
        role="backend", write_domains=["backend/service-a"],
    )
    task_b = ledger.create_task(
        title="Write another service", description="Implement service B",
        role="frontend", write_domains=["frontend/service-b"],
    )
    snapshot = project_iteration_backlog(project, "DEMO")
    calls = []

    def choose(**kwargs):
        calls.append(kwargs)
        return {
            "selection": _batch_response(task_a["id"], task_b["id"]),
            "manager_execution": {"job_id": "fixture"},
        }

    spec = dict(
        workspace=tmp_path, project_key="DEMO", root=project,
        project_config=tmp_path / "unused-project.yaml",
        expected_backlog_version=snapshot["version"],
        confirmed=True, manager_runner=choose,
    )
    first = plan_next_iteration(**spec)
    second = plan_next_iteration(**spec)
    assert first["iteration_choice"]["execution_mode"] == "parallel"
    assert first["workflow_started"] is False
    assert second["reused"] is True
    assert len(calls) == 1
    read = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    choice = latest_iteration_choice(read, snapshot)
    assert choice["selected_task_ids"] == [task_a["id"], task_b["id"]]
    assert choice["execution_authorized"] is False


def test_full_planning_inventory_is_not_limited_by_ui_candidate_preview(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    store = LocalTaskStore(project, "DEMO")
    created = [
        store.create_task(
            title=f"Independent feature {i}", description=f"Implement independent component {i}",
            role="implementation", write_domains=[f"src/module-{i}"],
        ) for i in range(16)
    ]
    ui_snapshot = project_iteration_backlog(project, "DEMO")
    full_snapshot = project_iteration_backlog(project, "DEMO", limit=None)
    assert len(ui_snapshot["candidates"]) == 8
    assert ui_snapshot["truncated"] is True
    assert len(full_snapshot["candidates"]) == 16
    assert full_snapshot["truncated"] is False
    assert ui_snapshot["version"] == full_snapshot["version"]
    captures = []

    def choose(**kwargs):
        captures.append(kwargs["backlog"])
        eligible = kwargs["backlog"]["candidates"]
        assert len(eligible) == 16
        ids = [row["task_id"] for row in eligible]
        return {"selection": _batch_response(*ids), "manager_execution": {"job_id": "synthetic"}}

    result = plan_next_iteration(
        workspace=tmp_path, project_key="DEMO", root=project,
        project_config=tmp_path / "project.yaml",
        expected_backlog_version=ui_snapshot["version"],
        confirmed=True, manager_runner=choose,
    )
    assert len(result["iteration_choice"]["selected_task_ids"]) == len(created)
    assert len(captures) == 1
    assert result["workflow_started"] is False
