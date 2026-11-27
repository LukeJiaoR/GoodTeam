"""[INPUT]: 接收 CodeBuddy CLI 路径、已准入的 Manager 轮次和工具回调。
[OUTPUT]: 通过 ACP 读取模型目录，交付 Manager 会话、回复和取消结果。
[POS]: 目录探测使用独立会话。Manager 会话将工具调用转给现有 MCP 服务。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from uuid import uuid4

from goodteam_os.codebuddy_worker import worker_environment
from goodteam_os.codex_app_server import CodexAppServer, CodexAppServerError, CodexAppServerRejected
from goodteam_os.manager_mcp import ManagerMcp
from goodteam_os.skills.filesystem import directory


class _CodeBuddyAcp(CodexAppServer):
    _label = "CodeBuddy ACP"
    _jsonrpc_version = "2.0"
    _process_group = True

    def __init__(self, binary, *, cwd, config, args, **transport_options):
        if any((parent / ".codebuddy" / "models.json").exists() for parent in (cwd, *cwd.parents)):
            raise ValueError("CodeBuddy ACP cannot load a workspace model endpoint override.")
        env = worker_environment(cwd)
        env.update(CODEBUDDY_CONFIG_DIR=str(config), CODEBUDDY_DEFER_TOOL_LOADING="0")
        command = [binary, "--acp", "--agent", "cli", "--setting-sources", "", "--tools", "",
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dontAsk",
                   "--settings", '{"disableAllHooks":true,"autoMemoryEnabled":false}', *args]
        super().__init__(command, cwd=cwd, env=env, **transport_options)

    def _initialize(self, timeout):
        result = self.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout=timeout)
        if result.get("protocolVersion") != 1:
            raise CodexAppServerError("CodeBuddy did not negotiate ACP version 1.")
        self._can_load = result.get("agentCapabilities", {}).get("loadSession") is True


def query_codebuddy_models(binary: str) -> tuple[tuple[str, ...], str] | None:
    """Read the CLI's model list from a disposable session without sending a prompt."""
    try:
        with TemporaryDirectory(prefix="goodteam-codebuddy-models-") as root:
            cwd = Path(root).resolve()
            config = cwd / "config"
            config.mkdir()
            client = _CodeBuddyAcp(binary, cwd=cwd, config=config, args=["--no-session-persistence"])
            try:
                client.start(timeout=4)
                session = client.request("session/new", {"cwd": str(cwd), "mcpServers": []}, timeout=6)
            finally:
                client.close()
    except (CodexAppServerError, OSError, ValueError):
        return None

    catalog = session.get("models")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("availableModels"), list):
        return None
    models = []
    for entry in catalog["availableModels"]:
        model_id = entry.get("modelId") if isinstance(entry, dict) else None
        if isinstance(model_id, str) and model_id.strip():
            models.append(model_id.strip())
    if not models:
        return None
    current = catalog.get("currentModelId")
    return tuple(models), current if current in models else "auto"


