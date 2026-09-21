"""Role–Skill binding contracts.

Bindings are project configuration, not Runtime permission. They pin exact Skill
versions for future task resolution, while task-scoped execution locking remains
#444. This module has no Worker dispatch or model side effects.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .models import SkillError, identifier

ACTIVATIONS = ("required", "on_demand")
BINDING_MODES = ("default", "custom")
ROLE_CONTRACT_VERSION = 1
ROLE_SKILL_BINDING_SCHEMA_VERSION = "role_skill_bindings.v1"
MAX_BINDINGS_PER_SUBJECT = 24

BindingResolver = Callable[[str, str], dict[str, Any]]


def role_subject(role: str) -> str:
    return f"role:{role}"


def manager_subject(manager_role: str) -> str:
    return f"manager:{manager_role}"


def workflow_role_subject(role: str) -> str:
    return f"workflow_role:{role}"


def contract_ref(subject: str) -> str:
    return f"{subject}@v{ROLE_CONTRACT_VERSION}"


def default_binding(subject: str, *, project_key: str = "") -> dict[str, Any]:
    return {
        "schema_version": ROLE_SKILL_BINDING_SCHEMA_VERSION,
        "project_key": project_key,
        "subject": subject,
        "role_contract_ref": contract_ref(subject),
        "binding_revision": 0,
        "mode": "default",
        "items": [],
        "updated_at": "",
    }


def allowed_subjects(role_keys, manager_role: str, workflow_role_keys=()) -> tuple[str, ...]:
    return tuple([
        *(role_subject(role) for role in role_keys),
        *(workflow_role_subject(role) for role in workflow_role_keys),
        manager_subject(manager_role),
    ])


def _binding_item(raw: object, resolver: BindingResolver | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillError("invalid_binding", "Skill bindings must be objects.")
    skill_id = identifier(str(raw.get("skill_id", "") or ""), "skill-")
    version_id = identifier(str(raw.get("version_id", "") or ""), "version-")
    activation = str(raw.get("activation", "") or "").strip()
    if activation not in ACTIVATIONS:
        raise SkillError("invalid_binding", f"activation must be one of: {', '.join(ACTIVATIONS)}.")
    enabled = raw.get("enabled", True)
    if type(enabled) is not bool:
        raise SkillError("invalid_binding", "Skill binding enabled must be a boolean.")
    supplied_digest = str(raw.get("package_digest", "") or "")
    if resolver is not None:
        resolved = resolver(skill_id, version_id)
        resolved_digest = identifier(str(resolved.get("package_digest", "") or ""), "sha256:")
        if supplied_digest:
            supplied_digest = identifier(supplied_digest, "sha256:")
            if supplied_digest != resolved_digest:
                raise SkillError(
                    "binding_version_changed",
                    "Selected Skill version no longer matches the package that was reviewed.",
                )
        package_digest = resolved_digest
    else:
        package_digest = identifier(supplied_digest, "sha256:")
    return {
        "skill_id": skill_id,
        "version_id": version_id,
        "package_digest": package_digest,
        "activation": activation,
        "enabled": enabled,
    }


def normalize_items(raw: object, resolver: BindingResolver | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SkillError("invalid_binding", "Skill bindings must be a list.")
    if len(raw) > MAX_BINDINGS_PER_SUBJECT:
        raise SkillError("binding_limit", "Too many Skill bindings for one subject.")
    items = [_binding_item(item, resolver) for item in raw]
    skill_ids: set[str] = set()
    for item in items:
        if item["skill_id"] in skill_ids:
            raise SkillError("duplicate_binding", "One subject cannot bind multiple versions of the same installed Skill.")
        skill_ids.add(item["skill_id"])
    return items


def normalize_binding(
    raw: object,
    *,
    subject: str,
    project_key: str = "",
    resolver: BindingResolver | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    current = default_binding(subject, project_key=project_key)
    mode = str(raw.get("mode", current["mode"]) or "").strip()
    if mode not in BINDING_MODES:
        raise SkillError("invalid_binding", f"Skill binding mode must be one of: {', '.join(BINDING_MODES)}.")
    revision = raw.get("binding_revision", current["binding_revision"])
    if type(revision) is not int or revision < 0:
        raise SkillError("invalid_binding", "Skill binding revision must be a non-negative integer.")
    items = normalize_items(raw.get("items", []), resolver=resolver)
    if mode == "default" and items:
        raise SkillError("invalid_binding", "Default Skill mode cannot contain custom package bindings.")
    supplied_schema = str(raw.get("schema_version", ROLE_SKILL_BINDING_SCHEMA_VERSION) or "")
    supplied_project = str(raw.get("project_key", project_key) or "")
    supplied_subject = str(raw.get("subject", subject) or "")
    supplied_contract = str(raw.get("role_contract_ref", contract_ref(subject)) or "")
    if (
        supplied_schema != ROLE_SKILL_BINDING_SCHEMA_VERSION
        or supplied_project != project_key
        or supplied_subject != subject
        or supplied_contract != contract_ref(subject)
    ):
        raise SkillError("binding_identity_mismatch", "Role–Skill binding identity does not match its project/subject.")
    return {
        "schema_version": ROLE_SKILL_BINDING_SCHEMA_VERSION,
        "project_key": project_key,
        "subject": subject,
        "role_contract_ref": contract_ref(subject),
        "binding_revision": revision,
        "mode": mode,
        "items": items,
        "updated_at": str(raw.get("updated_at", "") or ""),
    }


def normalize_binding_map(
    raw: object,
    *,
    role_keys,
    manager_role: str,
    project_key: str = "",
    workflow_role_keys=(),
) -> dict[str, dict[str, Any]]:
    source = dict(raw) if isinstance(raw, dict) else {}
    allowed = allowed_subjects(role_keys, manager_role, workflow_role_keys)
    unknown = set(source) - set(allowed)
    if unknown:
        raise SkillError("binding_subject_unknown", "Role–Skill bindings contain an unknown subject.")
    return {
        subject: normalize_binding(source.get(subject, {}), subject=subject, project_key=project_key)
        for subject in allowed
    }


def project_binding(binding: dict[str, Any], *, execution_connected: bool = False) -> dict[str, Any]:
    mode = str(binding.get("mode", "default"))
    items = deepcopy(list(binding.get("items", []) or []))
    if mode == "default":
        status = "default"
    elif items:
        status = "configured"
    else:
        status = "unconfigured"
    return {
        "schema_version": binding["schema_version"],
        "project_key": binding["project_key"],
        "subject": binding["subject"],
        "role_contract_ref": binding["role_contract_ref"],
        "binding_revision": binding["binding_revision"],
        "mode": mode,
        "status": status,
        "items": items,
        "execution_status": "connected" if execution_connected else "not_connected",
        "updated_at": binding.get("updated_at", ""),
    }
