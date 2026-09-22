"""Reject legacy Worker requests that still name an authoritative Skill task.

A logical job key is not the task identity. Removing its method fields and
changing that key must not detach a Worker from an already captured workflow
or issue execution. These checks match existing explicit task-kind records;
they never create a task, infer a new task kind, or query current Team methods.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import SkillError
from .task_locks import TaskSkillIdentity, TaskSkillLockStore


def reject_legacy_task_downgrade(store, request: dict[str, Any]) -> None:
    """Check task ownership independently of mutable job IDs and logical keys.

    Original legacy tasks remain legacy: a different new task's method capture
    does not match this request, even within the same project. Manager's shared
    conversation run ID is never treated as a logical message/intent ID.
    """
    from goodteam_os.service import service_state_dir
    from .library import SkillLibraryService

    operator = Path(service_state_dir()).expanduser().resolve()
    root = operator / "skills" / "task-locks"
    if not root.exists():
        return
    project_key = request.get("project_key")
    run_id = request.get("run_id")
    execution_id = request.get("execution_id")
    if not isinstance(project_key, str) or not project_key:
        # Missing caller fields are not proof of a standalone legacy project.
        # Resolve only the server-owned project registry, never a client path.
        from .task_start import registered_project_scope

        scope = registered_project_scope(Path(store.project_root))
        if scope is None:
            raise SkillError("project_scope_unknown", "Skill-aware Worker dispatch requires explicit project scope.")
        project_key = scope[1]
    identities = []
    if isinstance(run_id, str) and run_id:
        identities.append(TaskSkillIdentity("workflow_run", project_key, run_id))
    if isinstance(execution_id, str) and execution_id:
        identities.append(TaskSkillIdentity("issue_execution", project_key, execution_id))
    if not identities:
        raise SkillError("task_scope_unknown", "Skill-aware Worker dispatch requires a bounded task identity.")
    packages = SkillLibraryService(operator, worker_roots=(Path(store.project_root),)).store
    locks = TaskSkillLockStore(root, packages, worker_roots=(Path(store.project_root),))
    for identity in identities:
        if locks.registered_task(task=identity):
            raise SkillError(
                "task_context_missing",
                "This task has a registered method lock; changing the job key cannot authorize legacy dispatch.",
            )
