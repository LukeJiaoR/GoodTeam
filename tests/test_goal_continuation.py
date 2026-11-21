"""Model-free continuation contracts using real run facts and isolated workflow.

The synthetic assessor is deliberately separate from real model validation.
"""

import pytest

from goodteam_os.goal_assessment import run_evidence, validate_assessment
from goodteam_os.goal_continuation import authority_snapshot, handle_goal_action, tick_goal
from goodteam_os.goals import GoalStore
from goodteam_os.state import create_initial_state
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


def test_changed_runtime_scope_or_stop_prevents_dispatch(setup):
    workspace, root, config, store, create = setup
    goal = create()
    config.write_text(config.read_text() + "delivery_mode: custom\n")
    result = tick_goal(workspace, "DEMO", goal["id"])
    assert result["status"] == "blocked"
    stopped = handle_goal_action(workspace, "DEMO", root, dict(action="stop_goal", goal_id=goal["id"],
        expected_revision=result["revision"], idempotency_key="stop"))["goal"]
    assert stopped["status"] == "stopped"
    assert tick_goal(workspace, "DEMO", goal["id"])["status"] == "stopped"


def test_current_run_refs_and_literal_quotes_required(tmp_path):
    state = create_initial_state("bound", "DEMO", "task")
    state.current_node = state.next_node = "complete"
    foreign = tmp_path / "artifacts/runs/other/result.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("Forged evidence")
    state.artifacts = {"qa": str(foreign)}
    with pytest.raises(ValueError, match="no readable"):
        run_evidence(tmp_path, state)
    goal = {"acceptance_criteria": ["criterion"]}
    result = {"requires_new_scope": False, "checks": [{"criterion": 0, "status": "met", "evidence_ref": "current", "quote": "Not the result"}]}
    with pytest.raises(ValueError, match="citation"):
        validate_assessment(result, goal, {"current": "Actual evidence"})
    result["requires_new_scope"] = True
    with pytest.raises(ValueError, match="beyond"):
        validate_assessment(result, goal, {})


@pytest.mark.parametrize("source", ["workflow", "manager"])
def test_changed_effective_model_catalog_cannot_expand_confirmed_routing(setup, monkeypatch, source):
    from pathlib import Path
    workspace, _, _, _, create = setup
    catalog_text = (Path(__file__).resolve().parents[1] / "models.yaml").read_text()
    if source == "workflow":
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
    result = tick_goal(workspace, "DEMO", goal["id"])
    assert result["status"] == "blocked"
    assert "configuration changed" in result["stop_reason"]
