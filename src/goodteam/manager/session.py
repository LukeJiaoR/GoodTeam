"""Persistent Manager conversation session over Codex app-server."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable

from goodteam.config import get_state_dir
from goodteam.manager.tools import ManagerToolset, get_manager_settings
from goodteam.manager.transport import CodexAppServer, CodexAppServerError
from goodteam.providers import ProviderStore, codex_env, codex_runtime_args
from goodteam.schema import new_id
from goodteam.store import Store


MockTurnFn = Callable[[str, str, ManagerToolset, Callable[[str], None]], str]


@dataclass
class _ActiveTurn:
    thread_id: str
    turn_id: str
    toolset: ManagerToolset
    assistant_msg_id: str
    chunks: list[str] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    error: str = ""
    stopped: bool = False


class _ProjectSession:
    def __init__(
        self,
        hub: ManagerHub,
        project_id: str,
    ) -> None:
        self.hub = hub
        self.project_id = project_id
        self.lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self.client: CodexAppServer | None = None
        self.fingerprint: str = ""
        self.active: _ActiveTurn | None = None
        self._closed = threading.Event()
        self._wake_lock = threading.Lock()
        self._pending_wake_runs: list[dict[str, Any]] = []
        self._waking = False

    def close(self) -> None:
        self._closed.set()
        with self._wake_lock:
            self._pending_wake_runs.clear()
        with self.lock:
            if self.client is not None:
                self.client.close()
                self.client = None
            self.fingerprint = ""

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.hub.event_bus is not None:
            self.hub.event_bus.publish(self.project_id, event_type, payload)

    def _ensure_client(self, project: dict[str, Any]) -> CodexAppServer:
        settings = get_manager_settings(project)
        provider = self.hub.provider_store.resolve_for_run("codex", settings["provider_id"])
        binary = os.environ.get("GOODTEAM_CODEX_PATH", "").strip() or shutil.which("codex") or ""
        if not binary:
            raise RuntimeError("codex CLI is not installed on PATH.")
        args = [binary, "app-server", *codex_runtime_args(provider)]
        fp = f"{args}|{settings['model']}|{settings['reasoning_effort']}|{provider['id'] if provider else 'native'}"
        with self.lock:
            if self.client is not None and self.client.alive and self.fingerprint == fp:
                return self.client
            if self.client is not None:
                self.client.close()
            stderr_path = get_state_dir(self.hub.state_dir) / "logs" / f"manager-{self.project_id}.stderr.log"
            client = CodexAppServer(
                args,
                cwd=Path(project["path"]),
                env=codex_env(provider),
                stderr_path=stderr_path,
                on_event=self._on_server_event,
                on_request=self._on_server_request,
            )
            client.start(timeout=30.0)
            self.client = client
            self.fingerprint = fp
            return client

    def _on_server_event(self, record: dict[str, Any]) -> None:
        method = record.get("method")
        params = record.get("params") or {}
        turn = self.active
        if not turn:
            return
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                turn.chunks.append(delta)
                self._emit("chat_delta", {"message_id": turn.assistant_msg_id, "delta": delta})
        elif method == "error":
            msg = (params.get("error") or {}).get("message") or str(params.get("message") or "Manager error")
            turn.error = str(msg)
        elif method == "turn/completed":
            t = params.get("turn") or {}
            if t.get("status") == "failed" and not turn.error:
                turn.error = str((t.get("error") or {}).get("message") or "Manager turn failed.")
            turn.done.set()

    def _on_server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "item/tool/call":
            return {"error": f"Unsupported client request: {method}"}
        turn = self.active
        if not turn:
            return {"contentItems": [{"type": "inputText", "text": '{"error":"No active turn"}'}], "success": False}
        tool_name = str(params.get("tool") or params.get("name") or "")
        args = params.get("arguments")
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        try:
            res = turn.toolset.call(tool_name, dict(args or {}))
            import json
            return {"contentItems": [{"type": "inputText", "text": json.dumps(res, ensure_ascii=False)}], "success": True}
        except Exception as exc:
            import json
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"error": str(exc)})}], "success": False}

    def send(
        self,
        text: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        actor: str = "user",
        base_ref_override: str | None = None,
        iteration_id: str | None = None,
        sync: bool = False,
    ) -> dict[str, Any]:
        project = self.hub.store.get_project(self.project_id)
        if not project:
            raise ValueError(f"Unknown project: {self.project_id}")
        user_msg = self.hub.store.add_message(
            project_id=self.project_id,
            role=actor,
            content=text,
            attachments=attachments or [],
            meta={"iteration_id": iteration_id} if iteration_id else {},
        )
        self._emit("chat_message", user_msg)

        # Steer active turn if user sends a follow-up mid-turn.
        with self.lock:
            active = self.active
            if active is not None and not active.done.is_set() and self.client and self.client.alive and not sync:
                self.client.steer_turn(active.thread_id, active.turn_id, self._build_input(text, attachments))
                return {"user_message": user_msg, "steered": True}

        if sync:
            reply_msg, spawned = self._run_turn(
                project, text, attachments=attachments or [],
                parent_message_id=user_msg["id"],
                base_ref_override=base_ref_override, iteration_id=iteration_id,
            )
            return {"user_message": user_msg, "assistant_message": reply_msg, "spawned_run_ids": spawned, "steered": False}

        t = threading.Thread(
            target=self._run_turn,
            args=(project, text),
            kwargs={
                "attachments": attachments or [],
                "parent_message_id": user_msg["id"],
                "base_ref_override": base_ref_override,
                "iteration_id": iteration_id,
            },
            daemon=True,
            name=f"goodteam-mgr-{self.project_id}",
        )
        t.start()
        return {"user_message": user_msg, "steered": False}

    def _build_input(self, text: str, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        notes: list[str] = []
        for att in attachments or []:
            path = str(att.get("path") or "")
            mime = str(att.get("mime_type") or "")
            if path and mime.startswith("image/"):
                items.append({"type": "localImage", "path": path})
            elif path:
                notes.append(f"[Attached file: {att.get('name', path)} at {path}]")
        combined = (text + ("\n\n" + "\n".join(notes) if notes else "")).strip()
        items.insert(0, {"type": "text", "text": combined})
        return items

    def _run_turn(
        self,
        project: dict[str, Any],
        text: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        parent_message_id: str | None = None,
        base_ref_override: str | None = None,
        iteration_id: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        with self._turn_lock:
            toolset = ManagerToolset(
                self.hub.store,
                self.hub.runner,
                self.project_id,
                state_dir=self.hub.state_dir,
                parent_message_id=parent_message_id,
                base_ref_override=base_ref_override,
                iteration_id=iteration_id,
            )
            assistant_id = new_id("msg")
            placeholder = self.hub.store.add_message(
                project_id=self.project_id,
                role="manager",
                content="",
                meta={"status": "streaming", **({"iteration_id": iteration_id} if iteration_id else {})},
                message_id=assistant_id,
            )
            self._emit("chat_message", placeholder)

            if self.hub.mock_turn_fn is not None:
                chunks: list[str] = []
                try:
                    def on_delta(d: str) -> None:
                        chunks.append(d)
                        self._emit("chat_delta", {"message_id": assistant_id, "delta": d})

                    reply = self.hub.mock_turn_fn(self.project_id, text, toolset, on_delta)
                    final_text = reply or "".join(chunks)
                    self.hub.store.update_message(assistant_id, content=final_text, meta={"status": "completed"})
                    msg = {**placeholder, "content": final_text, "meta": {"status": "completed"}}
                    self._emit("chat_done", msg)
                    return msg, list(toolset.spawned_run_ids)
                except Exception as exc:
                    err_text = f"Manager error: {exc}"
                    self.hub.store.update_message(assistant_id, content=err_text, meta={"status": "failed"})
                    msg = {**placeholder, "content": err_text, "meta": {"status": "failed"}}
                    self._emit("chat_done", msg)
                    return msg, list(toolset.spawned_run_ids)

            try:
                client = self._ensure_client(project)
                settings = get_manager_settings(project)
                instructions = toolset.instructions()
                kv_key = f"manager_thread:{self.project_id}"
                thread_id = self.hub.store.get_kv(kv_key)
                if thread_id:
                    try:
                        client.resume_thread(thread_id, baseInstructions=instructions, dynamicTools=toolset.specs())
                    except CodexAppServerError:
                        thread_id = None
                if not thread_id:
                    res = client.start_thread(
                        cwd=str(project["path"]),
                        baseInstructions=instructions,
                        dynamicTools=toolset.specs(),
                        sandbox="read-only",
                        approvalPolicy="never",
                    )
                    thread_id = str((res.get("thread") or {}).get("id") or res.get("threadId") or "")
                    if not thread_id:
                        raise RuntimeError("Codex app-server did not return a thread ID.")
                    self.hub.store.set_kv(kv_key, thread_id)

                start_params: dict[str, Any] = {}
                if settings["model"]:
                    start_params["model"] = settings["model"]
                if settings["reasoning_effort"]:
                    start_params["effort"] = settings["reasoning_effort"]

                turn = _ActiveTurn(thread_id=thread_id, turn_id="", toolset=toolset, assistant_msg_id=assistant_id)
                with self.lock:
                    self.active = turn
                try:
                    turn_res = client.start_turn(thread_id, self._build_input(text, attachments), **start_params)
                    turn.turn_id = str((turn_res.get("turn") or {}).get("id") or turn_res.get("turnId") or "")
                    waited = 0.0
                    while not turn.done.is_set() and waited < 600.0:
                        if turn.done.wait(timeout=0.5):
                            break
                        waited += 0.5
                        if not client.alive:
                            if not turn.stopped and not turn.error:
                                turn.error = "Codex app-server exited unexpectedly."
                            break
                    if not turn.done.is_set() and waited >= 600.0 and not turn.error:
                        turn.error = "Manager turn timed out."
                finally:
                    with self.lock:
                        if self.active is turn:
                            self.active = None

                raw_text = "".join(turn.chunks).strip()
                has_error = bool(turn.error and not turn.stopped)
                final_text = raw_text or (f"Manager turn failed: {turn.error}" if has_error else "")
                status = "stopped" if turn.stopped else ("failed" if has_error else "completed")
                self.hub.store.update_message(assistant_id, content=final_text, meta={"status": status})
                msg = {**placeholder, "content": final_text, "meta": {"status": status}}
                self._emit("chat_done", msg)
                return msg, list(toolset.spawned_run_ids)
            except Exception as exc:
                if self._closed.is_set() or self.hub._closing:
                    return placeholder, list(toolset.spawned_run_ids)
                err_text = f"Manager unavailable: {exc}"
                self.hub.store.update_message(assistant_id, content=err_text, meta={"status": "failed"})
                msg = {**placeholder, "content": err_text, "meta": {"status": "failed"}}
                self._emit("chat_done", msg)
                return msg, list(toolset.spawned_run_ids)

    def stop(self) -> bool:
        with self.lock:
            active = self.active
            client = self.client
        if active is not None and client is not None and client.alive:
            active.stopped = True
            try:
                client.interrupt_turn(active.thread_id, active.turn_id)
            except CodexAppServerError:
                pass
            active.done.set()
            return True
        return False

    def enqueue_subagent_wakeup(self, run: dict[str, Any]) -> None:
        if self._closed.is_set() or self.hub._closing:
            return
        with self._wake_lock:
            self._pending_wake_runs.append(run)
            if self._waking:
                return
            self._waking = True
        threading.Thread(target=self._drain_wakeups, daemon=True, name=f"goodteam-wake-{self.project_id}").start()

    def _drain_wakeups(self) -> None:
        while True:
            if self._closed.is_set() or self.hub._closing:
                with self._wake_lock:
                    self._pending_wake_runs.clear()
                    self._waking = False
                return
            with self._wake_lock:
                if not self._pending_wake_runs:
                    self._waking = False
                    return
                batch = list(self._pending_wake_runs)
                self._pending_wake_runs.clear()
            lines = [
                f"- Run `{r['id']}` (`{r['role_key']}`): status={r['status']}, scope_ok={r['scope_ok']}, "
                f"changed_files={r['changed_files']}, error={r['error'] or 'none'}\n  Summary: {r['summary'][:500]}"
                for r in batch
            ]
            prompt = (
                "Sub-agent execution finished:\n" + "\n".join(lines) +
                "\n\nSummarize the result concisely for the user, inspect diffs or record memory if appropriate, and suggest next steps."
            )
            try:
                if self._closed.is_set() or self.hub._closing:
                    return
                project = self.hub.store.get_project(self.project_id)
                if project and not self._closed.is_set() and not self.hub._closing:
                    self.send(prompt, actor="system", sync=True)
            except Exception:
                if self._closed.is_set() or self.hub._closing:
                    return
                raise


class ManagerHub:
    def __init__(
        self,
        store: Store,
        provider_store: ProviderStore,
        runner: Any,
        *,
        state_dir: str | Path | None = None,
        event_bus: Any = None,
        mock_turn_fn: MockTurnFn | None = None,
    ) -> None:
        self.store = store
        self.provider_store = provider_store
        self.runner = runner
        self.state_dir = state_dir
        self.event_bus = event_bus
        self.mock_turn_fn = mock_turn_fn
        self._lock = threading.Lock()
        self._sessions: dict[str, _ProjectSession] = {}
        self._closing = False

    def session(self, project_id: str) -> _ProjectSession:
        with self._lock:
            sess = self._sessions.get(project_id)
            if sess is None:
                sess = _ProjectSession(self, project_id)
                self._sessions[project_id] = sess
            return sess

    def on_subagent_finished(self, run: dict[str, Any]) -> None:
        if self._closing or run.get("iteration_id"):
            return
        self.session(run["project_id"]).enqueue_subagent_wakeup(run)

    def close_session(self, project_id: str) -> None:
        with self._lock:
            sess = self._sessions.pop(project_id, None)
        if sess is not None:
            sess.stop()
            sess.close()

    def close(self) -> None:
        self._closing = True
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.close()
