"""Contract tests for the Team Control Plane (#402).

The matrix below treats the core invariants as contract: Role Definition !=
TeamRole != Worker, dispatch gates fail closed in the backend, capability
states derive from Worker records, every mutation shares causal decision
identity, deletion is a tombstone, and historical execution snapshots never
change after later Team mutations.
"""

import json
from pathlib import Path

import pytest

from goodteam_os.graph import implementation_worker_job_specs, worker_type_settings_for_role
from goodteam_os.manager_execution import _manager_settings
from goodteam_os.projects import load_project_target, write_project_config
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant import (
    TaskAssistantService,
    detect_team_commands,
    detect_task_scoped_team_preference,
)
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_control_plane import (
    MANAGER_ROLE,
    MIGRATION_BASELINE_ROLES,
    ROLE_CATALOG,
    TEAM_STATE_TARGET_KEY,
    TeamControlPlaneError,
    TeamControlPlaneStore,
    TeamDispatchBlocked,
    build_team_execution_snapshot,
    ensure_team_state,
    execute_team_commands,
    load_team_view,
    resolve_worker_for_role,
)


def make_project(tmp_path: Path, *, worker_mode: str = "fake", extra_yaml: str = "") -> Path:
    workspace = tmp_path / "ws"
    (workspace / "projects").mkdir(parents=True)
    (tmp_path / "repo").mkdir(exist_ok=True)
    config = write_project_config(
        workspace / "projects",
        "demo",
        "Demo",
        repo_path=str(tmp_path / "repo"),
        worker_mode=worker_mode,
    )
    if extra_yaml:
        config.write_text(config.read_text(encoding="utf-8") + extra_yaml, encoding="utf-8")
    return config


def migrated_target(config: Path) -> dict:
    """Trigger the explicit team touchpoint once, then load with attached state."""
    ensure_team_state(config.parent.parent, "demo", config)
    return load_project_target(config)


def execute(config_path: Path, commands, **kwargs):
    workspace = config_path.parent.parent
    return execute_team_commands(
        workspace,
        "demo",
        commands,
        actor=kwargs.pop("actor", "user"),
        surface=kwargs.pop("surface", "team_ui"),
        config_path=config_path,
        **kwargs,
    )


def worker_ids(config_path: Path, role: str, *, lifecycle: str | None = None) -> list[str]:
    workspace = config_path.parent.parent
    state = TeamControlPlaneStore(workspace, "demo").read()
    return [
        str(worker["id"])
        for worker in state["workers"]
        if worker["role"] == role and (lifecycle is None or worker["lifecycle"] == lifecycle)
    ]


# ---------------------------------------------------------------- 1. domain


def test_migration_covers_catalog_with_baseline_roles_enabled(tmp_path):
    config = make_project(tmp_path)
    target = migrated_target(config)
    state = target[TEAM_STATE_TARGET_KEY]

    # Role catalog is global and immutable; TeamRole state is per project;
    # Workers are individual records. Migration enables the frozen
    # legacy-dispatchable baseline (MIGRATION_BASELINE_ROLES) so migrated
    # teams keep historical dispatch behavior.
    assert {item["key"] for item in ROLE_CATALOG} >= {"research", "implementation", "qa"}
    assert set(state["roles"]) == {item["key"] for item in ROLE_CATALOG}
    for baseline_role in MIGRATION_BASELINE_ROLES:
        assert state["roles"][baseline_role]["state"] == "enabled"
    assert MANAGER_ROLE not in state["roles"]
    assert all(worker["lifecycle"] == "active" for worker in state["workers"])


def test_future_catalog_roles_are_not_auto_enabled_by_migration(tmp_path, monkeypatch):
    """Migration compatibility boundary: catalog growth must not expand teams.

    MIGRATION_BASELINE_ROLES is an explicit frozen literal, deliberately NOT
    derived from ROLE_CATALOG, so monkeypatching the catalog here faithfully
    simulates a future release whose source grew the catalog while the
    baseline stayed unchanged. A Role Definition added to ROLE_CATALOG in a
    later release must migrate as disabled and unstaffed — otherwise every
    existing team would silently gain the new capability, and enabling it
    could never be a deliberate user decision.
    """
    from goodteam_os import team_control_plane

    # Tripwires for un-reviewed baseline edits: the hypothetical future role
    # must not already be baseline-enabled, and the hand-frozen baseline must
    # stay a maintained subset of the live catalog (catches typos and
    # orphaned keys after catalog renames/removals).
    assert "security_audit" not in MIGRATION_BASELINE_ROLES
    assert MIGRATION_BASELINE_ROLES <= {item["key"] for item in ROLE_CATALOG}

    future_catalog = (
        *ROLE_CATALOG,
        {"key": "security_audit", "label": "Security audit", "worker_prefix": "Auditor"},
    )
    monkeypatch.setattr(team_control_plane, "ROLE_CATALOG", future_catalog)

    config = make_project(tmp_path)
    state = migrated_target(config)[TEAM_STATE_TARGET_KEY]

    assert set(state["roles"]) == {item["key"] for item in future_catalog}
    assert state["roles"]["security_audit"]["state"] == "disabled"
    # The post-baseline role gets no workers: enabling it later is an explicit
    # staffing decision, not a migration side effect.
    assert all(worker["role"] != "security_audit" for worker in state["workers"])
    assert state["roles"]["implementation"]["state"] == "enabled"


def test_load_project_target_without_standard_layout_keeps_legacy_semantics(tmp_path):
    config = tmp_path / "scratch.local.yaml"
    config.write_text("name: Scratch\nkey: SCRATCH\nrepos:\n  app: /tmp\n", encoding="utf-8")
    target = load_project_target(config)
    assert TEAM_STATE_TARGET_KEY not in target


