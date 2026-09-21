import json
from pathlib import Path

import pytest

from goodteam_os import service
from goodteam_os.projects import write_project_config
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.team_control_plane import (
    TeamControlPlaneError,
    TeamControlPlaneStore,
    ensure_team_state,
    execute_team_commands,
    load_team_view,
)


def make_project(tmp_path: Path, key: str = "DEMO") -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    projects = workspace / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / f"{key.lower()}-repo"
    repo.mkdir(exist_ok=True)
    config = write_project_config(
        projects,
        key,
        f"{key} project",
        repo_path=str(repo),
        worker_mode="fake",
    )
    assert ensure_team_state(workspace, key, config) is not None
    return workspace, config


def install_skill(tmp_path: Path, monkeypatch, *, name: str = "custom-review") -> dict:
    state_root = tmp_path / "operator-state"
    monkeypatch.setattr(service, "service_state_dir", lambda: state_root)
    source = tmp_path / name
    source.mkdir()
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Custom review method.\n---\n"
        "Check persistence boundaries and cite source evidence.\n",
        encoding="utf-8",
    )
    library = SkillLibraryService(state_root)
    preview = library.preview_local(str(source), command_key=f"preview-{name}")
    installed = library.install(preview["proposal_id"])
    return {
        "skill_id": installed["installed"]["skill_id"],
        "version_id": installed["installed"]["version_id"],
        "package_digest": preview["package_digest"],
        "activation": "required",
        "enabled": True,
    }


def set_binding(workspace: Path, config: Path, key: str, item: dict, **kwargs):
    state = TeamControlPlaneStore(workspace, key).read()
    binding = state["skill_bindings"]["role:code_review"]
    return execute_team_commands(
        workspace,
        key,
        [{
            "kind": "set_role_skills",
            "subject": "role:code_review",
            "mode": "custom",
            "bindings": [item],
            "expected_binding_revision": binding["binding_revision"],
        }],
        actor="user",
        surface="team_ui",
        command_key=kwargs.pop("command_key", "bind-review"),
        expected_revision=kwargs.pop("expected_revision", state["revision"]),
        config_path=config,
        **kwargs,
    )


def test_existing_team_projects_default_binding_without_claiming_execution(tmp_path):
    workspace, _config = make_project(tmp_path)
    view = load_team_view(workspace, "DEMO")
    review = next(item for item in view["roles"] if item["role"] == "code_review")
    assert review["skill_binding"] == {
        "subject": "role:code_review",
        "role_contract_ref": "role:code_review@v1",
        "binding_revision": 0,
        "mode": "default",
        "status": "default",
        "items": [],
        "execution_status": "not_connected",
        "updated_at": "",
    }
    assert view["manager"]["skill_binding"]["subject"] == "manager:orchestrator"
    assert view["manager"]["skill_binding"]["execution_status"] == "not_connected"


def test_user_skill_can_fully_replace_role_method_without_changing_role_or_workers(tmp_path, monkeypatch):
    workspace, config = make_project(tmp_path)
    item = install_skill(tmp_path, monkeypatch)
    before = TeamControlPlaneStore(workspace, "DEMO").read()
    before_workers = json.dumps(before["workers"], sort_keys=True)
    before_role_state = before["roles"]["code_review"]["state"]

    outcome = set_binding(workspace, config, "DEMO", item)

    review = next(entry for entry in outcome["team"]["roles"] if entry["role"] == "code_review")
    assert review["state"] == before_role_state
    assert review["skill_binding"]["status"] == "configured"
    assert review["skill_binding"]["mode"] == "custom"
    assert review["skill_binding"]["binding_revision"] == 1
    assert review["skill_binding"]["items"] == [item]
    assert review["skill_binding"]["execution_status"] == "not_connected"
    after = TeamControlPlaneStore(workspace, "DEMO").read()
    assert json.dumps(after["workers"], sort_keys=True) == before_workers
    event = next(item for item in outcome["events"] if item["event"] == "role_skills.changed")
    assert event["before"]["status"] == "default"
    assert event["after"]["status"] == "configured"


