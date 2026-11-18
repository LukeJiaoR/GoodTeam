"""[INPUT]: 团队存储、Manager 运行配置和独立项目目录。
[OUTPUT]: 验证配置迁移和本机 Codex 授权。
[POS]: 通过团队配置验证 Manager 启动边界。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from copy import deepcopy

import pytest

from goodteam_os.graph import worker_type_settings_for_role
from goodteam_os.manager_execution import _manager_settings
from goodteam_os.team_control_plane import (
    TeamControlPlaneError, TeamControlPlaneStore, build_team_capability_view,
    build_team_state_from_legacy_config, resolve_worker_from_state,
)
from goodteam_os.web import normalize_worker_type_settings


def test_real_manager_runtime_migrates_without_replacing_worker_backends(tmp_path, monkeypatch):
    target = {"worker_backend": {"mode": "pi"}, "workers": {"orchestrator": "pi", "implementation": "claude"},
              "role_model_overrides": {"orchestrator": "pi:chosen-model"},
              "worker_type_settings": {"orchestrator": {"reasoning_depth": "high", "additional_prompt": "Keep context.",
                                                        "desired_instances": 7}}}
    store = TeamControlPlaneStore(tmp_path, "DEMO")
    store.load_or_migrate(legacy_target=target)
    state = store.read()
    runtime = state["manager"]["runtime"]
    assert runtime == dict(provider="codex", model="chosen-model", reasoning_depth="high", additional_prompt="Keep context.", trust_local_codex=False)
    assert next(item for item in state["workers"] if item["role"] == "implementation")["runtime"]["provider"] == "claude"
    manager = resolve_worker_from_state(state, "orchestrator")
    assert manager["adapter"] == "codex" and manager["runtime_kind"] == "app_server"
    assert "desired_instances" not in manager
    assert normalize_worker_type_settings(target["worker_type_settings"])["orchestrator"] == {
        "reasoning_depth": "high", "additional_prompt": "Keep context."}
    assert normalize_worker_type_settings({"orchestrator": {"desired_instances": "obsolete"}}) == {}
    assert normalize_worker_type_settings({"research": {"desired_instances": 2}})["research"]["desired_instances"] == 2
    with pytest.raises(ValueError, match="desired_instances"):
        normalize_worker_type_settings({"research": {"desired_instances": 0}})
    for runtime_target in (target, {**target, "team_control_plane": state}):
        assert "desired_instances" not in worker_type_settings_for_role(runtime_target, "orchestrator")
        assert "desired_instances" not in _manager_settings(runtime_target)
    assert build_team_capability_view(state)["manager"]["runtime_kind"] == "app_server"
    assert build_team_state_from_legacy_config("FIXTURE", {"worker_backend": {"mode": "fake"}})["manager"]["runtime"]["provider"] == "fake"
    historical = deepcopy(state)
    historical["manager"]["runtime"]["provider"] = "pi"
    assert resolve_worker_from_state(historical, "orchestrator")["adapter"] == "codex"
    assert historical["manager"]["runtime"]["provider"] == "pi"

    with pytest.raises(TeamControlPlaneError, match="Codex app-server"):
        store.execute([{"kind": "change_manager_runtime", "provider": "pi"}], actor="user", surface="team_ui")
    monkeypatch.setattr("goodteam_os.model_discovery.validate_runtime_choice", lambda *args, **kwargs: None)
    changed = store.execute([{"kind": "change_manager_runtime", "provider": "codex", "model": "another-model",
                              "reasoning_depth": "xhigh", "additional_prompt": "Retain context and coordinate."}],
                            actor="user", surface="team_ui")
    assert changed["changed"] and changed["team"]["manager"]["runtime"]["model"] == "another-model"
    assert changed["team"]["manager"]["runtime"]["reasoning_depth"] == "xhigh"



def test_only_team_settings_can_authorize_local_manager(tmp_path, monkeypatch):
    from goodteam_os.manager_app_server import ManagerAppServer
    from goodteam_os.manager_execution import ManagerExecutionError

    root = tmp_path / "repo"
    root.mkdir()
    config = tmp_path / "projects" / "demo.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: codex\n")
    store = TeamControlPlaneStore(tmp_path, "DEMO")
    store.load_or_migrate(legacy_target={"worker_backend": {"mode": "codex"}})
    runtime = ManagerAppServer(config, "DEMO")
    command = {"kind": "change_manager_runtime", "trust_local_codex": True}
    with pytest.raises(ManagerExecutionError, match="Enable Use local Codex"):
        runtime._runtime_config()
    with pytest.raises(TeamControlPlaneError, match="requires a boolean choice"):
        store.execute([command], actor="user", surface="manager_conversation")
    saved = store.execute([command], actor="user", surface="team_ui")
    assert saved["team"]["manager"]["runtime"]["trust_local_codex"] is True
    monkeypatch.setenv("GOODTEAM_CODEX_PATH", str(tmp_path / "codex"))
    argv, _env, public, _fingerprint = runtime._runtime_config()
    assert argv == [str(tmp_path / "codex"), "app-server"]
    assert public["provider_authority"] == "native_managed"
