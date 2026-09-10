import pytest

from goodteam_os.graph import _worker_job_fields, resolve_role_adapter
from goodteam_os.projects import load_project_target, write_project_config
from goodteam_os.state import create_initial_state
from goodteam_os.team_control_plane import ensure_team_state, execute_team_commands, TeamDispatchBlocked


def test_intake_uses_research_identity_and_preserves_capability_gate(tmp_path, monkeypatch):
    # Isolate dispatch mapping from the host's installed/authenticated Codex CLI.
    monkeypatch.setattr(
        "goodteam_os.team_control_plane.worker_backend_doctor",
        lambda mode: {"mode": mode, "ready": True},
    )
    root = tmp_path / "repo"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="codex")
    team = ensure_team_state(tmp_path, "DEMO", config)
    researcher = next(worker for worker in team["workers"] if worker["role"] == "research")
    state = create_initial_state("run", "DEMO", "Inspect existing project")
    state.role_models["research"] = {"primary": "codex:gpt-5.5"}
    fields = _worker_job_fields(load_project_target(config), "project_intake", state)
    assert researcher["id"] == fields["worker_id"]
    assert "codex" == resolve_role_adapter(load_project_target(config), "project_intake")
    execute_team_commands(
        tmp_path,
        "DEMO",
        [{"kind": "disable_role", "role": "research"}],
        actor="user",
        surface="team_ui",
        config_path=config,
    )
    with pytest.raises(TeamDispatchBlocked):
        _worker_job_fields(load_project_target(config), "project_intake", state)
