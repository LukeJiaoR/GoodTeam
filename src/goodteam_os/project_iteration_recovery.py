"""Explicit recovery of the existing confirmed batch, never fresh task intake."""
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from goodteam_os.dispatch_workspace import (
    dispatch_artifact_root, dispatch_job_terminal, read_dispatch_recovery, verify_dispatch_result,
)
from goodteam_os.goal_continuation import goal_guard
from goodteam_os.memory_storage import read_bytes
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workflow_mutations import workflow_guard


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _batch(workspace, project_key, root, run_id):
    if not isinstance(run_id, str) or re.fullmatch(r"task-batch-[a-f0-9]{64}", run_id) is None:
        raise ValueError("An existing project batch identity is required.")
    store = TaskAssistantSessionStore(workspace, project_key)
    batch = store.read().get("project_task_batches", {}).get(run_id)
    if not batch or not batch.get("confirmed") or batch.get("project_root") != str(Path(root).resolve()):
        raise ValueError("The original confirmed project batch is unavailable.")
    return store, batch


def effective_dispatch_id(batch):
    return batch.get("current_dispatch_run_id") or batch["dispatch_run_id"]


def _quality_quiescent(root, batch):
    """Inspect protected started/terminal receipts; session labels are not proof."""
    from goodteam_os.execution.candidate_validation import CandidateValidationService, _read
    from goodteam_os.execution.task_snapshot import canonical_bytes, digest, execution_key
    from goodteam_os.execution.task_snapshot_store import store_for_project
    from goodteam_os.project_iteration_quality import _requirement

    store = store_for_project(Path(root), existing_only=True)
    required = _requirement(store, batch["run_id"])
    if required is None:
        return not batch.get("quality", {}).get("required")
    snapshot = store.load(required["snapshot"]["execution_id"])
    if snapshot is None or snapshot.reference != required["snapshot"]:
        return False
    quality_root = store.root / execution_key(snapshot.execution_id)
    validation = CandidateValidationService(root)
    for started in quality_root.glob("validation-started-*.json"):
        key = started.name[len("validation-started-"):-len(".json")]
        plan = _read(quality_root / ("validation-plan-" + key + ".json"))
        result = _read(started.with_name(started.name.replace("validation-started-", "validation-result-")))
        if plan is None or plan.get("request", {}).get("snapshot") != snapshot.reference or result is None:
            return False
        expected = {"operation_key": key, "plan_digest": digest(canonical_bytes(plan))}
        checked = validation._verify_result(plan, expected, _read(started), result)
        if checked["observation"]["status"] == "unknown":
            return False
    from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
    from goodteam_os.execution.correction_store import CorrectionAttemptStore
    from goodteam_os.skills.review_api import _jobs
    from goodteam_os.skills.review_results import ReviewResultStore

    service = AutomaticCandidateReviewService(root)
    if service.configuration(snapshot.execution_id) is not None:
        _start, context = service._load(snapshot)
        for job_id, _snapshot, _reference in _jobs(service.workers, context):
            detail = ReviewResultStore(service.workers, job_id).detail(expected_task_context=context)
            # A prepared Reviewer may already own an acquired route/container.
            # Only a matching protected terminal outcome proves retirement.
            if detail["status"] not in {"received", "failed", "invalid", "cancelled", "interrupted"}:
                return False
    from goodteam_os.execution.correction_loop import CorrectionLoopService

    loop = CorrectionLoopService(root)
    state = loop._load(snapshot)
    for row in (state or {}).get("rounds", []):
        if row.get("worker_job_id"):
            authority = CorrectionAttemptStore(root, row["worker_job_id"])
            value = authority.load()
            started, outcome = authority.outcome(value)
            if started is not None and outcome is None:
                return False
    return True


