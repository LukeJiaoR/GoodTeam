"""Read native Manager completion from the original conversation receipt."""


def completed_manager_result(store, logical_id):
    record = store.read().get("manager_app_server", {}).get("requests", {}).get(logical_id)
    if not record or record.get("status") != "completed":
        return None
    result = record.get("result") or {}
    evidence = result.get("manager_execution") or {}
    if (not isinstance(result.get("text"), str) or evidence.get("adapter") != "codex-app-server"
            or evidence.get("thread_id") != record.get("thread_id")
            or evidence.get("turn_id") != record.get("turn_id") or not evidence.get("turn_id")
            or evidence.get("status") != "completed"):
        from goodteam_os.manager_execution import ManagerExecutionError
        raise ManagerExecutionError("Native Manager completion identity is unconfirmed.", code="manager_interrupted")
    return result
