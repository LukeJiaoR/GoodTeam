import pytest

from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_task_assistant import FakeReadiness, readiness


def setup_service(tmp_path, *, mode="existing_project", turn=None):
    root = tmp_path / "target"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n")
    executor = FakeReadiness(readiness(mode))
    config = tmp_path / "demo.yaml"
    config.write_text("key: DEMO\n")
    calls = []

    def manager(**values):
        calls.append(values)
        return turn or {"intent": "work_request", "response": "Draft ready."}

    def confirm(_workspace, _key, _root, selected):
        executor.state = readiness(selected)
        return executor.state

    service = TaskAssistantService(tmp_path, project_config=config, readiness_executor=executor,
                                   manager_turn=manager, confirm_mode=confirm)
    return service, root, executor, calls


def send(service, root, text, message_id="m1"):
    return service.send_message(project_key="DEMO", root=root, text=text, message_id=message_id)["session"]


def test_work_draft_survives_unconfirmed_mode_without_preparation(tmp_path):
    service, root, executor, calls = setup_service(tmp_path, mode="unconfirmed")
    session = send(service, root, "Add task search by title.")
    assert "Add task search by title." == session["proposed_task"]["exact_request"]
    assert "project_mode" == session["attention_request"]["kind"]
    assert "succeeded" == session["assistant_requests"]["m1"]["status"]
    assert [] == executor.started
    assert session["proposed_task"] == service.session("DEMO", root)["proposed_task"]
    assert 1 == len(calls)


def test_explicit_addition_preserves_id_sources_and_original_goal(tmp_path):
    service, root, executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add task search by title.")["proposed_task"]
    second = send(service, root, "Also add regression tests, with no new dependencies.", "m2")["proposed_task"]
    assert first["id"] == second["id"]
    assert 2 == second["revision"]
    assert ["m1", "m2"] == second["source_message_ids"]
    assert first["exact_request"] + "\n\nAlso add regression tests, with no new dependencies." == second["exact_request"]
    assert [] == executor.started


def test_unclear_relation_does_not_overwrite_and_blocks_old_confirmation(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    second = send(service, root, "Build a login page instead.", "m2")["proposed_task"]
    assert first["exact_request"] == second["exact_request"]
    assert "m2" == second["pending_change"]["message_id"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    with pytest.raises(ValueError):
        store.confirm_task(first["id"], expected_revision=first["revision"])
    with pytest.raises(ValueError):
        store.confirm_task(first["id"], expected_revision=second["revision"])


def test_confirmed_request_is_not_replaced_by_later_message(tmp_path):
    service, root, _executor, _calls = setup_service(tmp_path)
    first = send(service, root, "Add search.")["proposed_task"]
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.confirm_task(first["id"])
    before = store.read()["proposed_task"]
    result = send(service, root, "Also add authentication.", "m2")
    assert before == result["proposed_task"]
    assert "task_draft_locked" == result["timeline"][-1]["event"]


@pytest.mark.parametrize("text", [
    "Please acknowledge this message only. Do not inspect files, start work, or change the Team.",
    "Cancel the task. Do not start any work.",
])
def test_non_work_message_never_starts_preparation_even_if_model_misclassifies(tmp_path, text):
    service, root, executor, _calls = setup_service(tmp_path)
    result = send(service, root, text)
    assert [] == executor.started
    assert result["proposed_task"] is None


def test_store_confirmation_rejects_stale_revision(tmp_path):
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("m1", "user", "Add search.")
    first = store.propose_task(["m1"], "Add search.")["proposed_task"]
    store.revise_task(first["id"], "Add search and tests.")
    with pytest.raises(ValueError, match="changed"):
        store.confirm_task(first["id"], expected_revision=1)
    assert not store.read()["proposed_task"]["confirmed"]


@pytest.mark.parametrize("ready", [True, False])
def test_confirmed_draft_offers_resume_when_preparation_is_ready_or_not_started(tmp_path, ready):
    service, root, executor, _calls = setup_service(tmp_path)
    proposal = send(service, root, "Add search.")["proposed_task"]
    TaskAssistantSessionStore(tmp_path, "DEMO").confirm_task(proposal["id"])
    executor.state = readiness(ready=ready)
    assert "task_start_resume" == service.session("DEMO", root)["attention_request"]["kind"]
