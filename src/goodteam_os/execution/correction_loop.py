"""Supervised, bounded correction rounds for one captured issue execution.

A round never launches a model or repeats an old Worker transport. The operator
fixes/commits the candidate using their existing tools, then explicitly invokes
the same local check. Semantic acceptance and external costs remain unknown.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
import time

from goodteam_os.memory_storage import role_lock

from .candidate_probe import probe_candidate
from .candidate_validation import CandidateValidationService, _bound, _read, _write
from .task_snapshot import TaskInputError, canonical_bytes, digest

MAX_ROUNDS = 5
MAX_WALL_SECONDS = 3600
HALT_REASONS = {"correction_no_progress", "correction_stopped_by_operator",
                "correction_time_budget_exhausted", "correction_round_budget_exhausted",
                "correction_rejected", "correction_check_unknown", "correction_attempt_indeterminate"}


def _operation_records(service, snapshot, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise TaskInputError("correction_state_unavailable")
    root = service._root(snapshot.execution_id)
    plan = _read(root / ("validation-plan-" + key + ".json"))
    if plan is None or plan.get("request", {}).get("snapshot") != snapshot.reference:
        raise TaskInputError("correction_evidence_missing")
    expected = {"operation_key": key, "plan_digest": digest(canonical_bytes(plan))}
    started = _read(root / ("validation-started-" + key + ".json"))
    result = _read(root / ("validation-result-" + key + ".json"))
    review = _read(root / ("validation-review-" + key + ".json"))
    if result is not None:
        service._verify_result(plan, expected, started, result)
    if review is not None and (result is None or review != _bound(review)
            or review.get("validation_receipt_id") != result["receipt_id"]
            or review.get("candidate") != result["candidate"]
            or review.get("decision") not in {"approve", "request_changes", "reject"}):
        raise TaskInputError("correction_evidence_invalid")
    return plan, started, result, review


def _failure_signature(plan, result):
    observation = result["observation"]
    if observation["status"] != "failed":
        return ""
    return digest(canonical_bytes({"argv": plan["request"]["argv"],
        "exit_code": observation["exit_code"], "output": observation["output"]}))


class CorrectionLoopService:
    def __init__(self, project_root: Path, *, clock=None):
        self.validation = CandidateValidationService(project_root)
        self.clock = clock or time.time

    def _load(self, snapshot):
        root = self.validation._root(snapshot.execution_id)
        required = _read(root / "correction-required.json")
        state = _read(root / "correction-loop.json")
        if required is None and state is None:
            if any(_read(root / name) is not None for name in ["correction-halt.json", "correction-clock.json",
                    *(f"correction-round-{i}.json" for i in range(1, MAX_ROUNDS + 1))]):
                raise TaskInputError("correction_state_unavailable")
            return None
        if (required is None or state is None or state != _bound(state)
                or state.get("schema_version") != "supervised_correction_loop.v1"
                or state.get("snapshot") != snapshot.reference
                or required != {"loop_id": state.get("loop_id"), "snapshot": snapshot.reference,
                                "initial_digest": digest(canonical_bytes(state.get("initial")))}
                or not isinstance(state.get("rounds"), list)):
            raise TaskInputError("correction_state_unavailable")
        initial = state.get("initial")
        if (not isinstance(initial, dict) or set(initial) != {"receipt_id", "head_sha", "operation_key", "review_id",
                "max_rounds", "wall_seconds", "started_at"}
                or type(initial["max_rounds"]) is not int or not 1 <= initial["max_rounds"] <= MAX_ROUNDS
                or type(initial["wall_seconds"]) is not int or not 1 <= initial["wall_seconds"] <= MAX_WALL_SECONDS
                or type(initial["started_at"]) not in {int, float} or not math.isfinite(initial["started_at"])
                or len(state["rounds"]) > initial["max_rounds"]
                or state["loop_id"] != "correction:" + digest(canonical_bytes(initial))
                or state.get("stop_reason") not in {"", *HALT_REASONS}):
            raise TaskInputError("correction_state_unavailable")
        keys = {initial["operation_key"]}
        for entry in state["rounds"]:
            if (not isinstance(entry, dict) or set(entry) != {"ordinal", "operation_key", "plan_digest"}
                    or any(not isinstance(entry.get(k), str) or re.fullmatch(r"[a-f0-9]{64}", entry[k]) is None
                           for k in ("operation_key", "plan_digest"))):
                raise TaskInputError("correction_state_unavailable")
            keys.add(entry["operation_key"])
        # Commit-once reservations retain the high-water round independently of
        # the mutable journal and selected historical validation receipt.
        for ordinal in range(1, MAX_ROUNDS + 1):
            marker = _read(root / f"correction-round-{ordinal}.json")
            expected = ({"loop_id": state["loop_id"], "snapshot": snapshot.reference,
                         "round": state["rounds"][ordinal - 1]} if ordinal <= len(state["rounds"]) else None)
            if marker != expected:
                raise TaskInputError("correction_state_unavailable")
        halt = _read(root / "correction-halt.json")
        if halt is not None:
            if (halt != _bound(halt) or halt.get("loop_id") != state["loop_id"]
                    or halt.get("snapshot") != snapshot.reference or halt.get("reason") not in HALT_REASONS
                    or state["stop_reason"] not in {"", halt["reason"]}):
                raise TaskInputError("correction_state_unavailable")
            # A crash after the independent halt but before journal publication
            # is a committed stop, not permission to resume.
            state["stop_reason"] = halt["reason"]
        elif state["stop_reason"]:
            raise TaskInputError("correction_state_unavailable")
        selected = _read(root / "validation-current.json")
        if selected is None or selected.get("operation_key") not in keys:
            # A separately retained current receipt can expose a lost/rolled
            # back round journal. Never reset its consumed budget from a prefix.
            raise TaskInputError("correction_state_unavailable")
        return state

    def start(self, execution_id, *, receipt_id, head_sha, max_rounds=3, wall_seconds=900):
        if (type(max_rounds) is not int or not 1 <= max_rounds <= MAX_ROUNDS
                or type(wall_seconds) is not int or not 1 <= wall_seconds <= MAX_WALL_SECONDS):
            raise TaskInputError("correction_budget_invalid")
        snapshot = self.validation._snapshot(execution_id)
        root = self.validation._root(execution_id)
        with role_lock(root, create=False):
            state = self._load(snapshot)
            if state is not None:
                if (state["initial"]["receipt_id"] != receipt_id or state["initial"]["head_sha"] != head_sha
                        or state["initial"]["max_rounds"] != max_rounds or state["initial"]["wall_seconds"] != wall_seconds):
                    raise TaskInputError("correction_already_started")
                return self._project(snapshot, state)
            current = self.validation._current(snapshot)
            if current is None:
                raise TaskInputError("correction_evidence_missing")
            plan, result, review = current
            if (result["receipt_id"] != receipt_id or result["candidate"]["head_sha"] != head_sha
                    or review is None or review.get("decision") != "request_changes"):
                raise TaskInputError("correction_feedback_mismatch")
            probe_candidate(self.validation.project_root, head_sha)
            initial = {"receipt_id": receipt_id, "head_sha": head_sha,
                       "operation_key": result["operation_key"], "review_id": review["receipt_id"],
                       "max_rounds": max_rounds, "wall_seconds": wall_seconds,
                       "started_at": self.clock()}
            state = _bound({"schema_version": "supervised_correction_loop.v1", "snapshot": snapshot.reference,
                            "loop_id": "correction:" + digest(canonical_bytes(initial)),
                            "initial": initial, "rounds": [], "stop_reason": ""})
            # Missing one proof cannot silently initialize a fresh budget.
            _write(root / "correction-required.json", {"loop_id": state["loop_id"], "snapshot": snapshot.reference,
                    "initial_digest": digest(canonical_bytes(initial))})
            _write(root / "correction-clock.json", _bound({"loop_id": state["loop_id"], "snapshot": snapshot.reference,
                    "observed_at": initial["started_at"]}))
            _write(root / "correction-loop.json", state)
            return self._project(snapshot, state)

    def _save(self, snapshot, state):
        _write(self.validation._root(snapshot.execution_id) / "correction-loop.json", _bound(state))

    def _halt(self, snapshot, state, reason):
        if not state["stop_reason"]:
            _write(self.validation._root(snapshot.execution_id) / "correction-halt.json",
                   _bound({"loop_id": state["loop_id"], "snapshot": snapshot.reference, "reason": reason}))
            state["stop_reason"] = reason
            self._save(snapshot, state)
        return "needs_human", state["stop_reason"]

    def _observe_clock(self, snapshot, state):
        path = self.validation._root(snapshot.execution_id) / "correction-clock.json"
        recorded = _read(path)
        if (recorded is None or recorded != _bound(recorded) or recorded.get("loop_id") != state["loop_id"]
                or recorded.get("snapshot") != snapshot.reference
                or type(recorded.get("observed_at")) not in {int, float}
                or not math.isfinite(recorded["observed_at"])
                or recorded["observed_at"] < state["initial"]["started_at"]):
            raise TaskInputError("correction_state_unavailable")
        current = self.clock()
        if (type(current) not in {int, float} or not math.isfinite(current)
                or current < recorded["observed_at"]
                or current >= state["initial"]["started_at"] + state["initial"]["wall_seconds"]):
            self._halt(snapshot, state, "correction_time_budget_exhausted")
        elif current > recorded["observed_at"]:
            _write(path, _bound({**recorded, "observed_at": current}))
        return current

    def _history(self, snapshot, state):
        initial = state["initial"]
        history = [_operation_records(self.validation, snapshot, initial["operation_key"])]
        if (history[0][2] is None or history[0][2]["receipt_id"] != initial["receipt_id"]
                or history[0][3] is None or history[0][3]["receipt_id"] != initial["review_id"]):
            raise TaskInputError("correction_evidence_missing")
        keys = {initial["operation_key"]}
        for ordinal, entry in enumerate(state["rounds"], 1):
            if entry.get("ordinal") != ordinal or entry.get("operation_key") in keys:
                raise TaskInputError("correction_state_unavailable")
            records = _operation_records(self.validation, snapshot, entry["operation_key"])
            if digest(canonical_bytes(records[0])) != entry["plan_digest"]:
                raise TaskInputError("correction_evidence_invalid")
            keys.add(entry["operation_key"])
            history.append(records)
        return history

    def _phase(self, snapshot, state, history):
        if state["stop_reason"]:
            return "needs_human", state["stop_reason"]
        self._observe_clock(snapshot, state)
        if state["stop_reason"]:
            return "needs_human", state["stop_reason"]
        _plan, started, result, review = history[-1]
        if result is None:
            return self._halt(snapshot, state, "correction_attempt_indeterminate") if started else ("awaiting_recheck", "correction_check_prepared")
        if result["observation"]["status"] == "unknown":
            return self._halt(snapshot, state, "correction_check_unknown")
        signatures = [_failure_signature(plan, row) for plan, _, row, _ in history if row]
        if len(signatures) >= 3 and signatures[-1] and len(set(signatures[-3:])) == 1:
            return self._halt(snapshot, state, "correction_no_progress")
        if review and review.get("decision") == "reject":
            return self._halt(snapshot, state, "correction_rejected")
        if review and review.get("decision") == "approve":
            return "awaiting_human_acceptance", "semantic_acceptance_not_verified"
        if review is None:
            return "awaiting_review", "correction_review_required"
        if len(state["rounds"]) >= state["initial"]["max_rounds"]:
            return self._halt(snapshot, state, "correction_round_budget_exhausted")
        return "awaiting_fix", "correction_changes_requested"

    def admit_validation(self, snapshot, plan, expected):
        """Under the existing validation lock, before its durable call marker."""
        state = self._load(snapshot)
        if state is None:
            return
        history = self._history(snapshot, state)
        latest = state["rounds"][-1] if state["rounds"] else None
        if latest and latest["operation_key"] == expected["operation_key"]:
            if latest["plan_digest"] != expected["plan_digest"]:
                raise TaskInputError("correction_evidence_invalid")
            # Exact prepared operation can resume only before a call marker.
            if history[-1][1] is not None or history[-1][2] is not None:
                raise TaskInputError("correction_attempt_indeterminate")
            if state["stop_reason"]:
                raise TaskInputError(state["stop_reason"])
            current_time = self._observe_clock(snapshot, state)
            remaining = state["initial"]["started_at"] + state["initial"]["wall_seconds"] - current_time
            if state["stop_reason"] or plan["request"]["seconds"] > remaining:
                raise TaskInputError("correction_time_budget_exhausted")
            return
        phase, reason = self._phase(snapshot, state, history)
        if phase != "awaiting_fix":
            raise TaskInputError(reason)
        original = history[0][0]["request"]
        if (plan["request"]["argv"] != original["argv"] or plan["request"]["criteria"] != original["criteria"]):
            raise TaskInputError("correction_check_changed")
        seen = {record[0]["candidate"]["tree_sha"] for record in history}
        if plan["candidate"]["tree_sha"] in seen:
            self._halt(snapshot, state, "correction_no_progress")
            raise TaskInputError("correction_no_progress")
        remaining = state["initial"]["started_at"] + state["initial"]["wall_seconds"] - self._observe_clock(snapshot, state)
        if state["stop_reason"] or plan["request"]["seconds"] > remaining:
            raise TaskInputError("correction_time_budget_exhausted")
        entry = {"ordinal": len(state["rounds"]) + 1, **expected}
        _write(self.validation._root(snapshot.execution_id) / f"correction-round-{entry['ordinal']}.json",
               {"loop_id": state["loop_id"], "snapshot": snapshot.reference, "round": entry})
        state["rounds"].append(entry)
        self._save(snapshot, state)

    def stop(self, execution_id, *, loop_id):
        snapshot = self.validation._snapshot(execution_id)
        with role_lock(self.validation._root(execution_id), create=False):
            state = self._load(snapshot)
            if state is None or state["loop_id"] != loop_id:
                raise TaskInputError("correction_identity_mismatch")
            self._halt(snapshot, state, "correction_stopped_by_operator")
            return self._project(snapshot, state)

    def _project(self, snapshot, state):
        history = self._history(snapshot, state)
        phase, reason = self._phase(snapshot, state, history)
        if phase in {"awaiting_review", "awaiting_human_acceptance"}:
            try:
                probe_candidate(self.validation.project_root, history[-1][0]["candidate"]["head_sha"])
            except TaskInputError as exc:
                phase, reason = "needs_human", exc.code
        feedback = history[-1][3] or {}
        return {"available": True, "loop_id": state["loop_id"], "status": phase, "reason": reason,
                "completed_rounds": sum(row[2] is not None for row in history[1:]),
                "reserved_rounds": len(state["rounds"]), "max_rounds": state["initial"]["max_rounds"],
                "deadline_at": state["initial"]["started_at"] + state["initial"]["wall_seconds"],
                "candidate": history[-1][0]["candidate"], "feedback": feedback.get("reason", ""),
                "original_goal": snapshot.task.goal,
                "original_constraints": snapshot.task.task_contract.constraints,
                "reviewer": feedback.get("reviewer", ""), "review_decision": feedback.get("decision", ""),
                "automatic_provider_invocations": 0, "external_cost": "unknown_not_authorized_by_loop",
                "acceptance_status": "unknown", "verification": "unverified",
                "next_action": {"awaiting_fix": "Fix and commit a new candidate with your existing tools, then explicitly rerun the same check.",
                    "awaiting_recheck": "Resume the exact prepared local check; no call was recorded.",
                    "awaiting_review": "Review this exact candidate and its fresh check receipt.",
                    "awaiting_human_acceptance": "A person must decide acceptance; approval text is not semantic verification.",
                    "needs_human": "Inspect the preserved evidence and resolve the stop; no automatic retry or paid invocation."}[phase]}

    def evidence(self, execution_id):
        try:
            if self.validation.store is None:
                return {"available": False, "status": "not_started"}
            snapshot = self.validation._snapshot(execution_id)
            with role_lock(self.validation._root(execution_id), create=False):
                state = self._load(snapshot)
                return self._project(snapshot, state) if state else {"available": False, "status": "not_started"}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"available": False, "status": "needs_human", "reason": exc.code if isinstance(exc, TaskInputError)
                    else "correction_state_unavailable", "acceptance_status": "unknown", "verification": "unverified"}


def correction_evidence(project_root, execution_id):
    try:
        return CorrectionLoopService(project_root).evidence(execution_id)
    except (OSError, ValueError) as exc:
        return {"available": False, "status": "needs_human", "reason": exc.code if isinstance(exc, TaskInputError)
                else "correction_state_unavailable", "acceptance_status": "unknown", "verification": "unverified"}
