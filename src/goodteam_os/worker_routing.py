"""[INPUT]: 项目模型、Team 成员与角色运行配置。
[OUTPUT]: 绑定作业的执行器、模型和运行参数。
[POS]: 在派工前根据 Team 配置解析 Worker。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from collections.abc import Callable

from goodteam_os.session_runtimes import SESSION_BACKENDS
from goodteam_os.worker_capabilities import REASONING_DEPTHS


def resolve_role_adapter(target: dict[str, object], role: str) -> str:
    """Pick the worker backend for a single role.

    With Team Control Plane state attached, an explicitly selected Runtime
    source owns the routing choice; otherwise the first active Role member does.
    Projects without Team state use the per-role `workers.<role>` override or the
    global `worker_backend.mode`, defaulting to codex. This does not gate dispatch:
    dispatch must go through `resolve_worker_for_role`, which fails closed.
    """
    from goodteam_os.team_control_plane import (
        active_workers_for_role, attached_team_state, normalize_provider, resolve_worker_from_state,
    )

    state = attached_team_state(target)
    if state is not None and role != "orchestrator":
        if dict(dict(state.get("roles") or {}).get(role) or {}).get("runtime_source_worker_id"):
            return str(resolve_worker_from_state(state, role)["adapter"])
        active = sorted(active_workers_for_role(state, role), key=lambda w: (str(w["created_at"]), str(w["id"])))
        if active:
            return normalize_provider(dict(active[0]["runtime"]).get("provider", ""))
    default_mode = str(dict(target.get("worker_backend", {})).get("mode", "codex") or "codex")
    per_role = str(dict(target.get("workers", {})).get(role, "") or "").strip()
    adapter = per_role or default_mode
    if adapter == "manual":
        adapter = "human"
    return adapter


def worker_type_settings_for_role(target: dict[object, object], role: str) -> dict[str, object]:
    from goodteam_os.team_control_plane import MANAGER_ROLE, active_workers_for_role, attached_team_state

    state = attached_team_state(target)
    if state is not None:
        if role == MANAGER_ROLE:
            manager = dict(state.get("manager", {}) or {})
            runtime = dict(manager.get("runtime", {}) or {})
            reasoning_depth = str(runtime.get("reasoning_depth", "default") or "default")
            return {
                "reasoning_depth": reasoning_depth,
                "additional_prompt": str(runtime.get("additional_prompt", "") or ""),
                "pi_thinking_level": _pi_thinking_level(resolve_role_adapter(target, role), reasoning_depth),
            }
        active = sorted(active_workers_for_role(state, role), key=lambda w: (str(w["created_at"]), str(w["id"])))
        if active:
            runtime = dict(active[0]["runtime"])
            reasoning_depth = str(runtime.get("reasoning_depth", "default") or "default")
            return {
                "reasoning_depth": reasoning_depth,
                "additional_prompt": str(runtime.get("additional_prompt", "") or ""),
                # Worker count derives from Worker records, not a count field.
                "desired_instances": min(max(len(active), 1), 8),
                "pi_thinking_level": _pi_thinking_level(resolve_role_adapter(target, role), reasoning_depth),
            }
    all_settings = target.get("worker_type_settings", {})
    if not isinstance(all_settings, dict):
        all_settings = {}
    raw_settings = all_settings.get(role, {})
    if not isinstance(raw_settings, dict):
        raw_settings = {}
    reasoning_depth = str(raw_settings.get("reasoning_depth", "default") or "default").strip().lower()
    if reasoning_depth not in REASONING_DEPTHS:
        reasoning_depth = "default"
    additional_prompt = str(raw_settings.get("additional_prompt", "") or "").strip()
    try:
        desired_instances = int(raw_settings.get("desired_instances", 1) or 1) if role != MANAGER_ROLE else 1
    except (TypeError, ValueError):
        desired_instances = 1
    desired_instances = min(max(desired_instances, 1), 8)
    adapter = resolve_role_adapter(target, role)
    return {
        "reasoning_depth": reasoning_depth,
        "additional_prompt": additional_prompt,
        **({"desired_instances": desired_instances} if role != MANAGER_ROLE else {}),
        "pi_thinking_level": _pi_thinking_level(adapter, reasoning_depth),
    }


def _pi_thinking_level(adapter: str, reasoning_depth: str) -> str:
    return reasoning_depth if adapter in SESSION_BACKENDS and reasoning_depth != "default" else ""


def _worker_job_fields(
    target: dict[object, object],
    role: str,
    role_models: dict[str, dict[str, str]],
    *,
    model_fallback: Callable[[], str] | None = None,
    worker_id: str | None = None,
) -> dict[str, object]:
    """Resolve the dispatching Worker for a role, enforcing Team gates.

    Fails closed: a disabled TeamRole or a TeamRole without an active,
    runtime-eligible Worker raises TeamDispatchBlocked. The resolved Worker's
    runtime is authoritative; the role-level model profile only fills in when
    the Worker has no explicit model configured.
    """
    from goodteam_os.team_control_plane import resolve_worker_for_role

    resolution = resolve_worker_for_role(target, role, worker_id=worker_id)
    adapter = resolution["adapter"] if resolution else resolve_role_adapter(target, role)
    fallback_model = worker_model_for_role(role_models, role) if model_fallback is None else model_fallback()
    if adapter == "codebuddy" and model_fallback is None:
        # 模型留空时沿用 CodeBuddy 默认值。Codex 档位并非 CodeBuddy 模型 ID。
        primary = str(role_models.get(role, {}).get("primary", "")).strip()
        fallback_model = primary.removeprefix("codebuddy:").strip() if primary.startswith("codebuddy:") else ""
    if resolution is None:
        return {
            "adapter": adapter,
            "model": fallback_model,
            **worker_type_settings_for_role(target, role),
        }
    # The resolution IS the execution contract: identity, provider, model,
    # reasoning depth, and prompt all come from this one Worker. Never merge
    # fields from a second "first active worker" lookup here.
    return {
        "adapter": resolution["adapter"],
        "model": resolution["model"] or fallback_model,
        "worker_id": resolution["worker_id"],
        "worker_name": resolution["worker_name"],
        "reasoning_depth": resolution["reasoning_depth"],
        "additional_prompt": resolution["additional_prompt"],
        "pi_thinking_level": resolution["pi_thinking_level"],
        **({"desired_instances": resolution["desired_instances"]} if "desired_instances" in resolution else {}),
    }


def worker_model_for_role(role_models: dict[str, dict[str, str]], role: str) -> str:
    model = str(dict(role_models.get(role, {})).get("primary", "")).strip()
    if ":" in model:
        return model.split(":", 1)[1].strip()
    return model
