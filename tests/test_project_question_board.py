"""One lifecycle through real file stores/locks, with explicit Manager packets.

The initial decision packets are fixtures; no model, Git dispatch or delivery is
claimed. Answer/receipt, closure, grant archival and read projection are real.
"""
from copy import deepcopy
from threading import Event, Thread

import pytest

from goodteam_os.assistant_requests import conversation_lock, execute_message
from goodteam_os.goal_continuation import goal_guard
from goodteam_os.project_continuation_notice import project_continuation_notice
from goodteam_os.project_human_feedback import (
    human_feedback, pending_questions, question_board, question_context,
    record_human_question, record_question_updates,
)
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_continuation import _save, configure_project_continuation, stop_project_continuation
from goodteam_os.project_outcome_discovery import _current, _digest, validate_discovered_task
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_question_board_requires_manager_closure_and_retains_prior_grant_history(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = tmp_path / "projects" / "demo.local.yaml"
    config.parent.mkdir()
    config.write_text(f"key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n")
    tasks = LocalTaskStore(root, "DEMO")
    task = tasks.create_task(title="Document the approved API", description="A bounded task, not executed by this test.",
        role="implementation", write_domains=["docs"], acceptance_criteria=["Document the agreed API audience."])
    original_tasks = tasks.path.read_bytes()
    store = TaskAssistantSessionStore(tmp_path, "DEMO")
    service = TaskAssistantService(tmp_path, project_config=config)

    def authorize(key):
        return configure_project_continuation(workspace=tmp_path, project_key="DEMO", root=root,
            project_config=config, request={"confirmed": True, "billing_acknowledged": True,
                "idempotency_key": key, "task_ids": [task["id"]], "max_batches": 3,
                "expected_backlog_version": project_iteration_backlog(root, "DEMO")["version"],
                "discovery": {"objective": "Resolve product questions while preserving the authorized documentation scope.",
                              "allowed_write_domains": ["docs"], "max_new_tasks": 2, "max_planning_calls": 6}})

    def packet(value):
        # An explicit decision fixture; all retained question/answer fields come
        # from production projections, including questions from earlier grants.
        captured = {"schema_version": "project_outcome_discovery_packet.v1", "project_key": "DEMO",
            "authorization_digest": value["authorization_digest"], "discovery": value["discovery"],
            "authorized_task_ids": value["task_ids"], "authorized_done_task_ids": [],
            "backlog": project_iteration_backlog(root, "DEMO"), "human_feedback_supported": True,
            "human_questions": question_context(value), "human_feedback": human_feedback(value),
            "question_board": question_board({**store.read(), "project_continuation": value})}
        captured["packet_digest"] = _digest(captured)
        return captured

    def apply_decision(selection, apply):
        with goal_guard(tmp_path, "DEMO"):
            value = store.read()["project_continuation"]
            captured = packet(value)
            pending = {"packet": captured, "packet_digest": captured["packet_digest"],
                "backlog_version": captured["backlog"]["version"],
                "selection": validate_discovered_task(captured, selection),
                "manager_execution": {"fixture": True, "external_invocation": False}}

            def reserve(session):
                current = _current(session, value)
                current.update(discovery_pending=pending, manager_calls=current["manager_calls"] + 1)
                return session

            value = store._mutate(reserve)["project_continuation"]
            return apply(store, value, pending), pending

    original = authorize("question-board-first-grant")
    questions = ["Should the API serve internal users or external customers?",
                 "Do we still need the previously proposed migration report?",
                 "Which tenant should receive the next documented example?"]
    ids = []
    for text in questions:
        grant, _ = apply_decision({"new_task": None,
            "human_question": {"question": text, "why_human": "Only the owner can settle this product decision."},
            "rationale": "Keep this specific product question available alongside ongoing authorized work."},
            record_human_question)
        ids.append(next(item["id"] for item in question_board(store.read())["items"] if item["question"] == text))
    first, second, unanswered = ids
    assert all(item["status"] == "awaiting_answer" and item["can_answer"]
               for item in question_board(store.read())["items"])

    # A paused turn holds the real continuation and queue/conversation guards.
    # Answer receipt must persist before it finishes, and its older whole-grant save
    # must retain both the answer and the next judgment's responsibility.
    captured, release = Event(), Event()
    answered = [Event(), Event()]
    failures, settled = [], []
    def paused_turn():
        try:
            with goal_guard(tmp_path, "DEMO"), conversation_lock(store):
                old = store.read()["project_continuation"]
                captured.set()
                assert release.wait(5)
                settled.append(_save(store, {**old, "status": "completed", "discovery_pending": None,
                                             "reason": "discovery_no_followup"}))
        except Exception as exc:
            failures.append(exc)
    def answer_during_turn(done):
        try:
            execute_message(service, project_key="DEMO", root=root,
                text=f"/answer {first} Keep the first release internal.", message_id="owner-answer")
        except Exception as exc:
            failures.append(exc)
        finally:
            done.set()
    manager_thread = Thread(target=paused_turn)
    answer_threads = [Thread(target=answer_during_turn, args=(done,)) for done in answered]
    manager_thread.start()
    assert captured.wait(5)
    for thread in answer_threads:
        thread.start()
    try:
        received_before_settlement = all(done.wait(1) for done in answered)
    finally:
        release.set()
        manager_thread.join(5)
        for thread in answer_threads:
            thread.join(5)
    assert received_before_settlement and not failures
    assert settled[0]["status"] == "active" and settled[0]["reason"] == "human_answer_received"
    grant = TaskAssistantSessionStore(tmp_path, "DEMO").read()["project_continuation"]
    for message_id in ("owner-answer", "human-answer:owner-answer"):
        assert len([item for item in store.read()["timeline"] if item.get("message_id") == message_id]) == 1
    answer = human_feedback(grant)[0]
    store._mutate(lambda session: {**session, "project_continuation": {
        **session["project_continuation"], "discovery_consumed": [answer["receipt_id"]]}})
    items = {item["id"]: item for item in question_board(store.read())["items"]}
    assert items[first]["status"] == "answered" and items[first]["considered"] is True
    assert items[first]["resolution"] is None and items[first]["answer"] == answer
    assert items[first]["can_answer"] is False  # Consuming a reply is not resolving its question.

    updates = [{"question_id": first, "status": "resolved", "reason": "The internal-only audience is recorded in the next task."},
               {"question_id": second, "status": "not_needed", "reason": "Current delivery evidence makes that migration report unnecessary."}]
    selection = {"new_task": None, "rationale": "Apply the owner's answer and retire the obsolete information request.",
                 "question_updates": updates}
    with pytest.raises(ValueError):
        validate_discovered_task(packet(store.read()["project_continuation"]), {
            **selection, "question_updates": [{**updates[0], "question_id": "human-" + "0" * 32}]})
    grant, decision = apply_decision(selection, record_question_updates)
    closed = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    events = [event for event in closed["timeline"] if event.get("event") == "project_questions_updated"]
    assert len(events) == 1 and events[0]["details"]["decision"] == decision
    for update in updates:
        resolution = grant["human_questions"][update["question_id"]]["resolution"]
        assert resolution["status"] == update["status"] and resolution["reason"] == update["reason"]
        assert resolution["decision_continuation_id"] == grant["id"]
        assert resolution["decision_packet_digest"] == decision["packet_digest"]
        assert resolution["source_event_id"] == events[0]["message_id"]
    before_replay = store.path.read_bytes()
    with goal_guard(tmp_path, "DEMO"):
        record_question_updates(store, grant, decision)
    assert store.path.read_bytes() == before_replay
    assert [record["id"] for record in pending_questions(grant)] == [unanswered]
    assert [item["id"] for item in project_continuation_notice(store.read())["human_questions"]] == [unanswered]
    with pytest.raises(ValueError):
        execute_message(service, project_key="DEMO", root=root,
            text=f"/answer {second} A late answer must not reopen a closed question.", message_id="closed-answer")
    assert store.path.read_bytes() == before_replay
    assert grant["authority"] == original["authority"] and grant["authorization_digest"] == original["authorization_digest"]
    assert grant["batches_started"] == 0 and tasks.path.read_bytes() == original_tasks

    stop_project_continuation(workspace=tmp_path, project_key="DEMO", continuation_id=grant["id"])
    historical = deepcopy(store.read()["project_continuation"])
    current = authorize("question-board-new-grant")
    reopened = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    assert reopened["project_continuation_history"][historical["id"]] == historical
    assert current["id"] != historical["id"] and current["status"] == "active"
    assert {item["id"] for item in packet(current)["question_board"]["items"]} == set(ids)
    before_read = store.path.read_bytes()
    board = question_board(reopened)
    assert board["schema_version"] == "project_question_board.v1"
    assert question_board(reopened) == board and store.path.read_bytes() == before_read
    items = {item["id"]: item for item in board["items"]}
    assert {key: item["status"] for key, item in items.items()} == {
        first: "resolved", second: "not_needed", unanswered: "awaiting_answer"}
    assert not any(item["current"] or item["can_answer"] for item in items.values())
    assert authorize("question-board-first-grant") == historical
    assert store.path.read_bytes() == before_read  # Old authorization replay cannot replace the new grant.
    with pytest.raises(ValueError):
        execute_message(service, project_key="DEMO", root=root,
            text=f"/answer {unanswered} A late answer cannot revive the previous grant.", message_id="historical-answer")
    assert store.path.read_bytes() == before_read and tasks.path.read_bytes() == original_tasks
    assert current["manager_calls"] == current["batches_started"] == 0

    # The current Manager can maintain historical questions without reopening
    # the old grant or accepting a new Human answer under its revoked authority.
    current_after, historical_decision = apply_decision({"new_task": None,
        "rationale": "The current documentation scope no longer needs the old tenant-specific example.",
        "question_updates": [{"question_id": unanswered, "status": "not_needed",
                              "reason": "The revised documentation uses a tenant-neutral example instead."}]},
        record_question_updates)
    revised = TaskAssistantSessionStore(tmp_path, "DEMO").read()
    archived = revised["project_continuation_history"][historical["id"]]
    resolution = archived["human_questions"][unanswered]["resolution"]
    assert resolution["status"] == "not_needed" and resolution["decision_continuation_id"] == current["id"]
    assert resolution["decision_packet_digest"] == historical_decision["packet_digest"]
    expected_history = deepcopy(historical)
    expected_history["human_questions"][unanswered]["resolution"] = resolution
    assert archived == expected_history  # Old status, budget, answers, task bindings and scope are unchanged.
    assert current_after["authorization_digest"] == current["authorization_digest"]
    assert current_after["manager_calls"] == 1 and current_after["batches_started"] == 0
    assert current_after["new_tasks_created"] == 0 and tasks.path.read_bytes() == original_tasks
    final_bytes = store.path.read_bytes()
    with goal_guard(tmp_path, "DEMO"):
        record_question_updates(store, current_after, historical_decision)
    assert store.path.read_bytes() == final_bytes
    final_board = question_board(store.read())
    assert next(item for item in final_board["items"] if item["id"] == unanswered)["status"] == "not_needed"
    assert not any(item["can_answer"] for item in final_board["items"])
    assert store.path.read_bytes() == final_bytes
