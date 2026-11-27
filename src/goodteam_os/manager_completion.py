"""[INPUT]: 接收原生 Manager 请求与执行回执。
[OUTPUT]: 验证执行后端并复用原完成结果。
[POS]: 为对话重试和后台判断验证持久化回执。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""


NATIVE_MANAGER_ADAPTERS = {"codex-app-server", "codebuddy-acp"}


def recorded_manager_adapter(record):
    return (record.get("runtime") or {}).get("adapter", "codex-app-server")


def completed_manager_result(store, logical_id):
    record = store.read().get("manager_app_server", {}).get("requests", {}).get(logical_id)
    if not record or record.get("status") != "completed":
        return None
    result = record.get("result") or {}
    evidence = result.get("manager_execution") or {}
    adapter = recorded_manager_adapter(record)
    if (not isinstance(result.get("text"), str) or adapter not in NATIVE_MANAGER_ADAPTERS
            or evidence.get("adapter") != adapter
            or evidence.get("thread_id") != record.get("thread_id")
            or evidence.get("turn_id") != record.get("turn_id") or not evidence.get("turn_id")
            or evidence.get("status") != "completed"):
        from goodteam_os.manager_execution import ManagerExecutionError
        raise ManagerExecutionError("Native Manager completion identity is unconfirmed.", code="manager_interrupted")
    return result
