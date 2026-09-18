"""Runtime effort persistence is independent of installed provider CLIs.

These are the five contract cases extracted from test_team_control_plane.
Patch the doctor where dispatch resolves it, while retaining the unavailable
runtime gate. No real model, CLI, or active project workspace is used.
"""

import pytest

from goodteam_os import team_control_plane, workers
from goodteam_os.graph import worker_type_settings_for_role
from goodteam_os.manager_execution import _manager_settings
from goodteam_os.projects import load_project_target, write_project_config
from goodteam_os.team_control_plane import (
    TEAM_STATE_TARGET_KEY,
    TeamDispatchBlocked,
    build_team_execution_snapshot,
    execute_team_commands,
    resolve_worker_for_role,
)
from goodteam_os.web import normalize_worker_type_settings


@pytest.mark.parametrize("depth", ["none", "minimal", "xhigh", "max", "ultra"])
def test_model_effort_survives_team_persistence_snapshot_and_resolution(tmp_path, monkeypatch, depth):
    def ready_doctor(_provider):
        return {"ready": True}

    # team_control_plane imports this callable directly. Patching only its
    # defining workers module leaves dispatch dependent on the host's CLI.
    monkeypatch.setattr(workers, "worker_backend_doctor", ready_doctor)
    monkeypatch.setattr(team_control_plane, "worker_backend_doctor", ready_doctor)
    workspace = tmp_path / "ws"
    (workspace / "projects").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    config = write_project_config(
        workspace / "projects", "demo", "Demo",
        repo_path=str(repo), worker_mode="codex",
    )
    execute_team_commands(
        workspace,
        "demo",
        [
            {
                "kind": "change_worker_runtime",
                "worker_id": "implementation-a",
                "model": "model-deep",
                "reasoning_depth": depth,
            },
            {"kind": "change_manager_runtime", "reasoning_depth": depth},
        ],
        actor="user",
        surface="team_ui",
        config_path=config,
    )
    target = load_project_target(config)
    snapshot = build_team_execution_snapshot(target[TEAM_STATE_TARGET_KEY])
    assert snapshot["workers"]["implementation-a"]["reasoning_depth"] == depth
    assert snapshot["manager"]["runtime"]["reasoning_depth"] == depth
    assert resolve_worker_for_role(target, "implementation")["reasoning_depth"] == depth
    assert _manager_settings(target)["reasoning_depth"] == depth
    assert normalize_worker_type_settings(
        {"implementation": {"reasoning_depth": depth}}
    )["implementation"]["reasoning_depth"] == depth
    assert worker_type_settings_for_role(
        {"worker_type_settings": {"implementation": {"reasoning_depth": depth}}},
        "implementation",
    )["reasoning_depth"] == depth

    # Accepted persisted effort values must not bypass runtime eligibility.
    monkeypatch.setattr(
        team_control_plane, "worker_backend_doctor", lambda _provider: {"ready": False},
    )
    with pytest.raises(TeamDispatchBlocked, match="No active, runtime-eligible Worker"):
        resolve_worker_for_role(target, "implementation")
    assert build_team_execution_snapshot(target[TEAM_STATE_TARGET_KEY])["workers"][
        "implementation-a"
    ]["reasoning_depth"] == depth
