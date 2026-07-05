from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from goodteam_os.multi_reviewer_projection import build_multi_reviewer_projection
from goodteam_os.reviewer_dispatch import (
    artifact_version_for_path,
    normalize_reviewer_dispatch_config,
    reviewer_dispatch_rollout_status,
    select_failed_reviewer_jobs_for_gate,
    select_queued_reviewer_jobs_for_gate,
)
from goodteam_os.roles import approval_artifact_map
from goodteam_os.state import AgileDevState


OPERATOR_MESSAGE = "Reviewer evidence is advisory. Human operator remains authoritative."


def build_reviewer_evidence_snapshot(
    *,
    state: AgileDevState,
    approval: str,
    workspace: Path,
    worker_jobs: Iterable[Mapping[str, object]] = (),
    reviewer_statuses: Iterable[Mapping[str, object]] = (),
    reviewer_target: Mapping[str, object] | None = None,
) -> dict[str, object]:
    gate = str(approval)
    artifact = _artifact_payload(state, gate)
    jobs = [dict(job) for job in worker_jobs]
    statuses = [dict(status) for status in reviewer_statuses]
    target = reviewer_target if isinstance(reviewer_target, Mapping) else {}
    projection = build_multi_reviewer_projection(
        approval=gate,
        reviewer_target=target,
        reviewer_statuses=statuses,
        review_evidence_policy_config=_review_evidence_policy_config(target),
        artifact_key=str(artifact["key"]),
        artifact_path=str(artifact["path"]),
        artifact_version=str(artifact["version"]),
    )
    queued = _queued_jobs(state, gate, artifact, jobs, target)
    failed = _failed_jobs(state, gate, artifact, jobs, target)
    reviewers = [_snapshot_reviewer(reviewer) for reviewer in _projection_reviewers(projection)]
    return {
        "schema": "reviewer_evidence_snapshot.v1",
        "run_id": str(state.run_id),
        "approval": gate,
        "artifact": artifact,
        "reviewer_dispatch": _dispatch_payload(target),
        "multi_reviewer_projection": projection,
        "reviewers": reviewers,
        "jobs": _jobs_payload(queued, failed),
        "aggregate": _aggregate_payload(reviewers),
        "operator_message": OPERATOR_MESSAGE,
    }


def _artifact_payload(state: AgileDevState, approval: str) -> dict[str, object]:
    artifact_key = approval_artifact_map().get(approval, approval)
    artifact_path = str(state.artifacts.get(artifact_key, "") or "")
    return {
        "key": artifact_key,
        "path": artifact_path,
        "version": artifact_version_for_path(artifact_path),
    }


def _review_evidence_policy_config(target: Mapping[str, object]) -> object:
    return target.get("review_evidence_policy", {})


def _dispatch_payload(target: Mapping[str, object]) -> dict[str, object]:
    payload = {
        "mode": "single",
        "parallel_requested": False,
        "parallel_enabled": False,
        "advisory_only": True,
    }
    config = normalize_reviewer_dispatch_config(target)
    rollout = reviewer_dispatch_rollout_status(target)
    payload["mode"] = str(config.get("mode", "single") or "single")
    payload["parallel_requested"] = bool(rollout.get("parallel_requested", False))
    payload["parallel_enabled"] = bool(rollout.get("parallel_enabled", False))
    return payload


def _queued_jobs(
    state: AgileDevState,
    approval: str,
    artifact: Mapping[str, object],
    jobs: list[dict[str, object]],
    target: Mapping[str, object],
) -> list[dict[str, object]]:
    return select_queued_reviewer_jobs_for_gate(
        jobs,
        run_id=str(state.run_id),
        approval=approval,
        artifact_key=str(artifact["key"]),
        artifact_path=str(artifact["path"]),
        artifact_version=str(artifact["version"]),
        profile_order=_profile_order(target, approval),
    )


def _failed_jobs(
    state: AgileDevState,
    approval: str,
    artifact: Mapping[str, object],
    jobs: list[dict[str, object]],
    target: Mapping[str, object],
) -> list[dict[str, object]]:
    return select_failed_reviewer_jobs_for_gate(
        jobs,
        run_id=str(state.run_id),
        approval=approval,
        artifact_key=str(artifact["key"]),
        artifact_path=str(artifact["path"]),
        artifact_version=str(artifact["version"]),
        profile_order=_profile_order(target, approval),
    )


def _profile_order(target: Mapping[str, object], approval: str) -> list[str]:
    projection = build_multi_reviewer_projection(
        approval=approval,
        reviewer_target=target,
        reviewer_statuses=[],
        review_evidence_policy_config=_review_evidence_policy_config(target),
    )
    return [str(item.get("profile_id", "")) for item in _projection_reviewers(projection) if item.get("profile_id")]


def _projection_reviewers(projection: Mapping[str, object]) -> list[dict[str, object]]:
    reviewers = projection.get("reviewers", [])
    if not isinstance(reviewers, list):
        return []
    return [dict(reviewer) for reviewer in reviewers if isinstance(reviewer, Mapping)]


def _snapshot_reviewer(reviewer: Mapping[str, object]) -> dict[str, object]:
    status = reviewer.get("status")
    status_payload = dict(status) if isinstance(status, Mapping) else {}
    policy = reviewer.get("policy")
    policy_payload = dict(policy) if isinstance(policy, Mapping) else {}
    return {
        "profile_id": _text(reviewer.get("profile_id")),
        "profile_label": _text(reviewer.get("profile_label")),
        "status": _text(status_payload.get("status"), "missing"),
        "job_id": _text(status_payload.get("job_id") or status_payload.get("id")),
        "policy_state": _text(policy_payload.get("state")),
        "policy_severity": _text(policy_payload.get("severity")),
        "policy_badge": _policy_badge(policy_payload),
        "summary": _text(status_payload.get("summary")),
        "result_path": _text(status_payload.get("result_path")),
        "stdout_path": _text(status_payload.get("stdout_path")),
        "stderr_path": _text(status_payload.get("stderr_path")),
        "retry_attempt": _text(status_payload.get("retry_attempt") or status_payload.get("attempt")),
        "original_failed_job_id": _text(status_payload.get("original_failed_job_id")),
        "previous_failures": _previous_failures(reviewer.get("previous_failures")),
    }


def _policy_badge(policy: Mapping[str, object]) -> str:
    severity = _text(policy.get("severity"), "ok")
    state = _text(policy.get("state"), "unknown")
    return f"{severity}:{state}"


def _previous_failures(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _jobs_payload(
    queued: list[dict[str, object]],
    failed: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "queued_total": len(queued),
        "failed_total": len(failed),
        "queued_by_profile": _count_by_profile(queued),
        "failed_by_profile": _count_by_profile(failed),
    }


def _count_by_profile(jobs: list[dict[str, object]]) -> dict[str, int]:
    counts = Counter(_text(job.get("profile_id"), "unknown") for job in jobs)
    return dict(sorted(counts.items()))


def _aggregate_payload(reviewers: list[dict[str, object]]) -> dict[str, int]:
    statuses = Counter(_text(reviewer.get("status"), "missing") for reviewer in reviewers)
    policies = Counter(_text(reviewer.get("policy_severity"), "ok") for reviewer in reviewers)
    return {
        "reviewer_count": len(reviewers),
        "completed": statuses.get("completed", 0),
        "failed": statuses.get("failed", 0),
        "queued": statuses.get("queued", 0),
        "missing": statuses.get("missing", 0),
        "policy_blockers": policies.get("blocker", 0),
        "policy_warnings": policies.get("warning", 0),
    }


def _text(value: object, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default