# -------------------------------------------------------------- 2. migration


def test_desired_instances_migration_materializes_distinct_stable_workers(tmp_path):
    """Legacy compatibility boundary: `desired_instances` is migration input.

    Only the legacy → control-plane migration consumes role-level
    `desired_instances`; after migration, worker count is derived from
    first-class Worker records, so this must never be read as the canonical
    Worker model contract.
    """
    config = make_project(
        tmp_path,
        extra_yaml=(
            "role_model_overrides:\n"
            "  implementation: claude:claude-opus-4-5\n"
            "worker_type_settings:\n"
            "  implementation:\n"
            "    reasoning_depth: high\n"
            "    additional_prompt: Keep APIs stable.\n"
            "    desired_instances: 3\n"
        ),
    )
    target = migrated_target(config)
    state = target[TEAM_STATE_TARGET_KEY]

    implementation = [w for w in state["workers"] if w["role"] == "implementation"]
    assert [w["id"] for w in implementation] == ["implementation-a", "implementation-b", "implementation-c"]
    for worker in implementation:
        assert worker["lifecycle"] == "active"
        assert worker["runtime"]["provider"] == "fake"
        assert worker["runtime"]["model"] == "claude-opus-4-5"
        assert worker["runtime"]["reasoning_depth"] == "high"
        assert worker["runtime"]["additional_prompt"] == "Keep APIs stable."
    assert worker_ids(config, "qa") == ["qa-a"]

    # Resolution uses the Worker runtime, not the role-level config.
    resolution = resolve_worker_for_role(target, "implementation")
    assert resolution["worker_id"] == "implementation-a"
    assert resolution["model"] == "claude-opus-4-5"
    assert resolution["reasoning_depth"] == "high"


def test_migration_is_idempotent_without_duplicate_workers_or_events(tmp_path):
    config = make_project(tmp_path, extra_yaml="worker_type_settings:\n  qa:\n    desired_instances: 2\n")
    workspace = config.parent.parent

    first = migrated_target(config)
    store = TeamControlPlaneStore(workspace, "demo")
    first_events = store.read_events()
    assert first_events
    # Causal identity: every migration event is traceable to its decision.
    assert all(event["decision_id"] for event in first_events)
    assert all(event["actor"] == "migration" for event in first_events)
    qa_workers = [w for w in first[TEAM_STATE_TARGET_KEY]["workers"] if w["role"] == "qa"]
    assert [w["id"] for w in qa_workers] == ["qa-a", "qa-b"]

    # Rerun: no duplicate workers, no new events, no revision drift. The
    # number of decisions a migration uses is an implementation choice; the
    # contract is that replaying it causes no additional effective mutation.
    second = migrated_target(config)
    assert [w["id"] for w in second[TEAM_STATE_TARGET_KEY]["workers"] if w["role"] == "qa"] == [
        "qa-a",
        "qa-b",
    ]
    assert store.read_events() == first_events
    assert second[TEAM_STATE_TARGET_KEY]["revision"] == first[TEAM_STATE_TARGET_KEY]["revision"]


def test_migration_preserves_dispatch_behavior(tmp_path):
    config = make_project(tmp_path)
    target = migrated_target(config)
    settings = worker_type_settings_for_role(target, "implementation")
    assert settings["reasoning_depth"] == "default"
    assert settings["desired_instances"] == 1
    assert resolve_worker_for_role(target, "implementation")["adapter"] == "fake"


# ------------------------------------------------------- 3. dispatch gates


def test_disabled_team_role_can_never_dispatch(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "implementation"}])
    target = migrated_target(config)
    with pytest.raises(TeamDispatchBlocked) as excinfo:
        resolve_worker_for_role(target, "implementation")
    assert excinfo.value.code == "team_role_disabled"

    # Even a direct job spec build fails closed.
    state = create_initial_state("run-gate", "demo", "idea")
    with pytest.raises(TeamDispatchBlocked):
        implementation_worker_job_specs(state, target)


