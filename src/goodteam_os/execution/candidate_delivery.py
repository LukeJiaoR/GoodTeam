"""Local delivery handoff from a completed Builder to checks and independent review.

Only CLI or startup composition selects the command. The frozen plan survives
restart; an HTTP run request cannot select or resume this command executor.
"""
from pathlib import Path

from goodteam_os.memory_storage import MemoryStorageError, role_lock
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_sources import _Git, source_request
from goodteam_os.skills.task_start import registered_project_scope

from .automatic_review import AutomaticCandidateReviewService
from .candidate_validation import CandidateValidationService, _bound, _read, _write, validation_contract
from .ledger import ExecutionLedger
from .models import ExecutionRunStatus
from .task_snapshot import TaskInputError, digest

SCHEMA = "execution_candidate_delivery.v1"


class CandidateDeliveryService:
    def __init__(self, project_root, **review_runtime_kwargs):
        self.project_root = Path(project_root).resolve()
        self.runtime = review_runtime_kwargs
        self.validation = CandidateValidationService(self.project_root)
        self.automatic = AutomaticCandidateReviewService(self.project_root, **review_runtime_kwargs)

    @staticmethod
    def _require_same_plan(saved, automatic):
        if saved is not None and automatic is not None and any(automatic.get(key) != saved[key] for key in
                ("base_sha", "validation_contract", "reviewer_adapter", "reviewer_model")):
            raise TaskInputError("delivery_plan_changed")

    def configuration(self, execution_id):
        snapshot = self.validation._snapshot(execution_id)
        with role_lock(self.validation._root(execution_id), create=False):
            value = _read(self.validation._root(execution_id) / "delivery-plan.json")
            if value is None:
                return None
            if (value != _bound(value) or value.get("schema_version") != SCHEMA
                    or value.get("snapshot") != snapshot.reference
                    or value.get("reviewer_adapter") != "codex"
                    or not isinstance(value.get("reviewer_model"), str) or not value["reviewer_model"]
                    or not isinstance(value.get("validation_contract"), dict)
                    or set(value["validation_contract"]) != {"argv", "criteria", "seconds"}
                    or set(value) != {"schema_version", "snapshot", "base_sha", "validation_contract",
                                      "reviewer_adapter", "reviewer_model", "receipt_id"}):
                raise TaskInputError("delivery_plan_invalid")
            source_request({"kind": "git_change", "base_sha": value["base_sha"], "head_sha": value["base_sha"]})
            if validation_contract(snapshot, **value["validation_contract"]) != value["validation_contract"]:
                raise TaskInputError("delivery_plan_invalid")
            return value

    def configure(self, execution_id, *, base_sha, argv, criteria, seconds=30):
        """Freeze caller choices before dispatching a Builder, never issue/config text."""
        from goodteam_os.skills.codex_local import LocalCodexReviewAdmission
        from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission

        snapshot = self.validation._snapshot(execution_id)
        contract = validation_contract(snapshot, argv=argv, criteria=criteria, seconds=seconds)
        source_request({"kind": "git_change", "base_sha": base_sha, "head_sha": base_sha})
        if _Git(self.project_root).read("cat-file", "-t", base_sha, maximum=64) != b"commit\n":
            raise TaskInputError("automatic_review_base_invalid")
        saved = self.configuration(execution_id)
        admission = self.runtime.get("codex_review_admission")
        if isinstance(admission, (LocalCodexReviewAdmission, TrustedCodexReviewAdmission)):
            model = admission.policy["model"]
            scope = registered_project_scope(self.project_root)
            if scope is None:
                raise TaskInputError("automatic_review_project_unregistered")
            task = {"kind": "direct_task", "project_key": scope[1],
                    "logical_id": "candidate-review:" + snapshot.payload_digest}
            if admission.root.resolve() != self.project_root or admission.policy["task"] != task:
                raise TaskInputError("delivery_reviewer_task_mismatch")
        elif saved is not None:
            model = saved["reviewer_model"]
        else:
            raise TaskInputError("delivery_reviewer_admission_missing")
        proposed = _bound({"schema_version": SCHEMA, "snapshot": snapshot.reference, "base_sha": base_sha,
                          "validation_contract": contract, "reviewer_adapter": "codex", "reviewer_model": model})
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            saved = self.configuration(execution_id)
            if saved is not None and saved != proposed:
                raise TaskInputError("delivery_plan_changed")
            automatic = self.automatic.configuration(execution_id)
            self._require_same_plan(proposed, automatic)
            if saved is None:
                _write(root / "delivery-plan.json", proposed)
        return proposed

    def require_plan(self, execution_id):
        """Validate retained local choices before any Builder dispatch; never execute."""
        saved = self.configuration(execution_id)
        automatic = self.automatic.configuration(execution_id)
        self._require_same_plan(saved, automatic)
        if saved is None and automatic is None:
            raise TaskInputError("delivery_check_plan_missing")
        from .approved_plan import binding_for
        binding_for(self.project_root, self.validation._snapshot(execution_id), required=True)
        return saved, automatic

    def advance(self, execution_id, *, acknowledge_external_cost=False):
        """Resume retained evidence; never repeat an indeterminate process or grant admission."""
        try:
            saved, automatic = self.require_plan(execution_id)
            run = ExecutionLedger(self.project_root).read_run(execution_id)
            if run is None or run.status is not ExecutionRunStatus.SUCCEEDED:
                raise TaskInputError("delivery_builder_not_succeeded")
            if automatic is None:
                snapshot = self.validation._snapshot(execution_id)
                from .candidate_builder import builder_source
                root, _binding = builder_source(self.project_root, snapshot, snapshot.job_id)
                request = {"builder_worker": snapshot.job_id}
                head = _Git(root).read("rev-parse", "HEAD", maximum=128).decode().strip()
                checked = self.validation.validate(execution_id,
                    operation_id="delivery-check-" + digest(saved["receipt_id"].encode()),
                    head_sha=head, **saved["validation_contract"], **request)
                self.automatic.start(execution_id, base_sha=saved["base_sha"], receipt_id=checked["receipt_id"],
                    head_sha=checked["candidate"]["head_sha"], reviewer_adapter=saved["reviewer_adapter"],
                    reviewer_model=saved["reviewer_model"])
            else:
                # Recover a start interrupted before its protected task binding.
                self.automatic.start(execution_id, **{key: automatic[key] for key in
                    ("base_sha", "reviewer_adapter", "reviewer_model", "max_rounds", "wall_seconds", "fix_seconds")},
                    receipt_id=automatic["initial_receipt"], head_sha=automatic["initial_head"])
            outcome = self.automatic.advance(execution_id, acknowledge_external_cost=acknowledge_external_cost)
            return {**outcome, "candidate_validation": self.validation.evidence(execution_id)}
        except (TaskInputError, SkillError, OSError, MemoryStorageError) as exc:
            return {"status": "blocked", "reason": getattr(exc, "code", "delivery_store_unavailable"),
                    "acceptance_status": "unknown"}

    def review_for_head(self, execution_id, head_sha):
        """Read-only Operator gate: re-read protected current receipts, never dispatch."""
        evidence = self.validation.evidence(execution_id)
        try:
            self._require_same_plan(self.configuration(execution_id), self.automatic.configuration(execution_id))
        except (TaskInputError, SkillError, OSError, MemoryStorageError) as exc:
            return {"head_sha": head_sha, "recommendation": "escalate", "deterministic": False,
                    "reason": getattr(exc, "code", "delivery_store_unavailable"),
                    "blockers": ["delivery_plan_unavailable"], "candidate_validation": evidence}
        review = evidence.get("review") or {}
        decision = review.get("decision", "inconclusive")
        current = (evidence.get("candidate", {}).get("head_sha") == head_sha
                   and evidence.get("check_status") in {"passed", "failed"}
                   and evidence.get("review_status") == "machine_recorded"
                   and review.get("producer") == "automatic_candidate_review_controller")
        accepted = current and evidence.get("acceptance_status") == "accepted_by_review_policy"
        return {"head_sha": head_sha, "recommendation": "approve" if accepted else
                "request_changes" if current and decision == "request_changes" else "escalate",
                "reason": "candidate_review_policy_satisfied" if accepted else
                review.get("reason", evidence["reason"]) if current else "delivery_review_missing_or_stale",
                "blockers": [] if accepted else [review.get("reason", evidence["reason"]) if current
                                                else "delivery_review_missing_or_stale"],
                "deterministic": current and decision == "request_changes",
                "reviewer_identity": review.get("provenance", {}).get("job_id", ""),
                "validation_receipt_id": evidence.get("receipt", {}).get("receipt_id", ""),
                "review_receipt_id": review.get("receipt_id", ""), "candidate_validation": evidence}
