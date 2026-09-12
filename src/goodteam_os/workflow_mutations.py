"""Serialize workflow authority and retain interrupted human decisions."""

import fcntl
import json
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from uuid import uuid4

from goodteam_os.state import StateStore, normalize_project_key


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
INTERRUPTED_TRANSITION = "Workflow execution interrupted; inspect the evidence and retry to continue."
ACTIVE_TRANSITIONS = {"recording", "executing"}


class WorkflowBusyError(ValueError):
    pass


@contextmanager
def workflow_guard(workspace, project_key):
    directory = Path(workspace).resolve() / "state" / "workflow-locks"
    path = directory / f"{normalize_project_key(project_key)}.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), threading.Lock())
    if not lock.acquire(blocking=False):
        raise WorkflowBusyError("This project's workflow is busy. Refresh before retrying.")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkflowBusyError("This project's workflow is busy. Refresh before retrying.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


def serialized_workflow(operation):
    @wraps(operation)
    def wrapped(**kwargs):
        if kwargs.get("dry_run"):
            return operation(**kwargs)
        workspace = Path(kwargs["workspace"])
        key = str(kwargs.get("project_key", ""))
        if not key:
            state = StateStore(workspace).read_current()
            key = state.project_key if state else "default"
        key = normalize_project_key(key)
        with workflow_guard(workspace, key):
            try:
                return operation(**kwargs)
            except Exception:
                interrupt_transition(workspace, key)
                raise
    return wrapped


def begin_transition(workspace, state, action):
    identifier = uuid4().hex
    action = {**action, "client_context": {
        **(action.get("client_context") or {}), "workflow_operation_id": identifier,
    }}
    state.pending_transition = {
        "id": identifier, "status": "recording", "action": action,
        "current_node": state.current_node, "next_node": state.next_node,
    }
    persist_transition(workspace, state)
    publish_decision(workspace, state)


def publish_decision(workspace, state):
    # The operation ID closes the append/write interruption window on retry.
    from goodteam_os.web import append_action

    transition = state.pending_transition
    path = StateStore(workspace).project_state_dir(state.project_key) / "action-log.jsonl"
    identifier = transition["id"]
    recorded = False
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if dict(event.get("client_context") or {}).get("workflow_operation_id") == identifier:
                recorded = True
                break
    if not recorded:
        append_action(workspace, **transition["action"])
    transition["status"] = "executing"
    persist_transition(workspace, state)


def persist_transition(workspace, state):
    store = StateStore(workspace)
    store.write_project_current(state)
    store.write_project_checkpoint(state)


def finish_transition(state):
    if state.pending_transition:
        state.pending_transition["status"] = "completed"
    state.blockers = [item for item in state.blockers if item != INTERRUPTED_TRANSITION]


def interrupt_transition(workspace, project_key):
    store = StateStore(workspace)
    state = store.read_project_current(project_key)
    if state is None or state.pending_transition.get("status") not in ACTIVE_TRANSITIONS:
        return state
    state.pending_transition["status"] = "interrupted"
    state.next_node = "blocked"
    if INTERRUPTED_TRANSITION not in state.blockers:
        state.blockers.append(INTERRUPTED_TRANSITION)
    persist_transition(workspace, state)
    return state


def recover_transition(workspace, project_key):
    try:
        with workflow_guard(workspace, project_key):
            return interrupt_transition(workspace, project_key)
    except WorkflowBusyError:
        return StateStore(workspace).read_project_current(project_key)


def replay_approval(state, *, approval, decision, expected):
    transition = state.pending_transition
    action = transition.get("action") or {}
    if transition.get("status") != "completed" or not all(expected.values()):
        return False
    current = {
        "run_id": state.run_id, "target_type": "review", "target_id": action.get("gate"),
        "artifact_key": action.get("artifact"), "artifact_version": action.get("artifact_version"),
    }
    return (expected == current and action.get("gate") == approval and action.get("decision") == decision)