def test_empty_custom_is_unconfigured_and_restore_default_is_explicit(tmp_path):
    workspace, config = make_project(tmp_path)
    store = TeamControlPlaneStore(workspace, "DEMO")
    initial = store.read()
    role_state = initial["roles"]["code_review"]["state"]
    workers = json.dumps(initial["workers"], sort_keys=True)

    empty = execute_team_commands(
        workspace,
        "DEMO",
        [{
            "kind": "set_role_skills",
            "subject": "role:code_review",
            "mode": "custom",
            "bindings": [],
            "expected_binding_revision": 0,
        }],
        actor="user",
        surface="team_ui",
        command_key="empty-custom",
        expected_revision=initial["revision"],
        config_path=config,
    )
    review = next(item for item in empty["team"]["roles"] if item["role"] == "code_review")
    assert review["skill_binding"]["status"] == "unconfigured"
    assert review["skill_binding"]["mode"] == "custom"
    assert review["state"] == role_state
    assert json.dumps(store.read()["workers"], sort_keys=True) == workers

    restored = execute_team_commands(
        workspace,
        "DEMO",
        [{
            "kind": "set_role_skills",
            "subject": "role:code_review",
            "mode": "default",
            "bindings": [],
            "expected_binding_revision": 1,
        }],
        actor="user",
        surface="team_ui",
        command_key="restore-default",
        expected_revision=empty["team_revision"],
        config_path=config,
    )
    review = next(item for item in restored["team"]["roles"] if item["role"] == "code_review")
    assert review["skill_binding"]["status"] == "default"
    assert review["skill_binding"]["binding_revision"] == 2


def test_binding_rejects_stale_revision_and_uninstalled_version_without_mutation(tmp_path, monkeypatch):
    workspace, config = make_project(tmp_path)
    item = install_skill(tmp_path, monkeypatch)
    first = set_binding(workspace, config, "DEMO", item)
    store = TeamControlPlaneStore(workspace, "DEMO")
    before = store.read()
    event_count = len(store.read_events())

    with pytest.raises(TeamControlPlaneError, match="Skill binding changed"):
        execute_team_commands(
            workspace,
            "DEMO",
            [{
                "kind": "set_role_skills",
                "subject": "role:code_review",
                "mode": "custom",
                "bindings": [item],
                "expected_binding_revision": 0,
            }],
            actor="user",
            surface="team_ui",
            command_key="stale-binding",
            expected_revision=first["team_revision"],
            config_path=config,
        )

    missing = {**item, "version_id": "version-" + "0" * 64}
    with pytest.raises(TeamControlPlaneError):
        execute_team_commands(
            workspace,
            "DEMO",
            [{
                "kind": "set_role_skills",
                "subject": "role:qa",
                "mode": "custom",
                "bindings": [missing],
                "expected_binding_revision": 0,
            }],
            actor="user",
            surface="team_ui",
            command_key="missing-version",
            expected_revision=first["team_revision"],
            config_path=config,
        )
    assert store.read() == before
    assert len(store.read_events()) == event_count


def test_command_key_same_payload_replays_but_different_payload_conflicts(tmp_path):
    workspace, config = make_project(tmp_path)
    state = TeamControlPlaneStore(workspace, "DEMO").read()
    command = {
        "kind": "set_role_skills",
        "subject": "role:qa",
        "mode": "custom",
        "bindings": [],
        "expected_binding_revision": 0,
    }
    first = execute_team_commands(
        workspace, "DEMO", [command],
        actor="user", surface="team_ui", command_key="same-key",
        expected_revision=state["revision"], config_path=config,
    )
    replay = execute_team_commands(
        workspace, "DEMO", [command],
        actor="user", surface="team_ui", command_key="same-key",
        expected_revision=state["revision"], config_path=config,
    )
    assert replay["replayed"] is True
    assert replay["decision_id"] == first["decision_id"]

    changed = {**command, "subject": "role:release"}
    with pytest.raises(TeamControlPlaneError, match="different inputs"):
        execute_team_commands(
            workspace, "DEMO", [changed],
            actor="user", surface="team_ui", command_key="same-key",
            config_path=config,
        )


