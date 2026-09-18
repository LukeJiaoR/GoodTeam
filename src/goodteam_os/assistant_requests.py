from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps

from goodteam_os.assistant_conversation import continue_conversation
from goodteam_os.assistant_runtime import record_runtime, runtime_snapshot, safe_failure
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.project_readiness import assess_project_mode
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.state import normalize_project_key


@contextmanager
def conversation_lock(store):
    path = store.path.with_suffix(".operation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Task Assistant is busy. Reload before retrying.") from exc
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
        attempts = record.setdefault("attempts", [])
        entry = {key: record[key] for key in ("attempt", "status", "phase", "error", "updated_at") if key in record}
        if attempts and attempts[-1]["attempt"] == record["attempt"]:
            attempts[-1] = entry
        else:
            attempts.append(entry)
        return session
    return store._mutate(mutate)


def recover_interrupted(store):
    try:
        with conversation_lock(store):
            for key, request in store.read().get("assistant_requests", {}).items():
                if request.get("status") == "running":
                    update_request(store, key, status="interrupted", error={"code": "interrupted"})
            checked = store.read().get("runtime_verification") or {}
            if checked.get("status") == "checking":
                record_runtime(store, checked, "interrupted")
    except ValueError:
        pass


def response(service, project_key, root, *, intent="ambiguous"):
    session = service.session(project_key, root)
    return {"session": session, "task_assistant_session": session,
            "intent": intent, "workflow_started": False, "run_id": ""}


def execute_message(service, *, project_key, root, text, message_id, expected_attempt=None):
    message_id = message_id.strip()
    if not message_id.strip() or not text.strip():
        raise ValueError("Task Assistant message and message_id must not be blank")
    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store):
        previous = store.read().get("assistant_requests", {}).get(message_id)
        attempt = _next_attempt(previous, text, expected_attempt)
        if attempt is None:
            return response(service, project_key, root, intent=(previous or {}).get("intent", "ambiguous"))
        store.initialize_context(assess_project_mode(root))
        store.append_message(message_id, "user", text)
        update_request(store, message_id, text=text, attempt=attempt, status="running", phase="model", error=None)
        snapshot = None
        try:
            snapshot = runtime_snapshot(service.project_config)
            attempt_id = worker_attempt_id(project_key, message_id, attempt)
            result = continue_conversation(
                service, store=store, project_key=project_key, root=root,
                text=text, message_id=message_id, attempt_id=attempt_id,
            )
            if result is None:
                result = service._send_message(project_key=project_key, root=root, text=text,
                                               message_id=message_id, attempt_id=attempt_id)
        except Exception as exc:
            retryable = _record_failure(store, message_id, exc)
            if snapshot is not None:
                evidence = {**snapshot, "error": safe_failure(exc)} if retryable else snapshot
                record_runtime(store, evidence, "failed" if retryable else _verification_status(snapshot))
            raise ManagerExecutionError(safe_failure(exc)["message"]) from exc
        update_request(store, message_id, status="succeeded", intent=result["intent"])
        record_runtime(store, snapshot, _verification_status(snapshot))
        return response(service, project_key, root, intent=result["intent"])


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
    retryable = request["phase"] == "model"
    update_request(store, message_id, status="failed" if retryable else "interrupted", error=safe_failure(error))
    return retryable


def _verification_status(snapshot):
    return "simulated" if snapshot["provider"] in {"builtin", "fake", "noop", "human"} else "verified"


def retry_message(service, *, project_key, root, message_id, expected_attempt):
    if type(expected_attempt) is not int:
        raise ValueError("Retry requires the latest attempt number.")
    message_id = message_id.strip()
    request = TaskAssistantSessionStore(service.workspace, project_key).read().get("assistant_requests", {}).get(message_id)
    if request is None:
        raise ValueError("Task Assistant request is unavailable")
    return execute_message(service, project_key=project_key, root=root, message_id=message_id,
                           text=request["text"], expected_attempt=expected_attempt)


def check_runtime(service, *, project_key, root, message_id):
    if not message_id.strip():
        raise ValueError("Runtime check requires a message_id")
    store = TaskAssistantSessionStore(service.workspace, project_key)
    with conversation_lock(store):
        snapshot = runtime_snapshot(service.project_config)
        if message_id in store.read().get("runtime_checks", {}):
            return response(service, project_key, root)
        # Persist before dispatch so repeated HTTP requests cannot spend twice.
        _remember_check(store, message_id)
        record_runtime(store, {**snapshot, "check_id": message_id}, "checking")
        try:
            if not service._manager_runtime_is_builtin():
                service.manager_turn(project_config=service.project_config, project_key=project_key,
                                     message_id=f"runtime-check:{worker_attempt_id(project_key, message_id, 1)}", text="Reply with intent ambiguous and response Connection verified.",
                                     context="Read-only runtime connection check. Do not propose work or Team changes.")
        except Exception as exc:
            record_runtime(store, {**snapshot, "check_id": message_id, "error": safe_failure(exc)}, "failed")
            raise ManagerExecutionError(safe_failure(exc)["message"]) from exc
        record_runtime(store, {**snapshot, "check_id": message_id}, _verification_status(snapshot))
        return response(service, project_key, root)


def _remember_check(store, message_id):
    def mutate(session):
        session.setdefault("runtime_checks", {})[message_id] = datetime.now(UTC).isoformat()
        return session
    store._mutate(mutate)
