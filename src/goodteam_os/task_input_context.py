"""Task-facing, bounded input metadata; no private prompts or Runtime authority."""
from __future__ import annotations

from pathlib import Path
import os
import re

from .skills.filesystem import directory, subdirectory
from .skills.library import LOCAL_SCOPE_OWNER
from .skills.models import SkillError, SkillScope
from .skills.store import SkillStore
from .skills.task_context import workflow_skill_context
from .skills.task_locks import TaskSkillIdentity, TaskSkillLockStore
from .state import StateStore


SCHEMA = "goodteam.task-input-context.v1"


def _input_view(value: dict) -> dict:
    """Only original user-facing task fields, not the complete captured request."""
    if not isinstance(value, dict) or not isinstance(value.get("exact_request"), str):
        raise SkillError("task_start_input_missing", "The original task request was not captured.")
    result = {key: value.get(key, "") for key in ("exact_request", "source_type", "delivery_mode")}
    standards = value.get("delivery_standards", [])
    if (any(not isinstance(part, str) for part in result.values())
            or not isinstance(standards, list) or any(not isinstance(part, str) for part in standards)):
        raise SkillError("task_start_input_missing", "The captured task fields are unavailable.")
    return {**result, "delivery_standards": list(standards)}


def _lock_store(workspace: Path, root: Path) -> TaskSkillLockStore:
    from .service import service_state_dir

    operator = Path(service_state_dir()).expanduser().resolve()
    scope = SkillScope(kind="user", owner_id=LOCAL_SCOPE_OWNER)
    packages_root = operator / "skills" / "store"
    locks_root = operator / "skills" / "task-locks"
    # Absence may race this request. Every actual open must prohibit creation,
    # including the lock inode; an existence precheck is not sufficient.
    roots = (workspace, root)
    packages = SkillStore(packages_root, scope, worker_roots=roots, create=False)
    locks = TaskSkillLockStore(locks_root, packages, worker_roots=roots, create=False)
    return locks


def _authority_present() -> bool:
    """A surviving immutable object/lock cannot be treated as unregistered history."""
    from .service import service_state_dir

    root = Path(service_state_dir()).expanduser().resolve() / "skills" / "task-locks"
    try:
        root.lstat()
    except FileNotFoundError:
        return False  # Never initialized, or all authority lost: still unverified.
    scope = SkillScope(kind="user", owner_id=LOCAL_SCOPE_OWNER)
    with directory(root) as root_fd, subdirectory(root_fd, scope.namespace) as namespace:
        if {"index.json", ".lock"}.intersection(os.listdir(namespace)):
            return True
        with subdirectory(namespace, "objects") as objects, os.scandir(objects) as entries:
            return next(entries, None) is not None


def _locked_view(workspace: Path, root: Path, context: dict) -> dict:
    lock = _lock_store(workspace, root).read(context["task_lock_ref"], task=TaskSkillIdentity(**context["task"]))
    start = lock.get("start_input", {})
    if not isinstance(start, dict) or start.get("schema_version") != "task_skill_start_input.v1":
        raise SkillError("task_start_input_missing", "The original task request was not captured.")
    request = start.get("request")
    confirmed = request.get("confirmed_input") if isinstance(request, dict) else None
    if not isinstance(confirmed, dict):
        raise SkillError("task_start_input_missing", "The original task request was not captured.")
    binding = confirmed.get("producer_binding", {})
    if binding != {"workspace": str(workspace.resolve()), "worker_root": str(root.resolve())}:
        raise SkillError("snapshot_scope_mismatch", "Task input belongs to a different project root.")
    subjects = []
    for subject, record in sorted(lock["subjects"].items()):
        subjects.append({
            "subject": subject, "mode": record["binding"]["mode"],
            "diagnostics": [{"code": item["code"]} for item in record["diagnostics"]],
            "methods": [{key: item[key] for key in ("skill_id", "version_id", "package_digest", "activation")}
                        for item in record["items"]],
        })
    return {
        "capture_status": "locked", "input_origin": "task_start_lock",
        "input": _input_view(confirmed), "captured_at": lock["created_at"],
        "team_revision": lock["team_revision"],
        "task_lock_ref": {key: context["task_lock_ref"][key] for key in ("id", "digest")},
        "subjects": subjects,
    }


def handle_task_input_get(workspace: Path, path: str):
    match = re.fullmatch(r"/api/projects/([^/]+)/runs/([^/]+)/input-context", path)
    if not match:
        return None
    from . import web

    key = web._decode_project_api_key(match[1])
    run_id = web._decode_project_run_id(match[2])
    _key, _target, root = web._project_target_and_root(workspace, key)
    states = StateStore(workspace)
    state = states.read_project_current(key)
    if state is None or state.run_id != run_id:
        state = next((item for _path, item in states.list_project_archived(key) if item.run_id == run_id), None)
    if state is None:
        return 404, {"error": "run_not_found", "message": "This task does not belong to the selected project."}
    return 200, task_input_context_for_state(workspace, root, state, project_key=key, run_id=run_id)


def task_input_context_for_state(workspace: Path, root: Path, state, *, project_key: str, run_id: str) -> dict:
    """Share the existing read-only projection with the task's Manager context.

    Callers supply their resolved task and root; this never looks up a different
    run, captures new input, or substitutes live Team settings for missing proof.
    """
    key = project_key
    result = {"schema_version": SCHEMA, "project_key": key, "run_id": run_id,
              "task_kind": "workflow_run", "read_only": True,
              "capture_status": "unavailable", "input_origin": "unavailable", "input": None,
              "captured_at": "", "team_revision": None, "task_lock_ref": None, "subjects": []}
    if state.project_key != key or state.run_id != run_id:
        return {**result, "error_code": "snapshot_scope_mismatch"}
    try:
        context = workflow_skill_context(state.task_skill_context, run_id=run_id, project_key=key)
        if context:
            result.update(_locked_view(workspace, root, context))
        else:
            if _authority_present() and _lock_store(workspace, root).registered_task(
                    task=TaskSkillIdentity("workflow_run", key, run_id)):
                raise SkillError("task_context_missing", "A registered task lost its carried input reference.")
            result.update({"capture_status": "legacy_unverified", "input_origin": "workflow_state",
                           "input": _input_view({"exact_request": state.idea, "source_type": state.source_type,
                                                 "delivery_mode": state.delivery_mode,
                                                 "delivery_standards": state.delivery_standards})})
    except (SkillError, OSError, KeyError, TypeError, ValueError) as exc:
        # Do not disclose store paths, raw exceptions or substitute mutable
        # workflow/Team data for an unreadable registered capture.
        result["error_code"] = exc.code if isinstance(exc, SkillError) else "task_input_unavailable"
    return result
