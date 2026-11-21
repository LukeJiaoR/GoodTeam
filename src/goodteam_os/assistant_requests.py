from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from goodteam_os.assistant_conversation import continue_conversation
from goodteam_os.assistant_runtime import record_runtime, runtime_snapshot, safe_failure
from goodteam_os.manager_execution import ManagerExecutionError, run_manager_chat_turn
from goodteam_os.project_readiness import assess_project_mode
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.project_runtime import normalize_project_key
from goodteam_os import state_documents
from goodteam_os.database import database_configured


@contextmanager
def conversation_lock(store, *, blocking=False):
    with state_documents.document_lock(store.path.with_suffix(".operation.lock"), blocking=blocking):
        with _file_conversation_lock(store, blocking=blocking):
            yield


@contextmanager
def _file_conversation_lock(store, *, blocking=False):
    path = store.path.with_suffix(".operation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise state_documents.DocumentLockBusy("Task Assistant is busy. Reload before retrying.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def serialized_decision(method):
    @wraps(method)
    def wrapped(self, *, project_key, proposal_id):
        store = TaskAssistantSessionStore(self.workspace, project_key)
        with conversation_lock(store):
            return method(self, project_key=project_key, proposal_id=proposal_id)
    return wrapped


def update_request(store, message_id, **values):
    def mutate(session):
        requests = session.setdefault("assistant_requests", {})
        requests[message_id] = {**requests.get(message_id, {}), **values, "updated_at": datetime.now(UTC).isoformat()}
        record = requests[message_id]
        # A completion/failure racing with Stop cannot revive this attempt.
        if record.get("stop_requested") and record.get("status") in {"succeeded", "failed", "interrupted"}:
            record.update(status="stopped", error=None)
        attempts = record.setdefault("attempts", [])
        entry = {key: record[key] for key in ("attempt", "status", "phase", "error", "updated_at") if key in record}
        if attempts and attempts[-1]["attempt"] == record["attempt"]:
            attempts[-1] = entry
        else:
            attempts.append(entry)
        return session
    return store._mutate(mutate)


# A request owns its execution, not the whole project conversation. This also
# covers the interval between durable acceptance and binding a native turn.
_ACTIVE_REQUESTS: set[tuple[str, str, int]] = set()
_ACTIVE_CHECKS: set[tuple[str, str]] = set()
_ACTIVE_GUARD = threading.Lock()


def _request_key(store, message_id, attempt):
    return str(store.path.resolve()), message_id, attempt


@contextmanager
def _active_control_request(service, project_key, message_id):
    store = TaskAssistantSessionStore(service.workspace, project_key)
    key = _request_key(store, str(message_id).strip(), 1)
    with _ACTIVE_GUARD:
        owned = key not in _ACTIVE_REQUESTS
        _ACTIVE_REQUESTS.add(key)
    try:
        yield
    finally:
        if owned:
            with _ACTIVE_GUARD:
                _ACTIVE_REQUESTS.discard(key)


def _process_start(pid):
    try:
        # Linux process start ticks distinguish a recycled PID after a restart.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _owner_alive(request):
    pid = request.get("owner_pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    token = request.get("owner_process_start")
    return not token or _process_start(pid) == token


def recover_interrupted(store, *, project_config=None):
    """Reconcile native execution identity without acquiring a model-call lock.

    Live in-process requests are authoritative even before thread/start. On a
    restart, ask the native runtime about the saved request rather than treating
    a free conversation lock as evidence that every running request is dead.
    Unknown terminal outcomes stay interrupted; reads never replay tool calls.
    """
    for key, request in store.read().get("assistant_requests", {}).items():
        if request.get("status") != "running":
            continue
        attempt = request.get("attempt", 1)
        with _ACTIVE_GUARD:
            local = _request_key(store, key, attempt) in _ACTIVE_REQUESTS
        if local:
            continue
        from goodteam_os.manager_app_server import request_liveness

        native = request_liveness(project_config=project_config, project_key=store.project_key, logical_id=key)
        if native == "active" or native in {"unknown", "completed"} and _owner_alive(request):
            continue
        if native == "completed" and _recover_native_reply(store, key, attempt):
            continue
        status = "stopped" if native == "stopped" or request.get("stop_requested") else "interrupted"
        code = "manager_completion_unconfirmed" if native == "completed" else status

        def reconcile(session, message_id=key, expected_attempt=attempt, next_status=status, error_code=code):
            current = session.get("assistant_requests", {}).get(message_id, {})
            if current.get("status") == "running" and current.get("attempt") == expected_attempt:
                current.update(status=next_status, error={"code": error_code}, updated_at=datetime.now(UTC).isoformat())
                attempts = current.get("attempts", [])
                if attempts and attempts[-1].get("attempt") == expected_attempt:
                    attempts[-1].update(status=next_status, error=current["error"], updated_at=current["updated_at"])
            return session
        store._mutate(reconcile)
    checked = store.read().get("runtime_verification") or {}
    with _ACTIVE_GUARD:
        checking = (str(store.path.resolve()), str(checked.get("check_id", ""))) in _ACTIVE_CHECKS
    if checked.get("status") == "checking" and not checking and not _owner_alive(checked):
        record_runtime(store, checked, "interrupted")


def _recover_native_reply(store, message_id, attempt):
    """Publish an already completed native reply without replaying any tools."""
    def reconcile(session):
        request = session.get("assistant_requests", {}).get(message_id, {})
        if request.get("status") != "running" or request.get("attempt") != attempt:
            return session
        if request.get("stop_requested"):
            request.update(status="stopped", error=None)
            return session
        native = session.get("manager_app_server", {}).get("requests", {}).get(message_id, {})
        result = native.get("result")
        if native.get("status") != "completed" or not isinstance(result, dict):
            return session
        execution = result.get("manager_execution")
        if (not isinstance(result.get("text"), str) or not isinstance(execution, dict)
                or execution.get("adapter") != "codex-app-server" or execution.get("logical_id") != message_id
                or execution.get("status") != "completed" or native.get("phase") != "conversation"
                or native.get("attempt_id") != worker_attempt_id(store.project_key, message_id, attempt)
                or not native.get("thread_id") or not native.get("turn_id")
                or execution.get("thread_id") != native["thread_id"]
                or execution.get("turn_id") != native["turn_id"]):
            return session  # A different attempt or native turn cannot publish this reply.
        if not result.get("reply_suppressed"):
            text = str(result.get("text") or result.get("response") or "").strip()
            if not text:
                return session
            expected = {"message_id": "reply:" + message_id, "actor": "task_assistant",
                        "event": "conversation_replied", "content": text, "run_id": "",
                        "details": {"manager_execution": dict(result.get("manager_execution") or {}),
                                    "native_manager": True}}
            timeline = session.setdefault("timeline", [])
            existing = next((item for item in timeline if item.get("message_id") == expected["message_id"]), None)
            if existing is not None and any(existing.get(key) != value for key, value in expected.items()):
                return session  # A different receipt cannot be overwritten by recovery.
            if existing is None:
                timeline.append({**expected, "created_at": native.get("completed_at") or datetime.now(UTC).isoformat()})
        request.update(status="succeeded", intent="project_question", error=None, updated_at=datetime.now(UTC).isoformat())
        attempts = request.get("attempts", [])
        if attempts and attempts[-1].get("attempt") == attempt:
            attempts[-1].update(status="succeeded", error=None, updated_at=request["updated_at"])
        return session
    session = store._mutate(reconcile)
    return session.get("assistant_requests", {}).get(message_id, {}).get("status") in {"succeeded", "stopped"}


def response(service, project_key, root, *, intent="ambiguous", recover_requests=True):
    session = (service.session(project_key, root) if recover_requests
               else service.session(project_key, root, recover_requests=False))
    return {"session": session, "task_assistant_session": session,
            "intent": intent, "run_id": ""}


def execute_message(service, *, project_key, root, text, message_id, expected_attempt=None,
                    context_run_id="", attachments=None, background=False):
    # Exact question IDs make this an explicit reply, not inferred approval.
    # These existing control commands keep their own receipt/permission checks.
    if isinstance(text, str) and text.strip().split(maxsplit=1)[:1] == ["/answer"]:
        from goodteam_os.project_human_feedback import handle_answer_message

        with _active_control_request(service, project_key, message_id):
            return handle_answer_message(service, project_key=project_key, root=root, text=text,
                message_id=message_id, context_run_id=context_run_id, attachments=attachments,
                expected_attempt=expected_attempt)
    if isinstance(text, str) and text.strip().split(maxsplit=1)[:1] == ["/worker"]:
        from goodteam_os.manager_worker_control import handle_worker_command

        with _active_control_request(service, project_key, message_id):
            return handle_worker_command(service, project_key=project_key, root=root, text=text,
                message_id=message_id, context_run_id=context_run_id, attachments=attachments,
                expected_attempt=expected_attempt)
    from goodteam_os.conversation_attachments import resolve_attachments

    attachment_ids = [] if attachments is None else attachments
    files = resolve_attachments(service.workspace, project_key, root, attachment_ids)
    if context_run_id != "":
        raise ValueError("Project conversation requires the project context.")
    message_id = message_id.strip()
    if not message_id or not text.strip():
        raise ValueError("Task Assistant message and message_id must not be blank")
    store = TaskAssistantSessionStore(service.workspace, project_key)
    # Admission is short and blocking: concurrent inputs wait only for the
    # acceptance transaction, never for another model or Worker to finish.
    with conversation_lock(store, blocking=True):
        previous = store.read().get("assistant_requests", {}).get(message_id)
        if previous and previous.get("attachments", []) != attachment_ids:
            raise ValueError("message_id is already used with different attachments")
        attempt = _next_attempt(previous, text, expected_attempt)
        if attempt is not None:
            store.initialize_context(assess_project_mode(root))
            store.append_message(message_id, "user", text, attachments=files if files else None)
            update_request(store, message_id, text=text, attachments=attachment_ids,
                           attempt=attempt, status="running", phase="model", error=None,
                           started_at=datetime.now(UTC).isoformat(), stop_requested=False,
                           owner_pid=os.getpid(), owner_process_start=_process_start(os.getpid()),
                           stream_text="", activity="")
            with _ACTIVE_GUARD:
                _ACTIVE_REQUESTS.add(_request_key(store, message_id, attempt))
    if attempt is None:
        return {**response(service, project_key, root, intent=(previous or {}).get("intent", "ambiguous"),
                           recover_requests=False), "accepted": True}
    arguments = dict(project_key=project_key, root=root, text=text, message_id=message_id,
                     attempt=attempt)
    if not background:
        return _execute_accepted(service, store=store, **arguments)

    def execute():
        try:
            _execute_accepted(service, store=store, **arguments)
        except ManagerExecutionError:
            pass  # The durable request already owns the sanitized failure.

    try:
        threading.Thread(target=execute, name=f"manager-input-{message_id[:32]}", daemon=True).start()
    except Exception as exc:
        _record_failure(store, message_id, exc)
        with _ACTIVE_GUARD:
            _ACTIVE_REQUESTS.discard(_request_key(store, message_id, attempt))
        raise
    return {**response(service, project_key, root, recover_requests=False), "accepted": True}


def _execute_accepted(service, *, store, project_key, root, text, message_id, attempt):
    snapshot = None
    try:
        if store.read()["assistant_requests"][message_id].get("stop_requested"):
            update_request(store, message_id, status="stopped", error=None)
            return response(service, project_key, root, recover_requests=False)
        try:
            snapshot = runtime_snapshot(service.project_config)
            attempt_id = worker_attempt_id(project_key, message_id, attempt)
            result = None
            native = service.manager_turn is run_manager_chat_turn and not service._manager_runtime_is_builtin()
            if native:
                result = service._send_native_message(project_key=project_key, root=root, text=text,
                    message_id=message_id, attempt_id=attempt_id)
            elif database_configured():
                from goodteam_os.manager_dialogue import run_dialogue

                result = run_dialogue(service, store=store, project_key=project_key, root=root,
                                      text=text, message_id=message_id, attempt_id=attempt_id)
            if result is None:
                result = continue_conversation(
                    service, store=store, project_key=project_key, root=root,
                    text=text, message_id=message_id, attempt_id=attempt_id,
                )
            if result is None:
                result = service._send_message(project_key=project_key, root=root, text=text,
                                               message_id=message_id, attempt_id=attempt_id)
        except Exception as exc:
            retryable = _record_failure(store, message_id, exc)
            if snapshot is not None and getattr(exc, "code", None) != "manager_resource_busy":
                evidence = {**snapshot, "error": safe_failure(exc)} if retryable else snapshot
                record_runtime(store, evidence, "failed" if retryable else _verification_status(snapshot))
            failure = safe_failure(exc)
            raise ManagerExecutionError(failure["message"], code=failure["code"]) from exc
        finished = update_request(store, message_id, status="succeeded", intent=result["intent"])
        stopped = finished["assistant_requests"][message_id].get("stop_requested")
        if not result.get("model_call_skipped"):
            record_runtime(store, snapshot, _verification_status(snapshot))
        # The continuation adapter owns its existing batch and queue locks and
        # authority. A stopped request must never initiate follow-up work.
        advance = None if stopped else result.get("advance_continuation")
        if advance:
            grant = store.read().get("project_continuation") or {}
            if grant.get("id") == advance and grant.get("status") == "active":
                from goodteam_os.project_iteration_continuation import tick_project_continuation

                outcome = tick_project_continuation(workspace=service.workspace, project_key=project_key,
                                                    root=root, project_config=service.project_config)
                from goodteam_os.manager_dialogue import _status

                store.append_event("continuation-followup:" + message_id, "conversation_replied",
                    content=_status({"continuation": outcome}, chinese=any("\u3400" <= char <= "\u9fff" for char in text)),
                    details={"operation": "advance_continuation", "actions_applied": True})
        return response(service, project_key, root, intent=result["intent"], recover_requests=False)
    finally:
        with _ACTIVE_GUARD:
            _ACTIVE_REQUESTS.discard(_request_key(store, message_id, attempt))


def stop_message(service, *, project_key, root, message_id, expected_attempt):
    from goodteam_os.manager_app_server import stop_manager_request

    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store, blocking=True):
        request = store.read().get("assistant_requests", {}).get(message_id)
        if request is None or type(expected_attempt) is not int or request.get("attempt") != expected_attempt:
            raise ValueError("Stop requires the current Manager request and attempt.")
        if request.get("status") == "stopped":
            return response(service, project_key, root, recover_requests=False)
        if request.get("status") != "running":
            raise ValueError("The Manager request is no longer running.")
        if request.get("phase") not in {"model", "applying"}:
            raise ValueError("This request is not a Manager response; use its original control action.")
        update_request(store, message_id, stop_requested=True)
    # Recording the stop before transport cancellation closes the startup/tool
    # race. The native runtime also checks this flag before every operation.
    receipt = stop_manager_request(project_config=service.project_config, project_key=project_key, logical_id=message_id)
    update_request(store, message_id, status="stopped", stop_receipt=receipt, error=None)
    return response(service, project_key, root, recover_requests=False)


def _next_attempt(previous, text, expected_attempt):
    if previous is None:
        if expected_attempt is not None:
            raise ValueError("Task Assistant request is unavailable")
        return 1
    if previous["text"] != text:
        raise ValueError("message_id is already used with different content")
    if previous["status"] == "succeeded":
        return None
    if expected_attempt is None:
        return None
    if type(expected_attempt) is not int or expected_attempt != previous["attempt"]:
        raise ValueError("Task Assistant attempt changed. Reload before retrying.")
    if previous["status"] != "failed":
        raise ValueError("Task Assistant request needs reconciliation before retrying")
    return previous["attempt"] + 1


def worker_attempt_id(project_key, message_id, attempt):
    identity = json.dumps([normalize_project_key(project_key), message_id, attempt]).encode()
    return f"assistant:{hashlib.sha256(identity).hexdigest()}"


def _record_failure(store, message_id, error):
    request = store.read()["assistant_requests"][message_id]
    if request.get("stop_requested"):
        update_request(store, message_id, status="stopped", error=None)
        return False
    retryable = request["phase"] == "model"
    update_request(store, message_id, status="failed" if retryable else "interrupted", error=safe_failure(error))
    return retryable


def _verification_status(snapshot):
    return "simulated" if snapshot["provider"] in {"builtin", "fake", "noop", "human"} else "verified"


def retry_message(service, *, project_key, root, message_id, expected_attempt, background=False):
    if type(expected_attempt) is not int:
        raise ValueError("Retry requires the latest attempt number.")
    message_id = message_id.strip()
    request = TaskAssistantSessionStore(service.workspace, project_key).read().get("assistant_requests", {}).get(message_id)
    if request is None:
        raise ValueError("Task Assistant request is unavailable")
    return execute_message(service, project_key=project_key, root=root, message_id=message_id,
                           attachments=request.get("attachments", []),
                           text=request["text"], expected_attempt=expected_attempt, context_run_id=request.get("context_run_id", ""),
                           background=background)


def check_runtime(service, *, project_key, root, message_id):
    if not message_id.strip():
        raise ValueError("Runtime check requires a message_id")
    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store, blocking=True):
        snapshot = runtime_snapshot(service.project_config)
        if message_id in store.read().get("runtime_checks", {}):
            return response(service, project_key, root, recover_requests=False)
        # Persist before dispatch so repeated HTTP requests cannot spend twice.
        _remember_check(store, message_id)
        snapshot = {**snapshot, "check_id": message_id, "owner_pid": os.getpid(),
                    "owner_process_start": _process_start(os.getpid())}
        record_runtime(store, snapshot, "checking")
        with _ACTIVE_GUARD:
            _ACTIVE_CHECKS.add((str(store.path.resolve()), message_id))
    try:
        if not service._manager_runtime_is_builtin():
            identity = f"runtime-check:{worker_attempt_id(project_key, message_id, 1)}"
            context = "Read-only runtime connection check. Do not propose work or Team changes."
            if service.manager_turn is run_manager_chat_turn:
                from goodteam_os.manager_execution import run_manager_analysis
                # A synthetic connection check must never steer the user's
                # foreground turn or become its latest reply recipient.
                run_manager_analysis(project_config=service.project_config, project_key=project_key,
                    logical_id=identity, task="Reply Connection verified.", context=context, phase="runtime_check")
            else:
                service.manager_turn(project_config=service.project_config, project_key=project_key,
                                     message_id=identity, text="Reply Connection verified.", context=context)
    except Exception as exc:
        record_runtime(store, {**snapshot, "error": safe_failure(exc)}, "failed")
        raise ManagerExecutionError(safe_failure(exc)["message"]) from exc
    else:
        record_runtime(store, snapshot, _verification_status(snapshot))
        return response(service, project_key, root, recover_requests=False)
    finally:
        with _ACTIVE_GUARD:
            _ACTIVE_CHECKS.discard((str(store.path.resolve()), message_id))


def _remember_check(store, message_id):
    def mutate(session):
        session.setdefault("runtime_checks", {})[message_id] = datetime.now(UTC).isoformat()
        return session
    store._mutate(mutate)
