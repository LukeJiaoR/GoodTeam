"""Bound a Human answer to an existing discovery decision, not new authority.

Questions and receipts live in the original continuation session. The existing
continuation service owns every model call and Worker dispatch; this adapter only
records an answer through the ordinary project conversation.
"""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import re


def _question_id(value, pending):
    from goodteam_os.project_outcome_discovery import _digest

    return "human-" + _digest({"continuation_id": value["id"],
        "authorization_digest": value["authorization_digest"],
        "packet_digest": pending["packet_digest"], "selection": pending["selection"]})[7:39]


def _validated_record(value, record):
    from goodteam_os.project_outcome_discovery import _digest

    try:
        planning = record["planning"]
        packet = planning["packet"]
        if (record["id"] != _question_id(value, planning)
                or planning["packet_digest"] != packet["packet_digest"]
                or packet["packet_digest"] != _digest({key: item for key, item in packet.items() if key != "packet_digest"})):
            raise ValueError("The retained Human question identity changed.")
    except (KeyError, TypeError) as exc:
        raise ValueError("The retained Human question is incomplete.") from exc
    return record


def pending_questions(value):
    """Questions are retained Context, not a project-wide execution gate."""
    records = []
    for identifier, record in (value.get("human_questions") or {}).items():
        record = _validated_record(value, record)
        if record["id"] != identifier:
            raise ValueError("The retained Human question identity changed.")
        if record.get("answer") is None:
            records.append(record)
    return records


def question_context(value):
    return [{"id": record["id"], **record["planning"]["selection"]["human_question"],
             "source_packet_digest": record["planning"]["packet_digest"]}
            for record in pending_questions(value)]


def human_feedback(value):
    """Retain prior replies as contextual statements, never grants or proof."""
    from goodteam_os.project_outcome_discovery import _digest

    result = []
    for record in (value.get("human_questions") or {}).values():
        record = _validated_record(value, record)
        answer = record.get("answer")
        if answer is None:
            continue
        planning = record["planning"]
        if (not isinstance(answer, dict) or answer.get("question_id") != record["id"]
                or answer.get("continuation_id") != value["id"]
                or answer.get("project_key") != planning["packet"]["project_key"]
                or answer.get("question") != planning["selection"]["human_question"]
                or answer.get("source_packet_digest") != planning["packet_digest"]
                or answer.get("authorization_digest") != value["authorization_digest"]
                or answer.get("receipt_id") != _digest({key: item for key, item in answer.items() if key != "receipt_id"})):
            raise ValueError("The retained Human answer no longer matches its original question.")
        result.append(deepcopy(answer))
    return result


def validate_human_input(value, packet):
    retained = {item["receipt_id"]: item for item in human_feedback(value)}
    for answer in packet.get("human_feedback", []):
        if retained.get(answer.get("receipt_id")) != answer:
            raise ValueError("Human feedback changed during the reserved decision.")
    for answer in packet.get("human_answers", []):
        if (retained.get(answer.get("receipt_id")) != answer
                or answer not in packet.get("human_feedback", [])):
            raise ValueError("The answer trigger is not part of the captured Human feedback.")
    for question in packet.get("human_questions", []):
        record = _validated_record(value, (value.get("human_questions") or {}).get(question.get("id")))
        if question != {"id": record["id"], **record["planning"]["selection"]["human_question"],
                        "source_packet_digest": record["planning"]["packet_digest"]}:
            raise ValueError("The captured Human question changed during the reserved decision.")


def record_human_question(store, value, pending):
    from goodteam_os.project_outcome_discovery import _current

    identifier = _question_id(value, pending)
    # Publication adds task_id to the outbox. It is not part of the question's
    # immutable decision, and a replay must preserve any answer already saved.
    planning = {key: deepcopy(item) for key, item in pending.items() if key != "task_id"}
    record = {"id": identifier, "planning": planning, "answer": None}

    def save(session):
        current = _current(session, value)
        if current.get("discovery_pending") != pending:
            raise ValueError("The pending decision changed before requesting Human input.")
        questions = current.setdefault("human_questions", {})
        if identifier in questions and questions[identifier]["planning"] != planning:
            raise ValueError("This Human question already has a different retained result.")
        questions.setdefault(identifier, record)
        return session

    # The existing session mutation also publishes the current handoff notice.
    return store._mutate(save)["project_continuation"]


