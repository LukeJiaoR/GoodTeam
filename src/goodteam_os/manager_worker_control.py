"""Explicit project-chat supervision through the existing Pi control protocol.

A command acknowledgement is not a Worker reply, result or accepted delivery.
"""
from hashlib import sha256
from pathlib import Path
import re


def _command(text):
    parts = text.strip().split(maxsplit=3)
    if (len(parts) != 4 or parts[0] != "/worker" or parts[2] not in {"steer", "follow_up", "abort"}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", parts[1])
            or not parts[3].strip()):
        raise ValueError("Use /worker JOB_ID steer|follow_up|abort MESSAGE_OR_REASON.")
    return {"job_id": parts[1], "action": parts[2],
            "message": parts[3] if parts[2] != "abort" else "", "reason": parts[3] if parts[2] == "abort" else ""}


def _target(root, project_key, session, command, context_run_id):
    from goodteam_os.task_assistant import worker_invocation_context
    from goodteam_os.workers import SESSION_BACKENDS

    context = worker_invocation_context(root, project_key, session)
    target = next((row for row in context.get("current", []) if row["job_id"] == command["job_id"]), None)
    if target is None:
        raise ValueError("The exact job is not a current invocation of this project; historical jobs cannot be controlled.")
    if (target.get("current_dispatch_attempt") is False
            or context_run_id and context_run_id not in {target.get("run_id"), target.get("batch_run_id")}):
        raise ValueError("The control target is not the current referenced execution attempt.")
    batch = session.get("project_task_batches", {}).get(target.get("batch_run_id"), {})
    if not batch and command["action"] != "abort":
        raise ValueError("Instruction control currently requires an invocation bound to a confirmed project task batch.")
    if batch.get("status") in {"abandoned", "delivered"} and command["action"] != "abort":
        raise ValueError("The original batch ended; its invocation cannot receive new instructions.")
    grant_id = batch.get("expected_continuation_id")
    grant = session.get("project_continuation") or {}
    if (grant_id and (grant.get("id") != grant_id or grant.get("status") in {"stopped", "completed", "exhausted"})
            and command["action"] != "abort"):
        raise ValueError("The original continuation ended; no new instruction may revive it.")
    if target.get("adapter") not in SESSION_BACKENDS:
        raise ValueError("This invocation's Runtime does not support persistent session control.")
    return target


def control_context(session):
    """Recorded acknowledgements only; no polling or implied Worker reply."""
    return {"command_help": "/worker JOB_ID steer|follow_up|abort MESSAGE_OR_REASON",
        "scope": "Explicit user command only. Not an autonomous Manager action. Existing task scope and Runtime admission still apply.",
        "receipts": [item["details"]["worker_control"] for item in session.get("timeline", [])
                     if isinstance(item, dict) and isinstance(item.get("details"), dict)
                     and isinstance(item["details"].get("worker_control"), dict)][-10:],
        "interpretation": "Recorded queued/rpc_accepted is not proof of execution, reply or completion. No automatic resend or Runtime polling."}


def handle_worker_command(service, *, project_key, root, text, message_id, context_run_id="", attachments=None, expected_attempt=None):
    from goodteam_os.assistant_requests import conversation_lock, response, update_request
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.workers import WorkerJobStore

    if attachments or not isinstance(context_run_id, str) or not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("Worker control needs a message identity and cannot carry attachments.")
    message_id = message_id.strip()
    command = _command(text)
    control_id = "control-chat-" + sha256((project_key + "\0" + message_id).encode()).hexdigest()
    store, workers = TaskAssistantSessionStore(service.workspace, project_key), WorkerJobStore(Path(root))
    identity = {"text": text, "context_run_id": context_run_id, "worker_command": command, "control_id": control_id}
    # Bind exact bytes once in the existing request ledger; release the chat
    # lock before waiting for Runtime ack. A concurrent duplicate never sends.
    with conversation_lock(store):
        previous = store.read().get("assistant_requests", {}).get(message_id)
        if previous:
            if any(previous.get(key) != value for key, value in identity.items()):
                raise ValueError("This message identity is already bound to a different Worker command.")
            return response(service, project_key, root, intent="ambiguous", recover_requests=False)
        store.append_message(message_id, "user", text)
        update_request(store, message_id, **identity, attachments=[], attempt=1, status="running", phase="worker_control",
                       intent="ambiguous", error=None)
    result = {"job_id": command["job_id"], "action": command["action"], "control_id": control_id,
              "accepted": False, "control_state": "not_sent"}
    attempted = False
    try:
        def admit(session):
            nonlocal attempted, result
            # Serialize final authorization with Stop's mutation. Only local
            # validation/enqueue occurs here; the potentially slow ack wait is
            # outside the store lock. Commands admitted before Stop retain their
            # historical receipt, never reactivate the continuation.
            _target(root, project_key, session, command, context_run_id)
            existing = workers.find_project_session_control(control_id, project_key=project_key)
            if existing is not None:
                job_id, original = existing
                if (job_id != command["job_id"] or any(original.get(key, "") != command[key] for key in ("action", "message", "reason"))
                        or original.get("source") != "project_chat" or original.get("actor") != "user"):
                    raise ValueError("The durable Runtime control identity conflicts with this exact command.")
            attempted = True
            result = workers.session_control(command["job_id"], command["action"], message=command["message"],
                reason=command["reason"], actor="user", source="project_chat", control_id=control_id,
                audit_context={"project_key": project_key}, wait_for_ack=False)
            session["assistant_requests"][message_id]["phase"] = "worker_control_enqueued"
            return session
        store._mutate(admit)
        if result.get("control_state") == "queued":
            result = workers.session_control_result(command["job_id"], control_id, command["action"])
    except (OSError, ValueError) as exc:
        # A transport/storage exception may occur after enqueue. Never infer
        # non-delivery or automatically resubmit an uncertain command.
        result.update(control_state="unconfirmed" if attempted else "rejected", error=str(exc)[:1000])
    receipt = {"job_id": command["job_id"], "action": command["action"], "control_id": control_id,
               "control_state": str(result.get("control_state", "unconfirmed")),
               "accepted": result.get("accepted") is True, "error": str(result.get("error") or "")[:1000],
               "worker_reply_received": False, "execution_completed": False}
    state = receipt["control_state"]
    chinese = any("\u3400" <= c <= "\u9fff" for c in text)
    if state == "rpc_accepted":
        content = "Runtime 已接收控制请求；这不代表 Worker 已执行、回复或完成。" if chinese else "Runtime accepted the control request; this does not prove Worker execution, reply or completion."
    elif state == "queued":
        content = "请求已保存，尚未确认 Runtime 接收；不会自动重发。" if chinese else "Request saved; Runtime acceptance is not yet confirmed. It will not be automatically resent."
    else:
        content = "控制请求未获确认；未自动重发。" if chinese else "Control request not confirmed; no automatic resend."
        if receipt["error"]:
            content += " " + receipt["error"]
    content = command["job_id"] + ": " + content
    store.append_event("worker-control:" + message_id, "conversation_replied", content=content,
        details={"worker_control": receipt, "actions_applied": state == "rpc_accepted"})
    update_request(store, message_id, status="succeeded", phase="worker_control_recorded", worker_control=receipt)
    return response(service, project_key, root, intent="ambiguous", recover_requests=False)
