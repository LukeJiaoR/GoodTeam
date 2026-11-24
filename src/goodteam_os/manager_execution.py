"""[INPUT]: 项目配置、团队状态与 Manager 推理深度约定。
[OUTPUT]: Manager 对话、分析入口与运行设置。
[POS]: 将项目配置和输入交给 Manager 运行时。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from goodteam_os.models import resolve_project_model_profile
from goodteam_os.worker_capabilities import MANAGER_DEFAULT_REASONING_DEPTH
from goodteam_os.worker_routing import worker_type_settings_for_role


class ManagerExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "execution_failed"):
        super().__init__(message)
        self.code = code


class ManagerResourceBusy(ManagerExecutionError):
    """An original logical operation already owns its execution."""


def run_manager_chat_turn(*, project_config: Path, project_key: str, message_id: str,
                          text: str, context: str, logical_message_id: str = "") -> dict[str, object]:
    from goodteam_os.manager_app_server import runtime_for

    result = runtime_for(project_config, project_key).run(
        logical_id=logical_message_id or message_id, text=text, context=context, attempt_id=message_id)
    return {"schema_version": "manager_conversation.v1", "intent": "project_question",
            "response": result["text"], "native_manager": True,
            "reply_suppressed": result.get("reply_suppressed", False),
            "manager_execution": result["manager_execution"]}


def run_manager_analysis(*, project_config: Path, project_key: str, logical_id: str,
                         task: str, context: str, phase: str, run_id: str = "", authority_check=None):
    """Bounded background judgment on its own native thread, sharing business services."""
    from goodteam_os.manager_app_server import runtime_for

    return runtime_for(project_config, project_key).run(
        logical_id=logical_id, text=task, context=context, phase=phase, run_id=run_id,
        background=True, authority_check=authority_check)


def run_manager_interpretation(*, project_config: Path, run_id: str, project_key: str,
                               intent_id: str, change_request: str, context: str,
                               logical_intent_id: str = "") -> dict[str, object]:
    result = run_manager_analysis(project_config=project_config, project_key=project_key,
        logical_id="intent:" + (logical_intent_id or intent_id), task=_manager_interpretation_task(change_request),
        context=context, phase="manager_intent", run_id=run_id)
    parsed = _parse_interpretation(result["text"])
    return {"schema_version": "intent_interpretation.v1", "source": "manager_app_server",
            "summary": parsed["summary"], "items": [{"kind": "explicit_request", "text": item} for item in parsed["items"]],
            "request": change_request, "manager_execution": result["manager_execution"]}


def _model_name(configured_model: str) -> str:
    if ":" not in configured_model:
        return configured_model
    _configured_adapter, model = configured_model.split(":", 1)
    return model.strip()


def _configured_manager_model(target: dict[str, object]) -> str:
    from goodteam_os.team_control_plane import attached_team_state

    state = attached_team_state(target)
    if state is not None:
        manager = dict(state.get("manager", {}) or {})
        runtime = dict(manager.get("runtime", {}) or {})
        model = str(runtime.get("model", "") or "").strip()
        if model:
            provider = str(runtime.get("provider", "") or "").strip()
            return f"{provider}:{model}" if provider else model
    configured_path = str(os.getenv("GOODTEAM_MODEL_CONFIG", "") or "").strip()
    model_config = Path(configured_path).expanduser() if configured_path else Path(__file__).resolve().parents[2] / "models.yaml"
    try:
        _profile, role_models = resolve_project_model_profile(model_config, target, role_overrides=[])
    except (OSError, ValueError):
        return str(dict(target.get("role_model_overrides", {})).get("orchestrator", "") or "").strip()
    return str(dict(role_models.get("orchestrator", {})).get("primary", "") or "").strip()


def _manager_settings(target: dict[object, object]) -> dict[str, object]:
    from goodteam_os.team_control_plane import attached_team_state

    state = attached_team_state(target)
    if state is not None:
        runtime = dict(dict(state.get("manager", {}) or {}).get("runtime", {}) or {})
        settings = {"reasoning_depth": runtime.get("reasoning_depth", "default"),
                    "additional_prompt": str(runtime.get("additional_prompt", "") or "")}
    else:
        settings = worker_type_settings_for_role(target, "orchestrator")
    if settings.get("reasoning_depth", "default") == "default":
        settings["reasoning_depth"] = MANAGER_DEFAULT_REASONING_DEPTH
    settings.pop("desired_instances", None)
    settings.pop("pi_thinking_level", None)
    return settings


class _ManagerResourceLease:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.owner = (os.getpid(), threading.get_ident())
        self.active = True

    def valid_for(self, root):
        return (self.active and self.root == Path(root).resolve()
                and self.owner == (os.getpid(), threading.get_ident()))


@contextmanager
def manager_resource_slot(project_root: Path):
    """Compatibility scope lease; native threads no longer share a project slot.

    Existing continuation/Goal services still own their business locks. Each
    distinct native analysis keeps its own logical identity and original budget.
    """
    lease = _ManagerResourceLease(project_root)
    try:
        yield lease
    finally:
        lease.active = False


@contextmanager
def _manager_turn_lock(project_root: Path, message_id: str, *, resource_slot=None):
    digest = hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
    lock_root = project_root / ".goodteam" / "workers" / "task-assistant-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_root / f"{digest}.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if resource_slot is not None and (not isinstance(resource_slot, _ManagerResourceLease)
                or not resource_slot.valid_for(project_root)):
            raise ManagerExecutionError("Manager resource reservation is no longer valid.")
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _manager_interpretation_task(change_request: str) -> str:
    return "\n".join(
        [
            "Produce a bounded Task Assistant interpretation of this request for human confirmation.",
            "The JSON shape must be: {\"summary\": \"short restatement\", \"items\": [\"explicit item\"]}.",
            "Every item must be directly supported by the exact request below.",
            "",
            "## Exact user request",
            change_request,
        ]
    )


def _parse_chat_turn(output: str, *, require_task_intake: bool = True, require_delegation: bool = True) -> dict[str, object]:
    text = str(output or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a structured chat response.") from exc
    if not isinstance(payload, dict):
        raise ManagerExecutionError("The configured Task Assistant did not return a structured chat response.")
    intent = str(payload.get("intent", "") or "").strip()
    response = str(payload.get("response", "") or "").strip()
    if intent not in {"work_request", "project_question", "team_management", "project_operation", "ambiguous"} or not response or len(response) > 4_000:
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured chat response.")
    parsed: dict[str, object] = {"intent": intent, "response": response}
    if "task_action" in payload:
        action = payload["task_action"]
        from goodteam_os.team_control_plane import ROLE_KEYS
        from goodteam_os.project_iteration import normalize_write_domains

        fields = {"kind", "role", "execution_policy", "write_domains", "acceptance_criteria"}
        if (intent != "work_request" or not isinstance(action, dict)
                or set(action) - {"dependencies"} not in ((fields | {"delegation"},) if require_delegation else (fields, fields | {"delegation"}))
                or not isinstance(action.get("dependencies", []), list)
                or any(not isinstance(item, str) or not item.strip() for item in action.get("dependencies", []))
                or len(set(action.get("dependencies", []))) != len(action.get("dependencies", []))
                or action.get("kind") != "queue_task" or action.get("role") not in ROLE_KEYS
                or action.get("execution_policy") not in {"read_only", "workspace_write"}
                or not isinstance(action.get("write_domains"), list)
                or (action["write_domains"] and normalize_write_domains(action["write_domains"]) is None)
                or (action["execution_policy"] == "read_only" and action["write_domains"])
                or (action["execution_policy"] == "workspace_write" and not action["write_domains"])
                or not isinstance(action.get("acceptance_criteria"), list)
                or not 1 <= len(action["acceptance_criteria"]) <= 8
                or any(not isinstance(item, str) or not item.strip()
                       for item in action["acceptance_criteria"])):
            raise ManagerExecutionError("The Manager returned an invalid task action.")
        if "delegation" in action:
            from goodteam_os.task_delegation import validate_delegation

            try:
                validate_delegation(action["delegation"])
            except ValueError as exc:
                raise ManagerExecutionError("The Manager returned an invalid task delegation.") from exc
        parsed["task_action"] = action
    if intent == "project_operation":
        operation = payload.get("project_operation")
        if (not isinstance(operation, dict) or set(operation) != {"kind", "target_id"}
                or operation.get("kind") not in {"explain", "confirm_delivery", "stop_continuation", "advance_continuation"}
                or not isinstance(operation.get("target_id"), str) or len(operation["target_id"]) > 256):
            raise ManagerExecutionError("The Task Assistant returned an invalid project operation.")
        parsed["project_operation"] = operation
    if intent == "work_request" and require_task_intake and "task_intake" not in payload:
        raise ManagerExecutionError("The Task Assistant did not assess whether the task is ready to draft.")
    if intent in {"work_request", "ambiguous"} and "task_intake" in payload:
        intake = _parse_task_intake(payload["task_intake"])
        parsed["task_intake"] = intake
        if intent == "work_request" and not intake["ready"]:
            # An incomplete assessment cannot expose a Start task action, even
            # when the model contradicts itself with a work_request label.
            parsed.update(intent="ambiguous", response="\n\n".join(intake["missing_decisions"]))
    if intent == "team_management":
        commands = _parse_team_commands(payload.get("team_commands"))
        authorization = str(payload.get("team_authorization", "") or "").strip()
        if not commands:
            raise ManagerExecutionError("The configured Task Assistant returned a team decision without commands.")
        if authorization not in {"user_authorized", "manager_proposed"}:
            raise ManagerExecutionError("The configured Task Assistant returned an invalid team authorization.")
        parsed["team_commands"] = commands
        parsed["team_authorization"] = authorization
    return parsed


def _parse_task_intake(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or type(raw.get("ready")) is not bool:
        raise ManagerExecutionError("The Task Assistant returned an invalid task intake assessment.")
    questions = raw.get("missing_decisions")
    sources = raw.get("source_message_ids")
    for values, limit, width in ((questions, 8, 1_000), (sources, 24, 256)):
        if (not isinstance(values, list) or len(values) > limit
                or any(not isinstance(value, str) or not value.strip() or len(value) > width for value in values)):
            raise ManagerExecutionError("The Task Assistant returned an invalid task intake assessment.")
    ready = raw["ready"] and not questions
    if (not ready and not questions) or (ready and not sources):
        raise ManagerExecutionError("The Task Assistant returned an incomplete task intake assessment.")
    return {"ready": ready, "missing_decisions": questions, "source_message_ids": sources}


TEAM_COMMAND_KINDS = {
    "set_role_runtime_source",
    "enable_role",
    "disable_role",
    "initialize_worker",
    "activate_worker",
    "inactivate_worker",
    "release_worker",
    "delete_worker",
    "change_worker_runtime",
    "change_worker_role",
    "rename_worker",
    "change_manager_runtime",
}


def _parse_team_commands(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    commands: list[dict[str, object]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "") or "").strip()
        if kind not in TEAM_COMMAND_KINDS:
            raise ManagerExecutionError("The configured Task Assistant returned an unknown team command.")
        command: dict[str, object] = {"kind": kind}
        for key in ("role", "worker_id", "name"):
            value = str(item.get(key, "") or "").strip()
            if value:
                command[key] = value[:200]
        for key in ("provider", "model", "reasoning_depth", "additional_prompt"):
            if key in item:
                command[key] = str(item.get(key, "") or "")[:4000]
        if kind == "set_role_runtime_source" and "worker_id" in item:
            command["worker_id"] = str(item["worker_id"] or "").strip()[:200]
        commands.append(command)
    return commands


def _parse_interpretation(output: str) -> dict[str, Any]:
    text = str(output or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a structured interpretation.") from exc
    if not isinstance(payload, dict):
        raise ManagerExecutionError("The configured Task Assistant did not return a structured interpretation.")
    summary = str(payload.get("summary", "") or "").strip()
    raw_items = payload.get("items")
    if not summary or len(summary) > 4_000 or not isinstance(raw_items, list):
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured interpretation.")
    items: list[str] = []
    for raw_item in raw_items:
        item = str(raw_item or "").strip()
        if item and item not in items:
            items.append(item)
    if not items or len(items) > 20 or any(len(item) > 1_000 for item in items):
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured interpretation.")
    return {"summary": summary, "items": items}

