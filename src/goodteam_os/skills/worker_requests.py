"""Bind actual Worker requests to registered task snapshots before publication.

S4b is metadata integration, not S5 Runtime activation. Every Skill-backed run
currently stops after trusted preflight, including fake/noop: no copied Markdown,
model self-report, or fake result is represented as a loaded or executed Skill.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import uuid4

from .availability import SkillAvailabilityStore
from .filesystem import directory, regular_file, subdirectory, write_new
from .library import LOCAL_SCOPE_OWNER, SkillLibraryService
from .models import SkillConflict, SkillError, SkillScope, canonical_json, content_digest
from .task_context import task_skill_context, validate_task_skill_context
from .task_locks import MAX_REGISTRY_BYTES, MAX_RESOURCE_BYTES, REGISTRY_SCHEMA, TaskSkillIdentity, TaskSkillLockStore

WORKER_REQUEST_SCHEMA = "worker_skill_request.v1"
WORKER_METADATA_LOADER = "goodteam.worker-metadata.v1"
SKILL_JOB_RE = re.compile(r"job-skill-[0-9a-f]{64}")
SKILL_FIELDS = ("task_skill_context", "skill_execution_snapshot_ref", "skill_subject", "skill_loader_version")
_INPUT_EXCLUSIONS = frozenset({"self", "task_skill_context", "skill_subject", "skill_loader_version", "required_skill_ids"})


def is_skill_job_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("job-skill-")


def has_skill_fields(value: dict[str, Any]) -> bool:
    return any(str(key).startswith(("skill_", "task_skill_")) or key in {"snapshot_ref", "task_lock_ref"} for key in value)


def subject_for_role(role: str) -> str:
    if role == "orchestrator":
        return "manager:orchestrator"
    if role == "project_intake":
        return "workflow_role:project_intake"
    return "role:" + role


def _digest(value: object) -> str:
    try:
        return content_digest(canonical_json(value).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("worker_skill_input_invalid", "Worker Skill input must contain finite JSON values.") from exc


def _json(data: bytes) -> dict[str, Any]:
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
        _digest(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillError("worker_skill_input_invalid", "Persisted Worker Skill input is invalid.") from exc


def _services(store):
    from goodteam_os.service import service_state_dir

    worker_root = Path(store.project_root).expanduser().resolve()
    operator = Path(service_state_dir()).expanduser().resolve()
    if operator.is_relative_to(worker_root):
        raise SkillError("unsafe_store_root", "Worker Skill authority cannot live inside the Worker root.")
    lock_root = operator / "skills" / "task-locks"
    # A job may use an existing captured task, not bootstrap replacement authority.
    if not lock_root.is_dir():
        raise SkillError("snapshot_missing", "The task Skill authority is unavailable.")
    packages = SkillLibraryService(operator, worker_roots=(worker_root,)).store
    locks = TaskSkillLockStore(lock_root, packages, worker_roots=(worker_root,))
    status = SkillAvailabilityStore(operator / "skills" / "availability", packages, worker_roots=(worker_root,))
    return worker_root, operator, locks, status


@contextmanager
def _request_lock(operator: Path, job_id: str):
    if SKILL_JOB_RE.fullmatch(job_id) is None:
        raise SkillError("worker_skill_identity_invalid", "Worker Skill jobs require a registered stable identity.")
    with directory(operator / "skills" / "worker-requests", create=True) as fd:
        lock = os.open(job_id + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SkillError("unsafe_file", "Worker Skill request lock is unsafe.")
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield fd
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


def _job_id(root: Path, scope, command_key: object) -> str:
    if not isinstance(command_key, str) or not command_key.strip() or len(command_key) > 256:
        raise SkillError("worker_skill_identity_invalid", "Worker Skill requests require a stable logical command key.")
    # Only the logical key and server-owned namespace select the job. Changing
    # task/content/runtime/policy must conflict, never manufacture another job.
    return "job-skill-" + _digest({"scope": asdict(scope), "worker_root": str(root), "command_key": command_key})[7:]


def reject_legacy_key_collision(store, command_key: str) -> None:
    """Read-only check even when a caller removes all Skill metadata fields."""
    from goodteam_os.service import service_state_dir

    if not command_key:
        return
    operator = Path(service_state_dir()).expanduser().resolve()
    lock_root = operator / "skills" / "task-locks"
    if not lock_root.exists():
        return
    scope = SkillScope("user", LOCAL_SCOPE_OWNER)
    job_id = _job_id(Path(store.project_root).expanduser().resolve(), scope, command_key)
    with directory(lock_root) as root:
        with subdirectory(root, scope.namespace) as fd:
            try:
                data = regular_file(fd, "index.json", maximum=MAX_REGISTRY_BYTES)[0]
            except FileNotFoundError:
                with subdirectory(fd, "objects") as objects:
                    with os.scandir(objects) as entries:
                        if next(entries, None) is not None:
                            raise SkillError("snapshot_missing", "Missing task ownership cannot be interpreted as legacy.") from None
                return
            registry = _json(data)
            if (registry.get("schema_version") != REGISTRY_SCHEMA or registry.get("scope") != asdict(scope)
                    or not isinstance(registry.get("jobs"), dict)):
                raise SkillError("snapshot_corrupt", "Unknown task ownership cannot be interpreted as legacy.")
            if _digest(job_id)[7:] in registry["jobs"]:
                raise SkillConflict("operation_conflict", "A registered Skill job cannot be recreated with legacy inputs.")


def _request(values: dict[str, Any]) -> dict[str, Any]:
    request = deepcopy({key: value for key, value in values.items() if key not in _INPUT_EXCLUSIONS})
    for key in ("constraints", "expected_artifacts"):
        value = request.get(key)
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise SkillError("worker_skill_input_invalid", "Worker constraints and outputs must be explicit text lists.")
        request[key] = value
    for key in ("run_id", "role", "adapter", "project_key", "task", "context"):
        if not isinstance(request.get(key), str):
            raise SkillError("worker_skill_input_invalid", "Worker task identity and text must be explicit strings.")
    if request.get("execution_policy") not in {"read_only", "standard"}:
        raise SkillError("worker_skill_input_invalid", "Worker execution policy must be explicit.")
    _digest(request)
    return request


def _input_files(request: dict[str, Any], contract: dict[str, Any]) -> dict[str, str]:
    files = {
        "task.md": request["task"], "context.md": request["context"],
        "constraints.md": "\n".join("- " + item for item in request["constraints"]),
        "expected_artifacts.json": canonical_json(request["expected_artifacts"]),
        # Never replace a frozen contract with the current legacy Role prompt.
        "role_prompt.md": canonical_json(contract),
        "role_memory_paths.json": "{}", "role_pickup_packet.json": "{}",
    }
    if request["role"] == "code_review":
        from .review_protocol import is_review_contract, review_prompt_base

        if is_review_contract(contract):
            # Immutable preparation only. run_job and every Runtime entry stay
            # closed; the base alone cannot satisfy required Skill delivery.
            files["review_prompt_base.md"] = review_prompt_base(request, contract)
    source = request.get("research_report_source")
    if source:
        if not isinstance(source, str):
            raise SkillError("worker_skill_input_invalid", "Research must use an authorized text source reference.")
        path = Path(source).expanduser().absolute()
        try:
            with directory(path.parent) as fd:
                files["research-report.md"] = regular_file(fd, path.name, maximum=MAX_RESOURCE_BYTES)[0].decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillError("worker_skill_input_missing", "The original research input is unavailable.") from exc
    return files


def _metadata(job_id: str, snapshot_ref: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    request = snapshot["job_input"]["request"]
    job = {key: deepcopy(value) for key, value in request.items() if key not in {"task", "context", "constraints"}}
    job.update(id=job_id, status="queued", created_at=snapshot["created_at"], updated_at=snapshot["created_at"],
               task_skill_context=task_skill_context(TaskSkillIdentity(**snapshot["task"]), snapshot["task_lock_ref"]),
               skill_execution_snapshot_ref=deepcopy(snapshot_ref), skill_subject=snapshot["subject"],
               skill_loader_version=snapshot["loader_version"], skill_input_digest=_digest(snapshot["job_input"]))
    return job


def _verify_files(fd: int, expected: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    saved = _json(regular_file(fd, "job.json", maximum=MAX_RESOURCE_BYTES)[0])
    if saved != expected:
        raise SkillConflict("worker_skill_input_changed", "Worker metadata differs from its registered immutable request.")
    for name, content in snapshot["job_input"]["files"].items():
        if regular_file(fd, name, maximum=MAX_RESOURCE_BYTES)[0] != content.encode("utf-8"):
            raise SkillConflict("worker_skill_input_changed", "Worker input bytes differ from the original task snapshot.")
    return saved


def _publication_bytes(job_id: str, expected: dict[str, Any]) -> bytes:
    return canonical_json({"schema_version": "worker_skill_publication.v1", "job_id": job_id,
                           "snapshot_ref": expected["skill_execution_snapshot_ref"],
                           "metadata_digest": _digest(expected)}).encode("utf-8")


def _read_publication(fd: int, job_id: str, expected: dict[str, Any]) -> bool:
    try:
        data = regular_file(fd, job_id + ".published.json", maximum=MAX_RESOURCE_BYTES)[0]
    except FileNotFoundError:
        return False
    if data != _publication_bytes(job_id, expected):
        raise SkillError("worker_skill_publication_invalid", "Worker publication does not match its registered snapshot.")
    return True


def _publish_receipt(fd: int, job_id: str, expected: dict[str, Any]) -> None:
    if not _read_publication(fd, job_id, expected):
        name = ".publish-" + uuid4().hex
        try:
            write_new(fd, name, _publication_bytes(job_id, expected))
            os.rename(name, job_id + ".published.json", src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass
    os.fsync(fd)


def _publish_job(root: Path, job_id: str, expected: dict[str, Any], snapshot: dict[str, Any],
                 authority_fd: int) -> tuple[dict[str, Any], bool]:
    published = _read_publication(authority_fd, job_id, expected)
    with directory(root / ".goodteam" / "workers" / "jobs", create=True) as jobs:
        try:
            with subdirectory(jobs, job_id) as fd:
                saved = _verify_files(fd, expected, snapshot)
                try:
                    status = _json(regular_file(fd, "status.json", maximum=MAX_RESOURCE_BYTES)[0])
                except FileNotFoundError:
                    status = {}
                if not published and (status or regular_file(fd, "result.md", maximum=MAX_RESOURCE_BYTES)[0]):
                    raise SkillError("worker_skill_publication_missing", "Executed Worker evidence lost its publication receipt.")
                os.fsync(jobs)
                _publish_receipt(authority_fd, job_id, expected)
                return {**saved, **{key: status[key] for key in ("status", "summary", "skill_preflight_status", "skill_error_code") if key in status}}, False
        except FileNotFoundError:
            # Only a missing directory is recoverable. A published partial job
            # must not be silently overwritten using a current configuration.
            try:
                os.stat(job_id, dir_fd=jobs, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SkillError("worker_skill_input_missing", "A published Skill job has missing immutable inputs.") from None
            if published:
                raise SkillError("worker_skill_input_missing", "A published Skill job was lost; history cannot be reset to a new job.")
        temporary = ".skill-prepare-" + uuid4().hex
        os.mkdir(temporary, mode=0o700, dir_fd=jobs)
        written = []
        try:
            with subdirectory(jobs, temporary) as fd:
                files = {**snapshot["job_input"]["files"], "job.json": canonical_json(expected), "result.md": ""}
                for name, content in files.items():
                    written.append(name)
                    write_new(fd, name, content.encode("utf-8"))
                os.fsync(fd)
            os.rename(temporary, job_id, src_dir_fd=jobs, dst_dir_fd=jobs)
            os.fsync(jobs)
        finally:
            try:
                with subdirectory(jobs, temporary) as fd:
                    for name in written:
                        try:
                            os.unlink(name, dir_fd=fd)
                        except FileNotFoundError:
                            pass
                os.rmdir(temporary, dir_fd=jobs)
            except FileNotFoundError:
                pass
        _publish_receipt(authority_fd, job_id, expected)
    return expected, True


def create_skill_worker_job(store, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Capture/register before atomic job publication; exact replay uses old bytes."""
    context = validate_task_skill_context(values["task_skill_context"])
    task = TaskSkillIdentity(**context["task"])
    request = _request(values)
    subject = values["skill_subject"]
    loader = values["skill_loader_version"]
    required = values["required_skill_ids"]
    if (request["project_key"] != task.project_key or not request["run_id"]
            or subject != subject_for_role(request["role"])):
        raise SkillError("snapshot_scope_mismatch", "Worker role/project does not match its bounded task context.")
    if task.kind == "workflow_run" and request["run_id"] != task.logical_id:
        raise SkillError("snapshot_scope_mismatch", "Workflow Worker belongs to another run.")
    if task.kind == "issue_execution" and request.get("execution_id") != task.logical_id:
        raise SkillError("snapshot_scope_mismatch", "Issue Worker needs its explicit execution identity.")
    if not isinstance(required, tuple) or any(not isinstance(item, str) for item in required):
        raise SkillError("worker_skill_input_invalid", "Required methods must be an explicit immutable ID tuple.")
    root, operator, locks, _status = _services(store)
    job_id = _job_id(root, locks.scope, request.get("idempotency_key"))
    if request.get("original_failed_job_id") == job_id:
        raise SkillError("review_retry_identity", "A Reviewer retry cannot be its own original job.")
    with _request_lock(operator, job_id) as authority_fd:
        previous = locks.find_snapshot(command_key=job_id)
        if previous is None:
            if store.find_job_by_idempotency_key(request["idempotency_key"]) is not None:
                raise SkillConflict("operation_conflict", "Worker command key already belongs to an existing job.")
            lock = locks.read(context["task_lock_ref"], task=task)
            binding = lock.get("start_input", {}).get("request", {}).get("confirmed_input", {}).get("producer_binding")
            if binding is not None and binding.get("worker_root") != str(root):
                raise SkillError("snapshot_scope_mismatch", "Worker root differs from the authorized task start.")
            methods = lock["subjects"].get(subject)
            if methods is None:
                raise SkillError("subject_not_locked", "Worker subject was not captured at task start.")
            original_id = request.get("original_failed_job_id", "")
            files = (_retry_bundle(store, locks, operator, request, context, subject)
                     if is_skill_job_id(original_id) else _input_files(request, methods["role_contract"]))
            payload = {"schema_version": WORKER_REQUEST_SCHEMA, "worker_root": str(root),
                       "request": request, "files": files}
        else:
            old = previous["snapshot"]
            payload = old["job_input"]
            if (payload.get("schema_version") != WORKER_REQUEST_SCHEMA or payload.get("worker_root") != str(root)
                    or payload.get("request") != request):
                raise SkillConflict("operation_conflict", "Worker command key already owns different immutable inputs.")
        ref = locks.snapshot(context["task_lock_ref"], task=task, subject=subject, command_key=job_id,
                             job_input=payload, loader_version=loader, required_skill_ids=required)
        registered = locks.find_snapshot(command_key=job_id)
        if registered is None or registered["ref"] != ref:
            raise SkillError("snapshot_missing", "Worker snapshot registration is unavailable.")
        snapshot = registered["snapshot"]
        return _publish_job(root, job_id, _metadata(job_id, ref, snapshot), snapshot, authority_fd)