def handle_answer_message(service, *, project_key, root, text, message_id,
                          context_run_id="", attachments=None, expected_attempt=None):
    """Explicit /answer binding; ordinary conversation never grants consent.

    Persist the answer and visible acknowledgement together. An interruption
    after this commit is resumed by the existing active-continuation service.
    Never invoke a model or acquire the queue while holding conversation locks.
    """
    if context_run_id or attachments or expected_attempt is not None:
        raise ValueError("Answer the current project question without a historical task, attachment or runtime retry.")
    match = re.fullmatch(r"/answer\s+(human-[0-9a-f]{32})\s+([\s\S]+)", text.strip())
    if match is None or not 1 <= len(match[2].strip()) <= 8000:
        raise ValueError("Use /answer <question-id> <answer of 1-8000 characters> from the current question.")
    identifier, answer_text = match[1], match[2]
    if not isinstance(message_id, str) or not 1 <= len(message_id.strip()) <= 256:
        raise ValueError("A stable answer message ID is required.")
    message_id = message_id.strip()
    from goodteam_os.assistant_requests import conversation_lock, response
    from goodteam_os.goal_continuation import authority_snapshot, goal_guard
    from goodteam_os.project_iteration_continuation import _scope_tasks
    from goodteam_os.project_outcome_discovery import _current, _digest
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore

    store = TaskAssistantSessionStore(service.workspace, project_key)
    with goal_guard(service.workspace, project_key), conversation_lock(store):
        session = store.read()
        previous = session.get("assistant_requests", {}).get(message_id)
        if previous is not None:
            if (previous.get("human_question_id") == identifier and previous.get("text") == text
                    and previous.get("status") == "succeeded"):
                # A receipt replay after Stop is read-only, never a new tick.
                return response(service, project_key, root, intent="project_question")
            raise ValueError("message_id is already used with different content or context")
        if any(item.get("message_id") in {message_id, "human-answer:" + message_id}
               for item in session.get("timeline", [])):
            raise ValueError("message_id is already present in project history")
        value = session.get("project_continuation") or {}
        record = next((item for item in pending_questions(value) if item["id"] == identifier), None)
        if record is None:
            raise ValueError("The Human question is unavailable, already answered or no longer current.")
        _current(session, value)
        if service.project_config is None:
            raise ValueError("The original project configuration is unavailable.")
        _scope_tasks(value, LocalTaskStore(root, project_key).list_tasks())
        if (record["planning"]["packet"]["project_key"] != project_key
                or value["authority"] != authority_snapshot(service.workspace, project_key,
                                                            Path(service.project_config), Path(root))):
            raise ValueError("The question's project or current authority changed.")
        question = record["planning"]["selection"]["human_question"]
        answer = {"question_id": identifier, "project_key": record["planning"]["packet"]["project_key"],
                  "continuation_id": value["id"], "authorization_digest": value["authorization_digest"],
                  "question": question, "source_packet_digest": record["planning"]["packet_digest"],
                  "message_id": message_id, "text": answer_text, "authority_changed": False}
        answer["receipt_id"] = _digest(answer)
        now = datetime.now(UTC).isoformat()

        def save(current_session):
            current = _current(current_session, value)
            if (current.get("human_questions") or {}).get(identifier) != record:
                raise ValueError("The Human question changed while recording the answer.")
            if current["authority"] != authority_snapshot(service.workspace, project_key,
                                                          Path(service.project_config), Path(root)):
                raise ValueError("The current project authority changed before recording the answer.")
            current["human_questions"][identifier]["answer"] = answer
            current["reason"] = "human_answer_received"
            current_session.setdefault("assistant_requests", {})[message_id] = {
                "text": text, "context_run_id": "", "attachments": [], "attempt": 1,
                "status": "succeeded", "phase": "applying", "intent": "project_question",
                "human_question_id": identifier, "human_answer_receipt": answer["receipt_id"], "updated_at": now}
            current_session["timeline"].append({"message_id": message_id, "actor": "user", "content": text,
                "run_id": "", "created_at": now, "details": {"human_question_id": identifier}})
            chinese = any("\u3400" <= char <= "\u9fff" for char in text + question["question"])
            current_session["timeline"].append({"message_id": "human-answer:" + message_id,
                "actor": "task_assistant", "event": "conversation_replied", "run_id": "", "created_at": now,
                "content": ("回答已关联原问题。原项目循环将按剩余授权继续判断；未增加权限或额度。" if chinese else
                            "Answer linked to its original question. The existing project loop can resume within remaining authority; no scope or budget was added."),
                "details": {"question_id": identifier, "answer_receipt_id": answer["receipt_id"], "actions_applied": False}})
            return current_session

        store._mutate(save)
    # Do not issue a second chat-model call here. Existing periodic ticks consume
    # unconsumed answer receipts with the latest Context, even after a process
    # restart or a lost HTTP response. A replay cannot add another turn.
    return response(service, project_key, root, intent="project_question")
