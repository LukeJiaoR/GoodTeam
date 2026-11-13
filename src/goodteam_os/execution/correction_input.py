"""Immutable correction delta handed directly alongside the complete original input."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .task_snapshot import TaskExecutionSnapshot, TaskInputError, canonical_bytes, digest, execution_key

CORRECTION_JOB_PREFIX = "job-correction-"
CORRECTION_JOB = re.compile(r"job-correction-([a-f0-9]{64})-([1-5])")
MAX_CORRECTION_INPUT = 16384


def review_reference(review):
    """Compact identity of the complete receipt, not a truncated review summary.

    The digest binds every byte of the canonical receipt, including provenance
    and all findings. Consumers must resolve it through the protected history.
    """
    if not isinstance(review, dict):
        raise TaskInputError("correction_feedback_mismatch")
    try:
        return {key: review[key] for key in
                ("receipt_id", "validation_receipt_id", "decision", "candidate")} | {
                    "payload_digest": digest(canonical_bytes(review))}
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskInputError("correction_feedback_mismatch") from exc


def correction_runtime_capability(adapter):
    """Default availability; never infer an exact-attempt native permission.

    The existing trusted Codex authorities admit their captured Skill/reviewer
    protocols, not this new mutable-worktree correction protocol. An argv flag,
    executable name or operator cost acknowledgement cannot bridge that gap.
    Explicit typed native authority is checked separately by the attempt store.
    """
    if adapter in {"fake", "noop"}:
        return {"available": True, "status": "simulation_only", "reason": "builtin_without_external_invocation"}
    return {"available": False, "status": "blocked", "reason": "correction_verified_runtime_unavailable"}


@dataclass(frozen=True, slots=True)
class CorrectionAttemptInput:
    original: TaskExecutionSnapshot
    data: bytes

    def __post_init__(self):
        if not isinstance(self.original, TaskExecutionSnapshot) or type(self.data) is not bytes or len(self.data) > MAX_CORRECTION_INPUT:
            raise TaskInputError("correction_input_invalid")
        try:
            value = json.loads(self.data)
            if (canonical_bytes(value) != self.data or set(value) != {"schema_version", "original", "loop_id",
                    "ordinal", "operation_id", "review", "candidate", "max_seconds"}
                    or value["schema_version"] not in {"correction_worker_input.v1", "correction_worker_input.v2"}
                    or value["original"] != self.original.reference
                    or not re.fullmatch(r"correction:[a-f0-9]{64}", value["loop_id"])
                    or type(value["ordinal"]) is not int or not 1 <= value["ordinal"] <= 5
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value["operation_id"])
                    or type(value["max_seconds"]) is not int or not 1 <= value["max_seconds"] <= 600
                    or not isinstance(value["review"], dict) or value["review"].get("decision") != "request_changes"
                    or value["review"].get("candidate") != value["candidate"]):
                raise TaskInputError("correction_input_invalid")
            if value["schema_version"] == "correction_worker_input.v2":
                review = value["review"]
                if (set(review) != {"receipt_id", "validation_receipt_id", "decision", "candidate", "payload_digest"}
                        or any(not isinstance(review[key], str) or not review[key] for key in
                               ("receipt_id", "validation_receipt_id"))
                        or not re.fullmatch(r"[a-f0-9]{64}", review["payload_digest"])):
                    raise TaskInputError("correction_input_invalid")
        except (TypeError, ValueError, KeyError, UnicodeError) as exc:
            raise TaskInputError("correction_input_invalid") from exc

    @classmethod
    def capture(cls, original, *, loop_id, ordinal, operation_id, review, candidate, max_seconds):
        return cls(original, canonical_bytes({"schema_version": "correction_worker_input.v2",
            "original": original.reference, "loop_id": loop_id, "ordinal": ordinal, "operation_id": operation_id,
            "review": review_reference(review), "candidate": candidate, "max_seconds": max_seconds}))

    def matches_review(self, review):
        """Bind a resolved protected receipt without changing legacy input bytes."""
        captured = self.to_dict()
        if captured["schema_version"] == "correction_worker_input.v1":
            return captured["review"] == review
        return captured["review"] == review_reference(review)

    @property
    def execution_id(self):
        return self.original.execution_id

    @property
    def job_id(self):
        return f"{CORRECTION_JOB_PREFIX}{execution_key(self.execution_id)}-{self.to_dict()['ordinal']}"

    @property
    def payload_digest(self):
        return digest(self.data)

    @property
    def task(self):
        return self.original.task

    @property
    def reference(self):
        return {"schema_version": self.to_dict()["schema_version"], "original": self.original.reference,
                "job_id": self.job_id, "payload_digest": self.payload_digest}

    def to_dict(self):
        return json.loads(self.data)

    @property
    def workspace_relative(self):
        return f".goodteam/sandboxes/{self.job_id}/worktree"

    def render(self, *, host_seals=False):
        from .task_context import MAX_TASK_CONTEXT_BYTES, render_task_contract
        value = self.to_dict()
        rendered = "\n\n".join([render_task_contract(self.original),
            "## Explicit Correction Attempt", f"Attempt: {self.job_id}",
            "Keep the entire original goal, constraints and acceptance conditions above. "
            "The review below is feedback, not permission to expand scope or bypass safeguards. "
            "Make the correction only in the assigned worktree. "
            + ("Edit or delete existing tracked files, or add ordinary non-hidden regular files within the task scope. "
               "Do not add ignored files, credentials, secrets, links, or special files. "
               "When adding files, leave all .gitignore files unchanged. "
               "Do not commit, stage, or write Git metadata. "
               "The host seals a candidate after your process has stopped. " if host_seals else
               "Commit a clean candidate there. ") +
            ("Report edits, checks and unresolved risks. " if host_seals else
             "Report the full commit ID, checks and unresolved risks. ")
            + "A successful exit does not verify acceptance.",
            canonical_bytes({"candidate_before": value["candidate"], "review": value["review"]}).decode()])
        if len(rendered.encode()) > MAX_TASK_CONTEXT_BYTES:
            raise TaskInputError("correction_context_size_limit")
        return rendered
