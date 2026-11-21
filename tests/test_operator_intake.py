import json

import pytest

from goodteam_os.cli import main
from goodteam_os.execution.github import GitHubIssueSnapshot
from goodteam_os.operator import OperatorExecutionStateMachine, OperatorService
from goodteam_os.operator_github import GhCliGitHubClient
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.telegram import process_telegram_update


ISSUE = "https://github.com/owner/repo/issues/42"
REVISION = "2026-07-14T01:02:03Z"


@pytest.fixture
def registered_source(tmp_path, monkeypatch):
    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "service"))
    root = tmp_path / "target"
    root.mkdir()
    config = tmp_path / "projects" / "demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nname: Demo\nrepository: owner/repo\nrepos:\n  root: {root}\n"
                      "worker_backend:\n  mode: fake\nstaging:\n  enabled: false\n")
    fetches = []

    def fetch_issue(client, repo, number):
        fetches.append((client.cwd, repo, number))
        return GitHubIssueSnapshot(repo, number, "Example issue", "External details", ISSUE, [], updated_at=REVISION)

    monkeypatch.setattr(GhCliGitHubClient, "fetch_issue", fetch_issue)
    queue = DurableOperatorQueue(tmp_path / "service")
    return tmp_path, root, fetches, queue


def test_task_cli_and_authorized_telegram_share_one_manager_receipt(registered_source, capsys, monkeypatch):
    workspace, root, fetches, queue = registered_source
    store = TaskAssistantSessionStore(workspace, "DEMO")
    update = {"message": {"message_id": 8, "from": {"id": 111}, "chat": {"id": 12345},
                          "text": f"/gt task {ISSUE}"}}
    monkeypatch.setattr("goodteam_os.telegram._telegram_request", lambda *args: {"ok": True})

    for allowed_users in ([], ["222"]):
        denied = process_telegram_update(update, workspace=workspace, allowed_users=allowed_users,
                                         processed_callbacks=set())
        assert denied == {"status": "ignored", "reason": "unauthorized user"}
    assert not fetches
    assert not store.read().get("external_inputs")

    assert main(["task", "enqueue", ISSUE, "--workspace", str(workspace)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"] is True
    receipt = result["item"]
    assert receipt["route"] == "manager"
    assert receipt["phase"] == "manager_pending"
    accepted = process_telegram_update(update, workspace=workspace, allowed_users=["111"],
                                       processed_callbacks=set())
    assert accepted["accepted"] is True
    assert accepted["created"] is False
    assert accepted["task_id"] == receipt["message_id"]
    assert fetches == [(root, "owner/repo", 42)] * 2
    session = store.read()
    assert set(session["external_inputs"]) == {receipt["message_id"]}
    assert session["external_inputs"][receipt["message_id"]]["snapshot"]["body"] == "External details"
    assert not session.get("assistant_requests")
    assert queue.list_items() == []
    assert not queue.state_path.exists()


def test_history_remains_owned_by_operator_until_authorized_handoff(registered_source, capsys):
    workspace, root, fetches, queue = registered_source
    item, _ = queue.enqueue(idempotency_key="github_issue:DEMO:owner/repo#42", source_url=ISSUE,
                            source_revision=REVISION, project_key="DEMO", repository_identity="owner/repo",
                            metadata={"project_root": str(root)})
    store = TaskAssistantSessionStore(workspace, "DEMO")
    assert main(["task", "enqueue", ISSUE, "--workspace", str(workspace)]) == 0
    assert json.loads(capsys.readouterr().out) == {"created": False, "item": item}
    assert not fetches
    assert not store.read().get("external_inputs")

    # Queued history cannot be claimed by the old execution machine or moved
    # by the supervisor while the user has stopped the queue.
    actions = object()
    assert OperatorExecutionStateMachine(queue, actions).tick() is None
    queue.set_desired_state("stopped")
    service = OperatorService(workspace=workspace, state_dir=workspace / "service", actions=actions)
    assert service.tick() is None
    assert queue.get(item["id"]) == item
    assert not fetches

    queue.set_desired_state("running")
    transferred = service.tick()
    assert transferred["status"] == "transferred"
    assert transferred["execution_id"] == ""
    message_id = transferred["metadata"]["manager_input"]["message_id"]
    assert set(store.read()["external_inputs"]) == {message_id}
    assert fetches == [(root, "owner/repo", 42)]


def test_historical_active_item_reconciles_original_operation(registered_source):
    workspace, root, fetches, queue = registered_source
    item, _ = queue.enqueue(idempotency_key="github_issue:DEMO:owner/repo#42", source_url=ISSUE,
                            source_revision=REVISION, project_key="DEMO", repository_identity="owner/repo",
                            metadata={"project_root": str(root)})
    queue.transition(item["id"], status="active", phase="hydrating", updates={"attempt": 1})
    operation_id = f"{item['id']}:1:hydrate:none"
    queue.record_operation_intent(item["id"], operation_id, kind="hydrate",
                                  payload={"phase": "hydrating", "head_sha": ""})

    class Actions:
        def reconcile(self, kind, active, operation):
            assert (kind, active["id"], operation["operation_id"]) == ("hydrate", item["id"], operation_id)
            return {"reconciled_source": "original"}

        def perform(self, kind, active):
            pytest.fail("recovery must not repeat an operation with a durable intent")

    service = OperatorService(workspace=workspace, state_dir=workspace / "service", actions=Actions())
    assert service.recover()["id"] == item["id"]
    active = service.tick()
    assert (active["id"], active["phase"]) == (item["id"], "preflight")
    assert active["operations"][operation_id]["result"] == {"reconciled_source": "original"}
    assert not fetches
    assert not TaskAssistantSessionStore(workspace, "DEMO").read().get("external_inputs")