def test_inactive_released_and_deleted_workers_can_never_dispatch(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent

    execute(config, [{"kind": "inactivate_worker", "worker_id": "qa-a"}])
    with pytest.raises(TeamDispatchBlocked) as inactive_error:
        resolve_worker_for_role(migrated_target(config), "qa")
    assert inactive_error.value.code == "team_worker_unavailable"

    execute(config, [{"kind": "activate_worker", "worker_id": "qa-a"}])
    execute(config, [{"kind": "release_worker", "worker_id": "qa-a"}])
    with pytest.raises(TeamDispatchBlocked):
        resolve_worker_for_role(migrated_target(config), "qa")

    execute(config, [{"kind": "restore_worker", "worker_id": "qa-a"}])
    execute(config, [{"kind": "delete_worker", "worker_id": "qa-a"}])
    with pytest.raises(TeamDispatchBlocked):
        resolve_worker_for_role(migrated_target(config), "qa")
    assert TeamControlPlaneStore(workspace, "demo").read() is not None


def test_dispatch_falls_back_to_other_active_worker_of_same_role(tmp_path):
    config = make_project(tmp_path, extra_yaml="worker_type_settings:\n  qa:\n    desired_instances: 2\n")
    execute(config, [{"kind": "inactivate_worker", "worker_id": "qa-a"}])
    resolution = resolve_worker_for_role(migrated_target(config), "qa")
    assert resolution["worker_id"] == "qa-b"


def test_worker_runtime_eligibility_gates_dispatch(tmp_path, monkeypatch):
    from goodteam_os import team_control_plane

    config = make_project(tmp_path)
    monkeypatch.setattr(
        team_control_plane,
        "worker_backend_doctor",
        lambda mode: {"mode": mode, "ready": False},
    )
    with pytest.raises(TeamDispatchBlocked) as excinfo:
        resolve_worker_for_role(migrated_target(config), "qa")
    assert excinfo.value.code == "team_worker_unavailable"
    assert worker_ids(config, "qa", lifecycle="active") == ["qa-a"]


def test_project_without_team_state_keeps_legacy_resolution(tmp_path):
    config = tmp_path / "legacy.local.yaml"
    config.write_text(
        "key: LEGACY\nworker_backend:\n  mode: fake\n",
        encoding="utf-8",
    )
    target = load_project_target(config)
    assert TEAM_STATE_TARGET_KEY not in target
    assert resolve_worker_for_role(target, "qa") is None


# -------------------------------------------------- 4. capability read model


def test_capability_states_disabled_unstaffed_ready_degraded(tmp_path):
    config = make_project(tmp_path, extra_yaml="worker_type_settings:\n  research:\n    desired_instances: 2\n")
    workspace = config.parent.parent
    ensure_team_state(workspace, "demo", config)

    def capability(role: str) -> str:
        view = load_team_view(workspace, "demo")
        return next(entry["capability"] for entry in view["roles"] if entry["role"] == role)

    assert capability("research") == "ready"
    execute(config, [{"kind": "inactivate_worker", "worker_id": "research-a"}, {"kind": "inactivate_worker", "worker_id": "research-b"}])
    assert capability("research") == "degraded"
    execute(config, [{"kind": "release_worker", "worker_id": "research-a"}, {"kind": "release_worker", "worker_id": "research-b"}])
    assert capability("research") == "unstaffed"
    execute(config, [{"kind": "disable_role", "role": "research"}])
    assert capability("research") == "disabled"


def test_disabled_role_projection_keeps_workers_visible_but_not_dispatchable(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "research"}])
    view = load_team_view(config.parent.parent, "demo")
    entry = next(item for item in view["roles"] if item["role"] == "research")
    assert entry["state"] == "disabled"
    assert entry["capability"] == "disabled"
    assert [worker["lifecycle"] for worker in entry["workers"]] == ["inactive"]


# ---------------------------------------------- 5. decisions, causality, ledger


def test_disable_role_cascades_inactivation_under_one_decision(tmp_path):
    config = make_project(tmp_path, extra_yaml="worker_type_settings:\n  research:\n    desired_instances: 2\n")
    outcome = execute(config, [{"kind": "disable_role", "role": "research"}], reason="把 Research 关掉")

    events = outcome["events"]
    assert [event["event"] for event in events] == [
        "team_role.disabled",
        "worker.inactivated",
        "worker.inactivated",
    ]
    assert len({event["decision_id"] for event in events}) == 1
    assert events[0]["cause"] == "user_command"
    assert all(event["cause"] == "cascade" for event in events[1:])
    assert {event["actor"] for event in events} == {"user", "system"}
    assert events[0]["reason"] == "把 Research 关掉"

    assert worker_ids(config, "research", lifecycle="active") == []
    assert len(worker_ids(config, "research", lifecycle="inactive")) == 2


def test_re_enable_role_does_not_recreate_workers(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "research"}])
    before = worker_ids(config, "research")
    execute(config, [{"kind": "enable_role", "role": "research"}])
    # Identity invariant: re-enabling never duplicates or re-creates the
    # Workers that existed before the disable, and history stays intact.
    assert worker_ids(config, "research") == before
    assert len(worker_ids(config, "research", lifecycle="inactive")) == 1
    # Current product policy (not an identity invariant): enable alone does
    # not auto-reactivate; the capability stays unstaffed until a Worker is
    # explicitly activated, so re-enable can never silently resume dispatch.
    assert worker_ids(config, "research", lifecycle="active") == []


def test_compound_add_researcher_shares_decision_identity(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "research"}])
    outcome = execute(
        config,
        [{"kind": "initialize_worker", "role": "research", "ensure_role_enabled": True}],
        reason="加个 Researcher",
    )
    events = outcome["events"]
    assert [event["event"] for event in events] == ["team_role.enabled", "worker.initialized"]
    assert len({event["decision_id"] for event in events}) == 1
    assert worker_ids(config, "research", lifecycle="active") == ["research-b"]


def test_command_replay_is_idempotent(tmp_path):
    config = make_project(tmp_path)
    first = execute(config, [{"kind": "initialize_worker", "role": "qa", "ensure_role_enabled": True}], command_key="team:msg-1")
    second = execute(config, [{"kind": "initialize_worker", "role": "qa", "ensure_role_enabled": True}], command_key="team:msg-1")
    assert first["decision_id"] == second["decision_id"]
    assert second["replayed"] is True
    assert second["events"] == []
    assert len(worker_ids(config, "qa")) == 2


def test_ledger_is_append_only_with_required_fields(tmp_path):
    config = make_project(tmp_path)
    outcome = execute(
        config,
        [{"kind": "change_worker_runtime", "worker_id": "qa-a", "provider": "codex", "model": "gpt-5.4"}],
        actor="user",
        surface="manager_conversation",
        source_ref={"message_id": "msg-42"},
    )
    store = TeamControlPlaneStore(config.parent.parent, "demo")
    events = store.read_events()
    mutation_events = [event for event in events if event["cause"] == "user_command"]
    assert mutation_events
    event = mutation_events[-1]
    for field in ("id", "decision_id", "project_key", "timestamp", "actor", "surface", "source_ref", "event", "target", "reason", "before", "after"):
        assert field in event
    assert event["surface"] == "manager_conversation"
    assert event["source_ref"] == {"message_id": "msg-42"}
    assert outcome["decision_id"] == event["decision_id"]


