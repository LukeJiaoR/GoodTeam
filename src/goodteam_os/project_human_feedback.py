"""Bound a Human answer to an existing discovery decision, not new authority.

Questions and receipts live in the original continuation session. The existing
continuation service owns every model call and Worker dispatch; this adapter only
records an answer through the ordinary project conversation.
"""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import re


def validate_human_question(question, *, allow_impact=True):
    fields = {"question", "why_human"}
    if (not isinstance(question, dict) or not fields.issubset(question)
            or set(question) - fields - ({"impact"} if allow_impact else set())
            or any(not isinstance(question[key], str) or not 10 <= len(question[key].strip()) <= 1200 for key in fields)
            or "impact" in question and (not isinstance(question["impact"], str)
                                         or not 1 <= len(question["impact"].strip()) <= 1200)):
        raise ValueError("A Human question needs a specific question and why only Human input can resolve it.")
    return {key: item.strip() for key, item in question.items()}


def retain_observation_question(current, *, project_key, observation_id, record):
    """Called inside current observation publication's atomic session mutation.

    This is an observation source, never a fabricated discovery reservation.
    The caller owns current-binding, Stop and authority checks.
    """
    from goodteam_os.project_outcome_discovery import _digest

    question = validate_human_question(record["advice"]["human_question"])
    for existing in pending_questions(current):
        if existing["planning"]["selection"]["human_question"]["question"] == question["question"]:
            return existing["id"]  # Retain the original question, including its provenance.
    packet = {"schema_version": "running_human_question_source.v1", "project_key": project_key,
              "continuation_id": current["id"], "authorization_digest": current["authorization_digest"],
              "observation_id": observation_id, "observation": deepcopy(record["packet"])}
    packet["packet_digest"] = _digest(packet)
    planning = {"source_kind": "running_observation", "packet": packet, "packet_digest": packet["packet_digest"],
                "selection": {"human_question": question, "rationale": record["advice"]["rationale"]}}
    identifier = _question_id(current, planning)
    questions = current.setdefault("human_questions", {})
    previous = questions.get(identifier)
    if previous and previous["planning"] != planning:
        raise ValueError("Observation question identity already owns different evidence.")
    questions.setdefault(identifier, {"id": identifier, "planning": planning, "answer": None,
                                     "created_at": datetime.now(UTC).isoformat()})
    return identifier


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
        if record.get("answer") is None and not record.get("resolution"):
            records.append(record)
    return records


def question_context(value):
    return [{"id": record["id"], **record["planning"]["selection"]["human_question"],
             "source_packet_digest": record["planning"]["packet_digest"]}
            for record in pending_questions(value)]


def question_board(session):
    """Read the retained questions; neither an answer nor its consumption closes one."""
    current = session.get("project_continuation") or {}
    grants = dict(session.get("project_continuation_history") or {})
    if current.get("id"):
        grants[current["id"]] = current
    items = []
    for value in grants.values():
        answers = {item["question_id"]: item for item in human_feedback(value)}
        for identifier, record in (value.get("human_questions") or {}).items():
            record = _validated_record(value, record)
            if identifier != record["id"]:
                raise ValueError("The retained Human question identity changed.")
            planning = record["planning"]
            question = planning["selection"]["human_question"]
            answer = answers.get(identifier)
            resolution = record.get("resolution")
            active = value.get("id") == current.get("id")
            status = resolution["status"] if resolution else "answered" if answer else "awaiting_answer"
            items.append({"id": identifier, "continuation_id": value["id"],
                "question": question["question"], "why_human": question["why_human"],
                "impact": question.get("impact", ""), "rationale": planning["selection"]["rationale"],
                "status": status, "answer": deepcopy(answer), "resolution": deepcopy(resolution),
                "created_at": record.get("created_at", ""), "current": active,
                "can_answer": active and value.get("status") == "active" and status == "awaiting_answer",
                "considered": bool(answer and answer["receipt_id"] in value.get("discovery_consumed", []))})
    return {"schema_version": "project_question_board.v1", "items": items}


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
    planning = {key: deepcopy(item) for key, item in pending.items() if key not in {"task_id", "task_ids"}}
    record = {"id": identifier, "planning": planning, "answer": None,
              "created_at": datetime.now(UTC).isoformat()}

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


