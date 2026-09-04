import json
import multiprocessing
from importlib import import_module

import pytest


def _claim_from_process(workspace, proposal_id, claim_id, gate, results):
    gate.wait()
    claimed = session_store(workspace).claim_workflow_start(proposal_id, claim_id, f"task-assistant:{proposal_id}")
    results.put(claimed["proposed_task"]["start_claim_id"])


def session_store(tmp_path, project_key="DEMO"):
    store_type = import_module("goodteam_os.task_assistant_session").TaskAssistantSessionStore
    return store_type(tmp_path, project_key)


def test_session_preserves_ordered_exact_messages_and_is_idempotent(tmp_path):
    store = session_store(tmp_path)

    first = store.append_message("message-1", "user", "  看一下当前的项目  ")
    repeated = store.append_message("message-1", "user", "  看一下当前的项目  ")
    final = store.append_message("message-2", "task_assistant", "我先了解项目结构。")

    assert repeated == first
    assert [item["content"] for item in final["timeline"]] == [
        "  看一下当前的项目  ",
        "我先了解项目结构。",
    ]
    assert json.loads(store.path.read_text(encoding="utf-8")) == final


def test_session_rejects_conflicting_message_id(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "First request")

    with pytest.raises(ValueError, match="message_id"):
        store.append_message("message-1", "user", "Different request")


def test_session_initializes_deterministic_welcome_and_soft_suggestions(tmp_path):
    store = session_store(tmp_path)
    hint = {"mode": "existing_project", "confidence": "high", "evidence": ["go.mod"]}

    session = store.initialize_context(hint)
    repeated = store.initialize_context(hint)

    assert repeated == session
    assert session["project_mode_hint"] == hint
    assert session["timeline"][0]["event"] == "welcome_existing_project"
    assert session["suggestions"][0] == {"id": "understand-project"}


def test_proposed_task_revisions_bind_exact_human_request_to_one_run(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "修复登录问题")

    proposed = store.propose_task(["message-1"], "修复登录问题")
    proposal_id = proposed["proposed_task"]["id"]
    revised = store.revise_task(proposal_id, "修复登录问题，并补回归测试")
    bound = store.bind_workflow(proposal_id, "run-1")
    repeated = store.bind_workflow(proposal_id, "run-1")

    assert revised["proposed_task"]["exact_request"] == "修复登录问题，并补回归测试"
    assert revised["proposed_task"]["revision"] == 2
    assert bound["proposed_task"]["run_id"] == "run-1"
    assert bound["status"] == "workflow_active"
    assert repeated == bound

    with pytest.raises(ValueError, match="already bound"):
        store.bind_workflow(proposal_id, "run-2")


def test_task_confirmation_is_idempotent_and_keeps_proposal_editable_before_confirmation(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "修复登录问题")
    proposed = store.propose_task(["message-1"], "修复登录问题")
    proposal_id = proposed["proposed_task"]["id"]

    confirmed = store.confirm_task(proposal_id)
    repeated = store.confirm_task(proposal_id)

    assert confirmed["proposed_task"]["confirmed"] is True
    assert confirmed["status"] == "preparing_to_start"
    assert repeated == confirmed

    with pytest.raises(ValueError, match="unavailable"):
        store.revise_task(proposal_id, "换一个要求")


def test_only_first_confirm_request_can_claim_workflow_start(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "修复登录问题")
    proposal_id = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]["id"]
    store.confirm_task(proposal_id)

    first = store.claim_workflow_start(proposal_id, "claim-1", "task-proposal-1")
    second = store.claim_workflow_start(proposal_id, "claim-2", "task-proposal-1")

    assert first["proposed_task"]["start_claim_id"] == "claim-1"
    assert first["proposed_task"]["start_run_id"] == "task-proposal-1"
    assert second["proposed_task"]["start_claim_id"] == "claim-1"
    assert second["status"] == "workflow_starting"


def test_workflow_start_claim_is_atomic_across_processes(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "修复登录问题")
    proposal_id = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]["id"]
    store.confirm_task(proposal_id)
    context = multiprocessing.get_context("fork")
    gate = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_from_process,
            args=(tmp_path, proposal_id, f"claim-{index}", gate, results),
        )
        for index in range(8)
    ]
    for process in processes:
        process.start()
    gate.set()
    claims = [results.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)

    assert len(set(claims)) == 1
    assert store.read()["proposed_task"]["start_claim_id"] == claims[0]


def test_dead_workflow_start_owner_is_marked_indeterminate_instead_of_retried(tmp_path):
    store = session_store(tmp_path)
    store.append_message("message-1", "user", "修复登录问题")
    proposal_id = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]["id"]
    store.confirm_task(proposal_id)
    run_id = f"task-{proposal_id}"
    store.claim_workflow_start(proposal_id, "dead-claim", run_id, owner_pid=999_999_999)

    recovered = store.mark_workflow_start_indeterminate(proposal_id, reason="owner exited")

    assert recovered["status"] == "workflow_start_indeterminate"
    assert recovered["proposed_task"]["start_claim_id"] == "dead-claim"
    assert recovered["proposed_task"]["start_status"] == "indeterminate"
    assert recovered["proposed_task"]["start_error"] == "owner exited"