def project_batch_recovery_evidence(*, workspace, project_key, root, run_id):
    """Read-only eligibility and exact version, including live/unpublished runs."""
    store, batch = _batch(workspace, project_key, root, run_id)
    dispatch_id = effective_dispatch_id(batch)
    command = batch.get("recovery_commands", {}).get(batch.get("current_recovery_attempt"), {})
    attempt_id = command.get("attempt_id", "")
    value = {"status": batch["status"], "reason": "", "retryable_task_ids": [], "preserved_task_ids": [],
             "unknown_task_ids": [], "can_abandon": False, "can_resume_continuation": False,
             "continuation_id": "", "current_dispatch_run_id": dispatch_id, "attempt_id": attempt_id}
    proofs, raw, invalid_results = {}, None, []
    try:
        raw = read_bytes(dispatch_artifact_root(root, dispatch_id) / "report.json")
        if raw is None:
            raise ValueError("The batch has no terminal report; its Workers may still be running.")
        report = json.loads(raw)
        if (report.get("run_id") != dispatch_id or report.get("project_root") != str(Path(root).resolve())
                or len(report.get("jobs", [])) != len(batch["task_ids"])
                or {job.get("project_task_id") for job in report["jobs"]} != set(batch["task_ids"])):
            raise ValueError("The terminal report does not match the original tasks.")
        for entry in report["jobs"]:
            job = {"dispatch_run_id": dispatch_id, **entry}
            task_id = job["project_task_id"]
            try:
                status, proof = dispatch_job_terminal(root, job)
                proofs[task_id] = proof
            except (OSError, ValueError, KeyError, TypeError) as exc:
                value["unknown_task_ids"].append(task_id)
                value["reason"] = str(exc)
                continue
            # Bad output never proves a process is live. It may prevent safe
            # reuse/delivery while still allowing cancellation of ended work.
            if status["status"] == "failed" and job.get("outcome") != "ok":
                value["retryable_task_ids"].append(task_id)
            elif status["status"] == "completed" and job.get("outcome") == "ok":
                try:
                    if job.get("attribution_status") != "captured":
                        raise ValueError("The retained Worker output is not attributable.")
                    verify_dispatch_result(Path(root), job)
                    value["preserved_task_ids"].append(task_id)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    invalid_results.append(task_id)
                    value["reason"] = str(exc)
        if not _quality_quiescent(root, batch):
            raise ValueError("A quality check, Reviewer or correction has an unknown outcome; preserve its execution slot.")
        value["can_abandon"] = not value["unknown_task_ids"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        value.update(unknown_task_ids=list(batch["task_ids"]), reason=str(exc))
    if value["unknown_task_ids"] or invalid_results or batch["status"] != "blocked":
        value["retryable_task_ids"] = []
    if batch["status"] in {"delivered", "abandoned"}:
        value.update(retryable_task_ids=[], can_abandon=False, unknown_task_ids=[], reason=batch["status"])
    elif not value["reason"]:
        value["reason"] = ("Only the listed failed tasks will run again; successful results are retained."
                           if value["retryable_task_ids"] else "Retained results can be inspected or the undelivered batch explicitly abandoned.")
    current = store.read().get("project_continuation") or {}
    if (current.get("status") == "blocked" and (current.get("pending_batch") or {}).get("run_id") == run_id
            and batch.get("expected_continuation_id") == current.get("id")):
        value.update(can_resume_continuation=bool(value["retryable_task_ids"]), continuation_id=current["id"])
    value["version"] = _digest({"run_id": run_id, "dispatch_id": dispatch_id, "report": sha256(raw).hexdigest() if raw else None,
                              "proofs": proofs, "state": value})
    return value


def _response(workspace, project_key, root, batch, command):
    recovery = project_batch_recovery_evidence(workspace=workspace, project_key=project_key, root=root, run_id=batch["run_id"])
    return {"run_id": batch["run_id"], "attempt_id": command.get("attempt_id", ""),
            "dispatch_run_id": command.get("dispatch_run_id", effective_dispatch_id(batch)), "status": batch["status"],
            "recovery": recovery, "task_assistant_session": TaskAssistantSessionStore(workspace, project_key).read()}


def reconcile_recovery_tasks(root, project_key, batch, command):
    """Finish the admitted session command's LocalTaskStore projection once."""
    from goodteam_os.project_iteration_execution import TASK_INTENT_FIELDS

    retry = command["request"]["action"] == "retry_project_batch"
    selected = command["request"].get("retry_task_ids", []) if retry else batch["task_ids"]
    tasks = LocalTaskStore(root, project_key)
    with tasks._lock():
        ledger = tasks._read()
        known = {task["id"]: task for task in ledger["tasks"]}
        for binding in batch["task_bindings"]:
            task = known.get(binding["task_id"], {})
            if (task.get("run_id") != batch["run_id"] or task.get("delivery_verified")
                    or retry and task.get("status") == "canceled"
                    or {field: task.get(field) for field in TASK_INTENT_FIELDS} != binding["task_intent"]):
                raise ValueError("The original project task claim or scope changed.")
        changed = False
        for identifier in selected:
            task = known[identifier]
            target = "in_progress" if retry else "canceled"
            if task.get("status") != target:
                task.update(status=target, updated_at=tasks._now())
                changed = True
        if changed:
            tasks._write(ledger)


def _resume_command(workspace, project_key, root, batch, command_id):
    """Resume only this admitted command; never reapply continuation consent."""
    from goodteam_os.project_iteration_execution import run_queued_iteration_batch
    from goodteam_os.workflow_mutations import WorkflowBusyError

    command = batch["recovery_commands"][command_id]
    retry = command["request"]["action"] == "retry_project_batch"
    if retry:
        if batch.get("current_recovery_attempt") != command_id or batch["status"] in {"delivered", "abandoned"}:
            return
        # The existing batch registry observes a live attempt without acquiring
        # its long-running guard. A lost caller resumes that exact same identity.
        try:
            run_queued_iteration_batch(workspace=workspace, project_key=project_key, root=root,
                project_config=batch["project_config"], proposal_ids=batch["proposal_ids"],
                expected_revisions=batch["expected_revisions"], confirmed=True, background=True)
        except WorkflowBusyError:
            pass
    elif batch["status"] == "abandoned":
        with goal_guard(workspace, project_key), workflow_guard(workspace, project_key):
            reconcile_recovery_tasks(root, project_key, batch, command)


def recover_project_batch(*, workspace, project_key, root, request):
    """Confirm one retry/abandon against current evidence, preserving originals."""
    from goodteam_os.assistant_requests import conversation_lock
    from goodteam_os.project_iteration_execution import (
        TASK_INTENT_FIELDS, _require_execution_slot, _require_authority,
    )
    action, key = request.get("action"), request.get("idempotency_key")
    if (action not in {"retry_project_batch", "abandon_project_batch"} or request.get("confirmed") is not True
            or not isinstance(key, str) or not 1 <= len(key) <= 256):
        raise ValueError("Confirm recovery with a stable command identity.")
    retry = action == "retry_project_batch"
    if "resume_continuation" in request and type(request["resume_continuation"]) is not bool:
        raise ValueError("Explicitly choose whether to resume the same blocked continuation.")
    if retry and request.get("billing_acknowledged") is not True:
        raise ValueError("Confirm the additional failed-task Worker charges.")
    store, batch = _batch(workspace, project_key, root, request.get("run_id"))
    command_id = sha256(key.encode()).hexdigest()
    # Replaying repairs only this command's incomplete projection/launch. The
    # continuation consent is never applied again, including after owner Stop.
    previous = batch.get("recovery_commands", {}).get(command_id)
    if previous:
        if previous["request"] != request:
            raise ValueError("Recovery command identity was already used with different inputs.")
        _resume_command(workspace, project_key, root, batch, command_id)
        return _response(workspace, project_key, root, store.read()["project_task_batches"][batch["run_id"]], previous)
    with goal_guard(workspace, project_key), workflow_guard(workspace, project_key), conversation_lock(store):
        _store, batch = _batch(workspace, project_key, root, batch["run_id"])
        previous = batch.get("recovery_commands", {}).get(command_id)
        if previous:
            if previous["request"] != request:
                raise ValueError("Recovery command identity was already used with different inputs.")
            command = previous
        else:
            evidence = project_batch_recovery_evidence(workspace=workspace, project_key=project_key, root=root, run_id=batch["run_id"])
            if evidence["unknown_task_ids"]:
                raise ValueError(evidence["reason"])
            if request.get("expected_recovery_version") != evidence["version"]:
                raise ValueError("Recovery evidence changed; inspect the current batch before confirming.")
            if not evidence["can_abandon"]:
                raise ValueError("This batch is not available for recovery.")
            if retry:
                _require_execution_slot(workspace, project_key, batch["run_id"])
                _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
            selected = request.get("retry_task_ids")
            if retry and (not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected)
                          or len(set(selected)) != len(selected)
                          or not set(selected).issubset(evidence["retryable_task_ids"])):
                raise ValueError("Select only the observed terminal failures displayed for this batch.")
            tasks = LocalTaskStore(root, project_key)
            with tasks._lock():
                ledger = tasks._read()
                known = {task["id"]: task for task in ledger["tasks"]}
                for binding in batch["task_bindings"]:
                    task = known.get(binding["task_id"], {})
                    if (task.get("run_id") != batch["run_id"] or task.get("delivery_verified")
                            or retry and task.get("status") == "canceled"
                            or {field: task.get(field) for field in TASK_INTENT_FIELDS} != binding["task_intent"]):
                        raise ValueError("The original project task claim or scope changed.")
                command = {"request": request, "created_at": datetime.now(UTC).isoformat()}
                if retry:
                    raw = read_bytes(dispatch_artifact_root(root, effective_dispatch_id(batch)) / "report.json")
                    parent = json.loads(raw)
                    source = {"run_id": parent["run_id"], "report_digest": sha256(raw).hexdigest(), "retry_task_ids": selected}
                    read_dispatch_recovery(Path(root), source)
                    identity = batch["run_id"] + "-retry-" + command_id
                    dispatch_id = "dispatch-" + sha256(identity.encode()).hexdigest()
                    by_task = {job["project_task_id"]: job for job in parent["jobs"]}
                    tracks = []
                    for original in batch["tracks"]:
                        if original["project_task_id"] in selected:
                            old = by_task[original["project_task_id"]]
                            status, _proof = dispatch_job_terminal(Path(root), {"dispatch_run_id": parent["run_id"], **old})
                            previous_job = json.loads(read_bytes(Path(root) / ".goodteam/workers/jobs" / old["job_id"] / "job.json"))
                            tracks.append({**original, "original_failed_job_id": old["job_id"],
                                "retry_attempt": len(batch.get("recovery_commands", {})) + 1,
                                "retry_of_idempotency_key": previous_job.get("idempotency_key", ""),
                                "context": original["context"] + "\nPrevious observed failed attempt " + old["job_id"]
                                    + ": " + status.get("summary", "")[:1800]
                                    + "\nUse the original task scope; the retained failure is feedback, not new authority."})
                    command.update(attempt_id=identity, dispatch_run_id=dispatch_id, source=source, tracks=tracks, started=False)
                def persist(session):
                    current = session.get("project_continuation") or {}
                    if request.get("resume_continuation") is True:
                        if (not retry or not evidence["can_resume_continuation"]
                                or current.get("id") != request.get("continuation_id") or current.get("status") != "blocked"
                                or (current.get("pending_batch") or {}).get("run_id") != batch["run_id"]):
                            raise ValueError("Only the same blocked continuation can resume; stopped authorization is not revived.")
                        current.update(status="active", reason="explicit_failed_task_recovery")
                    saved = session["project_task_batches"][batch["run_id"]]
                    saved.setdefault("recovery_commands", {})[command_id] = command
                    if retry:
                        saved.update(current_recovery_attempt=command_id, current_dispatch_run_id=dispatch_id, status="claimed")
                    else:
                        saved.update(status="abandoned")
                        if (current.get("pending_batch") or {}).get("run_id") == batch["run_id"]:
                            current.update(status="stopped", pending_batch=None, reason="batch_abandoned")
                    for proposal in session.get("queued_task_proposals", []):
                        if proposal.get("run_id") == batch["run_id"] and (not retry or proposal.get("project_task_id") in selected):
                            proposal["status"] = "running" if retry else "cancelled"
                    return session
                # Commit authority first. Replaying this command never creates a
                # replacement attempt if task projection or launch is interrupted.
                store._mutate(persist)
                for identifier in selected if retry else batch["task_ids"]:
                    known[identifier].update(status="in_progress" if retry else "canceled", updated_at=tasks._now())
                tasks._write(ledger)
    _resume_command(workspace, project_key, root, store.read()["project_task_batches"][batch["run_id"]], command_id)
    batch = store.read()["project_task_batches"][batch["run_id"]]
    return _response(workspace, project_key, root, batch, command)


