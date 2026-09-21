import pytest

from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService, detect_team_commands
from goodteam_os.team_control_plane import TeamControlPlaneStore, execute_team_commands
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def setup(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    workspace = tmp_path / "workspace"
    config = write_project_config(workspace / "projects", "DEMO", "Demo", repo_path=str(root), worker_mode="fake")

    def turn(**_request):
        return {"intent": "team_management", "response": "Already disabled QA.",
                "team_commands": [{"kind": "disable_role", "role": "qa"}],
                "team_authorization": "user_authorized"}

    assistant = TaskAssistantService(workspace, project_config=config, manager_turn=turn)
    assistant.team_view("DEMO")
    return assistant, root, config


def test_model_authorization_cannot_mutate_team_and_confirmation_replays(tmp_path):
    assistant, root, _config = setup(tmp_path)
    result = assistant.send_message(project_key="DEMO", root=root, text="Explain QA. Do not change it.", message_id="m1")
    store = TeamControlPlaneStore(assistant.workspace, "DEMO")
    assert "enabled" == store.read()["roles"]["qa"]["state"]
    pending = result["session"]["pending_team_decision"]
    assert pending["expected_team_revision"] == store.read()["revision"]
    assert "Already disabled" not in result["session"]["timeline"][-1]["content"]
    first = assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    second = assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    assert first == second
    assert "disabled" == store.read()["roles"]["qa"]["state"]


def test_stale_team_proposal_is_rejected_under_command_lock(tmp_path):
    assistant, root, config = setup(tmp_path)
    result = assistant.send_message(project_key="DEMO", root=root, text="Disable QA", message_id="m1")
    pending = result["session"]["pending_team_decision"]
    execute_team_commands(assistant.workspace, "DEMO", [{"kind": "disable_role", "role": "research"}],
                          actor="user", surface="team_ui", config_path=config)
    with pytest.raises(ValueError, match="changed|stale"):
        assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    assert "enabled" == TeamControlPlaneStore(assistant.workspace, "DEMO").read()["roles"]["qa"]["state"]
    dismissed = assistant.dismiss_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    assert None is dismissed["pending_team_decision"]


def test_ambiguous_worker_name_requires_specific_identity(tmp_path):
    assistant, _root, _config = setup(tmp_path)
    view = assistant.team_view("DEMO")
    detection = detect_team_commands("Pause Builder A", view)
    assert detection is not None and not detection.commands
    assert {item["id"] for item in detection.candidates} == {"implementation-a", "frontend-a", "backend-a"}


def test_preview_preserves_state_and_ledger_then_matches_confirmed_effects(tmp_path):
    assistant, root, config = setup(tmp_path)
    team = TeamControlPlaneStore(assistant.workspace, "DEMO")
    before = team.read()
    events = team.read_events()
    config_bytes = config.read_bytes()
    result = assistant.send_message(project_key="DEMO", root=root, text="Disable QA", message_id="preview")
    pending = result["session"]["pending_team_decision"]
    preview = pending["preview"]
    assert preview["team_revision"] == before["revision"]
    assert [(change["target"]["id"], change["before"], change["after"]) for change in preview["changes"]] == [
        ("qa", {"state": "enabled"}, {"state": "disabled"}),
        ("qa-a", {"lifecycle": "active"}, {"lifecycle": "inactive"}),
    ]
    assert preview["changes"][1]["target"] == {
        "kind": "worker", "id": "qa-a", "name": "QA A", "role": "qa", "label": "QA",
    }
    assert team.read() == before
    assert team.read_events() == events
    assert config.read_bytes() == config_bytes
    restored = TaskAssistantService(assistant.workspace, project_config=config)
    assert restored.session("DEMO", root)["pending_team_decision"]["preview"] == preview
    restored.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    applied = team.read_events()[len(events):]
    assert [(item["target"]["id"], item["before"], item["after"]) for item in applied] == [
        (item["target"]["id"], item["before"], item["after"]) for item in preview["changes"]
    ]
    assert [(item["actor"], item["cause"]) for item in applied] == [("user", "user_command"), ("system", "cascade")]


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("text,identifiers", [
    ("Pause Builder A", {"implementation-a", "frontend-a", "backend-a"}),
    ("暂停 Builder A", {"implementation-a", "frontend-a", "backend-a"}),
    ("让 Reviewer A 改用 codex", {"review-a", "code_review-a"}),
    ("激活 Reviewer A", {"review-a", "code_review-a"}),
])
def test_ambiguous_names_clarify_before_model_can_select_first_match(tmp_path, text, identifiers, running):
    assistant, root, _config = setup(tmp_path)
    if running:
        from goodteam_os.state import StateStore, create_initial_state
        StateStore(assistant.workspace).write_project_current(create_initial_state("live", "DEMO", "Existing task"))
    def must_not_choose(**_request):
        pytest.fail("Ambiguous Worker identity must be resolved before a model call")
    assistant.manager_turn = must_not_choose
    team = TeamControlPlaneStore(assistant.workspace, "DEMO")
    before, events = team.read(), team.read_events()
    result = assistant.send_message(project_key="DEMO", root=root, text=text, message_id="ambiguous")
    assert result["intent"] == "ambiguous"
    assert result["session"]["pending_team_decision"] is None
    reply = result["session"]["timeline"][-1]["content"]
    assert all(identifier in reply for identifier in identifiers)
    assert team.read() == before
    assert team.read_events() == events
    assert TaskAssistantSessionStore(assistant.workspace, "DEMO").read()["timeline"][-1]["content"] == reply


def test_deterministic_clarification_does_not_claim_real_runtime_was_verified(tmp_path):
    assistant, root, config = setup(tmp_path)
    execute_team_commands(assistant.workspace, "DEMO", [{"kind": "change_manager_runtime", "provider": "codex"}],
                          actor="user", surface="team_ui", config_path=config)
    result = assistant.send_message(project_key="DEMO", root=root, text="Pause Builder A", message_id="clarify")
    assert result["session"]["runtime"]["status"] == "unverified"
    assert not result["session"].get("runtime_verification")


def test_specific_id_after_clarification_only_proposes_that_worker(tmp_path):
    initial, root, config = setup(tmp_path)
    assistant = TaskAssistantService(initial.workspace, project_config=config)
    assistant.send_message(project_key="DEMO", root=root, text="Pause Builder A", message_id="ambiguous")
    result = assistant.send_message(project_key="DEMO", root=root, text="Pause frontend-a", message_id="specific")
    pending = result["session"]["pending_team_decision"]
    assert pending["commands"] == [{"kind": "inactivate_worker", "worker_id": "frontend-a"}]
    assert pending["preview"]["changes"][0]["target"]["role"] == "frontend"
    team = TeamControlPlaneStore(assistant.workspace, "DEMO")
    assert all(worker["lifecycle"] == "active" for worker in team.read()["workers"])
    assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    assert {worker["id"] for worker in team.read()["workers"] if worker["lifecycle"] == "inactive"} == {"frontend-a"}


def test_stale_preview_survives_reload_without_becoming_current_or_executable(tmp_path):
    assistant, root, config = setup(tmp_path)
    result = assistant.send_message(project_key="DEMO", root=root, text="Disable QA", message_id="m1")
    pending = result["session"]["pending_team_decision"]
    execute_team_commands(assistant.workspace, "DEMO", [{"kind": "rename_worker", "worker_id": "qa-a", "name": "New QA"}],
                          actor="user", surface="team_ui", config_path=config)
    restored = TaskAssistantService(assistant.workspace, project_config=config).session("DEMO", root)
    assert restored["pending_team_decision"]["status"] == "stale"
    assert restored["pending_team_decision"]["preview"] == pending["preview"]
    with pytest.raises(ValueError, match="changed"):
        assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    assistant.dismiss_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
    with pytest.raises(ValueError, match="unavailable"):
        assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])