def test_unknown_command_and_unknown_worker_are_rejected(tmp_path):
    config = make_project(tmp_path)
    with pytest.raises(TeamControlPlaneError):
        execute(config, [{"kind": "team_updated"}])
    with pytest.raises(TeamControlPlaneError):
        execute(config, [{"kind": "activate_worker", "worker_id": "missing"}])
    with pytest.raises(TeamControlPlaneError):
        execute(config, [{"kind": "change_worker_runtime", "worker_id": "qa-a", "provider": "not-a-backend"}])


# ------------------------------------------------- 6. lifecycle semantics


def test_delete_worker_is_a_tombstone_and_history_stays_resolvable(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "delete_worker", "worker_id": "qa-a"}])
    workspace = config.parent.parent
    state = TeamControlPlaneStore(workspace, "demo").read()
    worker = next(item for item in state["workers"] if item["id"] == "qa-a")
    assert worker["lifecycle"] == "deleted"
    assert worker["deleted_at"]

    events = TeamControlPlaneStore(workspace, "demo").read_events(decision_id="")
    deleted = [event for event in events if event["event"] == "worker.deleted"]
    assert deleted and deleted[-1]["after"]["lifecycle"] == "deleted"
    # Ledger preserves the original identity and runtime for provenance.
    assert deleted[-1]["before"] == {"lifecycle": "active"}

    view = load_team_view(workspace, "demo")
    qa = next(item for item in view["roles"] if item["role"] == "qa")
    assert qa["capability"] == "unstaffed"
    assert all(worker["id"] != "qa-a" for worker in qa["workers"])


def test_worker_role_change_requires_enabled_target_role(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "planner"}])
    with pytest.raises(TeamControlPlaneError):
        execute(config, [{"kind": "change_worker_role", "worker_id": "qa-a", "role": "planner"}])
    execute(config, [{"kind": "enable_role", "role": "planner"}])
    execute(config, [{"kind": "change_worker_role", "worker_id": "qa-a", "role": "planner"}])
    state = TeamControlPlaneStore(config.parent.parent, "demo").read()
    worker = next(item for item in state["workers"] if item["id"] == "qa-a")
    assert worker["role"] == "planner"
    events = TeamControlPlaneStore(config.parent.parent, "demo").read_events()
    assert any(event["event"] == "worker.role_changed" for event in events)


# ----------------------------------------- 7. execution snapshot historical truth


def bind_snapshot(config: Path, run_id: str) -> object:
    workspace = config.parent.parent
    from goodteam_os.team_control_plane import ensure_team_state

    state = create_initial_state(run_id, "demo", "idea")
    team_state = ensure_team_state(workspace, "demo", config)
    state.team_snapshot = build_team_execution_snapshot(team_state)
    StateStore(root=workspace).write_project_current(state)
    return state


def test_runtime_change_updates_current_state_but_not_historical_snapshot(tmp_path):
    config = make_project(tmp_path)
    run = bind_snapshot(config, "run-182")
    original_snapshot = dict(run.team_snapshot)
    original_worker = original_snapshot["workers"]["qa-a"]

    execute(config, [{"kind": "change_worker_runtime", "worker_id": "qa-a", "provider": "codex", "model": "gpt-5.4"}])
    current = TeamControlPlaneStore(config.parent.parent, "demo").read()
    current_worker = next(item for item in current["workers"] if item["id"] == "qa-a")
    assert current_worker["runtime"]["provider"] == "codex"
    assert current_worker["runtime"]["model"] == "gpt-5.4"

    # Reload the historical run: its snapshot must show the original truth.
    reloaded = StateStore(root=config.parent.parent).read_project_current("demo")
    assert reloaded.team_snapshot["workers"]["qa-a"] == original_worker
    assert reloaded.team_snapshot["workers"]["qa-a"]["provider"] == "fake"


def test_role_change_and_delete_never_rewrite_historical_snapshot(tmp_path):
    config = make_project(tmp_path)
    run = bind_snapshot(config, "run-183")
    original = json.dumps(run.team_snapshot, sort_keys=True)

    execute(config, [{"kind": "change_worker_role", "worker_id": "qa-a", "role": "code_review"}])
    execute(config, [{"kind": "rename_worker", "worker_id": "qa-a", "name": "Renamed"}])
    execute(config, [{"kind": "delete_worker", "worker_id": "qa-a"}])

    reloaded = StateStore(root=config.parent.parent).read_project_current("demo")
    assert json.dumps(reloaded.team_snapshot, sort_keys=True) == original


def test_snapshot_records_enabled_roles_and_worker_runtime(tmp_path):
    config = make_project(tmp_path)
    execute(config, [{"kind": "disable_role", "role": "research"}])
    run = bind_snapshot(config, "run-184")
    snapshot = run.team_snapshot
    assert snapshot["roles"]["research"]["state"] == "disabled"
    assert snapshot["roles"]["implementation"]["state"] == "enabled"
    worker = snapshot["workers"]["implementation-a"]
    assert worker["role"] == "implementation"
    assert worker["lifecycle"] == "active"
    assert worker["provider"] == "fake"


# ------------------------------------------------------- 8. manager surface


def test_manager_runtime_comes_from_team_state(tmp_path):
    config = make_project(
        tmp_path,
        extra_yaml=(
            "worker_type_settings:\n"
            "  orchestrator:\n"
            "    reasoning_depth: high\n"
            "    additional_prompt: Be concise.\n"
        ),
    )
    target = load_project_target(config)
    settings = _manager_settings(target)
    assert settings["desired_instances"] == 1
    assert settings["reasoning_depth"] == "high"
    assert settings["additional_prompt"] == "Be concise."

    execute(config, [{"kind": "change_manager_runtime", "reasoning_depth": "medium"}])
    settings = _manager_settings(migrated_target(config))
    assert settings["reasoning_depth"] == "medium"


