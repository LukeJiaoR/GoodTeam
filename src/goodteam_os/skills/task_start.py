"""Explicit producer-side task capture; never a job-time latest resolver.

This connects bounded starts to the existing Team/Library service. Unconfigured
legacy projects stay legacy. Once a custom task has been captured, later default
restoration or Team loss cannot turn its replay into a legacy task.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import SkillConflict, SkillError, canonical_json
from .task_locks import TaskSkillIdentity
from .task_service import ProjectTaskSkillService


def capture_task_start(
    *, workspace: Path, project_key: str, worker_root: Path,
    task: TaskSkillIdentity, command_key: str, confirmed_input: dict[str, Any],
    subjects: tuple[str, ...] | None = None, expected_team_revision: int | None = None,
) -> dict[str, Any]:
    """Capture only at an authorized bounded start; replay saved confirmation.

    ``None`` subjects selects the workflow's complete non-Manager inventory.
    Manager/direct producers supply their explicit subject set. This helper does
    not guess task kind from a run ID, grant permissions, or dispatch a Worker.
    A supplied confirmation revision is checked, not silently rebased.
    """
    from goodteam_os.service import service_state_dir
    from goodteam_os.team_control_plane import TeamControlPlaneStore

    if task.project_key != project_key:
        raise SkillError("snapshot_scope_mismatch", "Task start belongs to another project.")
    if expected_team_revision is not None and (type(expected_team_revision) is not int or expected_team_revision < 0):
        raise SkillError("invalid_revision", "Task confirmation requires a valid Team revision.")
    if not isinstance(confirmed_input, dict):
        raise SkillError("invalid_snapshot", "Task start requires its exact confirmed input.")
    confirmed = deepcopy(confirmed_input)
    if "producer_binding" in confirmed:
        raise SkillError("invalid_snapshot", "Producer identity cannot come from the caller's confirmation payload.")
    confirmed["producer_binding"] = {"workspace": str(Path(workspace).expanduser().resolve()),
                                     "worker_root": str(Path(worker_root).expanduser().resolve())}
    try:
        canonical_json(confirmed)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SkillError("invalid_snapshot", "Task confirmation must contain finite JSON values.") from exc

    operator = Path(service_state_dir()).expanduser().resolve()
    # Avoid creating Operator stores for an entirely legacy project. Once the
    # authority exists, inspect its immutable receipt BEFORE reading Team.
    service = None
    if (operator / "skills" / "task-locks").exists():
        service = ProjectTaskSkillService(workspace, project_key, worker_roots=(worker_root,))
        previous = service.locks.find_task(task=task, command_key=command_key)
        if previous is not None:
            start = previous["lock"].get("start_input")
            if not isinstance(start, dict) or start.get("schema_version") != "task_skill_start_input.v1":
                raise SkillError("task_start_input_missing", "The original confirmation is unavailable; latest substitution is forbidden.")
            saved = start["request"]
            if (set(saved) != {"confirmed_input", "subjects", "role_contracts", "default_items", "expected_team_revision"}
                    or canonical_json(saved["confirmed_input"]) != canonical_json(confirmed)
                    or (subjects is not None and sorted(subjects) != saved["subjects"])
                    or (expected_team_revision is not None and expected_team_revision != saved["expected_team_revision"])):
                raise SkillConflict("operation_conflict", "The task already owns a different confirmation or subject inventory.")
            return service.begin(task, command_key=command_key, confirmed_input=confirmed,
                                 subjects=tuple(saved["subjects"]), role_contracts=saved["role_contracts"],
                                 expected_team_revision=saved["expected_team_revision"], default_items=saved["default_items"])

    team_store = TeamControlPlaneStore(workspace, project_key)
    with team_store._locked():
        team = team_store.read()
        if team is None:
            if expected_team_revision is not None:
                raise SkillError("team_unavailable", "The confirmed project Team is unavailable.")
            return {}
        if expected_team_revision is not None and team["revision"] != expected_team_revision:
            raise SkillConflict("team_revision_changed", "Team changed after confirmation; review the current methods before starting.")
        bindings = team.get("skill_bindings", {})
        selected = tuple(sorted(subjects if subjects is not None else (
            subject for subject in bindings if not subject.startswith("manager:")
        )))
        if not selected or len(selected) != len(set(selected)) or any(subject not in bindings for subject in selected):
            raise SkillError("binding_subject_unknown", "The task needs an explicit known subject inventory.")
        if not any(bindings[subject]["mode"] == "custom" for subject in selected):
            # Explicit pre-Skill compatibility. No custom-empty/default switch is
            # inferred here; already captured tasks took the receipt path above.
            return {}
        revision = team["revision"]
        contracts = {subject: {
            "ref": bindings[subject]["role_contract_ref"], "subject": subject,
            "input_contract": "Use only the bounded task and its supplied evidence.",
            "output_contract": "Return the result required by the explicit job output contract.",
            "authority_boundary": "Methods cannot grant permissions, advance workflow gates, or approve their own result.",
        } for subject in selected}
    if service is None:
        service = ProjectTaskSkillService(workspace, project_key, worker_roots=(worker_root,))
    return service.begin(task, command_key=command_key, confirmed_input=confirmed,
                         subjects=selected, role_contracts=contracts, expected_team_revision=revision)


def workflow_job_fields(state, *, role: str, command_key: str) -> dict[str, Any]:
    """Carry an existing workflow capture, never consult current configuration."""
    from .task_context import workflow_skill_context
    from .worker_requests import WORKER_METADATA_LOADER, subject_for_role

    context = workflow_skill_context(getattr(state, "task_skill_context", {}), run_id=state.run_id, project_key=getattr(state, "project_key", ""))
    if not context:
        if getattr(state, "project_key", ""):
            reject_missing_task_context(TaskSkillIdentity("workflow_run", state.project_key, state.run_id))
        return {}
    return {"task_skill_context": context, "skill_subject": subject_for_role(role),
            "skill_loader_version": WORKER_METADATA_LOADER, "project_key": state.project_key,
            "idempotency_key": command_key}


def manager_requires_skill_path(*, workspace: Path, project_key: str, worker_root: Path,
                                logical_message_id: str) -> bool:
    """A builtin adapter cannot silently bypass a current or original custom task."""
    from goodteam_os.service import service_state_dir
    from goodteam_os.team_control_plane import TeamControlPlaneStore

    if (Path(service_state_dir()) / "skills" / "task-locks").exists():
        service = ProjectTaskSkillService(workspace, project_key, worker_roots=(worker_root,))
        previous = service.locks.find_task(
            task=TaskSkillIdentity("manager_message", project_key, "message:" + logical_message_id),
            command_key="manager-start:" + project_key + ":message:" + logical_message_id,
        )
        if previous is not None:
            return True
    state = TeamControlPlaneStore(workspace, project_key).read()
    return bool(state and state.get("skill_bindings", {}).get("manager:orchestrator", {}).get("mode") == "custom")


def reject_missing_task_context(task: TaskSkillIdentity) -> None:
    """Only explicit producer identity can distinguish old legacy from lost refs."""
    from goodteam_os.service import service_state_dir
    from .library import SkillLibraryService
    from .task_locks import TaskSkillLockStore

    operator = Path(service_state_dir()).expanduser().resolve()
    root = operator / "skills" / "task-locks"
    if not root.exists():
        return
    packages = SkillLibraryService(operator).store
    if TaskSkillLockStore(root, packages).registered_task(task=task):
        raise SkillError("task_context_missing", "This task has a registered method lock; a missing reference cannot become legacy.")


def registered_project_scope(worker_root: Path) -> tuple[Path, str] | None:
    """Resolve a server-registered project root, never infer a task from a run ID.

    A standalone unregistered CLI project remains explicit legacy. A malformed or
    ambiguous registry is not interpreted as an empty set of Skill settings.
    """
    from goodteam_os.project_registry import get_default_registry_path
    from goodteam_os.state import normalize_project_key
    from .filesystem import directory, regular_file
    from .worker_requests import _json

    root = Path(worker_root).expanduser().resolve()
    registry_path = get_default_registry_path()

    def standalone_legacy():
        from goodteam_os.service import service_state_dir

        if (Path(service_state_dir()) / "skills" / "task-locks").exists():
            raise SkillError("project_scope_unknown", "Skill-aware dispatch requires a registered project; missing scope cannot become legacy.")
        return None
    try:
        with directory(registry_path.parent) as fd:
            try:
                raw = regular_file(fd, registry_path.name, maximum=4 * 1024 * 1024)[0]
            except FileNotFoundError:
                return standalone_legacy()
    except FileNotFoundError:
        return standalone_legacy()
    except SkillError:
        # An absent registry parent is a never-registered standalone project.
        if not registry_path.parent.exists():
            return standalone_legacy()
        raise
    payload = _json(raw)
    if payload.get("version", 1) != 1 or not isinstance(payload.get("projects"), list):
        raise SkillError("project_scope_unknown", "Project registry identity is unavailable.")
    matches = []
    for entry in payload["projects"]:
        if not isinstance(entry, dict):
            raise SkillError("project_scope_unknown", "Project registry contains an invalid identity.")
        configured_root = entry.get("root")
        if not configured_root:
            continue
        if not isinstance(configured_root, str):
            raise SkillError("project_scope_unknown", "Project registry root is invalid.")
        if Path(configured_root).expanduser().resolve() != root:
            continue
        config_path = entry.get("config_path")
        key = entry.get("key")
        if (not isinstance(config_path, str) or not isinstance(key, str)
                or normalize_project_key(key) != key):
            raise SkillError("project_scope_unknown", "Registered project is missing its canonical identity.")
        config = Path(config_path).expanduser().resolve()
        if config.parent.name != "projects" or not config.is_file():
            raise SkillError("project_scope_unknown", "Registered project has no supported task confirmation workspace.")
        matches.append((config.parent.parent, key))
    if len(set(matches)) > 1:
        raise SkillError("project_scope_ambiguous", "Worker root belongs to multiple project scopes; explicit selection is required.")
    return matches[0] if matches else standalone_legacy()


def execution_job_fields(store, *, execution_id: str, project_key: str, role: str) -> dict[str, Any]:
    """Carry a real issue-execution's original lock into Operator review/fix jobs."""
    from goodteam_os.execution.ledger import ExecutionLedger
    from .task_context import validate_task_skill_context
    from .worker_requests import WORKER_METADATA_LOADER, has_skill_fields, is_skill_job_id, subject_for_role

    run = ExecutionLedger(store.project_root).read_run(execution_id)
    task = TaskSkillIdentity("issue_execution", project_key, execution_id)
    if run is None:
        reject_missing_task_context(task)
        return {}
    jobs = [job for job in store.list_jobs() if job.get("id") == run.worker_job_id]
    if not jobs:
        if is_skill_job_id(run.worker_job_id):
            raise SkillError("snapshot_missing", "The original execution Worker is unavailable.")
        reject_missing_task_context(task)
        return {}
    original = jobs[0]
    context = original.get("task_skill_context")
    if not context:
        if is_skill_job_id(run.worker_job_id) or has_skill_fields(original):
            raise SkillError("task_context_missing", "The original execution lost its method reference.")
        reject_missing_task_context(task)
        return {}
    context = validate_task_skill_context(context)
    if context["task"] != {"kind": task.kind, "project_key": task.project_key, "logical_id": task.logical_id}:
        raise SkillError("snapshot_scope_mismatch", "Execution method reference belongs to another task.")
    return {"task_skill_context": context, "skill_subject": subject_for_role(role),
            "skill_loader_version": WORKER_METADATA_LOADER}
