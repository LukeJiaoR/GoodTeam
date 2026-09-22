"""Replay persisted Reviewer requests without inventing a new review task.

Legacy job files are the existing input record, not tamper-proof evidence. A
Skill-backed retry uses the registered task-lock/Worker path. Malformed Skill
metadata on a legacy job is rejected rather than downgraded to this path.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from goodteam_os.skills.filesystem import directory, regular_file, subdirectory
from goodteam_os.skills.models import SkillConflict, SkillError, canonical_json, content_digest, resource_path

MAX_REVIEW_INPUT_BYTES = 8 * 1024 * 1024
# These are the existing WorkerJobStore.create_job inputs, not projected status
# fields. New Skill inputs must be explicitly integrated, never silently dropped.
JOB_REQUEST_FIELDS = (
    "run_id", "role", "adapter", "model", "task_source", "constraints_source",
    "research_report_source", "expected_artifacts", "profile_id", "approval",
    "gate", "phase", "artifact_key", "artifact_path", "artifact_version",
    "dispatch_group_id", "attempt", "idempotency_key", "original_failed_job_id",
    "retry_attempt", "retry_of_idempotency_key", "session_policy", "worker_session_ref",
    "execution_id", "project_key", "pi_path", "pi_provider", "pi_model",
    "pi_thinking_level", "pi_session_dir", "reasoning_depth", "additional_prompt",
    "desired_instances", "execution_policy", "worker_id", "worker_name",
)
SELECTION_IDENTITY_FIELDS = (
    "run_id", "role", "profile_id", "approval", "gate", "phase", "artifact_key",
    "artifact_path", "artifact_version", "dispatch_group_id", "idempotency_key",
    "project_key", "execution_id",
)


def _job_id(job: Mapping[str, object]) -> str:
    value = job.get("id") or job.get("job_id")
    if not isinstance(value, str) or not value.startswith("job-") or "/" in resource_path(value):
        raise SkillError("review_retry_identity", "Reviewer retry requires one original job identity.")
    return value


def _decode(data: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    try:
        result = json.loads(data, object_pairs_hook=pairs)
        if not isinstance(result, dict):
            raise ValueError("Expected object")
        canonical_json(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("review_retry_inputs", "The original Reviewer request metadata is invalid.") from exc


def _reject_unwired_skills(job: Mapping[str, object]) -> None:
    if (any(str(key).startswith(("skill_", "task_skill_")) for key in job)
            or any(key in job for key in ("task_lock_ref", "snapshot_ref"))):
        raise SkillError("skill_retry_not_connected", "Skill-backed Reviewer retry requires the task-lock Worker path; legacy fallback is forbidden.")


def _read_optional(fd: int, name: str) -> bytes | None:
    try:
        return regular_file(fd, name, maximum=MAX_REVIEW_INPUT_BYTES)[0]
    except FileNotFoundError:
        return None


def _load_request(store, job: Mapping[str, object]) -> tuple[dict[str, Any], dict[str, str], bytes | None]:
    job_id = _job_id(job)
    _reject_unwired_skills(job)
    try:
        with directory(Path(store.root)) as jobs:
            with subdirectory(jobs, job_id) as fd:
                saved = _decode(regular_file(fd, "job.json", maximum=1024 * 1024)[0])
                _reject_unwired_skills(saved)
                if saved.get("id") != job_id:
                    raise SkillError("review_retry_identity", "The original Reviewer job identity does not match its directory.")
                for key in SELECTION_IDENTITY_FIELDS:
                    if key in job and job[key] != saved.get(key):
                        raise SkillConflict("review_retry_identity", "The selected Reviewer job no longer matches its persisted identity.")
                files = {
                    name: regular_file(fd, name, maximum=MAX_REVIEW_INPUT_BYTES)[0].decode("utf-8")
                    for name in ("task.md", "context.md", "constraints.md", "role_prompt.md", "expected_artifacts.json")
                }
                research = _read_optional(fd, "research-report.md")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillError("review_retry_inputs", "The original Reviewer input files are unavailable; a shortened retry task is not safe.") from exc
    if any(not isinstance(saved.get(key), str) or not saved[key] for key in ("run_id", "role", "adapter")):
        raise SkillError("review_retry_inputs", "The original Reviewer runtime identity is incomplete.")
    if saved.get("execution_policy") not in {"standard", "read_only"}:
        raise SkillError("review_retry_policy", "The original Reviewer execution policy is unknown; retry cannot infer broader permissions.")
    try:
        expected = json.loads(files["expected_artifacts.json"])
    except (ValueError, TypeError) as exc:
        raise SkillError("review_retry_inputs", "The original Reviewer output contract is invalid.") from exc
    if (not isinstance(expected, list) or any(not isinstance(value, str) for value in expected)
            or expected != saved.get("expected_artifacts")):
        raise SkillError("review_retry_inputs", "The original Reviewer output contract does not match its request.")
    if files["constraints.md"] and not files["constraints.md"].startswith("- "):
        raise SkillError("review_retry_inputs", "The original Reviewer constraints cannot be restored exactly.")
    if saved.get("research_report_source") and research is None:
        raise SkillError("review_retry_inputs", "The captured Reviewer research input is missing; retry cannot reopen its mutable source.")
    return saved, files, research


@contextmanager
def _retry_lock(store, job_id: str):
    # One original job serializes its explicit retry requests. This does not
    # replace the universal Worker idempotency contract required by S4.
    with directory(Path(store.root)) as jobs:
        with subdirectory(jobs, job_id) as fd:
            lock = os.open(".review-retry.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SkillError("unsafe_file", "Reviewer retry lock is unsafe.")
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)


def _verify_artifact(saved: Mapping[str, object]) -> None:
    version = saved.get("artifact_version", "")
    path = saved.get("artifact_path", "")
    # Old jobs sometimes have non-content-addressed artifact versions; retain
    # that legacy compatibility without claiming those versions are verified.
    if not isinstance(version, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", version) is None:
        return
    if not isinstance(path, str) or not path:
        raise SkillError("review_retry_artifact_changed", "The original Reviewer artifact is unavailable.")
    artifact = Path(path).expanduser().absolute()
    try:
        with directory(artifact.parent) as fd:
            data, _ = regular_file(fd, artifact.name, maximum=MAX_REVIEW_INPUT_BYTES)
    except (OSError, SkillError) as exc:
        raise SkillError("review_retry_artifact_changed", "The original Reviewer artifact is unavailable or unsafe.") from exc
    if content_digest(data) != version:
        raise SkillError("review_retry_artifact_changed", "The Reviewer artifact changed; this retry cannot silently review the latest bytes.")


def create_reviewer_retry(store, selected: Mapping[str, object], *, retry_attempt: int,
                          idempotency_key: str) -> tuple[dict[str, Any], bool]:
    """Queue an exact legacy request replay; never call a provider here.

    Original task/context/constraints and output bytes are reused. Runtime and
    policy come from saved job metadata, not the caller's status projection.
    Research is copied from the original captured file, never its mutable source.
    This path does not upgrade a legacy job to Skill execution.
    """
    if type(retry_attempt) is not int or retry_attempt < 2:
        raise SkillError("review_retry_identity", "Reviewer retry requires a valid explicit attempt.")
    if not isinstance(idempotency_key, str) or re.fullmatch(r"[0-9a-f]{64}", idempotency_key) is None:
        raise SkillError("review_retry_identity", "Reviewer retry requires its stable command key.")
    original_id = _job_id(selected)
    from goodteam_os.skills.worker_requests import create_skill_reviewer_retry, is_skill_job_id

    if is_skill_job_id(original_id):
        return create_skill_reviewer_retry(store, selected, retry_attempt=retry_attempt, idempotency_key=idempotency_key)
    with _retry_lock(store, original_id):
        saved, files, research = _load_request(store, selected)
        request = {key: deepcopy(saved[key]) for key in JOB_REQUEST_FIELDS if key in saved}
        # The legacy format only saved rendered constraint bytes. One multiline
        # element round-trips those bytes exactly, without inventing list items.
        constraints = files["constraints.md"]
        request.update(
            task=files["task.md"], context=files["context.md"],
            constraints=[constraints[2:]] if constraints else [],
            attempt=retry_attempt, idempotency_key=idempotency_key,
            original_failed_job_id=original_id, retry_attempt=retry_attempt,
            retry_of_idempotency_key=saved.get("idempotency_key", ""),
        )
        if research is not None:
            request["research_report_source"] = str(Path(store.root) / original_id / "research-report.md")
        existing = store.find_job_by_idempotency_key(idempotency_key)
        if existing is not None:
            prior, prior_files, prior_research = _load_request(store, existing)
            for key, value in request.items():
                if key in {"task", "context", "constraints"}:
                    continue
                # Worker identity fields serialize numeric attempt values as text.
                actual = prior.get(key)
                if key in {"attempt", "retry_attempt"}:
                    actual, value = str(actual), str(value)
                if actual != value:
                    raise SkillConflict("operation_conflict", "Reviewer retry key belongs to different saved inputs.")
            if (any(prior_files[name] != files[name] for name in ("task.md", "context.md", "constraints.md", "role_prompt.md"))
                    or prior_research != research):
                raise SkillConflict("operation_conflict", "Reviewer retry key belongs to different saved input bytes.")
            return existing, False
        with directory(Path(store.root)) as jobs:
            with subdirectory(jobs, original_id) as fd:
                raw_status = _read_optional(fd, "status.json")
                status = _decode(raw_status) if raw_status is not None else saved
        if status.get("status") != "failed":
            raise SkillConflict("review_retry_state_changed", "The original Reviewer is no longer failed; refresh its current state.")
        try:
            attempt = max(1, int(saved.get("retry_attempt") or saved.get("attempt") or 1)) + 1
        except (TypeError, ValueError):
            attempt = 2
        if retry_attempt != attempt:
            raise SkillConflict("review_retry_identity", "The retry attempt does not match the original Reviewer request.")
        _verify_artifact(saved)
        from goodteam_os.workers import _role_prompt_for_job

        if _role_prompt_for_job(saved["role"])[1] != files["role_prompt.md"]:
            raise SkillError("review_retry_role_changed", "The Reviewer Role contract changed; start a separately confirmed review instead of changing this retry.")
        return store.create_or_reuse_job(**request)