def _retry_bundle(store, locks, operator, request, context, subject):
    """Copy a failed Reviewer's original bundle, never reopen its source paths."""
    from goodteam_os.reviewer_dispatch import _retry_idempotency_key
    from goodteam_os.reviewer_retry_inputs import _verify_artifact

    original_id = request["original_failed_job_id"]
    # Resolve and validate the immutable parent before taking its lock. Invalid
    # self/cyclic references must not acquire a second request lock.
    registered = locks.find_snapshot(command_key=original_id)
    if registered is None:
        raise SkillError("snapshot_missing", "The original Reviewer snapshot is unavailable.")
    original = registered["snapshot"]
    expected = _metadata(original_id, registered["ref"], original)
    if (original["task_lock_ref"] != context["task_lock_ref"] or original["subject"] != subject
            or expected["execution_policy"] != "read_only" or not expected.get("profile_id")
            or expected.get("phase") not in {"implementation", "release"}):
        raise SkillError("review_retry_identity", "Reviewer retry must retain its original task, subject and read-only policy.")
    attempt = int(expected.get("retry_attempt") or expected.get("attempt") or 1) + 1
    if (request.get("retry_attempt") != attempt or request.get("attempt") != attempt
            or request.get("retry_of_idempotency_key") != expected["idempotency_key"]
            or request["idempotency_key"] != _retry_idempotency_key(expected, retry_attempt=attempt)):
        raise SkillConflict("review_retry_identity", "Reviewer retry must use its stable next-attempt identity.")
    mutable = {"idempotency_key", "attempt", "retry_attempt", "original_failed_job_id", "retry_of_idempotency_key"}
    original_request = original["job_input"]["request"]
    if ({key: value for key, value in request.items() if key not in mutable}
            != {key: value for key, value in original_request.items() if key not in mutable}):
        raise SkillConflict("operation_conflict", "Reviewer retry cannot change the original immutable request.")
    with _request_lock(operator, original_id) as authority_fd:
        if not _read_publication(authority_fd, original_id, expected):
            raise SkillError("worker_skill_publication_missing", "The original Reviewer was not completely published.")
        with directory(Path(store.root)) as jobs:
            with subdirectory(jobs, original_id) as fd:
                _verify_files(fd, expected, original)
                status = _json(regular_file(fd, "status.json", maximum=MAX_RESOURCE_BYTES)[0])
        attempt_id = status.get("skill_preflight_attempt_id")
        if not isinstance(attempt_id, str) or re.fullmatch(r"preflight-[0-9a-f]{32}", attempt_id) is None:
            raise SkillError("review_retry_state_changed", "Reviewer failure has no trusted attempt evidence.")
        recorded = _json(regular_file(authority_fd, original_id + "." + attempt_id + ".json", maximum=MAX_RESOURCE_BYTES)[0])
        if recorded != status or status.get("status") != "failed":
            raise SkillConflict("review_retry_state_changed", "The original Reviewer failure state is unavailable or changed.")
        _verify_artifact(expected)
        return deepcopy(original["job_input"]["files"])