def test_manager_and_team_ui_share_the_same_domain_mutation_path(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent

    ui_outcome = execute_team_commands(
        workspace,
        "demo",
        [{"kind": "disable_role", "role": "research"}],
        actor="user",
        surface="team_ui",
        config_path=config,
    )
    chat_outcome = execute_team_commands(
        workspace,
        "demo",
        [{"kind": "enable_role", "role": "research"}],
        actor="user",
        surface="manager_conversation",
        config_path=config,
    )
    assert ui_outcome["decision_id"] != chat_outcome["decision_id"]
    events = TeamControlPlaneStore(workspace, "demo").read_events()
    by_surface = {event["surface"] for event in events if event["cause"] == "user_command"}
    assert {"team_ui", "manager_conversation"} <= by_surface
    # Both surfaces produced the same event vocabulary from the same domain layer.
    assert {event["event"] for event in events} >= {"team_role.disabled", "team_role.enabled"}


# --------------------------------------------------- 9. chat intent handling


def test_detect_team_commands_covers_explicit_utterances(tmp_path):
    config = make_project(tmp_path, extra_yaml="worker_type_settings:\n  research:\n    desired_instances: 2\n")
    ensure_team_state(config.parent.parent, "demo", config)
    view = load_team_view(config.parent.parent, "demo")

    add = detect_team_commands("加个 Researcher", view)
    assert add is not None
    assert add.commands == [{"kind": "initialize_worker", "role": "research", "ensure_role_enabled": True}]

    add_en = detect_team_commands("add a researcher", view)
    assert add_en is not None and add_en.commands[0]["kind"] == "initialize_worker"

    disable = detect_team_commands("把 Research 关掉", view)
    assert disable is not None and disable.commands == [{"kind": "disable_role", "role": "research"}]

    ambiguous = detect_team_commands("暂停 Builder A", view)
    assert ambiguous is not None and not ambiguous.commands
    assert {worker["id"] for worker in ambiguous.candidates} == {"implementation-a", "frontend-a", "backend-a"}
    pause = detect_team_commands("暂停 implementation-a", view)
    assert pause is not None and pause.commands == [{"kind": "inactivate_worker", "worker_id": "implementation-a"}]

    runtime = detect_team_commands("让 implementation-a 改用 claude", view)
    assert runtime is not None
    assert runtime.commands[0]["kind"] == "change_worker_runtime"
    assert runtime.commands[0]["provider"] == "anthropic"

    # Object nouns never become workers.
    assert detect_team_commands("添加一个开发文档", view) is None
    assert detect_team_commands("这次不要做 Research", view) is None


def test_task_scoped_preference_is_not_a_team_mutation(tmp_path):
    config = make_project(tmp_path)
    assert detect_task_scoped_team_preference("这次不要做 Research") is True
    assert detect_task_scoped_team_preference("把 Research 关掉") is False

    service = TaskAssistantService(config.parent.parent, project_config=config)
    session = service.send_message(
        project_key="demo",
        root=config.parent.parent.parent / "repo",
        text="这次不要做 Research",
        message_id="msg-scope-1",
    )
    view = load_team_view(config.parent.parent, "demo")
    research = next(item for item in view["roles"] if item["role"] == "research")
    assert research["state"] == "enabled"
    events = [item.get("event") for item in session["session"]["timeline"]]
    assert "team_preference_recorded" in events


def test_chat_explicit_team_request_confirms_under_one_decision(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent
    service = TaskAssistantService(workspace, project_config=config)
    session = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="把 Research 关掉",
        message_id="msg-chat-1",
    )
    pending = session["session"]["pending_team_decision"]
    confirmed = service.confirm_pending_team_decision(project_key="demo", proposal_id=pending["id"])
    applied = [item for item in confirmed["timeline"] if item.get("event") == "team_decision_applied"]
    assert applied
    decision_id = applied[-1]["details"]["decision_id"]

    events = TeamControlPlaneStore(workspace, "demo").read_events(decision_id=decision_id)
    assert [event["event"] for event in events] == ["team_role.disabled", "worker.inactivated"]
    assert all(event["surface"] == "manager_conversation" for event in events)
    assert events[0]["actor"] == "user"
    assert all(event["actor"] == "system" for event in events[1:])

    # Conversation retry with the same message id replays idempotently.
    session_again = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="把 Research 关掉",
        message_id="msg-chat-1",
    )
    applied_again = [item for item in session_again["session"]["timeline"] if item.get("event") == "team_decision_applied"]
    # Replay appends nothing new and mutates nothing further.
    assert len(applied_again) == 1
    assert applied_again[0]["details"]["decision_id"] == decision_id
    assert len(worker_ids(config, "research")) == 1


def test_chat_add_researcher_enables_and_stores_worker(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent
    execute(config, [{"kind": "disable_role", "role": "research"}])
    service = TaskAssistantService(workspace, project_config=config)
    session = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="加个 Researcher",
        message_id="msg-chat-2",
    )
    pending = session["session"]["pending_team_decision"]
    confirmed = service.confirm_pending_team_decision(project_key="demo", proposal_id=pending["id"])
    applied = [item for item in confirmed["timeline"] if item.get("event") == "team_decision_applied"]
    assert applied
    events = TeamControlPlaneStore(workspace, "demo").read_events(decision_id=applied[-1]["details"]["decision_id"])
    assert [event["event"] for event in events] == ["team_role.enabled", "worker.initialized"]
    assert worker_ids(config, "research", lifecycle="active") == ["research-b"]


