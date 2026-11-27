"""[INPUT]: 接收已验证的派工目录、可选的 CodeBuddy 环境凭证和 CLI 输出。
[OUTPUT]: 生成原生登录可用的无头执行参数并提取 Worker 的最终答复。
[POS]: 为 Worker 作业提供 CodeBuddy 文件工具和结果协议。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import os
from pathlib import Path


AUTH_ENV = ("CODEBUDDY_API_KEY", "CODEBUDDY_AUTH_TOKEN")
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
_RUNTIME_ENV = {
    *AUTH_ENV, "CODEBUDDY_INTERNET_ENVIRONMENT",
    "PATH", "LANG", "LC_ALL", "SHELL", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}
_MAX_TURNS = 32


def worker_environment(workspace: Path) -> dict[str, str]:
    # CLI 仍会从项目读取模型端点，即使关闭了项目 settings。
    if (workspace / ".codebuddy" / "models.json").exists():
        raise ValueError("CodeBuddy Worker requires a host-configured model endpoint. Remove the project .codebuddy/models.json from the task source.")
    env = {name: os.environ[name] for name in _RUNTIME_ENV if name in os.environ}
    if any(env.get(name, "").strip() for name in AUTH_ENV) and "CODEBUDDY_BASE_URL" in os.environ:
        env["CODEBUDDY_BASE_URL"] = os.environ["CODEBUDDY_BASE_URL"]
    env.update({
        "CODEBUDDY_DISABLE_AUTO_MEMORY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
    })
    return env


def build_command(binary: str, model: str, _workspace: Path, _result_path: Path) -> list[str]:
    command = [
        binary, "-p", "--output-format", "json", "--agent", "cli",
        "--max-turns", str(_MAX_TURNS), "--no-session-persistence",
        "--permission-mode", "dontAsk", "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    if model and model != "auto":
        command.extend(["--model", model])
    return command


def file_tool_options(workspace: Path, *, read_only: bool) -> list[str]:
    tools = ["Read", "Glob", "Grep"]
    settings = {"disableAllHooks": True, "autoMemoryEnabled": False}
    if not read_only:
        # CodeBuddy 用双斜杠表示文件系统绝对路径。
        path = workspace.as_posix()
        if any(character in path for character in "*?[]{}()\\"):
            raise ValueError("CodeBuddy cannot scope file permissions to a workspace path containing glob metacharacters.")
        tools.extend(["Edit", "Write"])
        settings["permissions"] = {
            "allow": [f"{tool}(/{path}/**)" for tool in ("Edit", "Write")],
            "deny": [f"Edit(/{path}/{directory}/**)" for directory in (".git", ".goodteam")],
        }
    return ["--tools", ",".join(tools), "--settings", json.dumps(settings)]


def result_from_stdout(stdout: str) -> str:
    try:
        records = json.loads(stdout)
    except ValueError:
        raise ValueError("CodeBuddy returned invalid JSON.") from None
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValueError("CodeBuddy did not return a result record.")
    results = [item for item in records if isinstance(item, dict) and item.get("type") == "result"]
    if len(results) != 1:
        raise ValueError("CodeBuddy did not return one final result.")
    final = results[0]
    if final.get("subtype") != "success" or final.get("is_error") is not False:
        raise ValueError("CodeBuddy reported an execution error. See stdout.txt and stderr.txt.")
    response = final.get("result")
    if not isinstance(response, str) or not response.strip():
        raise ValueError("CodeBuddy did not return a final answer.")
    return response
