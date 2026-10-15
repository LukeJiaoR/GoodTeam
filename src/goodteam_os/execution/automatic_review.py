"""Trusted application coordinator for exact-candidate independent review.

The Reviewer supplies advisory findings, never an approval. This controller
applies one frozen policy to its protected report and an actual check receipt.
Runtime admission, external-cost authorization and delivery remain separate.
"""
from __future__ import annotations

import math
from pathlib import Path
import time

from goodteam_os.memory_storage import role_lock, MemoryStorageError
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_protocol import REVIEW_PROTOCOL
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.skills.review_sources import _Git, source_request
from goodteam_os.skills.task_context import validate_task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.task_start import capture_task_start, registered_project_scope
from goodteam_os.skills.worker_requests import original_worker_request, _services, _job_id
from goodteam_os.workers import WorkerJobStore

from .candidate_probe import probe_candidate
from .candidate_validation import CandidateValidationService, _bound, _read, _write
from .correction_loop import CorrectionLoopService, MAX_ROUNDS, MAX_WALL_SECONDS
from .task_snapshot import TaskInputError, canonical_bytes, digest

SCHEMA = "automatic_candidate_review.v1"
MACHINE_SCHEMA = "candidate_machine_review.v1"
POLICY = "candidate_review_policy.v1"
PLAN_POLICY = "candidate_review_policy.v2"


def review_policy(snapshot, approved_plan=None):
    policy = {"schema_version": POLICY, "protocol": REVIEW_PROTOCOL,
            "criterion_ids": [f"criterion:{snapshot.payload_digest}:{i}"
                              for i in range(len(snapshot.task.task_contract.acceptance_criteria))],
            "blocking_severities": ["critical", "high", "medium", "low"],
            "require_passed_host_check": True, "require_unique_checked_criteria": True,
            "judgment_verification": "not_independently_verified"}
    if approved_plan is not None:
        # This is coverage of semantic judgments, not a keyword check or a new
        # approval gate. The report retains each judgment's rationale/evidence.
        binding_id = approved_plan["binding_id"]
        policy.update(schema_version=PLAN_POLICY, approved_plan_binding_id=binding_id,
            plan_checks=[
                {"name": f"{binding_id}:artifact:{item['artifact_key']}",
                 "artifact_key": item["artifact_key"], "artifact_version": item["artifact_version"]}
                for item in approved_plan["approved_artifacts"]
            ] + [{"name": f"{binding_id}:derived-task", "artifact_key": approved_plan["derived_task"]["artifact_key"],
                  "artifact_version": approved_plan["derived_task"]["artifact_version"]}])
    return policy


def policy_decision(policy, result, report):
    status = result["observation"]["status"]
    if status == "unknown":
        return "inconclusive", "candidate_check_unknown"
    findings = [row for row in report["findings"] if row["severity"] in policy["blocking_severities"]]
    if status == "failed" or findings:
        feedback = "; ".join(f"{row['title']}: {row['recommendation']}" for row in findings)
        return "request_changes", ("Host check failed. " if status == "failed" else "") + feedback[:3500]
    checks = report["checks"]
    required = policy["criterion_ids"] + [row["name"] for row in policy.get("plan_checks", [])]
    if (not policy["criterion_ids"] or any(sum(row["name"] == name for row in checks) != 1 for name in required)
            or any(row["status"] != "checked" or not row["evidence_ids"] for row in checks)):
        return "inconclusive", "candidate_review_coverage_incomplete"
    return "approve", "candidate_review_policy_satisfied"


