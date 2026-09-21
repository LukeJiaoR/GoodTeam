"""Portable task-lock references for state, checkpoints and dispatch inputs.

Validation here is structural and identity-bound, not authorization. Runtime
preflight must still read the registered immutable resource in the trusted
Operator store and validate pins, current availability and adapter capability.
An absent legacy context is distinct from a malformed or unsupported reference.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .models import SkillError, SkillScope, canonical_json, content_digest, identifier
from .task_locks import LOCK_SCHEMA, REF_SCHEMA, TaskSkillIdentity

TASK_CONTEXT_SCHEMA = "task_skill_context.v1"


def task_skill_context(task: TaskSkillIdentity, lock_ref: dict[str, Any]) -> dict[str, Any]:
    """Build a portable context from an explicitly acquired task lock."""
    if not isinstance(task, TaskSkillIdentity):
        raise SkillError("task_scope_unknown", "An explicit bounded task identity is required.")
    return validate_task_skill_context({
        "schema_version": TASK_CONTEXT_SCHEMA,
        "task": asdict(task),
        "task_lock_ref": deepcopy(lock_ref),
    })


def validate_task_skill_context(value: object) -> dict[str, Any]:
    """Reject unknown schemas, mismatched scopes and detached task references.

    This function does not create a store, read project settings, acquire pins,
    recover a preparing resource, or claim the referenced bytes are available.
    """
    if (not isinstance(value, dict) or set(value) != {"schema_version", "task", "task_lock_ref"}
            or value["schema_version"] != TASK_CONTEXT_SCHEMA):
        raise SkillError("task_context_invalid", "Unknown or malformed task Skill context.")
    raw_task = value["task"]
    ref = value["task_lock_ref"]
    if not isinstance(raw_task, dict) or set(raw_task) != {"kind", "project_key", "logical_id"}:
        raise SkillError("task_context_invalid", "Task Skill context requires one explicit bounded identity.")
    if any(not isinstance(part, str) for part in raw_task.values()):
        raise SkillError("task_context_invalid", "Task Skill identity fields must be strings.")
    task = TaskSkillIdentity(**raw_task)
    if (not isinstance(ref, dict) or set(ref) != {"schema_version", "resource_schema", "scope", "id", "digest"}
            or ref["schema_version"] != REF_SCHEMA or ref["resource_schema"] != LOCK_SCHEMA):
        raise SkillError("task_context_invalid", "Task Skill context requires a versioned task-lock reference.")
    raw_scope = ref["scope"]
    if (not isinstance(raw_scope, dict) or set(raw_scope) != {"kind", "owner_id", "project_key"}
            or any(not isinstance(part, str) for part in raw_scope.values())):
        raise SkillError("task_context_invalid", "Task Skill reference has an invalid owner scope.")
    scope = SkillScope(**raw_scope)
    if scope.kind == "project" and scope.project_key != task.project_key:
        raise SkillError("snapshot_scope_mismatch", "Task Skill reference belongs to another project scope.")
    identifier(ref["id"], "task-")
    identifier(ref["digest"], "sha256:")
    expected = "task-" + content_digest(canonical_json({
        "scope": asdict(scope), "task": asdict(task),
    }).encode("utf-8"))[7:]
    if ref["id"] != expected:
        raise SkillError("snapshot_scope_mismatch", "Task Skill reference does not match its bounded task identity.")
    return deepcopy(value)


def workflow_skill_context(value: object, *, run_id: str, project_key: str) -> dict[str, Any]:
    """Validate a workflow state's optional context without migrating legacy runs."""
    if isinstance(value, dict) and not value:
        return {}
    context = validate_task_skill_context(value)
    if context["task"] != {"kind": "workflow_run", "project_key": project_key, "logical_id": run_id}:
        raise SkillError("snapshot_scope_mismatch", "Workflow Skill context belongs to another run, project or task kind.")
    return context
