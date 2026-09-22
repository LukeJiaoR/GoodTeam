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
    logical_message_id: str = "",
) -> dict[str, object]:
    target = load_project_target(project_config)
    project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if project_root is None:
        raise ManagerExecutionError("The Task Assistant project runtime root is unavailable.")

    adapter = resolve_role_adapter(target, "orchestrator")
    configured_model = _configured_manager_model(target)
    model = _model_name(configured_model)
    settings = _manager_settings(target)
    with _manager_turn_lock(project_root, "message:" + (logical_message_id or message_id)):
        store = WorkerJobStore(project_root)
        job, created = _create_manager_job(
            store, project_config=project_config, project_root=project_root, project_key=project_key,
            logical_id="message:" + (logical_message_id or message_id),
            preserve_captured_context=bool(logical_message_id), spec=dict(
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
            ),
        )
        job_id = str(job["id"])
        result = store.run_job(job_id) if created or job.get("status") == "queued" or job.get("task_skill_context") else job
    if result.get("skill_error_code"):
        raise ManagerExecutionError("The configured Manager methods are blocked before Runtime execution.",
                                    code=str(result["skill_error_code"]))
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
        "manager_execution": _manager_execution_evidence(
            job_id, str(job.get("adapter", adapter)), str(job.get("model", model)),
            {**settings, **{k: job[k] for k in ("reasoning_depth", "additional_prompt") if k in job}}, result),
    }


def run_manager_interpretation(
    *,
    project_config: Path,
    run_id: str,
    project_key: str,
    intent_id: str,
    change_request: str,
    context: str,
    logical_intent_id: str = "",
) -> dict[str, object]:
    target = load_project_target(project_config)
    project_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if project_root is None:
        raise ManagerExecutionError("The Task Assistant project runtime root is unavailable.")

    adapter = resolve_role_adapter(target, "orchestrator")
    configured_model = _configured_manager_model(target)
    model = _model_name(configured_model)
    settings = _manager_settings(target)

    with _manager_turn_lock(project_root, "intent:" + (logical_intent_id or intent_id)):
        store = WorkerJobStore(project_root)
        job, created = _create_manager_job(
            store, project_config=project_config, project_root=project_root, project_key=project_key,
            logical_id="intent:" + (logical_intent_id or intent_id), spec=dict(
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
                idempotency_key=f"manager-intent:{logical_intent_id or intent_id}",
                **settings,
            ),
        )
        job_id = str(job["id"])
        result = store.run_job(job_id) if created or job.get("status") == "queued" or job.get("task_skill_context") else job
    if result.get("skill_error_code"):
        raise ManagerExecutionError("The configured Manager methods are blocked before Runtime execution.",
                                    code=str(result["skill_error_code"]))
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
        "manager_execution": _manager_execution_evidence(
            job_id, str(job.get("adapter", adapter)), str(job.get("model", model)),
            {**settings, **{k: job[k] for k in ("reasoning_depth", "additional_prompt") if k in job}}, result),
    }


def _create_manager_job(store, *, project_config: Path, project_root: Path, project_key: str,
                        logical_id: str, spec: dict[str, Any], preserve_captured_context: bool = False) -> tuple[dict[str, Any], bool]:
    """Bind the actual logical message/intent, not the shared conversation run."""
    from goodteam_os.skills.models import SkillConflict, SkillError
    from goodteam_os.skills.task_locks import TaskSkillIdentity
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.worker_requests import (
        WORKER_METADATA_LOADER, has_skill_fields, is_skill_job_id, original_worker_request,
    )

    config = Path(project_config).expanduser().resolve()
    if config.parent.name == "projects":
        # A completed legacy message remains legacy when project configuration
        # changes later. It is not a fresh authorization to invoke new methods.
        existing = store.find_job_by_idempotency_key(spec["idempotency_key"])
        if existing is None and preserve_captured_context and logical_id.startswith("message:"):
            from goodteam_os.assistant_requests import worker_attempt_id

            first_key = "task-assistant-turn:" + worker_attempt_id(project_key, logical_id[len("message:"):], 1)
            first = store.find_job_by_idempotency_key(first_key)
            if first is not None and not is_skill_job_id(first.get("id")) and not has_skill_fields(first):
                return _replay_legacy_manager_request(store, first, spec)
        if existing is not None and not is_skill_job_id(existing.get("id")) and not has_skill_fields(existing):
            job_root = store.root / str(existing["id"])
            if (existing.get("project_key") != project_key or existing.get("phase") != spec["phase"]
                    or existing.get("run_id") != spec["run_id"]
                    or (job_root / "task.md").read_text(encoding="utf-8") != spec["task"]
                    or (not preserve_captured_context
                        and (job_root / "context.md").read_text(encoding="utf-8") != spec["context"])):
                raise SkillConflict("operation_conflict", "Manager message key already owns different original inputs.")
            return existing, False
        task = TaskSkillIdentity("manager_message", project_key, logical_id)
        start_key = "manager-start:" + project_key + ":" + logical_id
        confirmed = {key: spec[key] for key in ("run_id", "phase", "task", "context", "constraints", "expected_artifacts")}
        if preserve_captured_context:
            from goodteam_os.service import service_state_dir
            from goodteam_os.skills.task_service import ProjectTaskSkillService

            if (Path(service_state_dir()) / "skills" / "task-locks").exists():
                service = ProjectTaskSkillService(config.parent.parent, project_key, worker_roots=(project_root,))
                previous = service.locks.find_task(task=task, command_key=start_key)
                if previous is not None:
                    start = previous["lock"].get("start_input")
                    if not isinstance(start, dict) or start.get("schema_version") != "task_skill_start_input.v1":
                        raise SkillError("task_start_input_missing", "The original Manager confirmation is unavailable.")
                    original = start["request"]["confirmed_input"]
                    if any(original.get(key) != confirmed[key] for key in ("run_id", "phase", "task", "constraints", "expected_artifacts")):
                        raise SkillConflict("operation_conflict", "Logical Manager message already owns another request.")
                    # TaskAssistant's context is server-derived and changes as the
                    # timeline/Team updates. Its explicit logical retry must use
                    # the first captured context, not the newly rendered one.
                    confirmed = {key: value for key, value in original.items() if key != "producer_binding"}
                    spec = {**spec, "context": original["context"]}
        context = capture_task_start(
            workspace=config.parent.parent, project_key=project_key, worker_root=project_root,
            task=task, command_key=start_key, confirmed_input=confirmed,
            subjects=("manager:orchestrator",),
        )
        if context:
            spec = {**spec, "idempotency_key": "manager-job:" + project_key + ":" + logical_id}
            original = original_worker_request(store, command_key=spec["idempotency_key"], context=context)
            spec = original if original is not None else {
                **spec, "task_skill_context": context, "skill_subject": "manager:orchestrator",
                "skill_loader_version": WORKER_METADATA_LOADER,
            }
    return store.create_or_reuse_job(**spec)



