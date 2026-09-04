import json
from importlib import import_module

import pytest


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