def test_manager_proposed_team_change_requires_user_approval(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent
    ensure_team_state(workspace, "demo", config)
    session_store = TaskAssistantSessionStore(workspace, "demo")

    def fake_manager_turn(**_kwargs):
        return {
            "intent": "team_management",
            "response": "我建议增加一个 Researcher。",
            "team_commands": [{"kind": "initialize_worker", "role": "research"}],
            "team_authorization": "manager_proposed",
            "manager_execution": {"job_id": "job-1"},
        }

    service = TaskAssistantService(workspace, project_config=config, manager_turn=fake_manager_turn)
    before = len(worker_ids(config, "research"))
    session = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="这个任务最好补充一些调研。",
        message_id="msg-propose-1",
    )
    # Manager proposal alone never mutates the team.
    assert len(worker_ids(config, "research")) == before
    proposed = [item for item in session["session"]["timeline"] if item.get("event") == "team_decision_proposed"]
    assert proposed
    pending = dict(session["session"]["pending_team_decision"])
    assert pending["status"] == "pending"

    # User confirms through the approval primitive.
    confirmed = service.confirm_pending_team_decision(project_key="demo", proposal_id=str(pending["id"]))
    applied = [item for item in confirmed["timeline"] if item.get("event") == "team_decision_applied"]
    assert applied and applied[-1]["details"]["confirmed_by_user"] is True
    assert len(worker_ids(config, "research")) == before + 1
    assert dict(session_store.read()["pending_team_decision"] or {}) == {}

    events = TeamControlPlaneStore(workspace, "demo").read_events()
    init_events = [event for event in events if event["event"] == "worker.initialized" and event["cause"] == "user_command"]
    assert init_events[-1]["source_ref"]["proposal_id"] == pending["id"]


def test_model_user_authorized_flag_still_requires_confirmation(tmp_path):
    config = make_project(tmp_path)
    workspace = config.parent.parent

    def fake_manager_turn(**_kwargs):
        return {
            "intent": "team_management",
            "response": "好的，已暂停 Builder A。",
            "team_commands": [{"kind": "inactivate_worker", "worker_id": "implementation-a"}],
            "team_authorization": "user_authorized",
        }

    service = TaskAssistantService(workspace, project_config=config, manager_turn=fake_manager_turn)
    session = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="暂停 implementation-a",
        message_id="msg-auth-1",
    )
    assert worker_ids(config, "implementation", lifecycle="active") == ["implementation-a"]
    pending = session["session"]["pending_team_decision"]
    confirmed = service.confirm_pending_team_decision(project_key="demo", proposal_id=pending["id"])
    applied = [item for item in confirmed["timeline"] if item.get("event") == "team_decision_applied"]
    assert applied
    assert worker_ids(config, "implementation", lifecycle="active") == []
    assert dict(confirmed["pending_team_decision"] or {}) == {}


def test_builtin_manager_runtime_uses_deterministic_classification(tmp_path):
    config = make_project(tmp_path, worker_mode="fake")
    workspace = config.parent.parent
    service = TaskAssistantService(workspace, project_config=config)
    assert service._manager_runtime_is_builtin() is True
    session = service.send_message(
        project_key="demo",
        root=workspace.parent / "repo",
        text="把 QA 关掉",
        message_id="msg-fake-1",
    )
    pending = session["session"]["pending_team_decision"]
    confirmed = service.confirm_pending_team_decision(project_key="demo", proposal_id=pending["id"])
    applied = [item for item in confirmed["timeline"] if item.get("event") == "team_decision_applied"]
    assert applied
    assert worker_ids(config, "qa", lifecycle="active") == []


# ------------------------------------------------------- 10. web API surface


def _api_project(tmp_path: Path, key: str = "TEAM") -> Path:
    project_root = tmp_path / "target"
    project_root.mkdir(exist_ok=True)
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    config = projects / f"{key.lower()}.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Team Project",
                f"key: {key}",
                "repos:",
                f"  root: {project_root}",
                "worker_backend:",
                "  mode: fake",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def test_team_commands_endpoint_executes_and_returns_view(tmp_path):
    from goodteam_os import web

    _api_project(tmp_path)
    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/team/commands",
        {"commands": [{"kind": "disable_role", "role": "research"}], "reason": "不再需要"},
    )
    assert status == 200
    assert payload["changed"] is True
    research = next(entry for entry in payload["team"]["roles"] if entry["role"] == "research")
    assert research["state"] == "disabled"
    assert payload["events"][0]["surface"] == "team_ui"
    assert payload["events"][0]["actor"] == "user"

    with pytest.raises(web.ProjectApiError) as rejected:
        web.handle_project_api_post(
            tmp_path,
            "/api/projects/TEAM/team/commands",
            {"commands": [{"kind": "disable_role", "role": "not-a-role"}]},
        )
    assert rejected.value.status == 409
    assert rejected.value.code == "team_command_rejected"

    assert web._is_project_api_post_path("/api/projects/TEAM/team/commands") is True


def test_team_view_and_events_endpoints(tmp_path):
    from goodteam_os import web

    _api_project(tmp_path)
    status, payload = web.handle_project_api_get(tmp_path, "/api/projects/TEAM/team")
    assert status == 200
    assert payload["team"]["schema_version"] == "team_capability_view.v1"
    assert payload["team"]["manager"]["role"] == "orchestrator"

    web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/team/commands",
        {"commands": [{"kind": "inactivate_worker", "worker_id": "qa-a"}]},
    )
    events_status, events = web.handle_project_api_get(tmp_path, "/api/projects/TEAM/team/events")
    assert events_status == 200
    assert events["schema_version"] == "team_decision_ledger.v1"
    assert any(event["event"] == "worker.inactivated" for event in events["events"])


