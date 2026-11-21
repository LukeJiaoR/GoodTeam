"""Pre-seeded and custom Agent Role definitions and validation."""
from __future__ import annotations

import re
from typing import Any

from goodteam.store import Store


_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
VALID_POLICIES = ("read_only", "workspace_write")
VALID_ADAPTERS = ("codex", "claude", "mock")
VALID_EFFORTS = ("low", "medium", "high", "xhigh")

BUILTIN_ROLES: tuple[dict[str, Any], ...] = (
    {
        "key": "researcher",
        "name": "Researcher",
        "description": "Investigates codebase, docs, and facts strictly read-only with file:line citations.",
        "system_prompt": (
            "You are the Researcher sub-agent. Inspect code, configuration, and documentation thoroughly. "
            "Report concrete findings with exact file paths and line numbers. Never modify files."
        ),
        "execution_policy": "read_only",
        "write_domains": [],
        "allowed_tools": ["Read", "Glob", "Grep"],
        "adapter": "codex",
        "reasoning_effort": "high",
        "skill_bindings": [{"skill_id": "builtin:research", "mode": "required"}],
    },
    {
        "key": "planner",
        "name": "Planner",
        "description": "Breaks requirements into concrete, bounded implementation steps without editing code.",
        "system_prompt": (
            "You are the Planner sub-agent. Analyze dependencies and produce an actionable, minimal "
            "implementation plan specifying target files, interfaces, and verification steps. Never modify files."
        ),
        "execution_policy": "read_only",
        "write_domains": [],
        "allowed_tools": ["Read", "Glob", "Grep"],
        "adapter": "codex",
        "reasoning_effort": "high",
        "skill_bindings": [{"skill_id": "builtin:research", "mode": "on_demand"}],
    },
    {
        "key": "coder",
        "name": "Coder",
        "description": "Implements focused code changes and tests inside an isolated git worktree.",
        "system_prompt": (
            "You are the Coder sub-agent working in an isolated git worktree. Implement clean, minimal, "
            "well-tested code strictly within your assigned scope and run relevant verification checks."
        ),
        "execution_policy": "workspace_write",
        "write_domains": [],
        "allowed_tools": [],
        "adapter": "codex",
        "reasoning_effort": "high",
        "skill_bindings": [{"skill_id": "builtin:coding", "mode": "required"}],
    },
    {
        "key": "reviewer",
        "name": "Reviewer",
        "description": "Reviews diffs for correctness, security, edge cases, and regressions (read-only).",
        "system_prompt": (
            "You are the independent Reviewer sub-agent. Inspect diffs, surrounding code, and tests for bugs, "
            "security issues, and missing edge cases. Output severity-ordered findings and a clear verdict."
        ),
        "execution_policy": "read_only",
        "write_domains": [],
        "allowed_tools": ["Read", "Glob", "Grep"],
        "adapter": "codex",
        "reasoning_effort": "high",
        "skill_bindings": [{"skill_id": "builtin:code-review", "mode": "required"}],
    },
    {
        "key": "qa",
        "name": "QA",
        "description": "Designs and executes tests to verify functionality and prevent regressions.",
        "system_prompt": (
            "You are the QA sub-agent working in an isolated git worktree. Write and run targeted tests "
            "covering edge cases and failure modes, and report exact observed command outputs."
        ),
        "execution_policy": "workspace_write",
        "write_domains": [],
        "allowed_tools": [],
        "adapter": "codex",
        "reasoning_effort": "high",
        "skill_bindings": [{"skill_id": "builtin:qa-testing", "mode": "required"}],
    },
)


def normalize_write_domains(raw: Any) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("write_domains must be a list of relative paths.")
    cleaned: list[str] = []
    for item in raw:
        text = str(item).strip().replace("\\", "/")
        if not text:
            continue
        if text == "*":
            return ["*"]
        if text.startswith("/") or ".." in text.split("/"):
            raise ValueError(f"Invalid write domain path: {text}")
        cleaned.append(text.rstrip("/") or ".")
    return list(dict.fromkeys(cleaned))


