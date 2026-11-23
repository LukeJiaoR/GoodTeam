"""Model-free continuation contracts using real run facts and isolated workflow.

The synthetic assessor is deliberately separate from real model validation.
"""

import pytest

from goodteam_os.goal_assessment import validate_assessment
from goodteam_os.goal_continuation import authority_snapshot, handle_goal_action, tick_goal
from goodteam_os.goals import GoalStore
from goodteam_os.team_control_plane import ensure_team_state


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    (root / "README.md").write_text("# Isolated Goal fixture\n")
    projects = tmp_path / "projects"
    projects.mkdir()
    config = projects / "demo.local.yaml"
    config.write_text(f"key: DEMO\nname: Goal fixture\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\nstaging:\n  enabled: false\n")
    ensure_team_state(tmp_path, "DEMO", config)
    store = GoalStore(tmp_path, "DEMO")
    def create(limit=2):
        return store.create_goal(objective="Make the bounded fixture useful", acceptance_criteria=["Feature works", "Regression check exists"],
            allowed_scope=["Only the isolated fixture; no deployment or new dependencies"], max_work_units=limit,
            confirmed=True, idempotency_key="create", authority_snapshot=authority_snapshot(tmp_path, "DEMO", config, root))
    return tmp_path, root, config, store, create


def test_changed_project_source_or_stop_prevents_dispatch(setup, tmp_path):
    workspace, root, config, store, create = setup
    goal = create()
    other = tmp_path / "other"
    other.mkdir()
    config.write_text(config.read_text().replace(str(root), str(other)))
    result = tick_goal(workspace, "DEMO", goal["id"])
    assert result["status"] == "blocked" and "project source changed" in result["stop_reason"]
    config.write_text(config.read_text().replace(str(other), str(root)))
    stopped = handle_goal_action(workspace, "DEMO", root, dict(action="stop_goal", goal_id=goal["id"],
        expected_revision=result["revision"], idempotency_key="stop"))["goal"]
    assert stopped["status"] == "stopped"
    assert tick_goal(workspace, "DEMO", goal["id"])["status"] == "stopped"


def test_assessment_requires_literal_quotes():
    goal = {"acceptance_criteria": ["criterion"]}
    result = {"requires_new_scope": False, "checks": [{"criterion": 0, "status": "met", "evidence_ref": "current", "quote": "Not the result"}]}
    with pytest.raises(ValueError, match="citation"):
        validate_assessment(result, goal, {"current": "Actual evidence"})
    result["requires_new_scope"] = True
    with pytest.raises(ValueError, match="beyond"):
        validate_assessment(result, goal, {})


@pytest.mark.parametrize("source", ["batch", "manager"])
def test_changed_effective_model_catalog_notifies_and_keeps_goal(setup, monkeypatch, source):
    from pathlib import Path
    workspace, _, _, _, create = setup
    catalog_text = (Path(__file__).resolve().parents[1] / "models.yaml").read_text()
    if source == "batch":
        # CWD models.yaml wins in the workflow resolver even with no project
        # or Team model change. Manager still uses its repository fallback.
        monkeypatch.chdir(workspace)
        catalog = workspace / "models.yaml"
    else:
        catalog = workspace / "manager-models.yaml"
        monkeypatch.setenv("GOODTEAM_MODEL_CONFIG", str(catalog))
    catalog.write_text(catalog_text)
    goal = create()
    catalog.write_text(catalog_text.replace("codex:gpt-5.6-sol", "codex:changed-model"))
    from goodteam_os.manager_execution import ManagerResourceBusy
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    starts = []
    def starter(**kwargs):
        starts.append(kwargs)
        raise ManagerResourceBusy("Offline fixture leaves original work pending.")
    monkeypatch.setattr("goodteam_os.project_iteration_execution.run_queued_iteration_batch", starter)
    result = tick_goal(workspace, "DEMO", goal["id"])
    assert starts and result["status"] == "active"
    assert result["authority_snapshot"] == goal["authority_snapshot"]
    timeline = TaskAssistantSessionStore(workspace, "DEMO").read()["timeline"]
    assert sum(item["event"] == "project_continuation_environment_changed" for item in timeline) == 1



def test_goal_context_pause_preserves_original_work(setup, monkeypatch):
    from goodteam_os.assistant_runtime import safe_failure
    from goodteam_os.manager_execution import ManagerExecutionError

    workspace, _, _, store, create = setup
    goal = create()
    failure = safe_failure(RuntimeError('{"codexErrorInfo":"contextWindowExceeded"}'))
    def exhausted_context(*args, **kwargs):
        raise ManagerExecutionError(failure["message"], code=failure["code"])
    monkeypatch.setattr("goodteam_os.goal_continuation._tick_task_goal", exhausted_context)
    tick_goal(workspace, "DEMO", goal["id"])
    saved = store.get_goal(goal["id"])
    assert saved["status"] == "blocked" and saved["model_context_paused"]
    assert "larger context window" in saved["stop_reason"]
    assert saved["work_units"] == goal["work_units"]
    assert saved["continuation_stopped"] is False
