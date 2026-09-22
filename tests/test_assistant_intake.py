import json

import pytest

from goodteam_os.assistant_drafts import confirm_and_prepare, edit_draft, resolve_draft
from goodteam_os.assistant_intake import intake_source_messages
from goodteam_os.manager_execution import ManagerExecutionError, _parse_chat_turn
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_task_assistant import FakeReadiness, readiness


IDEA = "我希望做一个图片生成web app，给线上商家用，让他们生成和真实照片一样质感的商品宣传图。"
FLOW = "先做护肤品商家。上传商品实拍图，选择场景后生成宣传图。"
CRITERIA = "首版只做单张生成和下载；瓶身、标签和品牌文字不能改变，支持下载 1:1 的 PNG。"
QUESTION = "首版先服务哪类商品？是上传商品照片更换场景，还是输入文字生成图片？"


def intake(*sources, ready=True):
    return {"ready": ready, "missing_decisions": [] if ready else [QUESTION], "source_message_ids": list(sources)}


def make_service(tmp_path, turns):
    root = tmp_path / "target"
    root.mkdir()
    config = tmp_path / "demo.yaml"
    config.write_text("key: DEMO\n")
    executor = FakeReadiness(readiness("new_project"))
    calls = []

    def manager(**values):
        calls.append(values)
        return _parse_chat_turn(json.dumps(turns[len(calls) - 1], ensure_ascii=False))

    service = TaskAssistantService(tmp_path, project_config=config, readiness_executor=executor, manager_turn=manager)
    return service, root, executor, calls


def send(service, root, text, message_id):
    return service.send_message(project_key="DEMO", root=root, text=text, message_id=message_id)


def test_incomplete_assessment_blocks_premature_proposal_then_retains_all_clarifications(tmp_path):
    service, root, executor, calls = make_service(tmp_path, [
        {"intent": "work_request", "response": "方向已经足够具体。", "task_intake": intake("idea", ready=False)},
        {"intent": "ambiguous", "response": "生成图片时，哪些商品细节必须保持一致？", "task_intake": intake(ready=False)},
        {"intent": "work_request", "response": "目标：护肤品场景图。范围：上传、单张生成和下载。验收：保留商品外观，导出方形 PNG。",
         "task_intake": intake("criteria", "idea", "flow")},
    ])
    first = send(service, root, IDEA, "idea")
    assert first["intent"] == "ambiguous"
    assert first["session"]["proposed_task"] is None
    assert first["session"]["timeline"][-1]["content"] == QUESTION
    assert send(service, root, FLOW, "flow")["session"]["proposed_task"] is None
    final = send(service, root, CRITERIA, "criteria")
    proposal = final["session"]["proposed_task"]
    assert proposal["source_message_ids"] == ["idea", "flow", "criteria"]
    assert proposal["exact_request"] == "\n\n".join([IDEA, FLOW, CRITERIA])
    assert not proposal["confirmed"]
    assert not final["workflow_started"]
    assert executor.started == []
    assert IDEA in calls[-1]["context"] and '"message_id": "idea"' in calls[-1]["context"]
    replay = send(service, root, CRITERIA, "criteria")
    assert replay["session"]["proposed_task"] == proposal
    assert len(calls) == 3


@pytest.mark.parametrize("sources", [["old-task", "answer"], ["reply:idea", "answer"], ["unknown", "answer"], ["idea", "idea", "answer"], ["idea"]])
def test_invalid_or_incomplete_source_selection_never_mutates_a_draft(tmp_path, sources):
    # An omitted earlier goal is a model-quality concern, but an unknown,
    # non-user, duplicate or missing current ID must fail before persistence.
    service, root, executor, _calls = make_service(tmp_path, [
        {"intent": "ambiguous", "response": QUESTION},
        {"intent": "work_request", "response": "Task ready.", "task_intake": intake(*sources)},
    ])
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("old-task", "user", "Build an unrelated app.")
    store.append_event("old-boundary", "task_proposal_archived")
    send(service, root, IDEA, "idea")
    with pytest.raises(ManagerExecutionError):
        send(service, root, CRITERIA, "answer")
    session = store.read()
    assert session["proposed_task"] is None
    assert session["assistant_requests"]["answer"]["status"] == "failed"
    assert executor.started == []