def test_project_state_payload_includes_team_view(tmp_path):
    from goodteam_os import web

    _api_project(tmp_path)
    payload = web.build_project_work_state(tmp_path, "TEAM")
    assert payload["team"] is not None
    assert payload["team"]["schema_version"] == "team_capability_view.v1"
    roles = {entry["role"]: entry for entry in payload["team"]["roles"]}
    assert set(roles) >= {"implementation", "qa", "research"}
    assert roles["implementation"]["capability"] in {"ready", "degraded", "unstaffed"}


def test_legacy_settings_bridge_translates_onto_control_plane(tmp_path, monkeypatch):
    from goodteam_os import team_control_plane, web

    # Capability readiness depends on host runtime availability (doctor);
    # pin it so the assertion is deterministic on runners without CLIs.
    monkeypatch.setattr(
        team_control_plane,
        "worker_backend_doctor",
        lambda mode: {"mode": mode, "ready": True},
    )

    _api_project(tmp_path, key="BRIDGE")
    payload = web.update_project_worker_backend_settings(
        tmp_path,
        {
            "project": "BRIDGE",
            "worker_backend_mode": "fake",
            "workers": {"qa": "codex"},
            "role_models": {"qa": "codex:gpt-5.4"},
            "worker_type_settings": {
                "qa": {"reasoning_depth": "high", "additional_prompt": "", "desired_instances": 2},
            },
        },
    )
    assert payload["ok"] is True

    from goodteam_os.team_control_plane import TeamControlPlaneStore, load_team_view

    store = TeamControlPlaneStore(tmp_path, "BRIDGE")
    state = store.read()
    assert state is not None
    qa_workers = [w for w in state["workers"] if w["role"] == "qa"]
    assert len(qa_workers) == 2
    for worker in qa_workers:
        assert worker["lifecycle"] == "active"
        assert worker["runtime"]["provider"] == "codex"
        assert worker["runtime"]["model"] == "gpt-5.4"
        assert worker["runtime"]["reasoning_depth"] == "high"
    # Derived legacy view stays consistent for diagnostics.
    assert payload["role_worker_backends"]["qa"] == "codex"
    view = load_team_view(tmp_path, "BRIDGE")
    qa_entry = next(entry for entry in view["roles"] if entry["role"] == "qa")
    assert qa_entry["capability"] == "ready"


def test_task_assistant_team_decision_actions(tmp_path):
    from goodteam_os import web

    config = _api_project(tmp_path, key="APPROVE")
    team = ensure_team_state(tmp_path, "APPROVE", config)
    session_store = web.TaskAssistantSessionStore(tmp_path, "APPROVE")
    session_store.append_message("message-1", "user", "建议加个 Researcher")
    proposal = session_store.propose_team_decision(
        commands=[{"kind": "initialize_worker", "role": "research", "ensure_role_enabled": True}],
        reason="建议加个 Researcher",
        source_message_id="message-1",
        expected_team_revision=team["revision"],
    )["pending_team_decision"]

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/APPROVE/task-assistant/actions",
        {"action": "confirm_team_decision", "proposal_id": proposal["id"]},
    )
    assert status == 200
    assert payload["applied"] is True
    research = next(entry for entry in payload["team"]["roles"] if entry["role"] == "research")
    assert any(worker["lifecycle"] == "active" for worker in research["workers"])
    assert payload["task_assistant_session"]["pending_team_decision"] is None

    proposal2 = session_store.propose_team_decision(
        commands=[{"kind": "initialize_worker", "role": "planner", "ensure_role_enabled": True}],
        reason="再考虑一个 planner",
        source_message_id="message-2",
    )["pending_team_decision"]
    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/APPROVE/task-assistant/actions",
        {"action": "dismiss_team_decision", "proposal_id": proposal2["id"]},
    )
    assert status == 200
    assert payload["applied"] is False
    planner = next(entry for entry in payload["team"]["roles"] if entry["role"] == "planner")
    assert [worker["id"] for worker in planner["workers"]] == ["planner-a"]

    with pytest.raises(web.ProjectApiError) as unavailable:
        web.handle_project_api_post(
            tmp_path,
            "/api/projects/APPROVE/task-assistant/actions",
            {"action": "confirm_team_decision", "proposal_id": "missing"},
        )
    assert unavailable.value.status == 409
    assert unavailable.value.code == "team_decision_unavailable"


# ------------------------------------- 11. worker resolution is the whole contract


def test_resolution_provides_complete_runtime_without_cross_worker_mixing(tmp_path, monkeypatch):
    """One worker resolution must carry the full execution runtime.

    Regression for the P1 found in review: with Worker A active-but-unready
    and Worker B active-and-ready, dispatch must use B's identity AND B's
    reasoning depth / prompt — never B's identity with A's settings.
    """
    from goodteam_os import team_control_plane
    from goodteam_os.graph import _worker_job_fields
    from goodteam_os.state import create_initial_state

    config = make_project(
        tmp_path,
        extra_yaml="worker_type_settings:\n  implementation:\n    desired_instances: 2\n",
    )
    execute(config, [
        {"kind": "change_worker_runtime", "worker_id": "implementation-a",
         "provider": "codex", "reasoning_depth": "high", "additional_prompt": "Worker A prompt."},
        {"kind": "change_worker_runtime", "worker_id": "implementation-b",
         "reasoning_depth": "medium", "additional_prompt": "Worker B prompt."},
    ])

    def doctor(mode):
        return {"mode": mode, "ready": mode != "codex"}

    monkeypatch.setattr(team_control_plane, "worker_backend_doctor", doctor)
    target = migrated_target(config)
    state = create_initial_state("run-mix", "demo", "idea")

    resolution = team_control_plane.resolve_worker_for_role(target, "implementation")
    assert resolution is not None
    # Worker A (codex, unready) is skipped entirely; B provides everything.
    assert resolution["worker_id"] == "implementation-b"
    assert resolution["adapter"] == "fake"
    assert resolution["reasoning_depth"] == "medium"
    assert resolution["additional_prompt"] == "Worker B prompt."
    assert resolution["pi_thinking_level"] == ""
    assert resolution["desired_instances"] == 2

    fields = _worker_job_fields(target, "implementation", state)
    assert fields["worker_id"] == "implementation-b"
    assert fields["adapter"] == "fake"
    assert fields["reasoning_depth"] == "medium"
    assert fields["additional_prompt"] == "Worker B prompt."
    assert fields["desired_instances"] == 2