def create_skill_reviewer_retry(store, selected, *, retry_attempt: int, idempotency_key: str):
    """Route the real Reviewer retry entry through the same Worker contract."""
    original_id = selected.get("id") or selected.get("job_id")
    root, _operator, locks, _status = _services(store)
    if not is_skill_job_id(original_id):
        raise SkillError("review_retry_identity", "Reviewer retry requires its original Skill job identity.")
    registered = locks.find_snapshot(command_key=original_id)
    if registered is None:
        raise SkillError("snapshot_missing", "The original Reviewer snapshot is unavailable.")
    snapshot = registered["snapshot"]
    if snapshot["job_input"].get("worker_root") != str(root):
        raise SkillError("snapshot_scope_mismatch", "The original Reviewer belongs to another Worker root.")
    expected = _metadata(original_id, registered["ref"], snapshot)
    for key in ("id", "run_id", "project_key", "role", "profile_id", "phase", "approval", "gate",
                "artifact_key", "artifact_path", "artifact_version", "dispatch_group_id", "idempotency_key"):
        if key in selected and selected[key] != expected.get(key):
            raise SkillConflict("review_retry_identity", "Selected Reviewer evidence no longer matches its registered identity.")
    request = {**deepcopy(snapshot["job_input"]["request"]), "attempt": retry_attempt,
               "idempotency_key": idempotency_key, "original_failed_job_id": original_id,
               "retry_attempt": retry_attempt, "retry_of_idempotency_key": expected["idempotency_key"],
               "task_skill_context": expected["task_skill_context"], "skill_subject": snapshot["subject"],
               "skill_loader_version": snapshot["loader_version"], "required_skill_ids": tuple(snapshot["required_skill_ids"])}
    return store.create_or_reuse_job(**request)


