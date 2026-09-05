import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from goodteam_os.project_reviewer_profiles import REVIEWER_PROFILE_PHASES, normalize_reviewer_profile_config
from goodteam_os.review_provenance import reviewer_phase_for_approval
from goodteam_os.reviewer_profiles import ReviewerProfile, get_reviewer_profile
from goodteam_os.workers import SESSION_BACKENDS, WorkerJobStore


DISPATCH_MODES = ("single", "parallel")
REVIEWER_ROLE_BY_PHASE = {"implementation": "code_review", "release": "release"}
PARALLEL_ROLLOUT_ACKNOWLEDGEMENT = "parallel-reviewer-dispatch-is-advisory"
PARALLEL_ROLLOUT_WARNING = (
    "reviewer_dispatch.mode: parallel requires rollout.enabled: true and "
    'rollout.acknowledge: "parallel-reviewer-dispatch-is-advisory".'
)


def normalize_reviewer_dispatch_config(target: Mapping[str, object] | None) -> dict[str, object]:
    if target is None:
        return {"mode": "single", "phases": {}}
    raw_config = target.get("reviewer_dispatch", {})
    if raw_config in (None, ""):
        return {"mode": "single", "phases": {}}
    if not isinstance(raw_config, Mapping):
        raise ValueError("reviewer_dispatch must be a mapping.")

    mode = _clean_mode(raw_config.get("mode"), "reviewer_dispatch.mode")
    raw_phases = raw_config.get("phases", {})
    if raw_phases in (None, ""):
        raw_phases = {}
    if not isinstance(raw_phases, Mapping):
        raise ValueError("reviewer_dispatch.phases must be a mapping of phase to mode.")

    phases: dict[str, str] = {}
    for raw_phase, raw_mode in raw_phases.items():
        phase = str(raw_phase).strip()
        if phase not in REVIEWER_PROFILE_PHASES:
            allowed = ", ".join(REVIEWER_PROFILE_PHASES)
            raise ValueError(f"Unknown reviewer dispatch phase {phase!r}. Use one of: {allowed}.")
        phases[phase] = _clean_mode(raw_mode, f"reviewer_dispatch.phases.{phase}")
    return {"mode": mode, "phases": phases}


def reviewer_dispatch_rollout_status(target: Mapping[str, object] | None) -> dict[str, object]:
    warnings: list[str] = []
    errors: list[str] = []
    try:
        config = normalize_reviewer_dispatch_config(target)
    except ValueError as exc:
        return {
            "parallel_requested": False,
            "parallel_enabled": False,
            "warnings": warnings,
            "errors": [str(exc)],
        }

    parallel_requested = config["mode"] == "parallel"
    parallel_enabled = False
    if parallel_requested:
        raw_config = _raw_dispatch_config(target)
        rollout = raw_config.get("rollout", {})
        rollout = rollout if isinstance(rollout, Mapping) else {}
        acknowledged = (
            _truthy_bool(rollout.get("enabled"))
            and str(rollout.get("acknowledge", "") or "").strip() == PARALLEL_ROLLOUT_ACKNOWLEDGEMENT
        )
        if acknowledged:
            parallel_enabled = True
        else:
            warnings.append(PARALLEL_ROLLOUT_WARNING)

    return {
        "parallel_requested": parallel_requested,
        "parallel_enabled": parallel_enabled,
        "warnings": warnings,
        "errors": errors,
    }