class AutomaticCandidateReviewService:
    def __init__(self, project_root, *, clock=None, codex_review_admission=None, codex_correction_admission=None):
        from .correction_native import correction_worker_kwargs

        self.project_root = Path(project_root).resolve()
        self.validation = CandidateValidationService(self.project_root)
        self.workers = WorkerJobStore(self.project_root, codex_review_admission=codex_review_admission)
        self.correction_runtime_kwargs = correction_worker_kwargs(codex_correction_admission)
        self.clock = clock or time.time

    def _policy(self, snapshot, saved=None):
        from .approved_plan import binding_for

        binding = binding_for(self.project_root, snapshot)
        expected = review_policy(snapshot, binding)
        if binding is not None and saved is not None and saved.get("policy") == review_policy(snapshot):
            # Never rewrite an in-flight immutable request or reinterpret an old
            # report as having covered a requirement it was not asked to cover.
            raise TaskInputError("automatic_review_plan_coverage_upgrade_required")
        return expected

    def configuration(self, execution_id):
        """Read the frozen start, including incomplete-start recovery; never admit work."""
        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            saved = _read(root / "automatic-review-start.json")
            if saved is None:
                if _read(root / "automatic-review-binding.json") is not None:
                    raise TaskInputError("automatic_review_binding_missing")
                return None
            if (saved != _bound(saved) or saved.get("schema_version") != SCHEMA
                    or saved.get("snapshot") != snapshot.reference
                    or saved.get("policy") != self._policy(snapshot, saved)):
                raise TaskInputError("automatic_review_binding_invalid")
            if _read(root / "automatic-review-binding.json") is not None:
                self._load(snapshot)
            return saved

    def _confirmation(self, snapshot, start):
        from .approved_plan import binding_for

        binding = binding_for(self.project_root, snapshot)
        return {"schema_version": SCHEMA, "original": snapshot.to_dict(), "start": start,
                "policy": review_policy(snapshot, binding), **({"approved_plan": binding} if binding is not None else {})}

    def _capture(self, snapshot, start):
        scope = registered_project_scope(self.project_root)
        if scope is None:
            raise TaskInputError("automatic_review_project_unregistered")
        workspace, project_key = scope
        task_id = "candidate-review:" + snapshot.payload_digest
        context = capture_task_start(workspace=workspace, project_key=project_key, worker_root=self.project_root,
            task=TaskSkillIdentity("direct_task", project_key, task_id), command_key=task_id,
            confirmed_input=self._confirmation(snapshot, start), subjects=("role:code_review",),
            protocol_only_review=True)
        if not context:
            raise TaskInputError("automatic_review_team_unavailable")
        return context

    def _load(self, snapshot):
        root = self.validation._root(snapshot.execution_id)
        start = _read(root / "automatic-review-start.json")
        binding = _read(root / "automatic-review-binding.json")
        if start is None or binding is None or start != _bound(start):
            raise TaskInputError("automatic_review_binding_missing")
        if (start.get("schema_version") != SCHEMA or start.get("snapshot") != snapshot.reference
                or start.get("policy") != self._policy(snapshot, start)
                or set(binding) != {"start_receipt", "task_skill_context"}
                or binding["start_receipt"] != start["receipt_id"]):
            raise TaskInputError("automatic_review_binding_invalid")
        context = validate_task_skill_context(binding["task_skill_context"])
        scope = registered_project_scope(self.project_root)
        if scope is None:
            raise TaskInputError("automatic_review_project_unregistered")
        workspace, project_key = scope
        task_id = "candidate-review:" + snapshot.payload_digest
        task = TaskSkillIdentity("direct_task", project_key, task_id)
        if context["task"] != {"kind": task.kind, "project_key": project_key, "logical_id": task_id}:
            raise TaskInputError("automatic_review_binding_invalid")
        _root, _operator, locks, _status = _services(self.workers)
        lock = locks.read(context["task_lock_ref"], task=task)
        registered = locks.find_task(task=task, command_key=task_id)
        confirmed = {**self._confirmation(snapshot, start),
                     "producer_binding": {"workspace": str(workspace.resolve()), "worker_root": str(self.project_root)}}
        if (registered is None or registered["ref"] != context["task_lock_ref"]
                or lock.get("start_input", {}).get("request", {}).get("confirmed_input") != confirmed):
            raise TaskInputError("automatic_review_binding_invalid")
        return start, context

    def start(self, execution_id, *, base_sha, receipt_id, head_sha, reviewer_adapter,
              reviewer_model="", max_rounds=3, wall_seconds=900, fix_seconds=120):
        """Freeze trusted caller choices, never infer an exact base from main.

        This only prepares input. Calling advance explicitly authorizes use of
        already admitted Reviewer transport, not new runtime permissions.
        """
        source_request({"kind": "git_change", "base_sha": base_sha, "head_sha": head_sha})
        if (type(max_rounds) is not int or not 1 <= max_rounds <= MAX_ROUNDS
                or type(wall_seconds) is not int or not 1 <= wall_seconds <= MAX_WALL_SECONDS
                or type(fix_seconds) is not int or not 1 <= fix_seconds <= 600
                or not isinstance(reviewer_adapter, str) or not reviewer_adapter
                or not isinstance(reviewer_model, str)):
            raise TaskInputError("automatic_review_input_invalid")
        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            saved = _read(root / "automatic-review-start.json")
            selected = {"base_sha": base_sha, "initial_receipt": receipt_id, "initial_head": head_sha,
                        "reviewer_adapter": reviewer_adapter, "reviewer_model": reviewer_model,
                        "max_rounds": max_rounds, "wall_seconds": wall_seconds, "fix_seconds": fix_seconds}
            if saved is not None:
                if any(saved.get(k) != v for k, v in selected.items()) or saved != _bound(saved):
                    raise TaskInputError("automatic_review_already_started")
                if (saved.get("schema_version") != SCHEMA or saved.get("snapshot") != snapshot.reference
                        or saved.get("policy") != self._policy(snapshot, saved)):
                    raise TaskInputError("automatic_review_binding_invalid")
            else:
                if _read(root / "automatic-review-binding.json") is not None:
                    raise TaskInputError("automatic_review_binding_missing")
                current = self.validation._current(snapshot)
                if current is None or current[1]["receipt_id"] != receipt_id or current[1]["candidate"]["head_sha"] != head_sha:
                    raise TaskInputError("validation_review_mismatch")
                probe_candidate(self.validation.source_root(snapshot, current[0]["request"]), head_sha)
                if _Git(self.project_root).read("cat-file", "-t", base_sha, maximum=64) != b"commit\n":
                    raise TaskInputError("automatic_review_base_invalid")
                saved = _bound({"schema_version": SCHEMA, "snapshot": snapshot.reference,
                    "policy": self._policy(snapshot), "started_at": self.clock(),
                    "validation_contract": {key: current[0]["request"][key] for key in ("argv", "criteria", "seconds")},
                    **selected})
                _write(root / "automatic-review-start.json", saved)
            previous = _read(root / "automatic-review-binding.json")
            context = self._load(snapshot)[1] if previous is not None else self._capture(snapshot, saved)
            binding = {"start_receipt": saved["receipt_id"], "task_skill_context": context}
            if previous is not None and previous != binding:
                raise TaskInputError("automatic_review_binding_invalid")
            if previous is None:
                _write(root / "automatic-review-clock.json", {"start_receipt": saved["receipt_id"], "observed_at": saved["started_at"]})
                _write(root / "automatic-review-binding.json", binding)
        return {"status": "prepared", "snapshot": snapshot.reference, "base_sha": base_sha,
                "policy": saved["policy"], "task_skill_context": context}

    def _guard(self, snapshot, start, *, observe=True):
        root = self.validation._root(snapshot.execution_id)
        stopped = _read(root / "automatic-review-stop.json")
        if stopped is not None:
            if stopped != {"start_receipt": start["receipt_id"]}:
                raise TaskInputError("automatic_review_binding_invalid")
            raise TaskInputError("correction_stopped_by_operator")
        halt = _read(root / "automatic-review-halt.json")
        if halt is not None:
            if halt != {"start_receipt": start["receipt_id"], "reason": "correction_time_budget_exhausted"}:
                raise TaskInputError("automatic_review_binding_invalid")
            raise TaskInputError(halt["reason"])
        recorded = _read(root / "automatic-review-clock.json")
        if recorded is None or recorded.get("start_receipt") != start["receipt_id"]:
            raise TaskInputError("automatic_review_clock_missing")
        previous, current = recorded.get("observed_at"), self.clock()
        if (type(previous) not in {int, float} or not math.isfinite(previous)
                or not math.isfinite(current) or current < previous
                or current >= start["started_at"] + start["wall_seconds"]):
            if observe:
                _write(root / "automatic-review-halt.json", {"start_receipt": start["receipt_id"],
                                                           "reason": "correction_time_budget_exhausted"})
            raise TaskInputError("correction_time_budget_exhausted")
        if observe:
            _write(root / "automatic-review-clock.json", {"start_receipt": start["receipt_id"], "observed_at": current})
        state = CorrectionLoopService(self.project_root)._load(snapshot)
        if state:
            self._check_loop_binding(start, state)
            if state["stop_reason"]:
                raise TaskInputError(state["stop_reason"])

    @staticmethod
    def _check_loop_binding(start, state):
        expected = {"receipt_id": start["initial_receipt"], "head_sha": start["initial_head"],
                    "max_rounds": start["max_rounds"], "wall_seconds": start["wall_seconds"],
                    "started_at": start["started_at"]}
        if any(state["initial"].get(key) != value for key, value in expected.items()):
            raise TaskInputError("automatic_review_loop_incompatible")

    def _request(self, snapshot, start, context, plan, result):
        if any(plan["request"].get(key) != value for key, value in start["validation_contract"].items()):
            raise TaskInputError("correction_check_changed")
        key = "candidate-review:" + digest(result["receipt_id"].encode())
        payload = {**self._confirmation(snapshot, start), "validation_plan": plan, "validation_receipt": result}
        return {"run_id": context["task"]["logical_id"], "role": "code_review",
            "adapter": start["reviewer_adapter"], "model": start["reviewer_model"],
            "project_key": context["task"]["project_key"], "idempotency_key": key,
            "task": ("Review the full original task and this exact candidate. For every required criterion ID, "
                "include exactly one check whose name is that ID. Mark checked only when assessed with source evidence. "
                "Unmet requirements must have actionable findings; uncertainty must be inconclusive. "
                "Do not approve or advance any gate. Return the captured review report protocol."
                + (" Also assess the pinned approved workflow intent and derived task mapping. "
                   "For every policy.plan_checks entry include exactly one check named by its name. "
                   "Assess whether the actual candidate fulfills the approved artifact's relevant intent, scope and constraints; "
                   "for the derived-task entry assess its alignment with that intent and the original task. "
                   "Explain the semantic judgment and its limits in detail, with exact candidate source evidence. "
                   "Merely repeating identifiers, quoting the plan, a passing test, or an empty findings list is not an assessment. "
                   "Report unmet intent or scope/constraint conflicts as actionable findings; uncertain alignment is inconclusive."
                   if payload.get("approved_plan") else "")),
            "context": canonical_bytes(payload).decode(), "constraints": snapshot.task.task_contract.constraints,
            "expected_artifacts": [], "execution_policy": "read_only", "session_policy": "new",
            "worker_session_ref": "", "task_skill_context": context, "skill_subject": "role:code_review",
            "review_target": {"kind": "git_change", "base_sha": start["base_sha"],
                              "head_sha": result["candidate"]["head_sha"]}}

    def _registered_job(self, snapshot, start, context, plan, result, *, create):
        expected = self._request(snapshot, start, context, plan, result)
        original = original_worker_request(self.workers, command_key=expected["idempotency_key"], context=context)
        if original is None:
            if not create:
                raise TaskInputError("automatic_review_job_missing")
            job = self.workers.create_job(**expected)
        else:
            if any(original.get(k) != v for k, v in expected.items()):
                raise TaskInputError("automatic_review_job_mismatch")
            # Re-publication checks original registered bytes, never chooses a
            # job by public metadata or a 'latest reviewer' projection.
            root, _operator, locks, _status = _services(self.workers)
            return _job_id(root, locks.scope, expected["idempotency_key"])
        return job["id"]

    def _machine_receipt(self, snapshot, start, context, plan, result, job_id):
        try:
            detail = ReviewResultStore(self.workers, job_id).detail(expected_task_context=context, include_report=True)
        except SkillError as exc:
            raise TaskInputError(exc.code) from exc
        if detail["status"] != "received" or not detail["report_available"]:
            raise TaskInputError(detail.get("error_code") or "automatic_review_outcome_unknown")
        target = detail["target"]
        if (target.get("base_sha") != start["base_sha"]
                or target.get("head_sha") != result["candidate"]["head_sha"]):
            raise TaskInputError("automatic_review_target_mismatch")
        decision, reason = policy_decision(start["policy"], result, detail["report"])
        provenance = {key: detail[key] for key in ("job_id", "task_skill_context", "snapshot_ref", "context_ref",
                      "target", "methods", "runtime", "runtime_verification", "report_digest")}
        return _bound({"schema_version": MACHINE_SCHEMA, "producer": "automatic_candidate_review_controller",
            "snapshot": snapshot.reference, "validation_receipt_id": result["receipt_id"],
            "candidate": result["candidate"], "base_sha": start["base_sha"], "policy": start["policy"],
            "reviewer": job_id, "decision": decision, "reason": reason, "reviewed_at": detail["updated_at"],
            "independence": "registered_read_only_new_session", "semantic_verification": "judgment_not_independently_verified",
            "provenance": provenance})

    def verify_receipt(self, snapshot, plan, result, review):
        start, context = self._load(snapshot)
        job_id = self._registered_job(snapshot, start, context, plan, result, create=False)
        if review != self._machine_receipt(snapshot, start, context, plan, result, job_id):
            raise TaskInputError("validation_review_invalid")
        return review

    def review_current(self, execution_id):
        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            start, context = self._load(snapshot)
            self._guard(snapshot, start)
            current = self.validation._current(snapshot)
            if current is None:
                raise TaskInputError("validation_receipt_missing")
            plan, result, _review = current
            probe_candidate(self.validation.source_root(snapshot, plan["request"]), result["candidate"]["head_sha"])
            job_id = self._registered_job(snapshot, start, context, plan, result, create=True)
        # Existing protected attempt/outcome store deduplicates dispatch, even
        # across restarts. Never repeat an unknown process or invoke legacy fallback.
        outcome = self.workers.run_job(job_id)
        if outcome.get("status") != "review_received":
            raise TaskInputError(outcome.get("skill_error_code") or "automatic_review_outcome_unknown")
        with role_lock(root, create=False):
            self._guard(snapshot, start)
            current = self.validation._current(snapshot)
            if current is None or current[0] != plan or current[1] != result:
                raise TaskInputError("validation_review_mismatch")
            probe_candidate(self.validation.source_root(snapshot, plan["request"]), result["candidate"]["head_sha"])
            review = self._machine_receipt(snapshot, start, context, plan, result, job_id)
            path = root / ("validation-machine-review-" + result["operation_key"] + ".json")
            previous = _read(path)
            if previous is not None and previous != review:
                raise TaskInputError("validation_operation_conflict")
            if previous is None:
                # Independent high-water proof prevents a lost decision from
                # silently becoming an optional historical human approval.
                _write(root / ("validation-machine-committed-" + result["operation_key"] + ".json"),
                       {"validation_receipt_id": result["receipt_id"], "review_receipt_id": review["receipt_id"]})
                _write(path, review)
            return review

    def advance(self, execution_id, *, acknowledge_external_cost=False):
        """Run the bounded producer→consumer→fix→recheck chain without attestation.

        Callable by trusted application code. No HTTP command executor or paid
        runtime grant is added. Unknown outcomes stop; replay reuses reservations.
        """
        from .correction_store import CorrectionAttemptStore
        from .correction_worker import CorrectionWorkerService

        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root / "automatic-review-run", create=True):
            try:
                start, _context = self._load(snapshot)
                while True:
                    with role_lock(root, create=False):
                        # A terminal current receipt can be recovered after its
                        # admission budget ends, without any provider operation.
                        current = self.validation._current(snapshot)
                        if current and current[2] and current[2].get("decision") == "approve" and current[1]["observation"]["status"] == "passed":
                            probe_candidate(self.validation.source_root(snapshot, current[0]["request"]), current[1]["candidate"]["head_sha"])
                            return {"status": "reviewed", "reason": "candidate_review_policy_satisfied",
                                    "acceptance_status": ("accepted_by_review_policy" if current[2].get("schema_version") == MACHINE_SCHEMA else "human_recorded"),
                                    "review": current[2], "semantic_verification": "judgment_not_independently_verified"}
                        self._guard(snapshot, start)
                        loop = CorrectionLoopService(self.project_root)
                        state = loop._load(snapshot)
                        projected = loop._project(snapshot, state) if state else None
                    phase = projected["status"] if projected else "awaiting_review"
                    if phase in {"reviewed", "needs_human"}:
                        return projected
                    if phase == "awaiting_review":
                        review = self.review_current(execution_id)
                        if review["decision"] == "inconclusive":
                            return {"status": "inconclusive", "reason": review["reason"], "review": review}
                        if review["decision"] == "approve":
                            return {"status": "reviewed", "reason": "candidate_review_policy_satisfied",
                                    "acceptance_status": "accepted_by_review_policy", "review": review,
                                    "semantic_verification": "judgment_not_independently_verified"}
                        if state is None:
                            # The entire initial review consumes the same frozen
                            # wall budget; starting correction cannot reset it.
                            CorrectionLoopService(self.project_root, clock=lambda: start["started_at"]).start(
                                execution_id, receipt_id=review["validation_receipt_id"],
                                head_sha=review["candidate"]["head_sha"], max_rounds=start["max_rounds"],
                                wall_seconds=start["wall_seconds"])
                        continue
                    service = CorrectionWorkerService(self.project_root, **self.correction_runtime_kwargs)
                    if phase == "awaiting_fix":
                        with role_lock(root, create=False):
                            _plan, result, _review = self.validation._current(snapshot)
                        service.prepare(execution_id, loop_id=projected["loop_id"], receipt_id=result["receipt_id"],
                            head_sha=result["candidate"]["head_sha"],
                            operation_id="auto-fix-" + digest(result["receipt_id"].encode()), max_seconds=start["fix_seconds"])
                    elif phase == "awaiting_worker":
                        service.run(execution_id, projected["worker_job_id"], acknowledge_external_cost=acknowledge_external_cost)
                    elif phase == "awaiting_recheck" and projected["worker_job_id"]:
                        job_id = projected["worker_job_id"]
                        # A completed transport can precede its host seal on
                        # restart. Finalize retained native output without
                        # redispatch before selecting the exact check head.
                        service.finalize(execution_id, job_id)
                        authority = CorrectionAttemptStore(self.project_root, job_id)
                        value = authority.load()
                        workspace = authority.workspace(value)
                        head = _Git(workspace).read("rev-parse", "HEAD", maximum=128).decode().strip()
                        with role_lock(root, create=False):
                            history = loop._history(snapshot, loop._load(snapshot))
                            original = history[0][0]["request"]
                        self.validation.validate(execution_id, operation_id="auto-check-" + digest(job_id.encode()),
                            head_sha=head, argv=original["argv"], criteria=original["criteria"],
                            seconds=original["seconds"], correction_worker=job_id)
                    else:
                        return projected
            except (TaskInputError, SkillError, OSError, MemoryStorageError) as exc:
                return {"status": "blocked", "reason": getattr(exc, "code", "automatic_review_store_unavailable"),
                        "acceptance_status": "unknown"}

    def projection(self, execution_id):
        """Read-only overlay for the existing execution/correction evidence.

        None means this execution has no automatic start. Existing correction
        details are retained with the automatic coordinator's actual next step.
        No automatic clock, task lock, job, outcome or cancellation record is
        written. The existing correction projection retains its clock observation.
        """
        fallback = {"available": False, "automatic": True, "status": "needs_human",
                    "reason": "automatic_review_binding_missing", "loop_id": "",
                    "acceptance_status": "unknown", "verification": "unverified",
                    "next_action": "Inspect the preserved automatic review evidence before continuing."}
        try:
            if self.validation.store is None:
                return None
            root = self.validation._root(execution_id)
            if _read(root / "automatic-review-start.json") is None and _read(root / "automatic-review-binding.json") is None:
                return None
            snapshot = self.validation._snapshot(execution_id)
            with role_lock(root, create=False):
                start, context = self._load(snapshot)
                current = self.validation._current(snapshot)
                if current is None:
                    raise TaskInputError("validation_receipt_missing")
                plan, result, review = current
                state = CorrectionLoopService(self.project_root)._load(snapshot)
                history = CorrectionLoopService(self.project_root)._history(snapshot, state) if state else []
                fallback.update(available=True, candidate=result["candidate"], validation_receipt_id=result["receipt_id"],
                    completed_rounds=sum(row[2] is not None for row in history[1:]),
                    feedback=review.get("reason", "") if review else "",
                    loop_id=state["loop_id"] if state else "", max_rounds=start["max_rounds"],
                    reserved_rounds=len(state["rounds"]) if state else 0,
                    deadline_at=start["started_at"] + start["wall_seconds"],
                    original_goal=snapshot.task.goal, original_constraints=snapshot.task.task_contract.constraints,
                    automatic_provider_invocations=None, provider_invocation_count_status="not_attested",
                    semantic_verification="judgment_not_independently_verified")
                probe_candidate(self.validation.source_root(snapshot, plan["request"]), result["candidate"]["head_sha"])
                if review and review["decision"] == "approve" and result["observation"]["status"] == "passed":
                    # Keep the automatic mode with the existing Worker/round details.
                    if state:
                        return self._loop_projection(snapshot, state)
                    return {**fallback, "status": "reviewed", "reason": "candidate_review_policy_satisfied",
                        "acceptance_status": "accepted_by_review_policy" if review.get("schema_version") == MACHINE_SCHEMA else "human_recorded",
                        "next_action": "Candidate review is complete. Continue the separately authorized delivery process."}
                for name, reason in (("stop", "correction_stopped_by_operator"), ("halt", "correction_time_budget_exhausted")):
                    value = _read(root / ("automatic-review-" + name + ".json"))
                    if value is not None:
                        expected = {"start_receipt": start["receipt_id"]}
                        if name == "halt":
                            expected["reason"] = reason
                        if value != expected:
                            raise TaskInputError("automatic_review_binding_invalid")
                        return {**fallback, "reason": reason}
                clock = _read(root / "automatic-review-clock.json")
                if clock is None or clock.get("start_receipt") != start["receipt_id"]:
                    raise TaskInputError("automatic_review_clock_missing")
                current_time = self.clock()
                if (current_time < clock["observed_at"] or current_time >= fallback["deadline_at"]):
                    return {**fallback, "reason": "correction_time_budget_exhausted"}
                if state:
                    self._check_loop_binding(start, state)
                    return self._loop_projection(snapshot, state)
                if review:
                    return {**fallback, "status": "awaiting_fix" if review["decision"] == "request_changes" else "needs_human",
                            "reason": review["reason"], "feedback": review["reason"],
                            "next_action": ("Continue the automatic coordinator to prepare the bound correction."
                                if review["decision"] == "request_changes" else fallback["next_action"])}
                try:
                    job_id = self._registered_job(snapshot, start, context, plan, result, create=False)
                except TaskInputError as exc:
                    if exc.code != "automatic_review_job_missing":
                        raise
                    return {**fallback, "status": "awaiting_review", "reason": "automatic_review_not_started",
                            "next_action": "Continue the automatic coordinator to run the independent Reviewer."}
                detail = ReviewResultStore(self.workers, job_id).detail(expected_task_context=context)
                if detail["status"] == "received" and detail["report_available"]:
                    return {**fallback, "status": "awaiting_review", "reason": "automatic_review_result_ready",
                            "next_action": "Continue the automatic coordinator to consume the retained independent report."}
                return {**fallback, "reason": detail.get("error_code") or "automatic_review_outcome_unknown"}
        except (ValueError, SkillError, OSError, KeyError, TypeError) as exc:
            cause = exc.__cause__ if isinstance(exc, MemoryStorageError) else exc
            return {**fallback, "available": False,
                    "reason": getattr(cause, "code", "automatic_review_store_unavailable")}

    def _loop_projection(self, snapshot, state):
        value = CorrectionLoopService(self.project_root)._project(snapshot, state)
        value["automatic"] = True
        phase = value["status"]
        if phase in {"awaiting_fix", "awaiting_worker"} and not value["runtime_admission"]["available"]:
            value["next_action"] = "Automatic correction is blocked by runtime admission. Preserve the evidence until a supported executor is available."
        elif phase in {"awaiting_fix", "awaiting_recheck", "awaiting_review"}:
            value["next_action"] = "Continue automatic review from the CLI using the frozen base, checks and Reviewer selection."
        elif phase == "awaiting_worker" and value["runtime_admission"]["available"]:
            value["next_action"] = "Continue automatic review with the correction cost acknowledgement to run the prepared Worker."
        return value

    def stop(self, execution_id):
        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            start, _context = self._load(snapshot)
            _write(root / "automatic-review-stop.json", {"start_receipt": start["receipt_id"]})
        return {"status": "blocked", "reason": "correction_stopped_by_operator"}
