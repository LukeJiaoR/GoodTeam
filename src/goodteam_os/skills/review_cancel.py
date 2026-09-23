"""Exact-attempt operator stop requests, independent of model/runtime authority.

A receipt requests cancellation only. Only the existing execution owner can
publish a terminal outcome after its process and outer envelope are stopped.
No PID, command, new input, provider, or permission is accepted from the client.
"""
from __future__ import annotations

from .content import _bytes, _publish_immutable
from .models import SkillConflict, SkillError, content_digest
from .review_results import _now, _read_optional, _replace

CANCEL_SCHEMA = "goodteam.review-cancellation.v1"
ACTIVE = frozenset({"prepared", "running", "stopping"})


def read_cancellation(fd, record):
    """Read under the result lock; the immutable attempt selects all identity."""
    receipt = _read_optional(fd, "cancel-request.json")
    if receipt is None:
        return None
    expected = {"schema_version": CANCEL_SCHEMA,
                "binding_digest": content_digest(_bytes(record)), "job_id": record["job_id"],
                "snapshot_digest": record["snapshot_ref"]["digest"],
                "context_digest": record["context_ref"]["digest"]}
    if (set(receipt) != set(expected) | {"requested_at"}
            or any(receipt.get(key) != value for key, value in expected.items())
            or not isinstance(receipt["requested_at"], str) or not 1 <= len(receipt["requested_at"]) <= 64):
        raise SkillError("review_cancel_corrupt", "The stop request does not match the protected review attempt.")
    return receipt


def _record(results, fd, ref, expected):
    # Stopping must remain possible after revocation or loss of Worker input
    # files. Do not reopen private method/source bodies or authorize a Runtime.
    record, state = _read_optional(fd, "attempt.json"), _read_optional(fd, "state.json")
    if (record is None or record.get("job_id") != results.job_id or record.get("snapshot_ref") != ref
            or record.get("task_skill_context") != expected["task_skill_context"]
            or record.get("context_ref", {}).get("job_id") != results.job_id
            or record.get("context_ref", {}).get("snapshot_ref") != ref):
        raise SkillError("review_cancel_identity", "The selected review has no matching protected execution attempt.")
    results._check_state(state, record)
    return record, state


def control_detail(results):
    """Narrow cancellation metadata; readable even when content was revoked."""
    operator, _snapshot, ref, expected = results._registered(verify_files=False)
    with results._store(operator) as fd:
        record, state = _record(results, fd, ref, expected)
        receipt, outcome = read_cancellation(fd, record), _read_optional(fd, "outcome.json")
        return {"context_ref": record["context_ref"], "cancel_requested": receipt is not None,
                "cancel_available": state["status"] in ACTIVE and outcome is None and receipt is None,
                "control_status": "stopping" if receipt is not None and outcome is None else state["status"]}


def check_cancellation(results):
    operator, _snapshot, ref, expected = results._registered(verify_files=False)
    with results._store(operator) as fd:
        record, _state = _record(results, fd, ref, expected)
        if read_cancellation(fd, record) is not None:
            raise SkillError("review_cancel_requested", "The operator requested cancellation of this exact review attempt.")


def request_cancellation(results, request, *, check_task):
    """Persist before acknowledging; same-attempt replay never sends signals.

    This does not acquire the long-running workflow execution guard: a stop
    request must be receivable while that execution owns the guard. The caller
    rechecks the current task at admission and again under this result lock.
    Task/Worker/context binding prevents a task switch from cancelling new work.
    """
    if not isinstance(request, dict) or set(request) != {"snapshot_digest", "context_digest"}:
        raise SkillError("review_cancel_request", "Cancellation requires only the displayed snapshot and context digests.")
    operator, _snapshot, ref, expected = results._registered(verify_files=False)
    with results._store(operator) as fd:
        record, state = _record(results, fd, ref, expected)
        if (request["snapshot_digest"] != ref["digest"]
                or request["context_digest"] != record["context_ref"]["digest"]):
            raise SkillConflict("review_cancel_identity", "Reload the exact attempt before cancelling it.")
        check_task()  # No callbacks after this check and before the stop receipt.
        receipt, outcome = read_cancellation(fd, record), _read_optional(fd, "outcome.json")
        if outcome is not None:
            results._validate_outcome(record, outcome)
            if receipt is None or outcome["status"] == "received":
                raise SkillConflict("review_cancel_finished", "The attempt already has a durable outcome; cancellation cannot overwrite it.")
            # A lost response may be replayed after the execution owner stopped.
            confirmed = (state["status"] == outcome["status"]
                         and state.get("outcome_digest") == content_digest(_bytes(outcome)))
            return _response(record, status=outcome["status"] if confirmed else "stopping", confirmed=confirmed)
        if state["status"] not in ACTIVE:
            raise SkillConflict("review_cancel_finished", "The selected attempt is not a cancellable active execution.")
        if receipt is None:
            receipt = {"schema_version": CANCEL_SCHEMA, "binding_digest": state["binding_digest"],
                       "job_id": results.job_id, "snapshot_digest": ref["digest"],
                       "context_digest": record["context_ref"]["digest"], "requested_at": _now()}
            _publish_immutable(fd, "cancel-request.json", _bytes(receipt), code="review_cancel_conflict")
        # An interrupted state projection cannot erase the durable stop request.
        if state["status"] != "stopping":
            _replace(fd, "state.json", {**state, "status": "stopping", "updated_at": receipt["requested_at"]})
        return _response(record, status="stopping", confirmed=False)


def _response(record, *, status, confirmed):
    return {"job_id": record["job_id"], "snapshot_digest": record["snapshot_ref"]["digest"],
            "context_digest": record["context_ref"]["digest"], "cancel_requested": True,
            "status": status, "termination_confirmed": confirmed,
            "advisory": True, "approval_changed": False}
