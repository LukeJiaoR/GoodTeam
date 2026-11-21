"""Sub-agent runtime adapters: Codex CLI, Claude CLI, and deterministic Mock."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Callable, Protocol

from goodteam.providers import claude_env, codex_env, codex_runtime_args


EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class AdapterContext:
    run_id: str
    role: dict[str, Any]
    prompt: str
    worktree_path: Path
    execution_policy: str
    provider: dict[str, Any] | None = None
    model: str = ""
    reasoning_effort: str = "high"
    staged_skills_dir: Path | None = None
    skill_prompt: str = ""


@dataclass
class AdapterResult:
    summary: str
    usage: dict[str, int] = field(default_factory=dict)
    exit_code: int = 0
    error: str = ""


class RuntimeAdapter(Protocol):
    def run(self, ctx: AdapterContext, on_event: EventCallback) -> AdapterResult: ...
    def stop(self) -> None: ...


class _SubprocessRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._stopped = False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def launch(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdin_text: str | None = None) -> subprocess.Popen[str]:
        with self._lock:
            if self._stopped:
                raise RuntimeError("Run was stopped before start.")
            self._proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", shell=False, bufsize=1,
            )
            proc = self._proc
        if stdin_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
        return proc

    @staticmethod
    def _start_stderr_drain(proc: subprocess.Popen[str]) -> tuple[list[str], threading.Thread | None]:
        chunks: list[str] = []
        if proc.stderr is None:
            return chunks, None

        def _reader() -> None:
            assert proc.stderr is not None
            try:
                chunks.append(proc.stderr.read())
            except OSError:
                pass

        thread = threading.Thread(target=_reader, daemon=True, name="goodteam-stderr-drain")
        thread.start()
        return chunks, thread


class CodexAdapter(_SubprocessRunner):
    def run(self, ctx: AdapterContext, on_event: EventCallback) -> AdapterResult:
        exe = os.environ.get("GOODTEAM_CODEX_PATH", "").strip() or shutil.which("codex") or ""
        if not exe:
            return AdapterResult(summary="", exit_code=1, error="codex CLI is not installed.")
        sandbox = "read-only" if ctx.execution_policy == "read_only" else "workspace-write"
        cmd = [exe, "exec", "--json", "--ephemeral", "--color", "never", "-C", str(ctx.worktree_path), "-s", sandbox]
        if ctx.staged_skills_dir:
            cmd.extend(["--add-dir", str(ctx.staged_skills_dir)])
        if ctx.model:
            cmd.extend(["-m", ctx.model])
        if ctx.reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={json.dumps(ctx.reasoning_effort)}"])
        cmd.extend(codex_runtime_args(ctx.provider))
        cmd.append("-")

        env = {**os.environ, **codex_env(ctx.provider)}
        sections = [ctx.role.get("system_prompt", "").strip(), ctx.skill_prompt.strip(), ctx.prompt.strip()]
        full_prompt = "\n\n".join(part for part in sections if part)
        proc = self.launch(cmd, cwd=ctx.worktree_path, env=env, stdin_text=full_prompt)
        stderr_chunks, stderr_thread = self._start_stderr_drain(proc)

        messages: list[str] = []
        usage: dict[str, int] = {}
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                on_event("log", {"text": line})
                continue
            etype = ev.get("type", "")
            if etype == "item.completed":
                item = ev.get("item") or {}
                itype = item.get("type")
                if itype == "agent_message" and item.get("text"):
                    messages.append(str(item["text"]))
                    on_event("message", {"text": str(item["text"])})
                elif itype:
                    on_event("tool", {"item_type": itype, "command": item.get("command", ""), "status": item.get("status", "")})
            elif etype == "turn.completed" and isinstance(ev.get("usage"), dict):
                u = ev["usage"]
                usage = {
                    "input_tokens": int(u.get("input_tokens", 0) or 0),
                    "cached_input_tokens": int(u.get("cached_input_tokens", 0) or 0),
                    "output_tokens": int(u.get("output_tokens", 0) or 0),
                }
        code = proc.wait()
        if stderr_thread is not None:
            stderr_thread.join(timeout=2)
        stderr_text = "".join(stderr_chunks).strip()
        summary = "\n\n".join(messages).strip()
        if self._stopped:
            return AdapterResult(summary=summary, usage=usage, exit_code=code, error="Stopped by user.")
        if code != 0:
            return AdapterResult(summary=summary, usage=usage, exit_code=code, error=stderr_text[:400] or f"codex exited {code}")
        return AdapterResult(summary=summary, usage=usage, exit_code=0)


class ClaudeAdapter(_SubprocessRunner):
    def run(self, ctx: AdapterContext, on_event: EventCallback) -> AdapterResult:
        exe = os.environ.get("GOODTEAM_CLAUDE_PATH", "").strip() or shutil.which("claude") or ""
        if not exe:
            return AdapterResult(summary="", exit_code=1, error="claude CLI is not installed.")
        cmd = [
            exe, "-p", "--output-format", "stream-json", "--verbose",
            "--no-session-persistence", "--permission-prompts", "none",
        ]
        if ctx.provider:
            # Isolate custom GoodTeam provider from ~/.claude/settings.json env/hooks/MCP overrides.
            cmd.extend(["--setting-sources", "", "--strict-mcp-config"])
        else:
            # Native CLI mode: keep user auth/settings, skip user hooks and failing MCP servers.
            cmd.append("--safe-mode")
        if ctx.staged_skills_dir:
            cmd.extend(["--add-dir", str(ctx.staged_skills_dir)])
        sys_parts = [ctx.role.get("system_prompt", "").strip(), ctx.skill_prompt.strip()]
        sys_prompt = "\n\n".join(p for p in sys_parts if p)
        if sys_prompt:
            cmd.extend(["--append-system-prompt", sys_prompt])
        if ctx.model:
            cmd.extend(["--model", ctx.model])
        if ctx.reasoning_effort:
            cmd.extend(["--effort", ctx.reasoning_effort])
        tools = [str(t) for t in (ctx.role.get("allowed_tools") or []) if str(t).strip()]
        if ctx.execution_policy == "read_only":
            cmd.extend(["--permission-mode", "plan", "--allowedTools", ",".join(tools or ["Read", "Glob", "Grep"])])
        else:
            cmd.extend(["--permission-mode", "bypassPermissions", "--allow-dangerously-skip-permissions"])
            if tools:
                cmd.extend(["--allowedTools", ",".join(tools)])

        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        if ctx.provider:
            for k in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
                env.pop(k, None)
            env.update(claude_env(ctx.provider))
        proc = self.launch(cmd, cwd=ctx.worktree_path, env=env, stdin_text=ctx.prompt)
        stderr_chunks, stderr_thread = self._start_stderr_drain(proc)

        messages: list[str] = []
        final_result = ""
        result_error = ""
        usage: dict[str, int] = {}
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                on_event("log", {"text": line})
                continue
            etype = ev.get("type", "")
            if etype == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        messages.append(str(block["text"]))
                        on_event("message", {"text": str(block["text"])})
                    elif block.get("type") == "tool_use":
                        on_event("tool", {"name": block.get("name", "")})
            elif etype == "result":
                if ev.get("is_error"):
                    result_error = str(ev.get("result") or ev.get("error") or "Claude returned an error.")
                elif ev.get("result"):
                    final_result = str(ev["result"])
                u = ev.get("usage") or {}
                if isinstance(u, dict):
                    usage = {
                        "input_tokens": int(u.get("input_tokens", 0) or 0),
                        "cached_input_tokens": int(u.get("cache_read_input_tokens", 0) or 0),
                        "output_tokens": int(u.get("output_tokens", 0) or 0),
                    }
        code = proc.wait()
        if stderr_thread is not None:
            stderr_thread.join(timeout=2)
        stderr_text = "".join(stderr_chunks).strip()
        summary = (final_result or "\n\n".join(messages)).strip()
        if self._stopped:
            return AdapterResult(summary=summary, usage=usage, exit_code=code, error="Stopped by user.")
        if code != 0 or result_error:
            err = (result_error or stderr_text)[:400] or f"claude exited {code}"
            return AdapterResult(summary=summary, usage=usage, exit_code=code or 1, error=err)
        return AdapterResult(summary=summary, usage=usage, exit_code=0)


MockHandler = Callable[[AdapterContext, EventCallback, threading.Event], AdapterResult]
_MOCK_HANDLER: MockHandler | None = None


def set_mock_handler(handler: MockHandler | None) -> None:
    global _MOCK_HANDLER
    _MOCK_HANDLER = handler


class MockAdapter:
    def __init__(self) -> None:
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self, ctx: AdapterContext, on_event: EventCallback) -> AdapterResult:
        if _MOCK_HANDLER is not None:
            return _MOCK_HANDLER(ctx, on_event, self._stop_event)
        on_event("message", {"text": f"[{ctx.role['key']}] executed: {ctx.prompt[:80]}"})
        if self._stop_event.is_set():
            return AdapterResult(summary="", exit_code=1, error="Stopped by user.")
        return AdapterResult(
            summary=f"Completed {ctx.role['key']} task. Skills: {bool(ctx.skill_prompt)}",
            usage={"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
            exit_code=0,
        )


def build_adapter(name: str) -> RuntimeAdapter:
    if name == "codex":
        return CodexAdapter()
    if name == "claude":
        return ClaudeAdapter()
    if name == "mock":
        return MockAdapter()
    raise ValueError(f"Unsupported adapter: {name}")
