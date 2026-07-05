import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from goodteam_os.project_reviewer_profiles import normalize_reviewer_profile_config
from goodteam_os.review_policy import evaluate_review_evidence_policy
from goodteam_os.review_provenance import (
    REVIEW_ROLES_BY_APPROVAL,
    build_reviewer_provenance,
    latest_reviewer_status_for_approval,
    reviewer_phase_for_approval,
)
from goodteam_os.reviewer_profiles import ReviewerProfile, get_reviewer_profile


AGGREGATE_OPERATOR_MESSAGE = "Human operator decides; reviewers are advisory only."
SEVERITY_RANK = {"ok": 0, "warning": 1, "blocker": 2}


def build_multi_reviewer_projection(
    *,
    approval: str,
    reviewer_target: Mapping[str, object] | None,
    reviewer_statuses: Iterable[Mapping[str, object]],
    review_evidence_policy_config: object = None,
    artifact_key: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
) -> dict[str, object]:
    gate = str(approval)
    phase = reviewer_phase_for_approval(gate)
    current_artifact_version = str(artifact_version or "").strip() or _artifact_version(artifact_path)
    statuses = _statuses_for_current_artifact(
        [dict(status) for status in reviewer_statuses],
        artifact_key=str(artifact_key or "").strip(),
        artifact_path=str(artifact_path or "").strip(),
        artifact_version=current_artifact_version,
    )
    profiles = _configured_profiles(reviewer_target, phase)
    used_status_ids: set[int] = set()
    reviewers: list[dict[str, object]] = []

    for order, profile in enumerate(profiles):
        matched_status, match_kind = _status_for_profile(
            profile=profile,
            profiles=profiles,
            statuses=statuses,
            used_status_ids=used_status_ids,
            approval=gate,
        )
        if matched_status is not None:
            used_status_ids.add(id(matched_status))
        reviewers.append(
            _reviewer_entry(
                approval=gate,
                order=order,
                profile=profile,
                status=matched_status,
                previous_failures=_previous_failures_for_profile(
                    profile=profile,
                    statuses=statuses,
                    approval=gate,
                    current_status=matched_status,
                ),
                match_kind=match_kind,
                policy_config=review_evidence_policy_config,
            )
        )

    if not profiles:
        reviewers.extend(
            _unmatched_entry(
                approval=gate,
                order=index,
                status=status,
                policy_config=review_evidence_policy_config,
            )
            for index, status in enumerate(_fallback_statuses(statuses, gate))
        )
    elif len(profiles) > 1:
        reviewers.extend(
            _unmatched_entry(
                approval=gate,
                order=len(profiles) + index,
                status=status,
                policy_config=review_evidence_policy_config,
            )
            for index, status in enumerate(_ambiguous_legacy_statuses(statuses, used_status_ids, gate))
        )

    return {
        "approval": gate,
        "phase": phase,
        "advisory_only": True,
        "can_human_override": True,
        "operator_message": AGGREGATE_OPERATOR_MESSAGE,
        "aggregate_policy": _aggregate_policy(reviewers),
        "reviewers": reviewers,
    }


def _configured_profiles(target: Mapping[str, object] | None, phase: str | None) -> list[ReviewerProfile]:
    if phase is None or target is None:
        return []
    try:
        configured = normalize_reviewer_profile_config(target)
    except ValueError:
        return []
    profiles: list[ReviewerProfile] = []
    for profile_id in configured.get(phase, ()):
        try:
            profiles.append(get_reviewer_profile(profile_id))
        except ValueError:
            continue
    return profiles


def _status_for_profile(
    *,
    profile: ReviewerProfile,
    profiles: list[ReviewerProfile],
    statuses: list[dict[str, object]],
    used_status_ids: set[int],
    approval: str,
) -> tuple[dict[str, object] | None, str]:
    gate_candidates = _statuses_for_approval(statuses, approval)
    explicit_matches = [
        status
        for status in gate_candidates
        if id(status) not in used_status_ids and str(status.get("profile_id", "")).strip() == profile.profile_id
    ]
    if explicit_matches:
        return _latest_status(explicit_matches), "profile_id"
    if len(profiles) == 1:
        legacy_match = latest_reviewer_status_for_approval(
            [status for status in gate_candidates if id(status) not in used_status_ids and not _has_profile_id(status)],
            approval,
        )
        if legacy_match is not None:
            return legacy_match, "legacy_single_profile"
    return None, "missing"


def _reviewer_entry(
    *,
    approval: str,
    order: int,
    profile: ReviewerProfile,
    status: Mapping[str, object] | None,
    previous_failures: list[dict[str, object]],
    match_kind: str,
    policy_config: object,
) -> dict[str, object]:
    provenance = build_reviewer_provenance(status, profile=profile)
    policy = evaluate_review_evidence_policy(provenance, gate=approval, config=policy_config)
    return {
        "profile_id": profile.profile_id,
        "profile_label": profile.label,
        "order": order,
        "match_kind": match_kind,
        "status": dict(status) if status is not None else None,
        "provenance": provenance,
        "policy": policy,
        "evidence_handles": dict(provenance.get("evidence_handles", {})),
        "previous_failures": previous_failures,
    }