def test_binding_is_project_scoped_while_installed_skill_is_reusable(tmp_path, monkeypatch):
    workspace, config_a = make_project(tmp_path, "ALPHA")
    projects = workspace / "projects"
    repo_b = tmp_path / "beta-repo"
    repo_b.mkdir()
    config_b = write_project_config(projects, "BETA", "Beta", repo_path=str(repo_b), worker_mode="fake")
    assert ensure_team_state(workspace, "BETA", config_b) is not None
    item = install_skill(tmp_path, monkeypatch, name="shared-review")

    set_binding(workspace, config_a, "ALPHA", item, command_key="bind-alpha")
    alpha = load_team_view(workspace, "ALPHA")
    beta = load_team_view(workspace, "BETA")
    a = next(x for x in alpha["roles"] if x["role"] == "code_review")
    b = next(x for x in beta["roles"] if x["role"] == "code_review")
    assert a["skill_binding"]["status"] == "configured"
    assert b["skill_binding"]["status"] == "default"


def test_interrupted_audit_append_recovers_once_without_reapplying_binding(tmp_path, monkeypatch):
    workspace, config = make_project(tmp_path)
    item = install_skill(tmp_path, monkeypatch)
    store = TeamControlPlaneStore(workspace, "DEMO")
    state = store.read()
    original_append = store._append_events
    calls = {"count": 0}

    def fail_first(events):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated ledger interruption")
        return original_append(events)

    monkeypatch.setattr(store, "_append_events", fail_first)
    with pytest.raises(OSError, match="ledger interruption"):
        store.execute(
            [{
                "kind": "set_role_skills",
                "subject": "role:code_review",
                "mode": "custom",
                "bindings": [item],
                "expected_binding_revision": 0,
            }],
            actor="user",
            surface="team_ui",
            command_key="crash-binding",
            expected_revision=state["revision"],
            config_path=config,
        )

    committed = store.read()
    assert committed["skill_bindings"]["role:code_review"]["binding_revision"] == 1
    assert len(committed["pending_events"]) == 1
    monkeypatch.setattr(store, "_append_events", original_append)

    recovered = store.reconcile_pending_audit()
    assert recovered["pending_events"] == []
    matching = [event for event in store.read_events() if event["event"] == "role_skills.changed"]
    assert len(matching) == 1

    replay = store.execute(
        [{
            "kind": "set_role_skills",
            "subject": "role:code_review",
            "mode": "custom",
            "bindings": [item],
            "expected_binding_revision": 0,
        }],
        actor="user",
        surface="team_ui",
        command_key="crash-binding",
        expected_revision=state["revision"],
        config_path=config,
    )
    assert replay["replayed"] is True
    assert len([event for event in store.read_events() if event["event"] == "role_skills.changed"]) == 1


def test_http_rejects_stale_team_revision_before_binding_mutation(tmp_path):
    from goodteam_os import web

    workspace, _config = make_project(tmp_path)
    state = TeamControlPlaneStore(workspace, "DEMO").read()
    original_revision = state["revision"]

    status, first = web.handle_project_api_post(
        workspace,
        "/api/projects/DEMO/team/commands",
        {
            "commands": [{"kind": "disable_role", "role": "research"}],
            "reason": "create a concurrent change",
            "command_key": "other-tab",
            "expected_team_revision": original_revision,
        },
    )
    assert status == 200
    assert first["team_revision"] == original_revision + 1

    with pytest.raises(web.ProjectApiError) as rejected:
        web.handle_project_api_post(
            workspace,
            "/api/projects/DEMO/team/commands",
            {
                "commands": [{
                    "kind": "set_role_skills",
                    "subject": "role:qa",
                    "mode": "custom",
                    "bindings": [],
                    "expected_binding_revision": 0,
                }],
                "reason": "stale skills tab",
                "command_key": "stale-tab",
                "expected_team_revision": original_revision,
            },
        )
    assert rejected.value.status == 409
    assert rejected.value.code == "team_command_rejected"
    current = TeamControlPlaneStore(workspace, "DEMO").read()
    assert current["skill_bindings"]["role:qa"]["mode"] == "default"
    assert current["skill_bindings"]["role:qa"]["binding_revision"] == 0