def normalize_skill_bindings(raw: Any) -> list[dict[str, str]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("skill_bindings must be a list.")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            sid, mode = item.strip(), "required"
        elif isinstance(item, dict):
            sid = str(item.get("skill_id") or "").strip()
            mode = str(item.get("mode") or "required").strip()
        else:
            continue
        if not sid or sid in seen:
            continue
        if mode not in ("required", "on_demand"):
            raise ValueError(f"Invalid skill binding mode: {mode}")
        seen.add(sid)
        result.append({"skill_id": sid, "mode": mode})
    return result


def seed_project_roles(store: Store, project_id: str, *, force: bool = False) -> list[dict[str, Any]]:
    existing = store.list_roles(project_id)
    if existing and not force:
        return existing
    for spec in BUILTIN_ROLES:
        store.upsert_role(
            project_id=project_id,
            key=spec["key"],
            name=spec["name"],
            description=spec["description"],
            system_prompt=spec["system_prompt"],
            execution_policy=spec["execution_policy"],
            write_domains=list(spec["write_domains"]),
            allowed_tools=list(spec["allowed_tools"]),
            adapter=spec["adapter"],
            reasoning_effort=spec["reasoning_effort"],
            skill_bindings=list(spec["skill_bindings"]),
            is_builtin=True,
        )
    return store.list_roles(project_id)


def save_role(store: Store, project_id: str, payload: dict[str, Any], *, key_override: str | None = None) -> dict[str, Any]:
    key = (key_override or str(payload.get("key") or "")).strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise ValueError("Role key must match ^[a-z][a-z0-9_-]{1,48}$.")
    existing = store.get_role(project_id, key)
    name = str(payload.get("name", existing["name"] if existing else key)).strip()
    if not name:
        raise ValueError("Role name is required.")
    policy = str(payload.get("execution_policy", existing["execution_policy"] if existing else "read_only"))
    if policy not in VALID_POLICIES:
        raise ValueError("execution_policy must be read_only or workspace_write.")
    adapter = str(payload.get("adapter", existing["adapter"] if existing else "codex")).lower()
    if adapter not in VALID_ADAPTERS:
        raise ValueError("adapter must be codex, claude, or mock.")
    effort = str(payload.get("reasoning_effort", existing["reasoning_effort"] if existing else "high")).lower()
    if effort not in VALID_EFFORTS:
        raise ValueError("reasoning_effort must be low, medium, high, or xhigh.")

    write_domains = (
        [] if policy == "read_only"
        else normalize_write_domains(payload.get("write_domains", existing["write_domains"] if existing else []))
    )
    raw_tools = payload.get("allowed_tools", existing["allowed_tools"] if existing else [])
    allowed_tools = [str(t).strip() for t in (raw_tools or []) if str(t).strip()]
    skill_bindings = normalize_skill_bindings(
        payload.get("skill_bindings", existing["skill_bindings"] if existing else [])
    )
    provider_id = payload.get("provider_id", existing["provider_id"] if existing else None)
    if provider_id == "":
        provider_id = None

    return store.upsert_role(
        project_id=project_id,
        key=key,
        name=name[:80],
        description=str(payload.get("description", existing["description"] if existing else "")).strip()[:400],
        system_prompt=str(payload.get("system_prompt", existing["system_prompt"] if existing else "")).strip()[:8000],
        execution_policy=policy,
        write_domains=write_domains,
        allowed_tools=allowed_tools,
        adapter=adapter,
        provider_id=provider_id,
        model=str(payload.get("model", existing["model"] if existing else "")).strip()[:200],
        reasoning_effort=effort,
        skill_bindings=skill_bindings,
        is_builtin=bool(existing["is_builtin"]) if existing else False,
    )
