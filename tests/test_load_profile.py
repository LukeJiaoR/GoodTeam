from goodteam_os.state import AgileDevState, StateStore
from goodteam_os.workflow_table import (
    GREENFIELD_PROFILE,
    _GATE_TRANSITIONS,
    _WORKFLOW_NODES,
    gate_transition,
    resolve_profile,
    workflow_execution_plan,
)


def test_resolve_profile_defaults_to_greenfield():
    assert resolve_profile("greenfield") is GREENFIELD_PROFILE
    assert resolve_profile("") is GREENFIELD_PROFILE
    assert resolve_profile("unknown") is GREENFIELD_PROFILE
    assert resolve_profile() is GREENFIELD_PROFILE


def test_greenfield_profile_wraps_module_tables():
    assert dict(GREENFIELD_PROFILE.nodes) == dict(_WORKFLOW_NODES)
    assert dict(GREENFIELD_PROFILE.gate_transitions) == dict(_GATE_TRANSITIONS)
    assert GREENFIELD_PROFILE.gate_transition("rfc") == gate_transition("rfc")
    assert GREENFIELD_PROFILE.node_for("architect") is _WORKFLOW_NODES["architect"]


def test_greenfield_gate_transition_unknown_raises():
    import pytest

    with pytest.raises(ValueError):
        GREENFIELD_PROFILE.gate_transition("does-not-exist")


def test_greenfield_execution_plan_matches_module_function():
    profile_plan = GREENFIELD_PROFILE.execution_plan()
    module_plan = workflow_execution_plan()
    assert profile_plan == module_plan
    assert profile_plan.order == module_plan.order
    assert [level.to_dict() for level in profile_plan.levels] == [
        level.to_dict() for level in module_plan.levels
    ]


def test_state_defaults_load_profile_greenfield():
    state = AgileDevState(run_id="r", project_key="P", idea="i")
    assert state.load_profile == "greenfield"


def test_state_without_load_profile_deserializes(tmp_path):
    store = StateStore(tmp_path)
    path = tmp_path / "state" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"run_id": "r", "project_key": "P", "idea": "i", "approvals": {}}',
        encoding="utf-8",
    )
    loaded = store.read_current()
    assert loaded is not None
    assert loaded.load_profile == "greenfield"