def original_worker_request(store, *, command_key: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """Recover a producer's original Runtime/request only through trusted refs.

    The producer must compare its logical message/intent through task-start
    replay before calling. Mutable status projections never select inputs.
    """
    validated = validate_task_skill_context(context)
    root, _operator, locks, _status = _services(store)
    job_id = _job_id(root, locks.scope, command_key)
    registered = locks.find_snapshot(command_key=job_id)
    if registered is None:
        return None
    snapshot = registered["snapshot"]
    payload = snapshot["job_input"]
    if (snapshot["task_lock_ref"] != validated["task_lock_ref"] or snapshot["task"] != validated["task"]
            or payload.get("schema_version") != WORKER_REQUEST_SCHEMA or payload.get("worker_root") != str(root)):
        raise SkillConflict("operation_conflict", "The Worker command belongs to a different task or root.")
    return {**deepcopy(payload["request"]), "task_skill_context": validated,
            "skill_subject": snapshot["subject"], "skill_loader_version": snapshot["loader_version"],
            "required_skill_ids": tuple(snapshot["required_skill_ids"])}


def _write_status(fd: int, payload: dict[str, Any]) -> None:
    name = ".skill-status-" + uuid4().hex
    try:
        write_new(fd, name, canonical_json(payload).encode("utf-8"))
        os.replace(name, "status.json", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass


def run_skill_worker_job(store, job_id: str, *, sandbox_context=None) -> dict[str, Any]:
    """Every explicit attempt verifies trusted metadata; S5 Runtime stays closed."""
    root, operator, locks, status = _services(store)
    with _request_lock(operator, job_id) as authority_fd:
        registered = locks.find_snapshot(command_key=job_id)
        if registered is None:
            raise SkillError("snapshot_missing", "Skill job is not registered; legacy fallback is forbidden.")
        snapshot, ref = registered["snapshot"], registered["ref"]
        if (snapshot["job_input"].get("schema_version") != WORKER_REQUEST_SCHEMA
                or snapshot["job_input"].get("worker_root") != str(root)):
            raise SkillError("snapshot_scope_mismatch", "Skill job belongs to another Worker root.")
        expected = _metadata(job_id, ref, snapshot)
        if not _read_publication(authority_fd, job_id, expected):
            raise SkillError("worker_skill_publication_missing", "Skill job preparation is incomplete; explicit creation replay is required.")
        with directory(root / ".goodteam" / "workers" / "jobs") as jobs:
            with subdirectory(jobs, job_id) as fd:
                try:
                    _verify_files(fd, expected, snapshot)
                    locks.preflight(ref, task=TaskSkillIdentity(**snapshot["task"]), command_key=job_id,
                                    job_input=snapshot["job_input"], supported_loaders=(WORKER_METADATA_LOADER,),
                                    current_availability=status.current_status)
                except (SkillError, FileNotFoundError) as exc:
                    code = exc.code if isinstance(exc, SkillError) else "worker_skill_input_missing"
                    checked = "blocked"
                else:
                    code = "skill_runtime_not_connected"
                    checked = "verified"
                # Metadata verification never authorizes CLI/version probes,
                # session continuation, sandbox remapping, or fake Skill output.
                result = {"job_id": job_id, "run_id": expected["run_id"], "project_key": expected["project_key"],
                          "role": expected["role"], "adapter": expected["adapter"], "status": "failed",
                          "summary": "Skill execution is blocked before Runtime invocation.",
                          "updated_at": datetime.now(UTC).isoformat(), "artifacts": [], "changed_files": [],
                          "skill_preflight_status": checked, "skill_error_code": code,
                          "skill_runtime_status": "not_connected", "task_skill_context": expected["task_skill_context"],
                          "skill_execution_snapshot_ref": ref, "skill_subject": snapshot["subject"],
                          "skill_loader_version": snapshot["loader_version"],
                          "invocation_evidence": {"kind": "blocked", "adapter": expected["adapter"],
                                                  "provider": "none", "external_invocation": False,
                                                  "command": "", "model": expected["model"], "reason": code}}
                # Append durable preflight evidence in Operator storage before
                # changing the mutable status view. No failed history is erased.
                attempt_id = "preflight-" + uuid4().hex
                result["skill_preflight_attempt_id"] = attempt_id
                write_new(authority_fd, job_id + "." + attempt_id + ".json",
                          canonical_json(result).encode("utf-8"))
                os.fsync(authority_fd)
                # Preserve the immutable job request even when a caller tampers
                # with it. Failure reporting must not silently repair job.json.
                _write_status(fd, result)
                return result


def reject_unverified_runtime_entry(job_root: Path, job: dict[str, Any]) -> None:
    """S5 has not opened any Skill-backed lower-level Runtime entrypoint."""
    if is_skill_job_id(Path(job_root).name) or is_skill_job_id(job.get("id")) or has_skill_fields(job):
        raise SkillError("skill_runtime_not_connected", "Skill Runtime entry requires an independently verified loader; legacy bypass is forbidden.")


def public_skill_job_fields(job) -> dict[str, Any]:
    """Project method identity/evidence, never private request or package bodies.

    This is a read-only display projection, not execution authorization. Missing
    values remain missing instead of fabricating a successful preflight/load.
    Legacy summary shapes remain unchanged.
    """
    if not is_skill_job_id(job.get("id")) and not has_skill_fields(job):
        return {}
    fields = ("run_id", "project_key", "skill_subject", "skill_loader_version",
              "skill_preflight_status", "skill_preflight_attempt_id",
              "skill_error_code", "skill_runtime_status")
    result = {key: job[key] for key in fields if key in job and isinstance(job[key], str)}
    context = job.get("task_skill_context")
    if context is not None:
        try:
            result["task_skill_context"] = validate_task_skill_context(context)
        except SkillError:
            result["skill_error_code"] = "task_context_invalid"
    ref = job.get("skill_execution_snapshot_ref")
    if isinstance(ref, dict):
        # Do not copy unexpected nested fields even from mutable job metadata.
        result["skill_execution_snapshot_ref"] = {
            key: ref[key] for key in ("schema_version", "resource_schema", "id", "digest")
            if key in ref and isinstance(ref[key], str)
        }
        if isinstance(ref.get("scope"), dict):
            result["skill_execution_snapshot_ref"]["scope"] = {
                key: ref["scope"][key] for key in ("kind", "owner_id", "project_key")
                if key in ref["scope"] and isinstance(ref["scope"][key], str)
            }
    evidence = job.get("invocation_evidence")
    if isinstance(evidence, dict):
        result["invocation_evidence"] = {
            key: evidence[key] for key in ("kind", "adapter", "provider", "reason")
            if key in evidence and isinstance(evidence[key], str)
        }
        if type(evidence.get("external_invocation")) is bool:
            result["invocation_evidence"]["external_invocation"] = evidence["external_invocation"]
    return result
