from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from goodteam_os.graph import SESSION_BACKENDS, resolve_project_runtime_root, resolve_role_adapter, worker_type_settings_for_role
from goodteam_os.models import resolve_project_model_profile
from goodteam_os.projects import load_project_target
from goodteam_os.workers import WorkerJobStore


class ManagerExecutionError(RuntimeError):
    """Raised when a configured Task Assistant cannot produce a safe interpretation."""

    def __init__(self, message: str, *, code: str = "execution_failed"):
        super().__init__(message)
        self.code = code


def run_manager_chat_turn(
    *,
    project_config: Path,
    project_key: str,
    message_id: str,
    text: str,
    context: str,
) -> dict[str, object]:
    target = load_project_target(project_config)
    project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if project_root is None:
        raise ManagerExecutionError("The Task Assistant project runtime root is unavailable.")

    adapter = resolve_role_adapter(target, "orchestrator")
    configured_model = _configured_manager_model(target)
    model = _model_name(configured_model)
    settings = _manager_settings(target)
    with _manager_turn_lock(project_root, message_id):
        store = WorkerJobStore(project_root)
        job, created = store.create_or_reuse_job(
            run_id=f"task-assistant:{project_key}",
            role="orchestrator",
            adapter=adapter,
            model=model,
            task=_manager_chat_task(text),
            context=context,
            constraints=[
                "Classify the user's current message without performing consequential actions.",
                "Do not modify project files, workflow state, artifacts, approvals, or external systems.",
                "Return exactly one JSON object and no prose or markdown outside it.",
            ],
            expected_artifacts=[],
            project_key=project_key,
            phase="task_assistant_chat",
            execution_policy="read_only",
            idempotency_key=f"task-assistant-turn:{message_id}",
            **settings,
        )
        job_id = str(job["id"])
        result = store.run_job(job_id) if created or job.get("status") == "queued" else job
    if str(result.get("status", "")) != "completed":
        from goodteam_os.assistant_runtime import safe_failure

        summary = str(result.get("summary", "") or "Task Assistant worker failed.").strip()
        try:
            with (store.root / job_id / "stderr.txt").open(encoding="utf-8", errors="replace") as stderr:
                summary += "\n" + stderr.read(65536)
        except OSError:
            pass
        failure = safe_failure(summary)
        raise ManagerExecutionError(failure["message"], code=failure["code"])
    result_path = Path(str(result.get("result_path") or store.root / job_id / "result.md"))
    try:
        output = result_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a readable result.") from exc
    parsed = _parse_chat_turn(output)
    return {
        "schema_version": "task_assistant_turn.v1",
        **parsed,
        "manager_execution": _manager_execution_evidence(job_id, adapter, model, settings, result),
    }


def run_manager_interpretation(
    *,
    project_config: Path,
    run_id: str,
    project_key: str,
    intent_id: str,
    change_request: str,
    context: str,
) -> dict[str, object]:
    target = load_project_target(project_config)
    project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if project_root is None:
        raise ManagerExecutionError("The Task Assistant project runtime root is unavailable.")

    adapter = resolve_role_adapter(target, "orchestrator")
    configured_model = _configured_manager_model(target)
    model = _model_name(configured_model)
    settings = _manager_settings(target)

    store = WorkerJobStore(project_root)
    job = store.create_job(
        run_id=run_id,
        role="orchestrator",
        adapter=adapter,
        model=model,
        task=_manager_interpretation_task(change_request),
        context=context,
        constraints=[
            "Interpret only the user's explicit request; do not add requirements.",
            "Do not modify project files, workflow state, artifacts, approvals, or external systems.",
            "Return exactly one JSON object and no prose or markdown outside it.",
        ],
        expected_artifacts=[],
        project_key=project_key,
        phase="manager_intent",
        execution_policy="read_only",
        idempotency_key=f"manager-intent:{intent_id}",
        **settings,
    )
    job_id = str(job["id"])
    result = store.run_job(job_id)
    if str(result.get("status", "")) != "completed":
        summary = str(result.get("summary", "") or "Task Assistant worker failed.").strip()
        raise ManagerExecutionError(f"The configured Task Assistant could not complete: {summary}")

    result_path = Path(str(result.get("result_path") or store.root / job_id / "result.md"))
    try:
        output = result_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManagerExecutionError("The configured Task Assistant did not return a readable result.") from exc
    parsed = _parse_interpretation(output)
    return {
        "schema_version": "intent_interpretation.v1",
        "source": "manager_worker",
        "summary": parsed["summary"],
        "items": [
            {"kind": "explicit_request", "text": item}
            for item in parsed["items"]
        ],
        "request": change_request,
        "manager_execution": _manager_execution_evidence(job_id, adapter, model, settings, result),
    }


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
        manager = dict(state.get("manager", {}) or {})
        runtime = dict(manager.get("runtime", {}) or {})
        reasoning_depth = str(runtime.get("reasoning_depth", "default") or "default")
        settings = {
            "reasoning_depth": reasoning_depth,
            "additional_prompt": str(runtime.get("additional_prompt", "") or ""),
            # Task Assistant is one logical owner. Parallel authority is
            # intentionally unsupported.
            "desired_instances": 1,
            "pi_thinking_level": reasoning_depth
            if resolve_role_adapter(target, "orchestrator") in SESSION_BACKENDS and reasoning_depth != "default"
            else "",
        }
    else:
        settings = worker_type_settings_for_role(target, "orchestrator")
        # Task Assistant is one logical owner. Parallel authority is intentionally unsupported.
        settings["desired_instances"] = 1
    # "default" means GoodTeam's portable default, not an unbounded user-global CLI value.
    # Passing an explicit supported level keeps model compatibility deterministic.
    if settings.get("reasoning_depth") == "default":
        settings["reasoning_depth"] = "high"
    return settings


