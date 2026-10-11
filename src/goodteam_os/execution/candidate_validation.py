"""Explicit Operator CLI checks, durable host receipts and conservative outcome.

The command writer is invoked only by explicit CLI/trusted composition. Exact
Workbench forms may record human attestations, never execute commands. Labels
are not authenticated independent identities or semantic acceptance proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil

from goodteam_os.memory_storage import atomic_write, read_bytes, role_lock

from .candidate_probe import probe_candidate
from .candidate_process import MAX_SECONDS, now, run_explicit_command
from .task_snapshot import TaskInputError, canonical_bytes, digest, execution_key
from .task_snapshot_store import store_for_project

MAX_RECORD = 65536


def _read(path):
    raw = read_bytes(path, maximum=MAX_RECORD)
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise TaskInputError("validation_record_invalid")
    return value


def _write(path, value):
    atomic_write(path, canonical_bytes(value), maximum=MAX_RECORD)


def _bound(value):
    body = {key: item for key, item in value.items() if key != "receipt_id"}
    return {**body, "receipt_id": "validation:" + digest(canonical_bytes(body))}


def _operation(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value):
        raise TaskInputError("validation_operation_invalid")
    return digest(value.encode())


class CandidateValidationService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.store = store_for_project(self.project_root, existing_only=True)

    def _snapshot(self, execution_id):
        snapshot = self.store.load(execution_id) if self.store else None
        if snapshot is None:
            raise TaskInputError("validation_input_missing")
        # Keep the original input/history boundary; validation cannot wash away
        # a missing handoff journal or establish that an executor really ran.
        self.store.receipts(snapshot)
        return snapshot

    def _root(self, execution_id):
        return self.store.root / execution_key(execution_id)

    def source_root(self, snapshot, request):
        if not request.get("correction_worker"):
            return self.project_root
        from .correction_store import CorrectionAttemptStore
        authority = CorrectionAttemptStore(self.project_root, request["correction_worker"])
        value = authority.load()
        if value.original.data != snapshot.data:
            raise TaskInputError("correction_identity_mismatch")
        return authority.workspace(value)

    def validate(self, execution_id, *, operation_id, head_sha, argv, criteria, seconds=30, correction_worker=""):
        """Called only by an explicit local CLI action or trusted composition.

        Commands and criterion mappings are operator input, never extracted from
        source intent, Worker output or mutable project configuration.
        """
        key = _operation(operation_id)
        if (not isinstance(argv, list) or not argv or len(argv) > 128
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
                or len(canonical_bytes(argv)) > 16384 or type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS):
            raise TaskInputError("validation_command_invalid")
        executable = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0], path=os.defpath)
        if not executable:
            raise TaskInputError("validation_command_unavailable")
        command = [executable, *argv[1:]]
        snapshot = self._snapshot(execution_id)
        count = len(snapshot.task.task_contract.acceptance_criteria)
        if (not isinstance(criteria, list) or not criteria or any(type(i) is not int or not 0 <= i < count for i in criteria)
                or len(set(criteria)) != len(criteria)):
            raise TaskInputError("validation_criteria_invalid")
        request = {"schema_version": "candidate_validation_plan.v1", "snapshot": snapshot.reference,
                   "operation_id": operation_id, "head_sha": head_sha, "argv": command, "cwd": ".",
                   "criteria": sorted(criteria), "seconds": seconds, "producer": "explicit_operator_cli"}
        if correction_worker:
            request["correction_worker"] = correction_worker
        root = self._root(execution_id)
        with role_lock(root, create=False) as present:
            if not present or self._snapshot(execution_id).data != snapshot.data:
                raise TaskInputError("validation_input_missing")
            source_root = self.source_root(snapshot, request)
            plan_path = root / ("validation-plan-" + key + ".json")
            plan = _read(plan_path)
            if plan is not None and plan.get("request") != request:
                raise TaskInputError("validation_operation_conflict")
            if plan is None:
                if (_read(root / ("validation-started-" + key + ".json")) is not None
                        or _read(root / ("validation-result-" + key + ".json")) is not None):
                    raise TaskInputError("validation_plan_missing")
                candidate = probe_candidate(source_root, head_sha)
                plan = {"request": request, "candidate": candidate}
                _write(plan_path, plan)
            expected = {"operation_key": key, "plan_digest": digest(canonical_bytes(plan))}
            started_path = root / ("validation-started-" + key + ".json")
            result_path = root / ("validation-result-" + key + ".json")
            started, result = _read(started_path), _read(result_path)
            if result is not None:
                probe_candidate(source_root, head_sha)
                result = self._verify_result(plan, expected, started, result)
                from .correction_loop import CorrectionLoopService
                # Historical reads cannot change the active correction round.
                if CorrectionLoopService(self.project_root)._load(snapshot) is None:
                    _write(root / "validation-current.json", expected)
                return result
            if started is not None:
                raise TaskInputError("validation_attempt_indeterminate")
            # A resumed plan is checked again before entering the call boundary.
            probe_candidate(source_root, head_sha)
            from .correction_loop import CorrectionLoopService
            CorrectionLoopService(self.project_root).admit_validation(snapshot, plan, expected)
            _write(root / "validation-current.json", expected)
            _write(started_path, expected)
            observed = run_explicit_command(command, source_root, seconds=seconds)
            try:
                probe_candidate(source_root, head_sha)
                if self.source_root(snapshot, request) != source_root:
                    raise TaskInputError("correction_workspace_mismatch")
                if self._snapshot(execution_id).data != snapshot.data:
                    raise TaskInputError("validation_input_missing")
            except (OSError, ValueError) as exc:
                observed.update(status="unknown", reason=exc.code if isinstance(exc, TaskInputError)
                                else "validation_source_unavailable")
            result = _bound({"schema_version": "candidate_validation_receipt.v1", **expected,
                             "snapshot_digest": snapshot.payload_digest, "candidate": plan["candidate"],
                             "criteria": request["criteria"], "observation": observed})
            _write(result_path, result)
            return result

    def _verify_result(self, plan, expected, started, result):
        if (started != expected or result != _bound(result)
                or result.get("schema_version") != "candidate_validation_receipt.v1"
                or any(result.get(k) != v for k, v in expected.items())
                or result.get("snapshot_digest") != plan["request"]["snapshot"]["payload_digest"]
                or result.get("candidate") != plan["candidate"] or result.get("criteria") != plan["request"]["criteria"]
                or not isinstance(result.get("observation"), dict)
                or result["observation"].get("status") not in {"passed", "failed", "unknown"}):
            raise TaskInputError("validation_record_invalid")
        return result

    def _current(self, snapshot):
        root = self._root(snapshot.execution_id)
        selected = _read(root / "validation-current.json")
        if selected is None:
            return None
        key = selected.get("operation_key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise TaskInputError("validation_record_invalid")
        plan = _read(root / ("validation-plan-" + key + ".json"))
        if (plan is None or selected != {"operation_key": key, "plan_digest": digest(canonical_bytes(plan))}
                or plan.get("request", {}).get("snapshot") != snapshot.reference):
            raise TaskInputError("validation_record_invalid")
        result = _read(root / ("validation-result-" + key + ".json"))
        if result is None:
            raise TaskInputError("validation_attempt_indeterminate")
        started = _read(root / ("validation-started-" + key + ".json"))
        result = self._verify_result(plan, selected, started, result)
        review = self._review(snapshot, plan, result)
        return plan, result, review

    def _review(self, snapshot, plan, result):
        """Human and machine history remain distinct; machine authority is re-read."""
        root = self._root(snapshot.execution_id)
        key = result["operation_key"]
        human = _read(root / ("validation-review-" + key + ".json"))
        machine = _read(root / ("validation-machine-review-" + key + ".json"))
        if human is not None and (human != _bound(human)
                or human.get("schema_version") != "candidate_review_attestation.v1"
                or human.get("producer") not in {"explicit_operator_cli", "explicit_operator_workbench"}
                or human.get("decision") not in {"approve", "request_changes", "reject"}
                or human.get("validation_receipt_id") != result["receipt_id"]
                or human.get("candidate") != result["candidate"]):
            raise TaskInputError("validation_review_invalid")
        committed = _read(root / ("validation-machine-committed-" + key + ".json"))
        if (machine is not None or committed is not None) and (machine is None or committed != {
                "validation_receipt_id": result["receipt_id"], "review_receipt_id": machine["receipt_id"]}):
            raise TaskInputError("validation_review_invalid")
        if machine is not None:
            from .automatic_review import AutomaticCandidateReviewService
            AutomaticCandidateReviewService(self.project_root).verify_receipt(snapshot, plan, result, machine)
        return machine if machine is not None else human

    def attest_review(self, execution_id, *, receipt_id, head_sha, reviewer, decision, reason, origin="explicit_operator_cli"):
        if (origin not in {"explicit_operator_cli", "explicit_operator_workbench"}
                or decision not in {"approve", "request_changes", "reject"}
                or not all(isinstance(s, str) and s.strip() and len(s.encode()) <= 4096 for s in (reviewer, reason))):
            raise TaskInputError("validation_review_invalid")
        snapshot = self._snapshot(execution_id)
        root = self._root(execution_id)
        with role_lock(root, create=False):
            current = self._current(snapshot)
            if current is None:
                raise TaskInputError("validation_receipt_missing")
            _plan, result, _effective = current
            _review = _read(root / ("validation-review-" + result["operation_key"] + ".json"))
            if result["receipt_id"] != receipt_id or result["candidate"]["head_sha"] != head_sha:
                raise TaskInputError("validation_review_mismatch")
            probe_candidate(self.source_root(snapshot, _plan["request"]), head_sha)
            review = _bound({"schema_version": "candidate_review_attestation.v1", "validation_receipt_id": receipt_id,
                             "candidate": result["candidate"], "reviewer": reviewer.strip(), "decision": decision,
                             "reason": reason.strip(), "producer": origin,
                             "reviewed_at": _review["reviewed_at"] if _review else now(),
                             "independence": "not_authenticated", "semantic_verification": "unverified"})
            path = root / ("validation-review-" + result["operation_key"] + ".json")
            if _review is not None and _review != review:
                raise TaskInputError("validation_operation_conflict")
            if _review is None:
                _write(path, review)
            return review

    def evidence(self, execution_id):
        fallback = {"schema_version": "candidate_validation_evidence.v1", "available": False,
                    "check_status": "unknown", "review_status": "missing", "acceptance_status": "unknown",
                    "verification": "unverified", "reason": "validation_not_run", "criteria": []}
        try:
            snapshot = self._snapshot(execution_id)
            with role_lock(self._root(execution_id), create=False):
                current = self._current(snapshot)
                if current is None:
                    return fallback
                plan, result, review = current
                observed = result["observation"]
                reason, status = observed["reason"], observed["status"]
                try:
                    probe_candidate(self.source_root(snapshot, plan["request"]), result["candidate"]["head_sha"])
                except TaskInputError as exc:
                    status, reason = "unknown", exc.code
                mode = "simulated" if snapshot.to_dict()["adapter"] in {"fake", "noop"} else "runtime_unverified"
                machine = review is not None and review.get("schema_version") == "candidate_machine_review.v1"
                acceptance = ("accepted_by_review_policy" if machine else "human_recorded") if (
                    status == "passed" and review and review.get("decision") == "approve") else "unknown"
                human = _read(self._root(execution_id) / ("validation-review-" + result["operation_key"] + ".json"))
                return {**fallback, "available": True, "check_status": status,
                        "acceptance_status": acceptance,
                        "semantic_verification": "judgment_not_independently_verified",
                        "human_review": human,
                        "review_status": "machine_recorded" if machine else "operator_recorded" if review else "missing", "reason": reason,
                        "candidate": result["candidate"], "receipt": result, "review": review,
                        "task_execution_mode": mode, "command_name": Path(plan["request"]["argv"][0]).name,
                        "command_digest": digest(canonical_bytes(plan["request"]["argv"])), "cwd": ".",
                        "criteria": [{"index": i, "check_status": status, "mapping": "operator_supplied",
                                      "acceptance_status": "unknown", "reason": "semantic_acceptance_not_verified"}
                                     for i in result["criteria"]]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {**fallback, "reason": exc.code if isinstance(exc, TaskInputError) else "validation_store_unavailable"}


def candidate_evidence(project_root, execution_id):
    try:
        return CandidateValidationService(project_root).evidence(execution_id)
    except (OSError, ValueError) as exc:
        return {"available": False, "check_status": "unknown", "review_status": "missing",
                "acceptance_status": "unknown", "verification": "unverified", "criteria": [],
                "reason": exc.code if isinstance(exc, TaskInputError) else "validation_store_unavailable"}
