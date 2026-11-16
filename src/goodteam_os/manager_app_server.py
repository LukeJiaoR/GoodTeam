"""Native Manager conversations. Business state remains in the existing services.

The session document holds native thread/turn identities and tool receipts only;
tasks, confirmations, dispatch, review and continuation keep their original owners.
Foreground input can steer a turn while independent background threads are running.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any

from goodteam_os.codex_app_server import CodexAppServer, CodexAppServerRejected
from goodteam_os.task_assistant_session import TaskAssistantSessionStore


def _now():
    return datetime.now(UTC).isoformat()


def workspace_for_config(config):
    path = Path(config).expanduser().resolve()
    return path.parent.parent if path.parent.name == "projects" else path.parent


def _error(message, code="execution_failed"):
    from goodteam_os.manager_execution import ManagerExecutionError
    return ManagerExecutionError(message, code=code)


@dataclass
class _Turn:
    thread_key: str
    thread_id: str
    logical_ids: list[str]
    phase: str
    tools: Any
    client: Any = None
    turn_id: str = ""
    messages: dict = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)
    native_done: threading.Event = field(default_factory=threading.Event)
    native_status: str = ""
    result: dict | None = None
    error: Exception | None = None
    stopped: bool = False
    submitted: bool = False


_RUNTIMES = {}
_RUNTIMES_LOCK = threading.RLock()


def _identity(config, key):
    return str(Path(config).expanduser().resolve()), key


def runtime_for(config, project_key):
    identity = _identity(config, project_key)
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(identity)
        if runtime is None:
            runtime = ManagerAppServer(Path(config), project_key)
            _RUNTIMES[identity] = runtime
        return runtime


def close_runtimes():
    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    for runtime in runtimes:
        runtime.close()


atexit.register(close_runtimes)


class ManagerAppServer:
    def __init__(self, project_config, project_key):
        from goodteam_os.graph import resolve_project_runtime_root
        from goodteam_os.projects import load_project_target

        self.config = Path(project_config).resolve()
        self.key = project_key
        self.workspace = workspace_for_config(self.config)
        self.root = resolve_project_runtime_root(dict(load_project_target(self.config).get("repos", {})))
        if self.root is None:
            raise _error("Manager project root is unavailable.")
        self.root = Path(self.root).resolve()
        self.store = TaskAssistantSessionStore(self.workspace, project_key)
        self.lock = threading.RLock()
        self.client = None
        self.fingerprint = ""
        self.active: dict[str, _Turn] = {}
        self.by_request: dict[str, _Turn] = {}

    def _runtime_config(self):
        from goodteam_os.database import provider_environment
        from goodteam_os.manager_execution import _configured_manager_model, _manager_settings, _model_name
        from goodteam_os.projects import load_project_target
        from goodteam_os.providers import ProviderStore, PROVIDER_KEY_ENV, codex_connection_arguments

        target = load_project_target(self.config)
        settings = _manager_settings(target)
        providers = ProviderStore()
        reference = providers.capture()
        connection = providers.resolve(reference)
        model = _model_name(_configured_manager_model(target))
        if connection and not model:
            model = connection.get("model", "")
        binary = os.environ.get("GOODTEAM_CODEX_PATH") or shutil.which("codex")
        if not binary:
            raise _error("Codex app-server is unavailable. Install Codex or set GOODTEAM_CODEX_PATH.")
        command, env = [binary, "app-server"], provider_environment()
        if connection:
            command.extend(codex_connection_arguments(connection))
            if connection.get("api_key"):
                env[PROVIDER_KEY_ENV] = connection["api_key"]
        public = {"model": model, "reasoning_depth": settings.get("reasoning_depth", "high"),
                  "additional_prompt": settings.get("additional_prompt", ""), "provider_connection": reference}
        fingerprint = sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
        return command, env, public, fingerprint

    def _connect(self, command, env, fingerprint):
        if self.client is not None and self.client.alive and self.fingerprint == fingerprint:
            return
        if any(not turn.done.is_set() for turn in self.active.values()):
            raise _error("Manager runtime changed while an existing turn is active.", "manager_runtime_changed")
        if self.client is not None:
            self.client.close()
        self.client = CodexAppServer(command, cwd=self.root, env=env,
                                    on_event=self._event, on_request=self._request)
        self.client.start()
        self.fingerprint = fingerprint

    def _record(self, logical_id, **values):
        def mutate(session):
            requests = session.setdefault("manager_app_server", {}).setdefault("requests", {})
            current = requests.get(logical_id, {})
            applied = values
            if current.get("status") == "stopped" and values.get("status") not in {None, "stopped"}:
                # Stop is durable even if a delayed native notification or a
                # recovery read completes after the control action.
                applied = {key: value for key, value in values.items()
                           if key not in {"status", "result", "completed_at"}}
            requests[logical_id] = {**current, **applied, "updated_at": _now()}
            native = requests[logical_id]
            public = session.get("assistant_requests", {}).get(logical_id)
            if public is not None:
                for source, target in (("thread_id", "native_thread_id"), ("turn_id", "native_turn_id")):
                    if native.get(source):
                        public[target] = native[source]
            return session
        return self.store._mutate(mutate)["manager_app_server"]["requests"][logical_id]

    def _check_current(self, turn):
        if turn.error is not None:
            raise turn.error
        if turn.stopped:
            raise _error("Manager work was stopped.", "manager_stopped")
        if turn.done.is_set():
            raise _error("This Manager turn has already ended.", "manager_interrupted")
        session = self.store.read()
        for logical_id in turn.logical_ids:
            request = session.get("assistant_requests", {}).get(logical_id, {})
            if request.get("stop_requested"):
                turn.stopped = True
                raise _error("Manager work was stopped.", "manager_stopped")
        return session

    def _stream(self, turn, *, text=None, activity=None):
        def mutate(session):
            for logical_id in turn.logical_ids:
                record = session.get("assistant_requests", {}).get(logical_id)
                if record is None or record.get("stop_requested"):
                    continue
                if text is not None:
                    record["stream_text"] = text[-64_000:]
                    if text.strip():
                        record.setdefault("first_response_at", _now())
                if activity is not None:
                    record["activity"] = activity
            return session
        self.store._mutate(mutate)

    @staticmethod
    def _text(turn):
        values = list(turn.messages.values())
        finals = [item["text"] for item in values if item.get("phase") == "final_answer" and item.get("text")]
        return "\n\n".join(finals or [item.get("text", "") for item in values]).strip()

    def _event(self, event):
        method, params = event.get("method"), event.get("params") or {}
        thread_id = params.get("threadId")
        with self.lock:
            turn = next((item for item in self.active.values() if item.thread_id == thread_id), None)
            if turn is None:
                return
            event_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
            if turn.turn_id and event_turn and event_turn != turn.turn_id:
                return
            if not turn.turn_id and event_turn:
                turn.turn_id = event_turn
            if method == "turn/completed":
                turn.native_status = (params.get("turn") or {}).get("status", "")
                turn.native_done.set()
            if turn.stopped or turn.error is not None:
                return
            if method == "item/agentMessage/delta":
                item = turn.messages.setdefault(params.get("itemId", "reply"), {"text": ""})
                item["text"] += str(params.get("delta", ""))
                self._stream(turn, text=self._text(turn))
            elif method == "item/completed" and (params.get("item") or {}).get("type") == "agentMessage":
                item = params["item"]
                turn.messages[item["id"]] = {"text": item.get("text", ""), "phase": item.get("phase")}
                self._stream(turn, text=self._text(turn))
            elif method in {"item/started", "item/completed"}:
                item = params.get("item") or {}
                self._stream(turn, activity={"kind": item.get("type", "tool"), "status": method.split("/")[1]})
            elif method == "turn/started":
                for logical_id in turn.logical_ids:
                    self._record(logical_id, turn_id=turn.turn_id, status="running")

    def _request(self, method, params):
        if method != "item/tool/call":
            # No approval request can silently promote the Manager's read-only sandbox.
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                return {"decision": "decline"}
            if method == "item/permissions/requestApproval":
                return {"permissions": {}}
            if method == "mcpServer/elicitation/request":
                return {"action": "decline", "content": None}
            if method == "item/tool/requestUserInput":
                return {"answers": {}}
            raise ValueError("Unsupported app-server request: " + method)
        with self.lock:
            turn = next((item for item in self.active.values() if item.thread_id == params.get("threadId")), None)
        if turn is None or turn.native_done.is_set() or turn.turn_id and params.get("turnId") != turn.turn_id:
            raise ValueError("Tool call does not belong to an active Manager turn.")
        try:
            self._check_current(turn)
            result = turn.tools.call(params["tool"], params.get("arguments") or {},
                                     call_id=params["callId"], logical_ids=list(turn.logical_ids))
            return {"contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=False, default=str)}],
                    "success": True}
        except Exception as exc:
            # Business validation is actionable model input; never expose provider logs.
            from goodteam_os.assistant_runtime import safe_failure
            message = str(exc) if isinstance(exc, ValueError) else safe_failure(exc)["message"]
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"error": message[:2000]})}],
                    "success": False}

    @staticmethod
    def _result_for(turn, logical_id):
        result = dict(turn.result or {})
        result["manager_execution"] = {**result.get("manager_execution", {}), "logical_id": logical_id}
        result["reply_suppressed"] = logical_id != turn.logical_ids[-1]
        return result

    def _finish(self, turn, *, result=None, error=None):
        """Publish one terminal receipt before releasing the in-process waiters."""
        with self.lock:
            if turn.stopped:
                result, error = None, error or _error("Manager work was stopped.", "manager_stopped")
            turn.result, turn.error = result, error
            try:
                for identifier in turn.logical_ids:
                    if result is not None and not turn.stopped:
                        self._record(identifier, status="completed", result=self._result_for(turn, identifier),
                                     completed_at=_now())
                    else:
                        native_status = turn.native_status
                        if not turn.submitted and isinstance(error, CodexAppServerRejected):
                            native_status = "rejected"
                        self._record(identifier, status="stopped" if turn.stopped else "interrupted",
                                     native_status=native_status)
            except Exception as exc:
                turn.error = exc
            finally:
                if self.active.get(turn.thread_key) is turn:
                    self.active.pop(turn.thread_key, None)
                for identifier in turn.logical_ids:
                    if self.by_request.get(identifier) is turn:
                        self.by_request.pop(identifier, None)
                turn.done.set()

    def _cancel_native(self, turn):
        """Cancel this exact turn; close the owned process if cancellation is unknown."""
        client = turn.client
        if client is None or not turn.submitted:
            return "not_started"
        if turn.native_done.is_set():
            return turn.native_status
        try:
            if not turn.turn_id:
                raise _error("Native turn acknowledgement was lost.", "manager_interrupted")
            client.request("turn/interrupt", {"threadId": turn.thread_id, "turnId": turn.turn_id}, timeout=2)
            client.wait_turn(turn.thread_id, turn.turn_id, timeout=2)
            return "interrupted"
        except CodexAppServerRejected:
            if turn.native_done.wait(0.2):
                return turn.native_status
            client.close(grace=0.2)
            return "connection_closed"
        except Exception:
            # No new turn can share an unknown still-running native process.
            client.close(grace=0.2)
            return "connection_closed"

    def run(self, *, logical_id, text, context, phase="conversation", run_id="", background=False,
            authority_check=None, attempt_id=""):
        from goodteam_os.manager_completion import completed_manager_result
        from goodteam_os.manager_tools import ManagerTools

        input_digest = sha256(json.dumps([phase, text, run_id, context if background else None], ensure_ascii=False).encode()).hexdigest()
        timeout = float(os.getenv("GOODTEAM_MANAGER_TURN_TIMEOUT_SECONDS", "600"))
        while True:
            owner, attached, wait_for = False, False, None
            turn = None
            try:
                with self.lock:
                    previous = self.store.read().get("manager_app_server", {}).get("requests", {}).get(logical_id)
                    if previous and previous.get("input_digest") != input_digest:
                        raise _error("Manager request identity belongs to different input.", "operation_conflict")
                    if previous and previous.get("status") == "completed":
                        return completed_manager_result(self.store, logical_id)
                    if previous and previous.get("status") == "stopped":
                        raise _error("This Manager work was stopped.", "manager_stopped")
                    may_start = not previous or previous.get("status") == "not_accepted"
                    if previous and previous.get("status") != "not_accepted":
                        if logical_id not in self.by_request:
                            recovered = self.reconcile(logical_id)
                            if recovered.get("status") == "completed":
                                return completed_manager_result(self.store, logical_id)
                            explicit_retry = bool(attempt_id and previous.get("attempt_id")
                                                  and attempt_id != previous["attempt_id"])
                            native_status = recovered.get("native_status") or previous.get("native_status")
                            if not explicit_retry or native_status not in {"failed", "interrupted", "rejected"}:
                                raise _error("Original Manager turn needs reconciliation; it was not replayed.", "manager_interrupted")
                            _command, _env, retry_runtime, _fingerprint = self._runtime_config()
                            if previous.get("runtime") != retry_runtime:
                                raise _error("Retry must keep the original Manager provider and runtime.", "manager_runtime_changed")
                            # The UI's new attempt explicitly permits another turn;
                            # original inputs, tool receipts and history stay bound.
                            context = previous["context"]
                            history = [*previous.get("attempt_history", []),
                                       {key: previous.get(key) for key in ("attempt_id", "thread_id", "turn_id", "status", "native_status")}]
                            self._record(logical_id, attempt_history=history)
                            may_start = True
                        else:
                            turn = self.by_request[logical_id]
                    if may_start:
                        command, env, public, fingerprint = self._runtime_config()
                        self._connect(command, env, fingerprint)
                        thread_key = "background:" + logical_id if background else "conversation"
                        turn = self.active.get(thread_key)
                        if turn is not None and not turn.done.is_set():
                            if turn.native_done.is_set():
                                wait_for = turn
                            else:
                                self._check_current(turn)
                                self._record(logical_id, input_digest=input_digest, text=text, context=context,
                                    phase=phase, run_id=run_id, thread_id=turn.thread_id, turn_id=turn.turn_id,
                                    status="steering", owner_pid=os.getpid(), attempt_id=attempt_id,
                                    runtime=public, started_at=_now(), turn_input_index=len(turn.logical_ids))
                                turn.logical_ids.append(logical_id)
                                self.by_request[logical_id] = turn
                                attached = True
                                try:
                                    turn.client.steer_turn(turn.thread_id, turn.turn_id,
                                        [{"type": "text", "text": text + "\n\nCurrent project context:\n" + context}])
                                except CodexAppServerRejected as exc:
                                    # An explicit no-active-turn rejection proves this
                                    # input was not accepted. Let the completed owner
                                    # publish its receipt, then start a normal turn.
                                    turn.logical_ids.remove(logical_id)
                                    self.by_request.pop(logical_id, None)
                                    attached = False
                                    self._record(logical_id, status="not_accepted", turn_id="")
                                    if "no active turn" not in str(exc).lower():
                                        raise
                                    wait_for = turn
                                if wait_for is None:
                                    self._record(logical_id, status="running")
                        else:
                            tools = ManagerTools(self, logical_id=logical_id, background=background,
                                                 authority_check=authority_check)
                            instructions = tools.instructions(text=text, context=context)
                            saved = (self.store.read().get("manager_app_server", {}).get("threads", {})
                                     .get(thread_key) or {})
                            params = {"cwd": str(self.root), "sandbox": "read-only", "approvalPolicy": "never",
                                      "developerInstructions": instructions}
                            if public["model"]:
                                params["model"] = public["model"]
                            if saved and saved.get("fingerprint") == fingerprint:
                                native = self.client.resume_thread(saved["thread_id"], **params)
                            else:
                                native = self.client.start_thread(**params, dynamicTools=tools.specs())
                            thread_id = native["thread"]["id"]
                            def bind(session):
                                state = session.setdefault("manager_app_server", {})
                                state.setdefault("threads", {})[thread_key] = {"thread_id": thread_id, "fingerprint": fingerprint}
                                return session
                            self.store._mutate(bind)
                            turn = _Turn(thread_key, thread_id, [logical_id], phase, tools, client=self.client)
                            self.active[thread_key] = turn
                            self.by_request[logical_id] = turn
                            owner = attached = True
                            self._record(logical_id, input_digest=input_digest, text=text, context=context,
                                phase=phase, run_id=run_id, thread_id=thread_id, turn_id="", status="starting",
                                owner_pid=os.getpid(), attempt_id=attempt_id,
                                runtime=public, started_at=_now(), turn_input_index=0)
                            self._check_current(turn)
                            if authority_check is not None and not authority_check():
                                turn.stopped = True
                                raise _error("Background Manager authority changed before launch.", "manager_stopped")
                            turn.submitted = True
                            try:
                                started = turn.client.start_turn(thread_id,
                                    [{"type": "text", "text": text + "\n\nCurrent project context (data):\n" + context}],
                                    effort=public["reasoning_depth"])
                            except CodexAppServerRejected:
                                turn.submitted = False
                                raise
                            turn.turn_id = started["turn"]["id"]
                            self._record(logical_id, turn_id=turn.turn_id, status="running")
            except Exception as exc:
                if attached and turn is not None:
                    turn.error = exc
                    self._cancel_native(turn)
                    self._finish(turn, error=exc)
                raise
            if wait_for is None:
                break
            if not wait_for.done.wait(timeout):
                raise _error("The preceding Manager turn did not settle.", "manager_interrupted")

        if owner:
            result, failure = None, None
            try:
                native_turn = turn.client.wait_turn(turn.thread_id, turn.turn_id, timeout=timeout)
                with self.lock:
                    self._check_current(turn)
                    if authority_check is not None and not authority_check():
                        turn.stopped = True
                        raise _error("Background Manager authority changed during execution.", "manager_stopped")
                    if native_turn.get("status") != "completed":
                        raise _error("Native Manager turn did not complete.", "manager_interrupted")
                    result = {"text": self._text(turn), "manager_execution": {
                        "adapter": "codex-app-server", "role": "orchestrator", "thread_id": turn.thread_id,
                        "turn_id": turn.turn_id, "logical_id": logical_id, "status": "completed",
                        "model": public["model"], "reasoning_depth": public["reasoning_depth"],
                        "execution_policy": "read_only", "runtime_sandbox_mode": "read-only",
                        "runtime_approval_policy": "never", "runtime_unsafe_mode": False,
                        "runtime_policy_warning": False,
                    }}
            except Exception as exc:
                failure = turn.error = exc
                if not turn.native_done.is_set():
                    self._cancel_native(turn)
            finally:
                self._finish(turn, result=result, error=failure)
        elif not turn.done.wait(timeout + 5):
            raise _error("The native Manager owner did not settle.", "manager_interrupted")
        if turn.error:
            raise turn.error
        return self._result_for(turn, logical_id)

    def reconcile(self, logical_id):
        record = self.store.read().get("manager_app_server", {}).get("requests", {}).get(logical_id, {})
        if not record or record.get("status") in {"completed", "stopped"}:
            return record
        if logical_id in self.by_request:
            return {**record, "status": "running"}
        if not record.get("thread_id") or not record.get("turn_id"):
            return {**record, "status": "interrupted"}
        command, env, _public, fingerprint = self._runtime_config()
        self._connect(command, env, fingerprint)
        native = self.client.read_thread(record["thread_id"], includeTurns=True)["thread"]
        found = next((item for item in native.get("turns", []) if item.get("id") == record["turn_id"]), None)
        if found is None and not native.get("turns"):
            # Modern app-server threads have paginated history. Read the actual
            # turn before declaring its execution outcome; never replay here.
            cursor = None
            for _ in range(10):
                params = {"threadId": record["thread_id"], "itemsView": "full", "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                page = self.client.request("thread/turns/list", params)
                found = next((item for item in page.get("data", []) if item.get("id") == record["turn_id"]), None)
                cursor = page.get("nextCursor")
                if found is not None or not cursor:
                    break
        if found and found.get("status") == "completed":
            messages = [item for item in found.get("items", []) if item.get("type") == "agentMessage"]
            finals = [item for item in messages if item.get("phase") == "final_answer"]
            texts = [item.get("text", "") for item in finals or messages]
            requests = self.store.read().get("manager_app_server", {}).get("requests", {})
            linked = [identifier for identifier, candidate in requests.items()
                      if candidate.get("thread_id") == record["thread_id"]
                      and candidate.get("turn_id") == record["turn_id"]]
            if len(linked) > 1:
                indices = [requests[identifier].get("turn_input_index") for identifier in linked]
                if (any(type(index) is not int or index < 0 for index in indices)
                        or len(set(indices)) != len(indices)):
                    # Without an accepted input order we cannot safely choose
                    # which user request should publish the shared turn's reply.
                    return {**record, "status": "interrupted", "native_status": "completed"}
                # Session JSON sorts mapping keys. Preserve native input order
                # explicitly rather than treating lexical request IDs as time.
                linked.sort(key=lambda identifier: requests[identifier]["turn_input_index"])
            for identifier in linked:
                result = {"text": "\n\n".join(texts), "reply_suppressed": identifier != linked[-1],
                          "manager_execution": {"adapter": "codex-app-server", "thread_id": record["thread_id"],
                              "turn_id": record["turn_id"], "logical_id": identifier, "status": "completed", "recovered": True}}
                self._record(identifier, status="completed", result=result)
            return self.store.read()["manager_app_server"]["requests"][logical_id]
        return {**record, "status": "running" if found and found.get("status") == "inProgress" else "interrupted",
                "native_status": found.get("status") if found else None}

    def stop(self, logical_id):
        with self.lock:
            turn = self.by_request.get(logical_id)
            if turn is not None:
                turn.stopped = True
                for identifier in turn.logical_ids:
                    self._record(identifier, status="stopped", stopped_at=_now())
                def mark_stopped(session):
                    for identifier in turn.logical_ids:
                        request = session.get("assistant_requests", {}).get(identifier)
                        if request is not None:
                            request.update(stop_requested=True, status="stopped")
                    return session
                self.store._mutate(mark_stopped)
                receipt = {"status": "stopped", "thread_id": turn.thread_id, "turn_id": turn.turn_id}
            else:
                session = self.store.read()
                requests = session.get("manager_app_server", {}).get("requests", {})
                record = requests.get(logical_id, {})
                linked = [identifier for identifier, candidate in requests.items()
                          if record.get("turn_id") and candidate.get("thread_id") == record.get("thread_id")
                          and candidate.get("turn_id") == record["turn_id"]] or [logical_id]
                for identifier in linked:
                    self._record(identifier, status="stopped", stopped_at=_now())
                def stop_persisted(session):
                    for identifier in linked:
                        public = session.get("assistant_requests", {}).get(identifier)
                        if public is not None:
                            public.update(stop_requested=True, status="stopped")
                    return session
                self.store._mutate(stop_persisted)
                return {"status": "stopped", "thread_id": record.get("thread_id"), "turn_id": record.get("turn_id")}
        # The event callback needs self.lock to acknowledge final cancellation.
        # Waiting for the transport while holding it would deadlock Stop.
        return {**receipt, "native_state": self._cancel_native(turn)}

    def close(self):
        with self.lock:
            for turn in self.active.values():
                turn.error = _error("Manager connection closed.", "manager_interrupted")
        if self.client is not None:
            self.client.close()


def request_liveness(*, project_config, project_key, logical_id):
    if project_config is None:
        return "unknown"
    try:
        runtime = runtime_for(project_config, project_key)
        record = runtime.reconcile(logical_id)
        return {"running": "active", "starting": "active", "steering": "active"}.get(record.get("status"),
                                                                                   record.get("status", "unknown"))
    except Exception:
        return "unknown"


def stop_manager_request(*, project_config, project_key, logical_id):
    return runtime_for(project_config, project_key).stop(logical_id)


def stop_background_work(workspace, project_key):
    """Interrupt existing continuation analyses without starting a runtime."""
    phases = {"project_iteration_choice", "project_outcome_discovery", "project_running_observation"}
    with _RUNTIMES_LOCK:
        runtimes = [runtime for runtime in _RUNTIMES.values()
                    if runtime.workspace.resolve() == Path(workspace).resolve() and runtime.key == project_key]
    receipts = []
    for runtime in runtimes:
        with runtime.lock:
            identifiers = [turn.logical_ids[0] for turn in runtime.active.values()
                           if turn.thread_key.startswith("background:") and turn.phase in phases]
        for identifier in identifiers:
            receipts.append(runtime.stop(identifier))
    return receipts