def _manager_execution_evidence(
    job_id: str,
    adapter: str,
    model: str,
    settings: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    runtime_policy_warning = bool(result.get("runtime_policy_warning", True))
    return {
        "job_id": job_id,
        "role": "orchestrator",
        "adapter": adapter,
        "model": str(result.get("runtime_model") or model),
        "reasoning_depth": str(settings.get("reasoning_depth") or "default"),
        "execution_policy": "read_only",
        "runtime_sandbox_mode": str(result.get("runtime_sandbox_mode") or "unknown"),
        "runtime_approval_policy": str(result.get("runtime_approval_policy") or "unknown"),
        "runtime_unsafe_mode": bool(result.get("runtime_unsafe_mode", True)),
        "runtime_policy_warning": runtime_policy_warning,
        "runtime_policy_warning_reason": str(
            result.get("runtime_policy_warning_reason")
            or ("Runtime policy evidence was unavailable." if runtime_policy_warning else "")
        ),
    }


@contextmanager
def _manager_turn_lock(project_root: Path, message_id: str):
    digest = hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
    lock_root = project_root / ".goodteam" / "workers" / "task-assistant-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_root / f"{digest}.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
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


def _manager_chat_task(text: str) -> str:
    return "\n".join(
        [
            "Respond as the project's Task Assistant and classify the user's current message.",
            "Use intent work_request for a request that should become an executable task.",
            "Use intent project_understanding for a request to inspect or explain the current project.",
            "Use intent team_management only for a persistent Team composition change (enable/disable a capability, add/pause/release a worker, change a worker's runtime or role).",
            "Use intent ambiguous when a safe clarifying question is required.",
            "Acknowledgements, greetings, hypothetical discussion and cancellation are not work requests. Use ambiguous and respond without claiming any action happened.",
            "Only an explicit request to inspect the repository is project_understanding; discussing the task draft does not request inspection.",
            "For work requests, preserve the user's original wording. Existing drafts are revised, not silently replaced. Execution and preparation require separate user confirmation.",
            'The JSON shape must be: {"intent": "work_request|project_understanding|team_management|ambiguous", "response": "concise user-facing reply", "team_commands": [{"kind": "enable_role|disable_role|initialize_worker|activate_worker|inactivate_worker|release_worker|delete_worker|change_worker_runtime|change_worker_role|rename_worker|change_manager_runtime", "role": "optional role key", "worker_id": "optional worker id"}], "team_authorization": "user_authorized|manager_proposed"}.',
            "team_commands and team_authorization are required only for intent team_management; omit them otherwise.",
            "Every team command is a proposal requiring a separate user confirmation. The team_authorization field is descriptive and never grants execution authority.",
            "Never claim a Team change has already happened. Do not propose changes when the user asks for an explanation or explicitly says not to change the Team.",
            "A preference scoped to the current task or run (for example 'this time skip research') is never a team command.",
            "Never claim a capability the provided Team capability summary does not have; explain missing capability boundaries when they affect the request.",
            "Do not claim that project inspection or workflow execution has completed.",
            "",
            "## Current user message",
            text,
        ]
    )


def _parse_chat_turn(output: str) -> dict[str, object]:
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
    if intent not in {"work_request", "project_understanding", "team_management", "ambiguous"} or not response or len(response) > 4_000:
        raise ManagerExecutionError("The configured Task Assistant returned an invalid structured chat response.")
    parsed: dict[str, object] = {"intent": intent, "response": response}
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


TEAM_COMMAND_KINDS = {
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