def test_refining_existing_draft_blocks_confirmation_until_full_follow_up_is_accepted(tmp_path):
    service, root, executor, _calls = make_service(tmp_path, [
        {"intent": "ambiguous", "response": QUESTION, "task_intake": intake(ready=False)},
        {"intent": "ambiguous", "response": "下载格式和商品外观的验收标准是什么？", "task_intake": intake(ready=False)},
        {"intent": "work_request", "response": "把流程和验收条件补充到草稿。", "task_intake": intake("flow", "criteria")},
    ])
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("idea", "user", IDEA)
    original = store.propose_task(["idea"], IDEA)["proposed_task"]
    store.append_event("reply:idea", "task_proposed")
    pending = send(service, root, "我感觉这个还不够具体", "feedback")["session"]["proposed_task"]
    assert pending["needs_clarification"]
    assert pending["exact_request"] == original["exact_request"]
    assert pending["revision"] == original["revision"] + 1
    for revision in (original["revision"], pending["revision"]):
        with pytest.raises(ValueError):
            store.confirm_task(pending["id"], expected_revision=revision)
        with pytest.raises(ValueError):
            confirm_and_prepare(service, project_key="DEMO", root=root, request={
                "proposal_id": pending["id"], "expected_revision": revision,
            })
    send(service, root, FLOW, "flow")
    revised = send(service, root, CRITERIA, "criteria")["session"]["proposed_task"]
    assert revised["pending_change"]["text"] == FLOW + "\n\n" + CRITERIA
    assert revised["exact_request"] == IDEA
    assert revised["needs_clarification"]
    accepted = resolve_draft(store, {"proposal_id": revised["id"], "expected_revision": revised["revision"], "choice": "append"})["proposed_task"]
    assert accepted["exact_request"] == "\n\n".join([IDEA, FLOW, CRITERIA])
    assert accepted["source_message_ids"] == ["idea", "flow", "criteria"]
    assert not accepted.get("needs_clarification")
    assert not accepted["confirmed"]
    assert executor.started == []


def test_human_edit_can_resolve_a_draft_that_needs_clarification(tmp_path):
    service, root, _executor, _calls = make_service(tmp_path, [
        {"intent": "ambiguous", "response": QUESTION, "task_intake": intake(ready=False)},
    ])
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("idea", "user", IDEA)
    store.propose_task(["idea"], IDEA)
    pending = send(service, root, "还不够具体", "feedback")["session"]["proposed_task"]
    edited = edit_draft(store, {"proposal_id": pending["id"], "expected_revision": pending["revision"],
                               "text": FLOW + "\n\n" + CRITERIA, "message_id": "edit"})["proposed_task"]
    assert not edited.get("needs_clarification")
    assert edited["exact_request"] == FLOW + "\n\n" + CRITERIA
    assert store.confirm_task(edited["id"], expected_revision=edited["revision"])["proposed_task"]["confirmed"]


@pytest.mark.parametrize("assessment", [None, {}, {"ready": "true"},
    {"ready": True, "missing_decisions": [], "source_message_ids": []},
    {"ready": False, "missing_decisions": [], "source_message_ids": []},
    {"ready": True, "missing_decisions": [], "source_message_ids": [123]},
    {"ready": False, "missing_decisions": [""], "source_message_ids": []},
])
def test_invalid_assessment_cannot_produce_a_work_request(assessment):
    with pytest.raises(ManagerExecutionError):
        _parse_chat_turn(json.dumps({"intent": "work_request", "response": "Ready.", "task_intake": assessment}))


def test_claimed_readiness_with_unresolved_questions_stays_in_discovery():
    result = _parse_chat_turn(json.dumps({"intent": "work_request", "response": "Ready.",
                                         "task_intake": {**intake("m1", ready=False), "ready": True}}))
    assert result["intent"] == "ambiguous"
    assert result["response"] == QUESTION


def test_new_model_contract_requires_assessment_while_cached_legacy_output_remains_readable():
    output = '{"intent":"work_request","response":"Draft ready."}'
    with pytest.raises(ManagerExecutionError, match="did not assess"):
        _parse_chat_turn(output)
    assert _parse_chat_turn(output, require_task_intake=False)["intent"] == "work_request"


def test_source_context_keeps_complete_messages_beyond_the_recent_conversation_window():
    timeline = [{"actor": "user", "message_id": "goal", "content": IDEA}]
    for index in range(10):
        timeline.extend([{"actor": "task_assistant", "message_id": f"question-{index}", "content": QUESTION},
                         {"actor": "user", "message_id": f"answer-{index}", "content": f"Constraint {index}."}])
    sources = intake_source_messages({"timeline": timeline})
    assert sources[0] == {"message_id": "goal", "content": IDEA}
    assert len(sources) == 11


def test_new_model_task_after_terminal_archival_keeps_current_message_only(tmp_path):
    from goodteam_os.state import StateStore, create_initial_state

    service, root, executor, calls = make_service(tmp_path, [
        {"intent": "work_request", "response": "Add PNG export.", "task_intake": intake("new-task")},
    ])
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    store.append_message("old-task", "user", "Build a gallery.")
    old = store.propose_task(["old-task"], "Build a gallery.")["proposed_task"]
    store.confirm_task(old["id"])
    store.bind_workflow(old["id"], "old-run")
    state = create_initial_state("old-run", "DEMO", old["exact_request"])
    state.current_node = "complete"
    state.next_node = ""
    StateStore(tmp_path).write_project_current(state)
    result = send(service, root, "Add PNG export.", "new-task")
    assert result["session"]["proposed_task"]["source_message_ids"] == ["new-task"]
    assert result["session"]["proposed_task"]["exact_request"] == "Add PNG export."
    source_context = calls[-1]["context"].split("Task intake source messages: ")[1].splitlines()[0]
    assert json.loads(source_context) == [{"message_id": "new-task", "content": "Add PNG export."}]
    assert executor.started == []
