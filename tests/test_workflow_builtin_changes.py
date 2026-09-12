"""Builtin managers must preserve the explicit change-confirmation boundary."""

import pytest

from goodteam_os import web
from goodteam_os.projects import write_project_config
from goodteam_os.state import StateStore
from test_workflow_recovery import binding_for, gate_state


@pytest.mark.parametrize("adapter", ["fake", "noop", "human"])
def test_builtin_change_discussion_uses_explicit_request_without_worker(tmp_path, monkeypatch, adapter):
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "RECOVERY", "Recovery",
                                  repo_path=str(root), worker_mode=adapter)
    state = gate_state(tmp_path, "security")
    state.source_ref = str(config)
    StateStore(tmp_path).write_project_current(state)

    def unexpected_worker(**_kwargs):
        pytest.fail("A builtin manager cannot return a structured model interpretation")

    monkeypatch.setattr(web, "run_manager_interpretation", unexpected_worker)
    opened = web.start_request_changes_intent(
        workspace=tmp_path, project_key="RECOVERY", approval="security", **binding_for(state),
    )
    result = web.discuss_request_changes_intent(
        workspace=tmp_path, project_key="RECOVERY", intent_id=opened["intent_commit"]["id"],
        text="Include an explicit empty-state acceptance criterion.",
    )

    assert "ready_to_confirm" == result["intent_commit"]["status"]
    assert "deterministic_explicit_request" == result["intent_commit"]["interpretation"]["source"]
    assert state == StateStore(tmp_path).read_project_current("RECOVERY")
    assert [] == web.build_action_items(tmp_path, "RECOVERY")