def _unmatched_entry(
    *,
    approval: str,
    order: int,
    status: Mapping[str, object],
    policy_config: object,
) -> dict[str, object]:
    provenance = build_reviewer_provenance(status)
    policy = evaluate_review_evidence_policy(provenance, gate=approval, config=policy_config)
    status_payload = dict(status)
    profile_id = str(status_payload.get("profile_id", "") or "")
    return {
        "profile_id": profile_id or None,
        "profile_label": None,
        "order": order,
        "match_kind": "unmatched_profile_id" if profile_id else "unmatched_legacy",
        "status": status_payload,
        "provenance": provenance,
        "policy": policy,
        "evidence_handles": dict(provenance.get("evidence_handles", {})),
        "previous_failures": [],
    }


def _fallback_statuses(statuses: list[dict[str, object]], approval: str) -> list[dict[str, object]]:
    candidates = _statuses_for_approval(statuses, approval)
    return sorted(candidates, key=_fallback_status_key)


def _ambiguous_legacy_statuses(
    statuses: list[dict[str, object]],
    used_status_ids: set[int],
    approval: str,
) -> list[dict[str, object]]:
    candidates = [
        status
        for status in _statuses_for_approval(statuses, approval)
        if id(status) not in used_status_ids and not _has_profile_id(status)
    ]
    return sorted(candidates, key=_fallback_status_key)


def _statuses_for_approval(statuses: list[dict[str, object]], approval: str) -> list[dict[str, object]]:
    explicit = [status for status in statuses if _explicit_gate(status) == approval]
    legacy = [status for status in statuses if _explicit_gate(status) is None]
    roles = set(REVIEW_ROLES_BY_APPROVAL.get(approval, ()))
    if not roles:
        return explicit
    return explicit + [status for status in legacy if str(status.get("role", "")).strip() in roles]


def _statuses_for_current_artifact(
    statuses: list[dict[str, object]],
    *,
    artifact_key: str,
    artifact_path: str,
    artifact_version: str,
) -> list[dict[str, object]]:
    if not any((artifact_key, artifact_path, artifact_version)):
        return statuses
    return [
        status
        for status in statuses
        if not _artifact_identity_mismatch(status, "artifact_key", artifact_key)
        and not _artifact_identity_mismatch(status, "artifact_path", artifact_path)
        and not _artifact_identity_mismatch(status, "artifact_version", artifact_version)
    ]


def _artifact_identity_mismatch(status: Mapping[str, object], field: str, current_value: str) -> bool:
    if not current_value:
        return False
    status_value = str(status.get(field, "") or "").strip()
    return bool(status_value) and status_value != current_value


def _aggregate_policy(reviewers: list[dict[str, object]]) -> dict[str, object]:
    states = [
        str(policy.get("state", "") or "")
        for reviewer in reviewers
        if isinstance((policy := reviewer.get("policy")), Mapping) and str(policy.get("state", "") or "")
    ]
    severities = [
        str(policy.get("severity", "") or "ok")
        for reviewer in reviewers
        if isinstance((policy := reviewer.get("policy")), Mapping)
    ]
    severity = max(severities or ["ok"], key=lambda value: SEVERITY_RANK.get(value, -1))
    return {
        "severity": severity,
        "state_counts": dict(Counter(states)),
        "advisory_only": True,
        "can_human_override": True,
    }


def _latest_status(statuses: list[dict[str, object]]) -> dict[str, object]:
    return max(statuses, key=lambda status: str(status.get("updated_at", "") or ""))


def _previous_failures_for_profile(
    *,
    profile: ReviewerProfile,
    statuses: list[dict[str, object]],
    approval: str,
    current_status: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    current_id = id(current_status) if current_status is not None else None
    failures = [
        status
        for status in _statuses_for_approval(statuses, approval)
        if id(status) != current_id
        and str(status.get("profile_id", "") or "").strip() == profile.profile_id
        and str(status.get("status", "") or "").strip().lower() == "failed"
    ]
    return [_failure_summary(status) for status in sorted(failures, key=lambda item: str(item.get("updated_at", "") or ""))]


def _failure_summary(status: Mapping[str, object]) -> dict[str, object]:
    return {
        "job_id": str(status.get("job_id", "") or status.get("id", "") or ""),
        "summary": str(status.get("summary", "") or ""),
        "updated_at": str(status.get("updated_at", "") or ""),
        "result_path": str(status.get("result_path", "") or ""),
        "original_failed_job_id": str(status.get("original_failed_job_id", "") or ""),
        "retry_attempt": str(status.get("retry_attempt", "") or status.get("attempt", "") or ""),
    }


def _fallback_status_key(status: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(status.get("role", "") or ""),
        str(status.get("adapter", "") or ""),
        str(status.get("job_id", "") or status.get("id", "") or ""),
    )


def _has_profile_id(status: Mapping[str, object]) -> bool:
    return bool(str(status.get("profile_id", "") or "").strip())


def _explicit_gate(status: Mapping[str, object]) -> str | None:
    gate = str(status.get("approval", "") or status.get("gate", "")).strip()
    return gate or None


def _artifact_version(artifact_path: str) -> str:
    path = Path(artifact_path).expanduser() if artifact_path else None
    if path is None or not path.is_file():
        return ""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
