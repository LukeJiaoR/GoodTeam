from collections.abc import Iterable, Mapping
from typing import Any

from goodteam_os.project_reviewer_profiles import normalize_reviewer_profile_config
from goodteam_os.reviewer_profiles import ReviewerProfile, get_reviewer_profile


OPERATOR_MESSAGE = "Human operator decides; this reviewer is advisory only."
IMPLEMENTATION_APPROVALS = {"fix_plan", "code_review", "qa", "security", "staging"}
RELEASE_APPROVALS = {"release"}
REVIEW_ROLES_BY_APPROVAL = {
    "fix_plan": ("review", "planner"),
    "prd": ("review", "product_requirement", "architect"),
    "rfc": ("review", "architect", "qa"),
    "security": ("security", "review", "qa", "code_review"),
    "staging": ("code_review", "qa", "staging"),
    "release": ("release",),
    "code_review": ("code_review",),
    "qa": ("qa",),
}


def build_reviewer_provenance(
    status: Mapping[str, object] | None,
    *,
    profile: ReviewerProfile | Mapping[str, object] | None = None,
) -> dict[str, object]:
    status_payload = dict(status or {})
    evidence = status_payload.get("invocation_evidence")
    evidence_payload = dict(evidence) if isinstance(evidence, Mapping) else {}
    badge = _badge_payload(status_payload, evidence_payload, profile)
    invocation = _invocation_payload(status_payload, evidence_payload, badge["kind"])
    return {
        "reviewer": _reviewer_payload(profile),
        "badge": badge,
        "invocation": invocation,
        "evidence_handles": _evidence_handles(status_payload),
        "advisory_only": True,
        "operator_message": OPERATOR_MESSAGE,
    }


def reviewer_phase_for_approval(approval: str) -> str | None:
    gate = str(approval).strip()
    if gate in IMPLEMENTATION_APPROVALS:
        return "implementation"
    if gate in RELEASE_APPROVALS:
        return "release"
    return None


def reviewer_profile_for_approval(
    target: Mapping[str, object] | None,
    approval: str,
) -> ReviewerProfile | None:
    phase = reviewer_phase_for_approval(approval)
    if phase is None or target is None:
        return None
    try:
        configured = normalize_reviewer_profile_config(target)
    except ValueError:
        return None
    profile_ids = configured.get(phase, ())
    if not profile_ids:
        return None
    try:
        return get_reviewer_profile(profile_ids[0])
    except ValueError:
        return None


def latest_reviewer_status_for_approval(
    statuses: Iterable[Mapping[str, object]],
    approval: str,
) -> dict[str, object] | None:
    payloads = [dict(status) for status in statuses]
    explicit = [
        status
        for status in payloads
        if str(status.get("approval", "") or status.get("gate", "")).strip() == approval
    ]
    if explicit:
        return _latest_status(explicit)
    roles = set(REVIEW_ROLES_BY_APPROVAL.get(approval, ()))
    if not roles:
        return None
    role_matches = [status for status in payloads if str(status.get("role", "")).strip() in roles]
    return _latest_status(role_matches)


def _badge_payload(
    status: Mapping[str, object],
    evidence: Mapping[str, object],
    profile: ReviewerProfile | Mapping[str, object] | None,
) -> dict[str, str]:
    status_value = str(status.get("status", "") or "").lower()
    adapter = _first_text(evidence.get("adapter"), status.get("adapter"), _profile_value(profile, "adapter"), "unknown")
    provider = _first_text(evidence.get("provider"), "unknown")
    kind = str(evidence.get("kind", "") or "").lower()
    if status_value == "failed":
        return _badge("failed invocation", "failed", provider, adapter)
    if not evidence:
        return _badge("missing evidence", "missing", "unknown", adapter)
    if kind == "unavailable":
        return _badge("fallback", "fallback", provider, adapter)
    if kind == "builtin" or adapter in {"fake", "noop"}:
        return _badge(adapter if adapter in {"fake", "noop"} else "builtin", "builtin", provider, adapter)
    if kind == "manual" or adapter == "human":
        return _badge("human", "manual", provider, adapter)
    if kind == "provider":
        return _badge(adapter if adapter != "unknown" else provider, "provider", provider, adapter)
    return _badge(kind or "fallback", "fallback", provider, adapter)


def _invocation_payload(
    status: Mapping[str, object],
    evidence: Mapping[str, object],
    badge_kind: object,
) -> dict[str, object]:
    if badge_kind == "missing":
        return {
            "external_invocation": False,
            "command_available": None,
            "reason": "Missing invocation evidence.",
            "status": "missing",
        }
    if badge_kind == "failed":
        reason = _first_text(status.get("summary"), evidence.get("reason"), "")
    else:
        reason = _first_text(evidence.get("reason"), status.get("summary"), "")
    return {
        "external_invocation": _bool_value(evidence.get("external_invocation")),
        "command_available": _nullable_bool(evidence.get("command_available")),
        "reason": reason,
        "status": _first_text(status.get("status"), "missing"),
    }


def _reviewer_payload(profile: ReviewerProfile | Mapping[str, object] | None) -> dict[str, object]:
    return {
        "profile_id": _profile_value(profile, "profile_id"),
        "label": _profile_value(profile, "label"),
        "focus_areas": _profile_focus_areas(profile),
    }


def _evidence_handles(status: Mapping[str, object]) -> dict[str, object]:
    changed_files = status.get("changed_files")
    return {
        "result_path": _first_text(status.get("result_path"), ""),
        "stdout_path": _first_text(status.get("stdout_path"), ""),
        "stderr_path": _first_text(status.get("stderr_path"), ""),
        "changed_files": [str(item) for item in changed_files] if isinstance(changed_files, list) else [],
    }


def _latest_status(statuses: list[dict[str, object]]) -> dict[str, object] | None:
    if not statuses:
        return None
    return max(statuses, key=lambda status: str(status.get("updated_at", "") or ""))


def _badge(label: str, kind: str, provider: str, adapter: str) -> dict[str, str]:
    return {"label": label, "kind": kind, "provider": provider, "adapter": adapter}


def _profile_value(profile: ReviewerProfile | Mapping[str, object] | None, key: str) -> str | None:
    if isinstance(profile, Mapping):
        value = profile.get(key)
    elif profile is not None and hasattr(profile, key):
        value: Any = getattr(profile, key)
    else:
        return None
    return str(value) if value not in (None, "") else None


def _profile_focus_areas(profile: ReviewerProfile | Mapping[str, object] | None) -> list[str]:
    if isinstance(profile, Mapping) and isinstance(profile.get("focus_areas"), list):
        return [str(item) for item in profile["focus_areas"]]
    if profile is not None and hasattr(profile, "focus_areas"):
        focus = getattr(profile, "focus_areas")
        if isinstance(focus, (list, tuple)):
            return [str(item) for item in focus]
    return []


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""


def _bool_value(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _nullable_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
