"""Read original completed Manager receipts without starting a second Runtime.

This is result reuse, not execution admission or a replacement recovery protocol.
"""
import json
from hashlib import sha256

def verify_completed_manager_session(workers, job):
    """Recover only the original settled Manager prompt, never a status-only claim."""
    from goodteam_os.memory_storage import read_bytes
    from goodteam_os.pi_manager_tools import PiManagerTools
    from goodteam_os.skills.pi_methods import PiMethodInput
    from goodteam_os.skills.models import SkillError

    method = PiMethodInput(workers, job["id"], prepare=False)
    method.validate(workers.root / job["id"], job)
    metadata = json.loads(read_bytes(workers.root / job["id"] / "pi_session.json",
                                    maximum=256_000) or b"null")
    if not isinstance(metadata, dict):
        raise SkillError("manager_session_unconfirmed", "Original Manager session evidence is unavailable.")
    method.session_binding(metadata, create=False)
    policy = PiManagerTools()
    contract = {"version": policy.version, "tools": list(policy.tools),
                "guarantee": "tool-level; not an OS sandbox"}
    operation = metadata.get("active_operation") or {}
    # Re-render the same immutable method input, with its original scope checks.
    method.ref = method.provider.prepare(method.context_id)
    if (not method.manager_tools or metadata.get("manager_tool_contract") != contract
            or metadata.get("status") != "settled" or metadata.get("aborted") is True
            or not isinstance(operation, dict) or operation.get("state") != "settled"
            or operation.get("kind") != "initial_prompt" or not operation.get("id")
            or metadata.get("settled_operation_id") != operation["id"]
            or operation.get("prompt_sha256") != sha256(method.prompt().encode()).hexdigest()):
        raise SkillError("manager_session_unconfirmed", "Manager completion lacks its original settled tool-bound prompt.")


def completed_manager_result(store, job):
    """Return verified completed Pi Manager result, or None for non-completed paths.

    A claimed completion with missing/contradictory evidence must not silently
    turn into a fresh execution. Legacy and explicit Worker runs are unchanged.
    """
    from goodteam_os.memory_storage import read_bytes
    from goodteam_os.skills.models import SkillError

    if job.get("method_loading_mode") != "manager_pi_methods.v1":
        return None
    original = json.loads(read_bytes(store.root / job["id"] / "job.json", maximum=256_000) or b"null")
    if not isinstance(original, dict) or original.get("id") != job["id"]:
        raise SkillError("manager_completion_unconfirmed", "Original Manager request is unavailable.")
    raw = read_bytes(store.root / job["id"] / "status.json", maximum=256_000)
    if raw is None:
        return None
    terminal = json.loads(raw)
    if not isinstance(terminal, dict):
        raise SkillError("manager_completion_unconfirmed", "Original Manager status is invalid.")
    if terminal.get("status") != "completed":
        return None
    if (terminal.get("job_id") != job["id"] or terminal.get("skill_error_code")
            or any(terminal.get(field) != job.get(field) for field in
                   ("run_id", "role", "adapter", "phase", "idempotency_key"))):
        raise SkillError("manager_completion_unconfirmed", "Completion belongs to another Manager invocation.")
    verify_completed_manager_session(store, original)
    # Only the original local output belongs to this job, never a mutable path.
    result_path = store.root / job["id"] / "result.md"
    if read_bytes(result_path, maximum=64_000) is None:
        raise SkillError("manager_completion_unconfirmed", "Original Manager result is unavailable.")
    return {**terminal, "result_path": str(result_path)}