def record_question_updates(store, value, pending):
    """Apply explicit Manager conclusions alongside the original decision outbox."""
    from goodteam_os.project_outcome_discovery import _current, validate_discovered_task

    selection = validate_discovered_task(pending["packet"], pending["selection"])
    updates = selection.get("question_updates", [])
    if not updates:
        return value
    event_id = "question-decision:" + value["id"] + ":" + pending["packet_digest"]
    decision = {key: deepcopy(item) for key, item in pending.items() if key not in {"task_id", "task_ids"}}
    details = {"continuation_id": value["id"], "decision": decision}
    captured = {item["id"]: item for item in pending["packet"]["question_board"]["items"]}

    def save(session):
        current = _current(session, value)
        if current.get("discovery_pending") != pending:
            raise ValueError("The pending decision changed before updating its questions.")
        previous = next((item for item in session["timeline"] if item.get("message_id") == event_id), None)
        if previous and previous.get("details") != details:
            raise ValueError("This question decision already has different retained evidence.")
        now = previous["created_at"] if previous else datetime.now(UTC).isoformat()
        lines = []
        for update in updates:
            item = captured[update["question_id"]]
            origin = current if item["continuation_id"] == current["id"] else (
                session.get("project_continuation_history") or {}).get(item["continuation_id"])
            if not origin:
                raise ValueError("The captured question's original continuation is unavailable.")
            record = _validated_record(origin, (origin.get("human_questions") or {}).get(update["question_id"]))
            if record["id"] != item["id"]:
                raise ValueError("The captured question identity changed.")
            if item["answer"] and item["answer"] not in human_feedback(origin):
                raise ValueError("The captured question's answer changed before its conclusion.")
            if not previous and record.get("resolution") != item["resolution"]:
                raise ValueError("The question conclusion changed after this decision was captured.")
            resolution = {"status": update["status"], "reason": update["reason"],
                          "decision_continuation_id": current["id"],
                          "decision_packet_digest": pending["packet_digest"],
                          "source_event_id": event_id, "updated_at": now}
            if previous and record.get("resolution") != resolution:
                raise ValueError("The retained question conclusion changed during replay.")
            record["resolution"] = resolution
            lines.append(record["planning"]["selection"]["human_question"]["question"] + "\n" + update["reason"])
        if not previous:
            session["timeline"].append({"message_id": event_id, "event": "project_questions_updated",
                "actor": "task_assistant", "content": "\n\n".join(lines), "run_id": "",
                "details": details, "created_at": now})
        return session

    return store._mutate(save)["project_continuation"]


def handle_answer_message(service, *, project_key, root, text, message_id,
                          context_run_id="", attachments=None, expected_attempt=None):
    """Explicit /answer binding; ordinary conversation never grants consent.

    Persist the answer and visible acknowledgement together. An interruption
    after this commit is resumed by the existing active-continuation service.
    Receipt validation and persistence use only the short session mutation lock.
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
    from goodteam_os.assistant_requests import response
    from goodteam_os.goal_continuation import authority_snapshot
    from goodteam_os.project_iteration_continuation import _scope_tasks
    from goodteam_os.project_outcome_discovery import _current, _digest
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore

    store = TaskAssistantSessionStore(service.workspace, project_key)
    def replay(session):
        previous = session.get("assistant_requests", {}).get(message_id)
        if previous is not None:
            if (previous.get("human_question_id") == identifier and previous.get("text") == text
                    and previous.get("status") == "succeeded"):
                return True
            raise ValueError("message_id is already used with different content or context")
        return False

    # A saved receipt replay after Stop stays read-only. A concurrent first
    # receipt is checked again inside the atomic write, alongside all identity,
    # task-scope and authority checks; no planning/queue operation lock is held.
    if replay(store.read()):
        return response(service, project_key, root, intent="project_question", recover_requests=False)

    def save(session):
        if replay(session):
            return session
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
        now = datetime.now(UTC).isoformat()
        answer = {"question_id": identifier, "project_key": record["planning"]["packet"]["project_key"],
                  "continuation_id": value["id"], "authorization_digest": value["authorization_digest"],
                  "question": question, "source_packet_digest": record["planning"]["packet_digest"],
                  "message_id": message_id, "text": answer_text, "authority_changed": False, "received_at": now}
        answer["receipt_id"] = _digest(answer)
        value["human_questions"][identifier]["answer"] = answer
        value["reason"] = "human_answer_received"
        session.setdefault("assistant_requests", {})[message_id] = {
            "text": text, "context_run_id": "", "attachments": [], "attempt": 1,
            "status": "succeeded", "phase": "applying", "intent": "project_question",
            "human_question_id": identifier, "human_answer_receipt": answer["receipt_id"], "updated_at": now}
        session["timeline"].append({"message_id": message_id, "actor": "user", "content": text,
            "run_id": "", "created_at": now, "details": {"human_question_id": identifier}})
        chinese = any("\u3400" <= char <= "\u9fff" for char in text + question["question"])
        session["timeline"].append({"message_id": "human-answer:" + message_id,
            "actor": "task_assistant", "event": "conversation_replied", "run_id": "", "created_at": now,
            "content": ("回答已关联原问题。原项目循环将按剩余授权继续判断；未增加权限或额度。" if chinese else
                        "Answer linked to its original question. The existing project loop can resume within remaining authority; no scope or budget was added."),
            "details": {"question_id": identifier, "answer_receipt_id": answer["receipt_id"], "actions_applied": False}})
        return session

    store._mutate(save)
    # Do not issue a second chat-model call here. Existing periodic ticks consume
    # unconsumed answer receipts with the latest Context, even after a process
    # restart or a lost HTTP response. A replay cannot add another turn.
    return response(service, project_key, root, intent="project_question", recover_requests=False)