def _replay_legacy_manager_request(store, first: dict[str, Any], spec: dict[str, Any]):
    """Retry an already in-flight legacy message without adopting a new binding.

    Legacy files remain legacy evidence, not a tamper-proof Skill contract. Reuse
    the captured input/runtime/policy, retaining distinct physical attempt jobs.
    """
    from goodteam_os.reviewer_retry_inputs import JOB_REQUEST_FIELDS, _load_request
    from goodteam_os.skills.models import SkillConflict
    from goodteam_os.workers import _role_prompt_for_job

    saved, files, research = _load_request(store, first)
    if (any(saved.get(key) != spec.get(key) for key in ("run_id", "project_key", "role", "phase", "expected_artifacts"))
            or files["task.md"] != spec["task"]
            or files["role_prompt.md"] != _role_prompt_for_job(saved["role"])[1]):
        raise SkillConflict("operation_conflict", "Legacy Manager retry no longer matches its original task/Role contract.")
    request = {key: saved[key] for key in JOB_REQUEST_FIELDS if key in saved}
    constraints = files["constraints.md"]
    request.update(task=files["task.md"], context=files["context.md"],
                   constraints=[constraints[2:]] if constraints else [], idempotency_key=spec["idempotency_key"])
    if research is not None:
        request["research_report_source"] = str(store.root / saved["id"] / "research-report.md")
    return store.create_or_reuse_job(**request)

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
            "Use intent work_request only when the requested product, change or deliverable is concrete enough to draft a task from the current message and conversation context.",
            "A new-project introduction or request to brainstorm/shape an unspecified idea is discovery, even if it says start building. Use ambiguous and ask one concrete question about the product or intended users. Do not propose a task or invent scope until the user supplies a concrete outcome.",
            "Use intent project_understanding for a broad request to inspect or explain the current project.",
            "Use intent project_question for a factual question about the current project (Git, framework, files, commands). Answer it directly using read-only inspection or supplied evidence; do not start preparation or propose a task.",
            "Resolve short references such as 'it', 'this', '它' or '他' to the current project when conversation context supports that reading. For example '他有git吗' asks whether this project uses Git, not whether a Worker can operate Git.",
            "For project_question, inspect the current checkout when evidence is missing, using read-only tools. For Git presence use git rev-parse --is-inside-work-tree; do not initialize, fetch, commit, or modify the repository. If inspection is unavailable, say what could not be verified instead of guessing.",
            "Ask a clarifying question only when a materially different interpretation remains after using the project and recent conversation context. Do not append execution-confirmation boilerplate to factual answers.",
            "Use intent team_management only for a persistent Team composition change (enable/disable a capability, add/pause/release a worker, change a worker's runtime or role).",
            "Use intent ambiguous when a safe clarifying question is required.",
            "Acknowledgements, greetings, hypothetical discussion and cancellation are not work requests. Use ambiguous and respond without claiming any action happened.",
            "Only an explicit request to inspect the repository is project_understanding; discussing the task draft does not request inspection.",
            "For work requests, preserve the user's original wording. Existing drafts are revised, not silently replaced. Execution and preparation require separate user confirmation.",
            'The JSON shape must be: {"intent": "work_request|project_understanding|project_question|team_management|ambiguous", "response": "concise user-facing reply", "team_commands": [{"kind": "enable_role|disable_role|initialize_worker|activate_worker|inactivate_worker|release_worker|delete_worker|change_worker_runtime|change_worker_role|rename_worker|change_manager_runtime", "role": "optional role key", "worker_id": "optional worker id"}], "team_authorization": "user_authorized|manager_proposed"}.',
            "team_commands and team_authorization are required only for intent team_management; omit them otherwise.",
            "Every team command is a proposal requiring a separate user confirmation. The team_authorization field is descriptive and never grants execution authority.",
            "Use exact Worker IDs from the Team summary. When names match multiple Workers, list their roles, names and IDs and ask the user to select; never choose the first match.",
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
    if intent not in {"work_request", "project_understanding", "project_question", "team_management", "ambiguous"} or not response or len(response) > 4_000:
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
