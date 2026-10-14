"""Trusted startup wiring for the original Operator Builder candidate.

No command, base, model or permission is derived from a task's external text.
Preparing a review freezes selection; existing runtime admission still governs
the separate read-only Reviewer invocation.
"""
from dataclasses import asdict, dataclass
import json
import re

from goodteam_os.execution.candidate_builder import builder_source
from goodteam_os.execution.candidate_validation import _read, _write
from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes, digest
from goodteam_os.memory_storage import role_lock
from goodteam_os.skills.review_sources import _Git, source_request


@dataclass(frozen=True)
class OperatorCandidateReviewConfig:
    contract_digest: str
    base_sha: str
    argv: tuple[str, ...]
    criteria: tuple[int, ...]
    reviewer_model: str
    reviewer_adapter: str = "codex"
    seconds: int = 30

    def __post_init__(self):
        if (not isinstance(self.contract_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", self.contract_digest)
                or type(self.argv) is not tuple or not 1 <= len(self.argv) <= 128
                or any(not isinstance(a, str) or not a or "\0" in a for a in self.argv)
                or len(canonical_bytes(self.argv)) > 16384
                or type(self.criteria) is not tuple or not self.criteria
                or any(type(i) is not int or i < 0 for i in self.criteria)
                or len(set(self.criteria)) != len(self.criteria)
                or not isinstance(self.reviewer_model, str) or not self.reviewer_model
                or not isinstance(self.reviewer_adapter, str) or not self.reviewer_adapter
                or type(self.seconds) is not int or not 1 <= self.seconds <= 120):
            raise TaskInputError("operator_candidate_configuration_invalid")
        source_request({"kind": "git_change", "base_sha": self.base_sha, "head_sha": self.base_sha})


def configuration_for(configurations, item):
    config = configurations.get(str(item.get("id", "")))
    if not isinstance(config, OperatorCandidateReviewConfig):
        raise TaskInputError("operator_candidate_configuration_required")
    contract = dict(item.get("metadata", {})).get("task_contract")
    if not isinstance(contract, dict) or digest(canonical_bytes(contract)) != config.contract_digest:
        raise TaskInputError("operator_candidate_contract_mismatch")
    if any(i >= len(contract.get("acceptance_criteria", [])) for i in config.criteria):
        raise TaskInputError("validation_criteria_invalid")
    return config


def check_admission_identity(admission, snapshot, item, project_root, config):
    """Reject a known local/trusted policy mismatch before spending Builder work.

    This is only a precheck. The existing authority still checks admission,
    revocation and budget when the independent Reviewer actually runs.
    """
    from goodteam_os.skills.codex_local import LocalCodexReviewAdmission
    from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission
    if isinstance(admission, (LocalCodexReviewAdmission, TrustedCodexReviewAdmission)):
        task = {"kind": "direct_task", "project_key": str(item["project_key"]),
                "logical_id": "candidate-review:" + snapshot.payload_digest}
        if (admission.root.resolve() != project_root.resolve() or admission.policy["task"] != task
                or admission.policy["model"] != config.reviewer_model or config.reviewer_adapter != "codex"):
            raise TaskInputError("operator_reviewer_task_mismatch")


def freeze_configuration(service, snapshot, item, config, *, create):
    from goodteam_os.execution.approved_plan import binding_for

    binding_for(service.project_root, snapshot, required=True)
    if _Git(service.project_root).read("cat-file", "-t", config.base_sha, maximum=64) != b"commit\n":
        raise TaskInputError("automatic_review_base_invalid")
    root = service.validation._root(snapshot.execution_id)
    value = {"schema_version": "operator_candidate_configuration.v1", "snapshot": snapshot.reference,
             "operator_item": str(item["id"]), "configuration": json.loads(canonical_bytes(asdict(config)))}
    with role_lock(root, create=False) as present:
        if not present or service.validation._snapshot(snapshot.execution_id).data != snapshot.data:
            raise TaskInputError("validation_input_missing")
        path = root / "operator-candidate-configuration.json"
        saved = _read(path)
        if saved is not None and saved != value:
            raise TaskInputError("operator_candidate_configuration_changed")
        if saved is None:
            if not create:
                raise TaskInputError("operator_candidate_configuration_missing")
            _write(path, value)


def prepare_candidate(service, snapshot, item, config):
    freeze_configuration(service, snapshot, item, config, create=False)
    root, _binding = builder_source(service.project_root, snapshot, snapshot.job_id)
    head = _Git(root).read("rev-parse", "HEAD", maximum=128).decode().strip()
    # One initial operation, not one fresh invocation per changed head. Any
    # changed request conflicts with the existing plan instead of rerunning it.
    operation = "operator-initial-check-" + digest(canonical_bytes([item["id"], snapshot.execution_id]))
    result = service.validation.validate(snapshot.execution_id, operation_id=operation,
        head_sha=head, argv=list(config.argv), criteria=list(config.criteria), seconds=config.seconds,
        builder_worker=snapshot.job_id)
    return {"head_sha": head, "candidate_check_receipt": result["receipt_id"],
            "candidate_check_status": result["observation"]["status"], "candidate_review_prepared": True}


def start_candidate_review(service, snapshot, item, config):
    """Start the existing budget only when CI has reached the review phase.

    Repeating a started or partially started review reuses its initial identity
    and clock; a later correction candidate cannot reset either.
    """
    freeze_configuration(service, snapshot, item, config, create=False)
    saved = service.configuration(snapshot.execution_id)
    if saved is None:
        current = service.validation._current(snapshot)
        if current is None:
            raise TaskInputError("operator_candidate_check_required")
        plan, result, _review = current
        operation = "operator-initial-check-" + digest(canonical_bytes([item["id"], snapshot.execution_id]))
        if (plan["request"].get("operation_id") != operation
                or plan["request"].get("builder_worker") != snapshot.job_id
                or result["candidate"]["head_sha"] != item["metadata"].get("head_sha")):
            raise TaskInputError("operator_candidate_check_required")
        receipt, head = result["receipt_id"], result["candidate"]["head_sha"]
    else:
        receipt, head = saved["initial_receipt"], saved["initial_head"]
    service.start(snapshot.execution_id, base_sha=config.base_sha, receipt_id=receipt, head_sha=head,
                  reviewer_adapter=config.reviewer_adapter, reviewer_model=config.reviewer_model)