def test_reviewer_dispatch_binds_resolved_worker_identity_and_runtime(tmp_path, monkeypatch):
    """Reviewer jobs are executed by the resolved team Worker.

    Regression for the P1 found in review: the reviewer gate must not merely
    consult the team and then run a profile preset — the job must carry the
    executing Worker's identity (worker_id/worker_name) and that Worker's
    runtime, so provenance can always answer which Worker ran the review.
    """
    from goodteam_os import team_control_plane
    from goodteam_os.reviewer_dispatch import dispatch_reviewer_jobs_for_gate
    from goodteam_os.workers import WorkerJobStore

    config = make_project(
        tmp_path,
        extra_yaml=(
            "worker_type_settings:\n"
            "  code_review:\n"
            "    reasoning_depth: high\n"
            "    additional_prompt: Review carefully.\n"
        ),
    )
    execute(config, [{"kind": "change_worker_runtime", "worker_id": "code_review-a", "provider": "fake"}])

    def doctor(mode):
        return {"mode": mode, "ready": True}

    monkeypatch.setattr(team_control_plane, "worker_backend_doctor", doctor)
    target = migrated_target(config)
    target["reviewer_profiles"] = {"implementation": ["correctness-security"]}
    target["reviewer_dispatch"] = {
        "mode": "parallel",
        "phases": {"implementation": "parallel"},
        "rollout": {"enabled": True, "acknowledge": "parallel-reviewer-dispatch-is-advisory"},
    }

    project_root = tmp_path / "runtime"
    project_root.mkdir()
    store = WorkerJobStore(project_root)

    class RunState:
        run_id = "run-review"

    results = dispatch_reviewer_jobs_for_gate(
        store=store,
        state=RunState(),
        target=target,
        approval="code_review",
        artifact_key="code_review",
        artifact_path="missing.md",
        context="ctx",
    )
    assert len(results) == 1
    job = next(item for item in store.list_jobs() if item["id"] == results[0]["job_id"])
    assert job["worker_id"] == "code_review-a"
    assert job["worker_name"] == "Reviewer A"
    assert job["adapter"] == "fake"
    assert job["reasoning_depth"] == "high"
    assert job["additional_prompt"] == "Review carefully."


def test_debate_dispatch_binds_team_worker_and_gates_disabled_roles(tmp_path, monkeypatch):
    """Debate jobs run with the role's team Worker runtime and identity."""
    from goodteam_os import team_control_plane
    from goodteam_os.debate import default_debate_dispatch
    from goodteam_os.workers import WorkerJobStore

    config = make_project(
        tmp_path,
        extra_yaml=(
            "worker_type_settings:\n"
            "  code_review:\n"
            "    reasoning_depth: low\n"
            "    additional_prompt: Debate sharp.\n"
        ),
    )

    def doctor(mode):
        return {"mode": mode, "ready": True}

    monkeypatch.setattr(team_control_plane, "worker_backend_doctor", doctor)
    migrated_target(config)
    workspace = config.parent.parent

    dispatch = default_debate_dispatch(workspace, "demo", "run-debate")
    dispatch(role="code_review", kind="challenge", gate="code_review")

    store = WorkerJobStore(workspace)
    job = next(item for item in store.list_jobs() if item["role"] == "code_review")
    assert job["worker_id"] == "code_review-a"
    assert job["worker_name"] == "Reviewer A"
    assert job["adapter"] == "fake"
    assert job["reasoning_depth"] == "low"
    assert job["additional_prompt"] == "Debate sharp."

    # A disabled role gates the debate: no job, no lines.
    execute(config, [{"kind": "disable_role", "role": "code_review"}])
    before = len([item for item in store.list_jobs() if item["role"] == "code_review"])
    lines = dispatch(role="code_review", kind="challenge", gate="code_review")
    assert lines == []
    after = len([item for item in store.list_jobs() if item["role"] == "code_review"])
    assert after == before


def test_debate_dispatch_fails_closed_on_corrupt_team_state(tmp_path, monkeypatch):
    """Corrupt team state is unreadable truth, not absent truth.

    Regression for the follow-up review finding: a state file that exists but
    cannot be parsed must fail closed (no debate lines, no job), never fall
    back to the legacy explicit-adapter dispatch.
    """
    from goodteam_os.debate import default_debate_dispatch
    from goodteam_os.workers import WorkerJobStore

    config = make_project(tmp_path)
    migrated_target(config)
    workspace = config.parent.parent
    TeamControlPlaneStore(workspace, "demo").state_path.write_text("{corrupt", encoding="utf-8")

    dispatch = default_debate_dispatch(workspace, "demo", "run-corrupt")
    lines = dispatch(role="code_review", kind="challenge", gate="code_review")

    assert lines == []
    assert WorkerJobStore(workspace).list_jobs() == []