def execute_recovery(*, workspace, project_key, root, batch):
    """Called under the existing batch execution guard/Worker thread registry."""
    from goodteam_os.dispatch import dispatch_tracks
    from goodteam_os.project_iteration_execution import _settle_batch, _require_authority

    store = TaskAssistantSessionStore(workspace, project_key)
    command_id = batch["current_recovery_attempt"]
    command = batch["recovery_commands"][command_id]
    report_path = dispatch_artifact_root(root, command["dispatch_run_id"]) / "report.json"
    if report_path.exists():
        report = json.loads(read_bytes(report_path))
    else:
        _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
        read_dispatch_recovery(Path(root), command["source"])
        if command["started"]:
            # A reserved transport is not a new permission after restart. All
            # original attempt jobs need durable returns before report recovery.
            from goodteam_os.workers import WorkerJobStore
            found = [job for job in WorkerJobStore(root).list_jobs() if job.get("run_id") == command["dispatch_run_id"]]
            if len(found) != len(command["tracks"]):
                raise ValueError("Recovery dispatch outcome is unknown; no replacement attempt was started.")
            for job in found:
                dispatch_job_terminal(Path(root), {**job, "job_id": job["id"], "dispatch_run_id": command["dispatch_run_id"]})
        else:
            def started(session):
                session["project_task_batches"][batch["run_id"]]["recovery_commands"][command_id]["started"] = True
                return session
            store._mutate(started)
        reconcile_recovery_tasks(root, project_key, batch, command)
        report = dispatch_tracks(project_root=Path(root).resolve(), tracks=command["tracks"],
            projects_dir=Path(workspace) / "projects", logical_task_id=command["attempt_id"],
            require_isolation=True, recovery_source=command["source"])
    return _settle_batch(workspace, project_key, root, batch, report=report)