def test_team_change_during_model_turn_invalidates_proposal(tmp_path):
    assistant, root, config = setup(tmp_path)
    original = assistant.manager_turn
    def turn(**request):
        execute_team_commands(assistant.workspace, "DEMO", [{"kind": "disable_role", "role": "research"}],
                              actor="user", surface="team_ui", config_path=config)
        return original(**request)
    assistant.manager_turn = turn
    result = assistant.send_message(project_key="DEMO", root=root, text="Disable QA", message_id="m1")
    proposal = result["session"]["pending_team_decision"]
    with pytest.raises(ValueError, match="changed"):
        assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=proposal["id"])
    assert "enabled" == TeamControlPlaneStore(assistant.workspace, "DEMO").read()["roles"]["qa"]["state"]


def test_legacy_proposal_without_revision_fails_closed(tmp_path):
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    assistant, _root, _config = setup(tmp_path)
    store = TaskAssistantSessionStore(assistant.workspace, "DEMO")
    pending = store.propose_team_decision(commands=[{"kind": "disable_role", "role": "qa"}],
                                         reason="Legacy", source_message_id="legacy")["pending_team_decision"]
    with pytest.raises(ValueError, match="stale"):
        assistant.confirm_pending_team_decision(project_key="DEMO", proposal_id=pending["id"])