class CodeBuddyManager(_CodeBuddyAcp):
    def __init__(self, binary, *, state, instructions, model, effort, timeout, specs, on_event, on_request):
        cwd = state / "workspace"
        config = state / "config"
        with directory(cwd, create=True), directory(config, create=True):
            pass
        names = ", ".join("mcp__goodteam__" + spec["name"] for spec in specs)
        instructions += ("\nCall GoodTeam tools by their full MCP names. Available tools are " + names
                         + ". After the last tool call, include the complete answer in your final message.")
        args = ["--allowedTools", "mcp__goodteam", "--append-system-prompt", instructions, "--effort", effort]
        if model:
            args.extend(["--model", model])
        super().__init__(binary, cwd=cwd, config=config, args=args, stderr_path=state / "stderr.log",
                         on_event=on_event, on_request=self._client_request)
        self.timeout = timeout
        self.thread_id = ""
        self.prompt_id = ""
        self.message_index = 0
        self.prompt_open = Event()
        self._tool_request = on_request
        self._temp = TemporaryDirectory(prefix="gtm-")
        self._socket = Path(self._temp.name) / "mcp"
        try:
            self._mcp = ManagerMcp(self._socket, specs, self._tool_call)
        except BaseException:
            self._temp.cleanup()
            raise

    @staticmethod
    def _client_request(method, _params):
        if method == "session/request_permission":
            return {"outcome": {"outcome": "cancelled"}}
        raise ValueError("GoodTeam Manager accepts only its registered MCP tools.")

    def _tool_call(self, name, arguments, call_id):
        if not self.prompt_open.is_set() or not isinstance(arguments, dict):
            raise ValueError("MCP call does not belong to an active Manager turn.")
        return self._tool_request("item/tool/call", {"threadId": self.thread_id, "turnId": self.prompt_id,
                                  "tool": name, "arguments": arguments, "callId": f"{self.prompt_id}:{call_id}"})

    def open_session(self, resume=""):
        params = {"cwd": str(self.cwd), "mcpServers": [
            {"name": "goodteam", "command": sys.executable, "env": [],
             "args": ["-I", str(Path(__file__).with_name("manager_mcp.py").resolve()), str(self._socket)]}
        ]}
        if resume and self._can_load:
            self.request("session/load", {**params, "sessionId": resume})
            self.thread_id = resume
        else:
            self.thread_id = self.request("session/new", params).get("sessionId")
        if not isinstance(self.thread_id, str) or not self.thread_id:
            raise CodexAppServerError("CodeBuddy did not return a session ID.")
        return self.thread_id

    def start_turn(self, thread_id, input, **_params):
        self.prompt_id = f"gt-acp-{uuid4()}"
        sent = Event()
        self.prompt_open.set()
        Thread(target=self._prompt, args=(input, sent), daemon=True, name="goodteam-acp-prompt").start()
        # 返回前确认 prompt 已写入，Stop 才能可靠地发往同一会话。
        if not sent.wait(5):
            raise CodexAppServerError("CodeBuddy prompt could not be submitted.")
        return {"turn": {"id": self.prompt_id}}

    def _prompt(self, prompt, sent):
        connection = self._connection
        try:
            response = self.request("session/prompt", {"sessionId": self.thread_id, "prompt": prompt},
                                    timeout=self.timeout + 5, request_id=self.prompt_id, sent=sent)
            reason = response.get("stopReason")
            if reason == "end_turn":
                terminal = {"status": "completed"}
            elif reason == "cancelled":
                terminal = {"status": "interrupted"}
            else:
                terminal = {"status": "failed", "error": "CodeBuddy prompt ended before a complete reply."}
        except CodexAppServerRejected as exc:
            terminal = {"status": "failed", "error": exc.error.get("message", "CodeBuddy prompt failed.")}
        except CodexAppServerError as exc:
            connection.events.put(exc)
            return
        finally:
            self.prompt_open.clear()
        connection.events.put({"method": "turn/completed", "params": {"threadId": self.thread_id,
                              "turn": {**terminal, "id": self.prompt_id}}})

    def _notification(self, record):
        params = record.get("params") or {}
        if (record.get("method") != "session/update" or not self.prompt_open.is_set()
                or params.get("sessionId") != self.thread_id):
            return None
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        identity = {"threadId": self.thread_id, "turnId": self.prompt_id}
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                return {"method": "item/agentMessage/delta", "params": {**identity, "delta": content["text"],
                        "itemId": str(self.message_index)}}
        if kind in {"tool_call", "tool_call_update"}:
            if kind == "tool_call":
                self.message_index += 1
            method = "item/completed" if update.get("status") in {"completed", "failed"} else "item/started"
            return {"method": method, "params": {**identity, "item": {"type": "mcpToolCall"}}}
        return None

    def interrupt_turn(self, thread_id, turn_id, *, timeout=2):
        self.prompt_open.clear()
        self._write(self._connection, {"method": "session/cancel", "params": {"sessionId": thread_id}}, timeout=timeout)

    def close(self, *, grace=1.0):
        self.prompt_open.clear()
        with self._lifecycle_lock:
            try:
                super().close(grace=grace)
            finally:
                if self._mcp is not None:
                    self._mcp.close()
                    self._mcp = None
                    self._temp.cleanup()
