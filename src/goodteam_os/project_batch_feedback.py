"""Bound known terminal batch results to the existing continuation Context.

Observations never accept output, retry an invocation or abandon its batch.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from goodteam_os.project_investigation_feedback import _digest, _MAX_REPORT_BYTES

_SCHEMA = "project_batch_observation.v1"


def valid_receipt(value, receipt):
    return (isinstance(receipt, dict) and receipt.get("schema_version") == _SCHEMA
            and receipt.get("continuation_id") == value.get("id")
            and receipt.get("authorization_digest") == value.get("authorization_digest")
            and receipt.get("receipt_id") == _digest({k: v for k, v in receipt.items() if k != "receipt_id"}))


def _read_observation(*, workspace, project_key, root, value, run_id):
    from goodteam_os.dispatch_workspace import dispatch_artifact_root, prepare_dispatch_workspace, verify_dispatch_result
    from goodteam_os.memory_storage import read_bytes
    from goodteam_os.project_iteration_continuation import _intent
    from goodteam_os.project_iteration_recovery import effective_dispatch_id, project_batch_recovery_evidence
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore, project_task_intent

    root = Path(root).resolve()
    batch = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {}).get(run_id, {})
    ids = batch.get("task_ids", [])
    if (not value.get("discovery") or not re.fullmatch(r"task-batch-[a-f0-9]{64}", run_id)
            or not ids or len(set(ids)) != len(ids) or not set(ids).issubset(value["task_ids"])
            or batch.get("confirmed") is not True or batch.get("status") not in {"review", "blocked"}
            or batch.get("project_root") != str(root) or batch.get("expected_continuation_id") != value["id"]
            or batch.get("expected_authority") != value["authority"]):
        raise ValueError("Batch feedback requires this continuation's original settled batch.")
    evidence = project_batch_recovery_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    successful, failed = set(evidence["preserved_task_ids"]), set(evidence["retryable_task_ids"])
    if (evidence["unknown_task_ids"] or not evidence["can_abandon"] or successful & failed
            or successful | failed != set(ids)):
        raise ValueError("Every batch invocation must have attributable known terminal evidence before feedback releases scheduling.")
    dispatch_id = effective_dispatch_id(batch)
    raw = read_bytes(dispatch_artifact_root(root, dispatch_id) / "report.json")
    report = json.loads(raw) if raw is not None else {}
    jobs = report.get("jobs", [])
    if (report.get("run_id") != dispatch_id or report.get("project_root") != str(root)
            or len(jobs) != len(ids) or {job.get("project_task_id") for job in jobs} != set(ids)):
        raise ValueError("Batch feedback report does not match its original invocations.")
    tasks = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    bindings, tracks = batch.get("task_bindings", []), batch.get("tracks", [])
    if (len(bindings) != len(ids) or {b.get("task_id") for b in bindings} != set(ids)
            or len(tracks) != len(ids) or {t.get("project_task_id") for t in tracks} != set(ids)):
        raise ValueError("Batch feedback task bindings are incomplete.")
    observations = []
    for job in jobs:
        identifier = job["project_task_id"]
        task = tasks.get(identifier, {})
        binding = next(b for b in bindings if b["task_id"] == identifier)
        track = next(t for t in tracks if t["project_task_id"] == identifier)
        job_id, job_dispatch = job.get("job_id", ""), job.get("dispatch_run_id", dispatch_id)
        success = identifier in successful
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job_id)
                or task.get("run_id") != run_id or task.get("execution_origin") != "task_assistant_batch"
                or task.get("status") != ("review" if success else "blocked") or task.get("delivery_verified") is True
                or task.get("dispatch_job_id") != job_id or task.get("dispatch_run_id") != job_dispatch
                or _intent(task) != value["task_intents"].get(identifier)
                or project_task_intent(task) != binding["task_intent"]
                or task.get("role") != job.get("role") or task.get("role") != track.get("role")
                or task.get("execution_policy") != track.get("execution_policy")
                or (job.get("outcome") == "ok") != success
                or "worker_id" in task and (job.get("worker_id") != task["worker_id"]
                    or track.get("worker_id") != task["worker_id"])):
            raise ValueError("Batch feedback output belongs to another task, intent or attempt.")
        captured = None
        if job.get("attribution_status") == "captured":
            captured = verify_dispatch_result(root, {**job, "dispatch_run_id": job_dispatch})
            if task.get("execution_policy") == "read_only" and captured.get("changed_files") != []:
                raise ValueError("Read-only batch output changed project files.")
        elif success:
            raise ValueError("Successful batch output requires captured attribution.")
        source = prepare_dispatch_workspace(root, job_dispatch, None).source_head()
        content = read_bytes(root / ".goodteam" / "workers" / "jobs" / job_id / "result.md", maximum=_MAX_REPORT_BYTES)
        text = content.decode("utf-8") if content is not None else ""
        observations.append({"task_id": identifier, "task_intent_digest": _intent(task), "job_id": job_id,
            "dispatch_run_id": job_dispatch, "source_project_head": source, "role": task["role"],
            "task_status": task["status"], "worker_status": "completed" if success else "failed",
            "worker_outcome": job.get("outcome"), "attribution_status": job.get("attribution_status"),
            "captured_result": captured, "report": text,
            "report_digest": "sha256:" + sha256(content).hexdigest() if content is not None else None,
            "report_status": "missing" if content is None else "empty" if not text.strip() else "present" if success else "partial",
            "execution_summary": str(job.get("summary") or "")[:2000],
            "task_acceptance": "not_assessed", "delivery_verified": False})
    receipt = {"schema_version": _SCHEMA, "continuation_id": value["id"],
        "authorization_digest": value["authorization_digest"], "project_key": project_key,
        "project_root": str(root), "batch_run_id": run_id, "dispatch_run_id": dispatch_id,
        "observation_key": run_id + ":" + dispatch_id,
        "dispatch_report_digest": "sha256:" + sha256(raw).hexdigest(),
        "recovery_version": evidence["version"], "tasks": observations, "delivery_verified": False}
    return {**receipt, "receipt_id": _digest(receipt)}


def observe_pending_batch(*, workspace, project_key, root, project_config, value):
    from goodteam_os.goal_continuation import authority_snapshot
    from goodteam_os.project_outcome_discovery import _current
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    pending = value.get("pending_batch") or {}
    if not pending.get("run_id") or not value.get("discovery"):
        return None
    receipt = _read_observation(workspace=workspace, project_key=project_key, root=root, value=value, run_id=pending["run_id"])
    if set(pending["task_ids"]) != {t["task_id"] for t in receipt["tasks"]}:
        raise ValueError("Pending batch ownership changed before feedback capture.")
    def retain(session):
        current = _current(session, value)
        if (current.get("pending_batch") != pending or current.get("discovery_pending")
                or current["authority"] != authority_snapshot(workspace, project_key, Path(project_config), Path(root))):
            raise ValueError("Batch continuation changed before feedback capture.")
        history = dict(current.get("batch_observations") or {})
        previous = history.get(receipt["observation_key"])
        if previous is not None and previous != receipt:
            raise ValueError("The retained batch observation changed.")
        history[receipt["observation_key"]] = receipt
        current.update(batch_observations=history, batch_feedback_pending=receipt["observation_key"], pending_batch=None,
                       reason="batch_feedback_pending")
        return session
    return TaskAssistantSessionStore(workspace, project_key)._mutate(retain)["project_continuation"]


def pending_batch_feedback(*, workspace, project_key, root, value):
    run_id = value.get("batch_feedback_pending")
    if not run_id:
        return None
    receipt = (value.get("batch_observations") or {}).get(run_id)
    if not valid_receipt(value, receipt) or _read_observation(
            workspace=workspace, project_key=project_key, root=root, value=value, run_id=receipt["batch_run_id"]) != receipt:
        raise ValueError("The pending batch evidence changed after capture.")
    return receipt