def reviewer_dispatch_diagnostics(
    target: Mapping[str, object] | None,
    *,
    worker_jobs: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    warnings: list[str] = []
    errors: list[str] = []
    config = {"mode": "single", "phases": {}}
    rollout = {
        "parallel_requested": False,
        "parallel_enabled": False,
        "warnings": [],
        "errors": [],
    }

    try:
        config = normalize_reviewer_dispatch_config(target)
        rollout = reviewer_dispatch_rollout_status(target)
    except ValueError as exc:
        errors.append(str(exc))

    warnings.extend(str(warning) for warning in rollout.get("warnings", []) if str(warning))
    errors.extend(str(error) for error in rollout.get("errors", []) if str(error))

    profile_config: dict[str, tuple[str, ...]] = {}
    if target is not None:
        try:
            profile_config = normalize_reviewer_profile_config(target)
        except ValueError as exc:
            errors.append(str(exc))

    phases = {}
    configured_phases = dict(config.get("phases", {}))
    for phase in REVIEWER_PROFILE_PHASES:
        configured_profiles = list(profile_config.get(phase, ()))
        phases[phase] = {
            "dispatch_mode": str(configured_phases.get(phase, "")),
            "configured_profiles": configured_profiles,
            "profile_count": len(configured_profiles),
        }

    return {
        "mode": str(config.get("mode", "single") or "single"),
        "parallel_requested": bool(rollout.get("parallel_requested", False)),
        "parallel_enabled": bool(rollout.get("parallel_enabled", False)),
        "warnings": warnings,
        "errors": errors,
        "phases": phases,
        "queued_reviewer_jobs": _queued_reviewer_job_counts(worker_jobs),
        "failed_reviewer_jobs": _failed_reviewer_job_counts(worker_jobs),
        "advisory_only": True,
        "operator_message": "Reviewer evidence remains advisory. Human operator decides.",
    }


def dispatch_reviewer_jobs_for_gate(
    *,
    store: WorkerJobStore,
    state: object,
    target: Mapping[str, object] | None,
    approval: str,
    artifact_key: str,
    artifact_path: str,
    context: str,
) -> list[dict[str, Any]]:
    phase = reviewer_phase_for_approval(approval)
    if phase is None or not _parallel_enabled(target, phase):
        return []

    profiles = _configured_profiles(target, phase)
    if not profiles:
        return []

    role = REVIEWER_ROLE_BY_PHASE.get(phase, "review")
    resolution = None
    if target is not None:
        # Dispatch invariant, enforced backend-side: a disabled TeamRole or a
        # TeamRole without an active runtime-eligible Worker can never
        # dispatch, regardless of which surface asked for the review. The
        # resolved Worker is the actual executor: its identity and runtime are
        # bound to every job so provenance can always answer "which Worker
        # executed this review, with which runtime".
        from goodteam_os.team_control_plane import resolve_worker_for_role

        resolution = resolve_worker_for_role(dict(target), role)

    run_id = str(getattr(state, "run_id", "") or "")
    artifact_version = artifact_version_for_path(artifact_path)
    dispatch_group_id = _dispatch_group_id(
        run_id=run_id,
        approval=approval,
        artifact_key=artifact_key,
        artifact_path=artifact_path,
        artifact_version=artifact_version,
    )
    results: list[dict[str, Any]] = []
    for profile in profiles:
        job, _created = store.create_or_reuse_job(
            **_reviewer_job_spec(
                run_id=run_id,
                profile=profile,
                approval=approval,
                phase=phase,
                artifact_key=artifact_key,
                artifact_path=artifact_path,
                artifact_version=artifact_version,
                dispatch_group_id=dispatch_group_id,
                context=context,
                resolution=resolution,
            ),
            **_worker_type_job_settings(target, resolution, role=role, adapter=profile.adapter),
        )
        result = dict(job)
        result.setdefault("job_id", str(job.get("id", "")))
        results.append({**result, **_identity_payload(job)})
    return results


def select_queued_reviewer_jobs_for_gate(
    jobs: Iterable[Mapping[str, object]],
    *,
    run_id: str,
    approval: str,
    artifact_key: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
    profile_id: str = "",
    profile_order: Iterable[str] = (),
) -> list[dict[str, object]]:
    wanted_run_id = _clean_text(run_id)
    wanted_gate = _clean_text(approval)
    wanted_artifact_key = _clean_text(artifact_key)
    wanted_artifact_path = _clean_text(artifact_path)
    wanted_artifact_version = _clean_text(artifact_version)
    wanted_profile_id = _clean_text(profile_id)
    order = {_clean_text(value): index for index, value in enumerate(profile_order) if _clean_text(value)}
    selected = [
        dict(job)
        for job in jobs
        if _is_executable_queued_reviewer_job(job)
        and _clean_text(job.get("run_id")) == wanted_run_id
        and _job_gate(job) == wanted_gate
        and _matches_supplied_identity(job, "artifact_key", wanted_artifact_key)
        and _matches_supplied_identity(job, "artifact_path", wanted_artifact_path)
        and _matches_supplied_identity(job, "artifact_version", wanted_artifact_version)
        and _matches_supplied_identity(job, "profile_id", wanted_profile_id)
    ]
    return sorted(selected, key=lambda job: _reviewer_job_sort_key(job, order))


def select_failed_reviewer_jobs_for_gate(
    jobs: Iterable[Mapping[str, object]],
    *,
    run_id: str,
    approval: str,
    artifact_key: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
    profile_id: str = "",
    profile_order: Iterable[str] = (),
) -> list[dict[str, object]]:
    wanted_run_id = _clean_text(run_id)
    wanted_gate = _clean_text(approval)
    wanted_artifact_key = _clean_text(artifact_key)
    wanted_artifact_path = _clean_text(artifact_path)
    wanted_artifact_version = _clean_text(artifact_version)
    wanted_profile_id = _clean_text(profile_id)
    order = {_clean_text(value): index for index, value in enumerate(profile_order) if _clean_text(value)}
    job_list = [dict(job) for job in jobs]
    selected = [
        dict(job)
        for job in job_list
        if _is_failed_reviewer_job(job)
        and _clean_text(job.get("run_id")) == wanted_run_id
        and _job_gate(job) == wanted_gate
        and _matches_supplied_identity(job, "artifact_key", wanted_artifact_key)
        and _matches_supplied_identity(job, "artifact_path", wanted_artifact_path)
        and _matches_supplied_identity(job, "artifact_version", wanted_artifact_version)
        and _matches_supplied_identity(job, "profile_id", wanted_profile_id)
        and not _has_superseding_retry_or_result(job, job_list)
    ]
    return sorted(selected, key=lambda job: _reviewer_job_sort_key(job, order))


def run_queued_reviewer_jobs_for_gate(
    *,
    store: WorkerJobStore,
    jobs: Iterable[Mapping[str, object]],
    run_id: str,
    approval: str,
    artifact_key: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
    profile_id: str = "",
    max_jobs: int | None = None,
    profile_order: Iterable[str] = (),
) -> dict[str, object]:
    job_list = [dict(job) for job in jobs]
    selected = select_queued_reviewer_jobs_for_gate(
        job_list,
        run_id=run_id,
        approval=approval,
        artifact_key=artifact_key,
        artifact_path=artifact_path,
        artifact_version=artifact_version,
        profile_id=profile_id,
        profile_order=profile_order,
    )
    runnable = selected[:max_jobs] if max_jobs is not None and max_jobs >= 0 else selected
    results: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for job in runnable:
        job_id = _job_id(job)
        if not job_id:
            errors.append({"job_id": "", "error": "missing job id"})
            continue
        try:
            status = dict(store.run_job(job_id))
        except (FileNotFoundError, OSError, ValueError) as exc:
            errors.append({"job_id": job_id, "error": str(exc)})
            continue
        results.append(
            {
                "job_id": job_id,
                "status": _clean_text(status.get("status")),
                "summary": _clean_text(status.get("summary")),
                "profile_id": _clean_text(status.get("profile_id") or job.get("profile_id")),
                "approval": _clean_text(status.get("approval") or status.get("gate") or job.get("approval") or job.get("gate")),
                "artifact_key": _clean_text(status.get("artifact_key") or job.get("artifact_key")),
                "result_path": _clean_text(status.get("result_path")),
            }
        )
    return {
        "selected_count": len(selected),
        "ran_count": len(results),
        "skipped_count": max(0, len(job_list) - len(results)),
        "results": results,
        "errors": errors,
    }


def retry_failed_reviewer_jobs_for_gate(
    *,
    store: WorkerJobStore,
    jobs: Iterable[Mapping[str, object]],
    run_id: str,
    approval: str,
    artifact_key: str = "",
    artifact_path: str = "",
    artifact_version: str = "",
    profile_id: str = "",
    max_jobs: int | None = None,
    profile_order: Iterable[str] = (),
) -> dict[str, object]:
    job_list = [dict(job) for job in jobs]
    selected = select_failed_reviewer_jobs_for_gate(
        job_list,
        run_id=run_id,
        approval=approval,
        artifact_key=artifact_key,
        artifact_path=artifact_path,
        artifact_version=artifact_version,
        profile_id=profile_id,
        profile_order=profile_order,
    )
    retryable = selected[:max_jobs] if max_jobs is not None and max_jobs >= 0 else selected
    results: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for job in retryable:
        original_job_id = _job_id(job)
        if not original_job_id:
            errors.append({"job_id": "", "error": "missing failed job id"})
            continue
        retry_attempt = _next_retry_attempt(job)
        try:
            retry_job, _created = store.create_or_reuse_job(
                **_retry_reviewer_job_spec(job, retry_attempt=retry_attempt)
            )
        except (OSError, ValueError) as exc:
            errors.append({"job_id": original_job_id, "error": str(exc)})
            continue
        results.append(
            {
                "job_id": _job_id(retry_job),
                "status": _clean_text(retry_job.get("status")),
                "profile_id": _clean_text(retry_job.get("profile_id") or job.get("profile_id")),
                "approval": _job_gate(retry_job) or _job_gate(job),
                "artifact_key": _clean_text(retry_job.get("artifact_key") or job.get("artifact_key")),
                "artifact_version": _clean_text(retry_job.get("artifact_version") or job.get("artifact_version")),
                "original_failed_job_id": original_job_id,
                "retry_attempt": _clean_text(retry_job.get("retry_attempt") or retry_attempt),
                "retry_idempotency_key": _clean_text(retry_job.get("idempotency_key")),
            }
        )
    return {
        "selected_count": len(selected),
        "retried_count": len(results),
        "skipped_count": max(0, len(job_list) - len(results)),
        "results": results,
        "errors": errors,
        "advisory_only": True,
        "approval_unchanged": True,
    }


def _parallel_enabled(target: Mapping[str, object] | None, phase: str) -> bool:
    config = normalize_reviewer_dispatch_config(target)
    rollout = reviewer_dispatch_rollout_status(target)
    return (
        config["mode"] == "parallel"
        and rollout["parallel_enabled"] is True
        and dict(config["phases"]).get(phase) == "parallel"
    )


def _configured_profiles(target: Mapping[str, object] | None, phase: str) -> list[ReviewerProfile]:
    if target is None:
        return []
    configured = normalize_reviewer_profile_config(target)
    return [get_reviewer_profile(profile_id) for profile_id in configured.get(phase, ())]


def _reviewer_job_spec(
    *,
    run_id: str,
    profile: ReviewerProfile,
    approval: str,
    phase: str,
    artifact_key: str,
    artifact_path: str,
    artifact_version: str,
    dispatch_group_id: str,
    context: str,
    resolution: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    identity_key = "|".join((run_id, approval, artifact_key, artifact_path, artifact_version, profile.profile_id))
    # Worker runtime is authoritative for team-state projects; the profile
    # adapter/model remain only as the legacy fallback for unmigrated targets.
    if resolution is not None:
        adapter = str(resolution["adapter"])
        model = str(resolution["model"] or "") or (profile.model or "")
        worker_id = str(resolution["worker_id"])
        worker_name = str(resolution["worker_name"])
    else:
        adapter = profile.adapter
        model = profile.model or ""
        worker_id = ""
        worker_name = ""
    return {
        "run_id": run_id,
        "role": REVIEWER_ROLE_BY_PHASE.get(phase, "review"),
        "adapter": adapter,
        "model": model,
        "worker_id": worker_id,
        "worker_name": worker_name,
        "task": _reviewer_task(profile=profile, approval=approval, artifact_key=artifact_key, artifact_path=artifact_path),
        "context": context,
        "constraints": [
            "Act as an advisory reviewer only; do not approve or reject the gate.",
            "Review the named artifact and report findings, evidence, and recommendation.",
            "Do not modify project files unless the task explicitly requests a patch.",
        ],
        "expected_artifacts": [f"reviewer-report-{profile.profile_id}.md"],
        "profile_id": profile.profile_id,
        "approval": approval,
        "gate": approval,
        "phase": phase,
        "artifact_key": artifact_key,
        "artifact_path": artifact_path,
        "artifact_version": artifact_version,
        "dispatch_group_id": dispatch_group_id,
        "idempotency_key": hashlib.sha256(identity_key.encode("utf-8")).hexdigest(),
    }


def _worker_type_job_settings(
    target: Mapping[str, object] | None,
    resolution: Mapping[str, object] | None,
    *,
    role: str,
    adapter: str,
) -> dict[str, object]:
    """Reasoning depth / prompt / instance count for reviewer jobs.

    With a worker resolution, the settings come from the exact resolved
    Worker — the same Worker whose identity and provider/model are bound to
    the job. Legacy targets keep reading role-level YAML settings.
    """
    if resolution is not None:
        return {
            "reasoning_depth": str(resolution["reasoning_depth"]),
            "additional_prompt": str(resolution["additional_prompt"]),
            "desired_instances": int(resolution["desired_instances"]),
            "pi_thinking_level": str(resolution["pi_thinking_level"]),
        }
    raw_all = target.get("worker_type_settings", {}) if target is not None else {}
    raw = raw_all.get(role, {}) if isinstance(raw_all, Mapping) else {}
    if not isinstance(raw, Mapping):
        raw = {}
    reasoning = str(raw.get("reasoning_depth", "default") or "default").strip().lower()
    if reasoning not in {"default", "low", "medium", "high"}:
        reasoning = "default"
    try:
        desired_instances = min(max(int(raw.get("desired_instances", 1) or 1), 1), 8)
    except (TypeError, ValueError):
        desired_instances = 1
    return {
        "reasoning_depth": reasoning,
        "additional_prompt": str(raw.get("additional_prompt", "") or "").strip(),
        "desired_instances": desired_instances,
        "pi_thinking_level": reasoning if adapter in SESSION_BACKENDS and reasoning != "default" else "",
    }


def _reviewer_task(*, profile: ReviewerProfile, approval: str, artifact_key: str, artifact_path: str) -> str:
    focus = "\n".join(f"- {item}" for item in profile.focus_areas)
    sections = "\n".join(f"- {item}" for item in profile.required_report_sections)
    return "\n".join(
        [
            f"Review `{artifact_key}` for the `{approval}` approval gate.",
            f"Artifact path: `{artifact_path}`",
            f"Reviewer profile: {profile.label} (`{profile.profile_id}`)",
            "",
            "Focus areas:",
            focus,
            "",
            "Required report sections:",
            sections,
        ]
    )


def _retry_reviewer_job_spec(job: Mapping[str, object], *, retry_attempt: int) -> dict[str, Any]:
    original_job_id = _job_id(job)
    profile_id = _clean_text(job.get("profile_id"))
    approval = _job_gate(job)
    phase = _clean_text(job.get("phase"))
    artifact_key = _clean_text(job.get("artifact_key"))
    artifact_path = _clean_text(job.get("artifact_path"))
    artifact_version = _clean_text(job.get("artifact_version"))
    retry_idempotency_key = _retry_idempotency_key(job, retry_attempt=retry_attempt)
    expected = job.get("expected_artifacts")
    expected_artifacts = [str(item) for item in expected] if isinstance(expected, list) else []
    if not expected_artifacts and profile_id:
        expected_artifacts = [f"reviewer-report-{profile_id}.md"]
    return {
        "run_id": _clean_text(job.get("run_id")),
        "role": _clean_text(job.get("role")) or REVIEWER_ROLE_BY_PHASE.get(phase, "review"),
        "adapter": _clean_text(job.get("adapter")) or "fake",
        "model": _clean_text(job.get("model")),
        "task": _retry_reviewer_task(
            original_job_id=original_job_id,
            profile_id=profile_id,
            approval=approval,
            artifact_key=artifact_key,
            artifact_path=artifact_path,
            artifact_version=artifact_version,
        ),
        "context": _retry_reviewer_context(job),
        "constraints": [
            "Act as an advisory reviewer only; do not approve or reject the gate.",
            "Retry only the failed reviewer-dispatch job identity carried by this job.",
            "Review the named current artifact and report findings, evidence, and recommendation.",
        ],
        "expected_artifacts": expected_artifacts,
        "reasoning_depth": _clean_text(job.get("reasoning_depth")),
        "additional_prompt": _clean_text(job.get("additional_prompt")),
        "desired_instances": int(job.get("desired_instances", 1) or 1),
        "pi_thinking_level": _clean_text(job.get("pi_thinking_level")),
        "worker_id": _clean_text(job.get("worker_id")),
        "worker_name": _clean_text(job.get("worker_name")),
        "profile_id": profile_id,
        "approval": approval,
        "gate": approval,
        "phase": phase,
        "artifact_key": artifact_key,
        "artifact_path": artifact_path,
        "artifact_version": artifact_version,
        "dispatch_group_id": _clean_text(job.get("dispatch_group_id")),
        "attempt": retry_attempt,
        "idempotency_key": retry_idempotency_key,
        "original_failed_job_id": original_job_id,
        "retry_attempt": retry_attempt,
        "retry_of_idempotency_key": _clean_text(job.get("idempotency_key")),
    }


def _retry_reviewer_task(
    *,
    original_job_id: str,
    profile_id: str,
    approval: str,
    artifact_key: str,
    artifact_path: str,
    artifact_version: str,
) -> str:
    return "\n".join(
        [
            f"Retry failed reviewer job `{original_job_id}` for `{approval}`.",
            f"Reviewer profile: `{profile_id}`",
            f"Artifact key: `{artifact_key}`",
            f"Artifact path: `{artifact_path}`",
            f"Artifact version: `{artifact_version}`",
            "",
            "This is an explicit operator-controlled retry. It is advisory evidence only.",
        ]
    )


def _retry_reviewer_context(job: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "Original failed reviewer-dispatch job evidence:",
            f"- job_id: {_job_id(job)}",
            f"- profile_id: {_clean_text(job.get('profile_id'))}",
            f"- approval: {_job_gate(job)}",
            f"- phase: {_clean_text(job.get('phase'))}",
            f"- artifact_key: {_clean_text(job.get('artifact_key'))}",
            f"- artifact_path: {_clean_text(job.get('artifact_path'))}",
            f"- artifact_version: {_clean_text(job.get('artifact_version'))}",
            f"- dispatch_group_id: {_clean_text(job.get('dispatch_group_id'))}",
            f"- idempotency_key: {_clean_text(job.get('idempotency_key'))}",
            f"- summary: {_clean_text(job.get('summary'))}",
            f"- result_path: {_clean_text(job.get('result_path'))}",
            f"- stdout_path: {_clean_text(job.get('stdout_path'))}",
            f"- stderr_path: {_clean_text(job.get('stderr_path'))}",
        ]
    )


def artifact_version_for_path(artifact_path: str) -> str:
    path = Path(artifact_path).expanduser() if artifact_path else None
    if path is None or not path.is_file():
        return "missing"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _dispatch_group_id(
    *,
    run_id: str,
    approval: str,
    artifact_key: str,
    artifact_path: str,
    artifact_version: str,
) -> str:
    raw = "|".join((run_id, approval, artifact_key, artifact_path, artifact_version))
    return f"reviewer-dispatch-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _identity_payload(job: Mapping[str, object]) -> dict[str, Any]:
    keys = (
        "profile_id",
        "approval",
        "gate",
        "phase",
        "artifact_key",
        "artifact_path",
        "artifact_version",
        "dispatch_group_id",
        "idempotency_key",
    )
    return {key: job[key] for key in keys if key in job}


def _clean_mode(value: object, field: str) -> str:
    mode = str(value or "single").strip().lower()
    if mode not in DISPATCH_MODES:
        modes = ", ".join(DISPATCH_MODES)
        raise ValueError(f"{field} must be one of: {modes}.")
    return mode


def _raw_dispatch_config(target: Mapping[str, object] | None) -> Mapping[str, object]:
    if target is None:
        return {}
    raw_config = target.get("reviewer_dispatch", {})
    return raw_config if isinstance(raw_config, Mapping) else {}


def _truthy_bool(value: object) -> bool:
    if value is True:
        return True
    return str(value).strip().lower() == "true"


def _queued_reviewer_job_counts(worker_jobs: Iterable[Mapping[str, object]]) -> dict[str, object]:
    by_gate: dict[str, int] = {}
    by_profile: dict[str, int] = {}
    total = 0
    for job in worker_jobs:
        if not _is_queued_reviewer_job(job):
            continue
        total += 1
        gate = str(job.get("gate") or job.get("approval") or "unknown")
        profile_id = str(job.get("profile_id") or "unknown")
        by_gate[gate] = by_gate.get(gate, 0) + 1
        by_profile[profile_id] = by_profile.get(profile_id, 0) + 1
    return {"total": total, "by_gate": by_gate, "by_profile": by_profile}


def _failed_reviewer_job_counts(worker_jobs: Iterable[Mapping[str, object]]) -> dict[str, object]:
    by_gate: dict[str, int] = {}
    by_profile: dict[str, int] = {}
    total = 0
    for job in worker_jobs:
        if not _is_failed_reviewer_job(job):
            continue
        total += 1
        gate = _job_gate(job) or "unknown"
        profile_id = _clean_text(job.get("profile_id")) or "unknown"
        by_gate[gate] = by_gate.get(gate, 0) + 1
        by_profile[profile_id] = by_profile.get(profile_id, 0) + 1
    return {"total": total, "by_gate": by_gate, "by_profile": by_profile}


def _is_queued_reviewer_job(job: Mapping[str, object]) -> bool:
    if str(job.get("status", "") or "").strip().lower() != "queued":
        return False
    if str(job.get("profile_id", "") or "").strip():
        return True
    if str(job.get("phase", "") or "").strip() in REVIEWER_PROFILE_PHASES:
        return True
    return str(job.get("role", "") or "").strip() in set(REVIEWER_ROLE_BY_PHASE.values())


def _is_executable_queued_reviewer_job(job: Mapping[str, object]) -> bool:
    if str(job.get("status", "") or "").strip().lower() != "queued":
        return False
    return _has_reviewer_dispatch_identity(job)


def _is_failed_reviewer_job(job: Mapping[str, object]) -> bool:
    if str(job.get("status", "") or "").strip().lower() != "failed":
        return False
    return _has_reviewer_dispatch_identity(job)


def _has_reviewer_dispatch_identity(job: Mapping[str, object]) -> bool:
    if not _clean_text(job.get("profile_id")):
        return False
    phase = _clean_text(job.get("phase"))
    if phase not in REVIEWER_PROFILE_PHASES:
        return False
    if not _clean_text(job.get("dispatch_group_id")):
        return False
    if not _clean_text(job.get("idempotency_key")):
        return False
    role = _clean_text(job.get("role"))
    if role and role != REVIEWER_ROLE_BY_PHASE.get(phase, role):
        return False
    return True


def _has_superseding_retry_or_result(job: Mapping[str, object], jobs: Iterable[Mapping[str, object]]) -> bool:
    job_id = _job_id(job)
    job_stamp = _job_timestamp(job)
    for candidate in jobs:
        if _job_id(candidate) == job_id:
            continue
        if _clean_text(candidate.get("run_id")) != _clean_text(job.get("run_id")):
            continue
        if _job_gate(candidate) != _job_gate(job):
            continue
        if _clean_text(candidate.get("profile_id")) != _clean_text(job.get("profile_id")):
            continue
        if _clean_text(candidate.get("artifact_key")) != _clean_text(job.get("artifact_key")):
            continue
        if _clean_text(candidate.get("artifact_path")) != _clean_text(job.get("artifact_path")):
            continue
        if _clean_text(candidate.get("artifact_version")) != _clean_text(job.get("artifact_version")):
            continue
        status = str(candidate.get("status", "") or "").strip().lower()
        if status not in {"queued", "running", "completed", "failed"}:
            continue
        retry_of_job = _clean_text(candidate.get("original_failed_job_id")) == job_id
        retry_of_key = (
            _clean_text(candidate.get("retry_of_idempotency_key"))
            and _clean_text(candidate.get("retry_of_idempotency_key")) == _clean_text(job.get("idempotency_key"))
        )
        if not (retry_of_job or retry_of_key):
            continue
        candidate_stamp = _job_timestamp(candidate)
        if not job_stamp or not candidate_stamp or candidate_stamp >= job_stamp:
            return True
    return False


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _job_gate(job: Mapping[str, object]) -> str:
    return _clean_text(job.get("gate") or job.get("approval"))


def _job_id(job: Mapping[str, object]) -> str:
    return _clean_text(job.get("id") or job.get("job_id"))


def _matches_supplied_identity(job: Mapping[str, object], field: str, wanted: str) -> bool:
    if not wanted:
        return True
    return _clean_text(job.get(field)) == wanted


def _next_retry_attempt(job: Mapping[str, object]) -> int:
    raw_attempt = _clean_text(job.get("retry_attempt") or job.get("attempt") or "1")
    try:
        attempt = int(raw_attempt)
    except ValueError:
        attempt = 1
    return max(1, attempt) + 1


def _retry_idempotency_key(job: Mapping[str, object], *, retry_attempt: int) -> str:
    raw = "|".join(
        (
            "reviewer-retry",
            _job_id(job),
            str(retry_attempt),
            _clean_text(job.get("run_id")),
            _job_gate(job),
            _clean_text(job.get("artifact_key")),
            _clean_text(job.get("artifact_path")),
            _clean_text(job.get("artifact_version")),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _job_timestamp(job: Mapping[str, object]) -> str:
    return _clean_text(job.get("updated_at") or job.get("created_at"))


def _reviewer_job_sort_key(job: Mapping[str, object], profile_order: Mapping[str, int]) -> tuple[object, ...]:
    profile_id = _clean_text(job.get("profile_id"))
    configured_index = profile_order.get(profile_id)
    return (
        configured_index is None,
        configured_index if configured_index is not None else 0,
        _job_gate(job),
        profile_id,
        _clean_text(job.get("created_at")),
        _job_id(job),
    )
