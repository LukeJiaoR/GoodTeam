"""Control-plane-owned advisory results and execution details for locked reviews.

Worker-visible status files and model output never select task/method identity.
The immutable result is written and fsynced before its small status projection.
An interrupted projection can be recovered without invoking the Reviewer again.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import fcntl
import os
from pathlib import Path
from uuid import uuid4

from . import worker_requests as requests
from .content import MAX_CONTEXT_BYTES, OBSERVED_STAGES, SkillContextProvider, _bytes, _decode, _publish_immutable
from .filesystem import directory, open_lock_file, regular_file, write_new
from .models import SkillConflict, SkillError, content_digest, identifier
from .review_capture import CapturedReviewTurn
from .review_protocol import locked_review_contract
from .review_report import validate_report

RESULT_SCHEMA = "goodteam.review-result.v1"
DETAIL_SCHEMA = "goodteam.review-execution.v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
_TERMINAL = frozenset({"received", "invalid", "failed", "cancelled", "interrupted"})
_STATES = _TERMINAL | {"prepared", "running", "stopping"}


def _now():
    return datetime.now(UTC).isoformat()


def _replace(fd: int, name: str, value: dict) -> None:
    temporary = ".review-stage-" + uuid4().hex
    try:
        write_new(fd, temporary, _bytes(value))
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def _read_optional(fd: int, name: str):
    try:
        return _decode(regular_file(fd, name, maximum=MAX_CONTEXT_BYTES)[0])
    except FileNotFoundError:
        return None


class ReviewResultStore:
    """Internal parent-side write API; public HTTP integration is read-only."""

    def __init__(self, workers, job_id: str):
        if not isinstance(job_id, str) or requests.SKILL_JOB_RE.fullmatch(job_id) is None:
            raise SkillError("review_identity_invalid", "Review results require a registered Skill Worker.")
        self.workers, self.job_id = workers, job_id

    def _registered(self, *, verify_files=True):
        root, operator, locks, availability = requests._services(self.workers)
        with requests._request_lock(operator, self.job_id) as fd:
            registered = locks.find_snapshot(command_key=self.job_id)
            if registered is None:
                raise SkillError("snapshot_missing", "The Reviewer has no registered original snapshot.")
            snapshot, ref = registered["snapshot"], registered["ref"]
            if (snapshot["job_input"].get("schema_version") != requests.WORKER_REQUEST_SCHEMA
                    or snapshot["job_input"].get("worker_root") != str(root)
                    or snapshot["subject"] != "role:code_review"):
                raise SkillError("snapshot_scope_mismatch", "The report belongs to another Worker scope or Role.")
            expected = requests._metadata(self.job_id, ref, snapshot)
            if not requests._read_publication(fd, self.job_id, expected):
                raise SkillError("worker_skill_publication_missing", "The Reviewer input was not completely published.")
            if verify_files:
                with directory(root / ".goodteam" / "workers" / "jobs" / self.job_id) as job_fd:
                    requests._verify_files(job_fd, expected, snapshot)
        if (expected.get("execution_policy") != "read_only" or expected.get("session_policy", "new") != "new"
                or expected.get("worker_session_ref")
                or locked_review_contract(self.workers, expected["task_skill_context"]) is None):
            raise SkillError("review_protocol_unsupported", "This report path requires the captured read-only, new-session Reviewer protocol.")
        return operator, snapshot, ref, expected

    @contextmanager
    def _store(self, operator, *, create=False):
        with directory(operator / "skills" / "review-results" / self.job_id, create=create) as fd:
            lock = open_lock_file(fd, "result.lock", create=create)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield fd
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)

    def _target(self, snapshot):
        request = snapshot["job_input"]["request"]
        if request.get("review_target"):
            from .review_sources import captured_git_target

            return captured_git_target(snapshot)
        path, version, key = (request.get(name) for name in ("artifact_path", "artifact_version", "artifact_key"))
        if not isinstance(path, str) or not path or not isinstance(key, str) or not key:
            raise SkillError("review_target_missing", "This Reviewer needs an explicitly identified, versioned review artifact.")
        identifier(version, "sha256:")
        source_path = Path(path).expanduser().absolute()
        with directory(source_path.parent) as fd:
            data = regular_file(fd, source_path.name, maximum=MAX_SOURCE_BYTES)[0]
        if content_digest(data) != version:
            raise SkillError("review_target_changed", "The review target no longer matches the original task's artifact version.")
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise SkillError("review_target_unsupported", "The selected Reviewer path currently accepts bounded UTF-8 review input.") from exc
        return {"kind": "artifact", "artifact_key": key, "artifact_version": version,
                "display_name": source_path.name, "source_name": "candidate"}, {"candidate": text}

    def begin(self, provider: SkillContextProvider, context_ref: dict) -> dict:
        """Capture source bytes and bind a context before any process is launched."""
        if provider.workers is not self.workers or provider.job_id != self.job_id:
            raise SkillError("review_identity_invalid", "The content provider belongs to another Reviewer.")
        context_ref = deepcopy(context_ref)
        provider.private_input(context_ref)  # Current authority, complete input, original snapshot.
        _operator, _packages, snapshot, context = provider._read(context_ref)
        operator, current, ref, expected = self._registered()
        if current != snapshot or context_ref["snapshot_ref"] != ref:
            raise SkillError("snapshot_scope_mismatch", "The review context differs from its registered input.")
        target, sources = self._target(snapshot)
        record = {
            "schema_version": RESULT_SCHEMA, "job_id": self.job_id,
            "task_skill_context": expected["task_skill_context"], "snapshot_ref": ref,
            "context_ref": deepcopy(context_ref), "runtime_session": context["runtime_session"],
            "job_input_digest": content_digest(_bytes(snapshot["job_input"])), "target": target,
            "methods": [{key: item[key] for key in ("skill_id", "version_id", "package_digest", "activation")}
                        for item in context["input"]["catalog"]],
            "required_skill_ids": deepcopy(snapshot["required_skill_ids"]),
            "sources": sources,
        }
        if len(_bytes(record)) > MAX_CONTEXT_BYTES:
            raise SkillError("review_target_unsupported", "The captured review input exceeds its storage bound.")
        with self._store(operator, create=True) as fd:
            _publish_immutable(fd, "attempt.json", _bytes(record), code="review_attempt_conflict")
            state = _read_optional(fd, "state.json")
            if state is None:
                # No existing process may be inferred from a partial begin. The
                # durable running transition is separately written before launch.
                state = {"schema_version": RESULT_SCHEMA, "binding_digest": content_digest(_bytes(record)),
                         "status": "prepared", "updated_at": _now()}
                _replace(fd, "state.json", state)
            self._check_state(state, record)
        return self.detail()

    def _check_record(self, record, snapshot, ref, expected):
        if (record.get("schema_version") != RESULT_SCHEMA or record.get("job_id") != self.job_id
                or record.get("snapshot_ref") != ref
                or record.get("task_skill_context") != expected["task_skill_context"]
                or record.get("job_input_digest") != content_digest(_bytes(snapshot["job_input"]))):
            raise SkillError("review_result_identity", "Stored review evidence differs from the original task and Worker.")
        # Validates protected context bytes and current task availability, not a
        # public job flag or a model's claimed method/context identifiers.
        provider = SkillContextProvider(self.workers, self.job_id)
        _operator, _packages, _snapshot, context = provider._read(record.get("context_ref"))
        methods = [{key: item[key] for key in ("skill_id", "version_id", "package_digest", "activation")}
                   for item in context["input"]["catalog"]]
        if (record.get("runtime_session") != context["runtime_session"] or record.get("methods") != methods
                or record.get("required_skill_ids") != snapshot["required_skill_ids"]):
            raise SkillError("review_result_identity", "Stored review methods or Runtime identity changed.")
        source = record.get("sources")
        target = record.get("target")
        request = snapshot["job_input"]["request"]
        if request.get("review_target"):
            fixed_target, fixed_sources = self._target(snapshot)
            if target != fixed_target or source != fixed_sources:
                raise SkillError("review_result_identity", "Stored report sources differ from the original Git change snapshot.")
            return
        if (not isinstance(source, dict) or set(source) != {"candidate"} or not isinstance(source["candidate"], str)
                or not isinstance(target, dict) or target.get("artifact_version") != request.get("artifact_version")
                or target.get("artifact_key") != request.get("artifact_key")
                or content_digest(source["candidate"].encode("utf-8")) != request.get("artifact_version")):
            raise SkillError("review_result_identity", "Stored report sources no longer match the original candidate.")

    @staticmethod
    def _check_state(state, record):
        if (not isinstance(state, dict) or state.get("schema_version") != RESULT_SCHEMA
                or state.get("binding_digest") != content_digest(_bytes(record))
                or not isinstance(state.get("status"), str) or state.get("status") not in _STATES or not isinstance(state.get("updated_at"), str)):
            raise SkillError("review_result_corrupt", "Stored review state is missing, corrupt or bound to another input.")

    def private_attempt(self) -> dict:
        """Only the adapter may use the source bytes; the detail API never does."""
        operator, snapshot, ref, expected = self._registered()
        with self._store(operator) as fd:
            record = _read_optional(fd, "attempt.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The review attempt has not been prepared.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(_read_optional(fd, "state.json"), record)
        return record

    def observe_delivery(self, stage: str, evidence_digest: str) -> None:
        """Internal parent observation, never a model-reported method-use event."""
        if stage not in {"materialized", "catalog_presented", "content_delivered"}:
            raise SkillError("review_observation_invalid", "This adapter cannot attest resource reads or script execution.")
        identifier(evidence_digest, "sha256:")
        operator, snapshot, ref, expected = self._registered()
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "Delivery observation needs an original review attempt.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(state, record)
            if state["status"] not in {"prepared", "running"}:
                raise SkillConflict("review_observation_late", "Stopped execution cannot acquire new delivery observations.")
            receipt = {"schema_version": RESULT_SCHEMA, "binding_digest": state["binding_digest"],
                       "stage": stage, "evidence_digest": evidence_digest, "origin": "parent"}
            _publish_immutable(fd, "observed-" + stage + ".json", _bytes(receipt), code="review_observation_conflict")

    def mark_running(self) -> None:
        operator, snapshot, ref, expected = self._registered()
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The review attempt has not been prepared.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(state, record)
            if state["status"] != "prepared" or _read_optional(fd, "outcome.json") is not None:
                raise SkillConflict("review_already_started", "This Reviewer cannot be launched again; recover its existing outcome or explicitly retry the task.")
            _replace(fd, "state.json", {**state, "status": "running", "updated_at": _now()})

    @staticmethod
    def _validate_outcome(record, outcome):
        if (outcome.get("schema_version") != RESULT_SCHEMA
                or outcome.get("binding_digest") != content_digest(_bytes(record))
                or not isinstance(outcome.get("status"), str) or outcome.get("status") not in _TERMINAL
                or not isinstance(outcome.get("updated_at"), str)):
            raise SkillError("review_result_corrupt", "Stored review outcome has invalid provenance.")
        if outcome["status"] == "received":
            capture = outcome.get("capture")
            if (not isinstance(capture, dict) or set(capture) != {"transcript_digest", "thread_id", "event_count"}
                    or not isinstance(capture["thread_id"], str) or not capture["thread_id"]
                    or type(capture["event_count"]) is not int or capture["event_count"] < 4):
                raise SkillError("review_result_corrupt", "The report has no parent-controlled terminal capture.")
            identifier(capture["transcript_digest"], "sha256:")
            raw = outcome.get("raw_report")
            if not isinstance(raw, str):
                raise SkillError("review_result_corrupt", "The report payload is unavailable.")
            report = validate_report(raw.encode("utf-8"), sources={key: text.encode("utf-8") for key, text in record["sources"].items()})
            if outcome.get("report") != report:
                raise SkillError("review_result_corrupt", "The saved report differs from its validated source-bound payload.")
        elif (not isinstance(outcome.get("error_code"), str) or len(outcome["error_code"]) > 128
              or not outcome["error_code"].replace("_", "").isalnum()):
            raise SkillError("review_result_corrupt", "The terminal failure has no bounded diagnostic.")

    def _outcome(self, fd, record, outcome):
        self._validate_outcome(record, outcome)
        _publish_immutable(fd, "outcome.json", _bytes(outcome), code="review_result_conflict")
        state = {"schema_version": RESULT_SCHEMA, "binding_digest": outcome["binding_digest"],
                 "status": outcome["status"], "updated_at": outcome["updated_at"],
                 "outcome_digest": content_digest(_bytes(outcome))}
        _replace(fd, "state.json", state)

    def receive(self, capture: CapturedReviewTurn) -> dict:
        """Called only by the parent after a successful, reaped Reviewer process."""
        if not isinstance(capture, CapturedReviewTurn):
            raise SkillError("review_capture_required", "A model-written success claim is not a captured Reviewer turn.")
        operator, snapshot, ref, expected = self._registered()
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The result has no original review attempt.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(state, record)
            self._target(snapshot)  # Never receive a changed candidate as this task's current result.
            previous = _read_optional(fd, "outcome.json")
            observation = {"transcript_digest": capture.transcript_digest, "thread_id": capture.thread_id,
                           "event_count": capture.event_count}
            if previous is not None:
                if (previous.get("status") != "received" or previous.get("raw_report") != capture.report_bytes.decode("utf-8")
                        or previous.get("capture") != observation):
                    raise SkillConflict("review_result_conflict", "This attempt already has a different immutable outcome.")
                self._outcome(fd, record, previous)
            else:
                if state["status"] != "running":
                    raise SkillConflict("review_result_not_running", "A stopped or unstarted Reviewer cannot publish a successful report.")
                report = validate_report(capture.report_bytes, sources={key: text.encode("utf-8") for key, text in record["sources"].items()})
                outcome = {"schema_version": RESULT_SCHEMA, "binding_digest": content_digest(_bytes(record)),
                           "status": "received", "updated_at": _now(), "capture": observation,
                           "raw_report": capture.report_bytes.decode("utf-8"), "report": report}
                self._outcome(fd, record, outcome)
        return self.detail()

    def fail(self, code: str, *, status: str = "failed") -> dict:
        """A terminal failure is irreversible; a retry receives a new Worker ID.

        The caller may mark interrupted/cancelled only after reaping/confirming
        process termination. Uncertain termination must use mark_stopping().
        """
        if status not in _TERMINAL - {"received"} or not isinstance(code, str) or not code.replace("_", "").isalnum():
            raise SkillError("review_failure_invalid", "Review failure requires a bounded internal diagnostic.")
        if len(code) > 128:
            raise SkillError("review_failure_invalid", "Review failure diagnostic is too large.")
        operator, snapshot, ref, expected = self._registered(verify_files=False)
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The failure has no original review attempt.")
            # Failure does not read revoked Skill contents or authorize execution.
            if record.get("snapshot_ref") != ref or record.get("task_skill_context") != expected["task_skill_context"]:
                raise SkillError("review_result_identity", "The failure belongs to another registered attempt.")
            self._check_state(state, record)
            previous = _read_optional(fd, "outcome.json")
            if previous is not None:
                if previous.get("status") != status or previous.get("error_code") != code:
                    raise SkillConflict("review_result_conflict", "This attempt already has a different immutable outcome.")
                self._outcome(fd, record, previous)
            else:
                outcome = {"schema_version": RESULT_SCHEMA, "binding_digest": content_digest(_bytes(record)),
                           "status": status, "error_code": code, "updated_at": _now()}
                self._outcome(fd, record, outcome)
        return {"status": status, "error_code": code}

    def mark_stopping(self) -> None:
        operator, _snapshot, _ref, _expected = self._registered(verify_files=False)
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The review attempt is unavailable.")
            self._check_state(state, record)
            if state["status"] not in {"running", "stopping"}:
                raise SkillConflict("review_result_conflict", "A terminal Reviewer cannot be changed to stopping.")
            _replace(fd, "state.json", {**state, "status": "stopping", "updated_at": _now()})

    def assert_retryable(self) -> bool:
        """A failed status projection alone is never proof of process termination.

        False means there is no result directory at all: the caller must then
        prove an original blocked-before-invocation preflight. Partial storage,
        running/stopping, unreconciled and successful outcomes cannot be retried.
        """
        operator, snapshot, ref, expected = self._registered()
        try:
            with self._store(operator) as fd:
                record, state, outcome = (_read_optional(fd, name) for name in ("attempt.json", "state.json", "outcome.json"))
                if record is None:
                    raise SkillError("review_retry_unconfirmed", "Partial review preparation must be recovered before retry.")
                self._check_record(record, snapshot, ref, expected)
                self._check_state(state, record)
                if (outcome is None or state["status"] not in {"failed", "invalid", "cancelled", "interrupted"}
                        or outcome.get("status") != state["status"]
                        or state.get("outcome_digest") != content_digest(_bytes(outcome))):
                    raise SkillError("review_retry_unconfirmed", "Only a confirmed terminal failure can create a new Reviewer attempt.")
                self._validate_outcome(record, outcome)
        except SkillError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                # Only a wholly absent store means execution has not begun.
                path = operator / "skills" / "review-results" / self.job_id
                if not path.exists() and not path.is_symlink():
                    return False
            raise
        return True

    def recover(self) -> dict:
        """Republish only a validated durable outcome; never re-run a model."""
        operator, snapshot, ref, expected = self._registered()
        with self._store(operator) as fd:
            record, state, outcome = (_read_optional(fd, name) for name in ("attempt.json", "state.json", "outcome.json"))
            if record is None:
                raise SkillError("review_attempt_missing", "The review attempt is unavailable.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(state, record)
            if outcome is None:
                raise SkillError("review_recovery_pending", "There is no durable terminal result. Confirm process termination before retrying; no model was invoked.")
            if state["status"] in _TERMINAL and state["status"] != outcome.get("status"):
                raise SkillError("review_result_corrupt", "The terminal result conflicts with its status projection.")
            self._target(snapshot)
            self._outcome(fd, record, outcome)
        return self.detail()

    def detail(self, *, expected_task_context=None, include_report=False) -> dict:
        """Read authoritative data, without private prompts, paths or Skill bodies."""
        operator, snapshot, ref, expected = self._registered()
        if expected_task_context is not None and expected_task_context != expected["task_skill_context"]:
            raise SkillError("snapshot_scope_mismatch", "The requested review belongs to another task.")
        with self._store(operator) as fd:
            record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
            if record is None:
                raise SkillError("review_attempt_missing", "The review attempt is unavailable.")
            self._check_record(record, snapshot, ref, expected)
            self._check_state(state, record)
            target, _text = self._target(snapshot)
            if target != record["target"]:
                raise SkillError("review_target_changed", "The selected review target changed.")
            result = {"schema_version": DETAIL_SCHEMA, "job_id": self.job_id,
                      "task_skill_context": record["task_skill_context"], "snapshot_ref": ref,
                      "context_ref": record["context_ref"], "target": target,
                      "methods": record["methods"], "required_skill_ids": record["required_skill_ids"],
                      "runtime_verification": record["runtime_session"]["verification_kind"],
                      "runtime": {key: record["runtime_session"][key] for key in
                                  ("adapter", "adapter_version", "binary_version", "binary_digest", "model", "config_digest", "session_id")},
                      "stages": {key: "unobserved" for key in OBSERVED_STAGES},
                      "status": state["status"], "updated_at": state["updated_at"], "retry_available": False,
                      "advisory": True, "judgment_verification": "not_independently_verified", "report_available": False}
            for stage in ("materialized", "catalog_presented", "content_delivered"):
                observed = _read_optional(fd, "observed-" + stage + ".json")
                if observed is not None:
                    if (set(observed) != {"schema_version", "binding_digest", "stage", "evidence_digest", "origin"}
                            or observed["schema_version"] != RESULT_SCHEMA or observed["origin"] != "parent"
                            or observed["binding_digest"] != state["binding_digest"] or observed["stage"] != stage):
                        raise SkillError("review_result_corrupt", "The delivery observation belongs to another attempt.")
                    identifier(observed["evidence_digest"], "sha256:")
                    result["stages"][stage] = "observed"
            outcome = _read_optional(fd, "outcome.json")
            if outcome is not None:
                self._validate_outcome(record, outcome)
            if state["status"] in _TERMINAL:
                if (outcome is None or state.get("outcome_digest") != content_digest(_bytes(outcome))
                        or outcome.get("status") != state["status"]
                        or outcome.get("binding_digest") != state["binding_digest"]):
                    raise SkillError("review_result_corrupt", "The terminal review state has no matching durable outcome.")
                if outcome["status"] == "received":
                    # Revalidate source references on every report read, not just
                    # on the initial write. No mutable result.md is consulted.
                    raw = outcome.get("raw_report")
                    if not isinstance(raw, str):
                        raise SkillError("review_result_corrupt", "The saved report payload is unavailable.")
                    report = validate_report(raw.encode("utf-8"), sources={key: text.encode("utf-8") for key, text in record["sources"].items()})
                    if report != outcome.get("report"):
                        raise SkillError("review_result_corrupt", "The report no longer matches its captured source evidence.")
                    result.update(report_available=True, report_digest=content_digest(_bytes(report)),
                                  summary=report["summary"], finding_count=len(report["findings"]),
                                  unfinished_check_count=sum(item["status"] != "checked" for item in report["checks"]))
                    if include_report:
                        result["report"] = report
                else:
                    result["error_code"] = outcome.get("error_code", "review_failed")
                    result["retry_available"] = state["status"] in {"failed", "invalid", "cancelled", "interrupted"}
            elif outcome is not None:
                # A crash after immutable outcome publication but before status
                # publication is recoverable, not a successful report in the UI.
                result.update(status="recovery_required", error_code="review_result_projection_pending")
        return result
