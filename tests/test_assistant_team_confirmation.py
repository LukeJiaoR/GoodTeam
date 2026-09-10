import pytest

from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService, detect_team_commands
from goodteam_os.team_control_plane import TeamControlPlaneStore, execute_team_commands


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
    assert detection is None or not detection.commands


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
