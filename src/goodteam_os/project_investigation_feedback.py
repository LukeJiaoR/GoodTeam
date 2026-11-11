"""Observe settled investigation output without accepting it as delivery.

The existing continuation owns scheduling and its existing discovery outbox owns
billable decisions. These receipts only bind read-only Worker observations to
that grant; neither task/review state nor Git delivery is changed here.
"""
from hashlib import sha256
import json
from pathlib import Path
import re


_SCHEMA = "project_investigation_observation.v1"
_MAX_REPORT_BYTES = 64 * 1024


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _valid_receipt(value, receipt):
    return (isinstance(receipt, dict) and receipt.get("schema_version") == _SCHEMA
            and receipt.get("continuation_id") == value.get("id")
            and receipt.get("authorization_digest") == value.get("authorization_digest")
            and receipt.get("receipt_id") == _digest({key: item for key, item in receipt.items() if key != "receipt_id"}))


def is_observed_investigation(value, task):
    """Scheduling settlement only; never satisfies a done-task dependency."""
    receipt = (value.get("investigation_observations") or {}).get(task["id"])
    return ((value.get("discovery") or {}).get("allow_investigation") is True
            and _valid_receipt(value, receipt)
            and task["id"] in value.get("discovered_task_ids", [])
            and receipt.get("task_id") == task["id"]
            and receipt.get("task_intent_digest") == value["task_intents"].get(task["id"])
            and receipt.get("batch_run_id") == task.get("run_id")
            and task.get("role") == "research" and task.get("execution_policy") == "read_only"
            and task.get("write_domains") == []
            and receipt.get("task_status", "review") in {"review", "blocked"}
            and task.get("status") == receipt.get("task_status", "review")
            and task.get("delivery_verified") is not True)


def _read_observation(*, workspace, project_key, root, value, task_id, run_id):
    from goodteam_os.dispatch_workspace import (
        dispatch_artifact_root, prepare_dispatch_workspace, verify_dispatch_result,
    )
    from goodteam_os.memory_storage import read_bytes
    from goodteam_os.project_iteration_continuation import _intent, _require_clean_checkout
    from goodteam_os.tasks import project_task_intent
    from goodteam_os.project_iteration_recovery import effective_dispatch_id, project_batch_recovery_evidence
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore

    if ((value.get("discovery") or {}).get("allow_investigation") is not True
            or task_id not in value.get("discovered_task_ids", [])
            or re.fullmatch(r"task-batch-[a-f0-9]{64}", str(run_id)) is None):
        raise ValueError("Investigation feedback requires this grant's discovered read-only task.")
    root = Path(root).resolve()
    batch = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {}).get(run_id, {})
    tasks = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    task = tasks.get(task_id, {})
    bindings = batch.get("task_bindings") or []
    tracks = batch.get("tracks") or []
    task_status = task.get("status")
    failed = task_status == "blocked"
    if (batch.get("confirmed") is not True or task_status not in {"review", "blocked"}
            or batch.get("status") != task_status
            or batch.get("project_root") != str(root) or batch.get("expected_continuation_id") != value["id"]
            or batch.get("expected_authority") != value["authority"] or batch.get("task_ids") != [task_id]
            or task.get("role") != "research"
            or task.get("execution_policy") != "read_only" or task.get("write_domains") != []
            or task.get("delivery_verified") is True or task.get("run_id") != run_id
            or task.get("execution_origin") != "task_assistant_batch"
            or _intent(task) != value["task_intents"].get(task_id)
            or len(bindings) != 1 or bindings[0].get("task_id") != task_id
            or bindings[0].get("task_intent") != project_task_intent(task)
            or len(tracks) != 1 or tracks[0].get("project_task_id") != task_id
            or tracks[0].get("role") != "research" or tracks[0].get("execution_policy") != "read_only"
            or "worker_id" in task and tracks[0].get("worker_id") != task["worker_id"]):
        raise ValueError("Investigation feedback does not match its original confirmed task.")
    evidence = project_batch_recovery_evidence(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    # Recovery proves the original process ended; a blocked label or a timeout
    # alone cannot release its slot. Failed output also needs the unchanged,
    # attributable workspace receipt checked below. No retry is executed here.
    settled = evidence.get("retryable_task_ids" if failed else "preserved_task_ids")
    if (evidence["unknown_task_ids"] or settled != [task_id]
            or evidence.get("can_abandon") is not True):
        raise ValueError("Investigation termination or retained result is unverified; preserve its execution slot.")
    dispatch_id = effective_dispatch_id(batch)
    report_raw = read_bytes(dispatch_artifact_root(root, dispatch_id) / "report.json", maximum=2 * 1024 * 1024)
    report = json.loads(report_raw) if report_raw is not None else {}
    if not isinstance(report, dict):
        raise ValueError("The investigation dispatch report is invalid.")
    jobs = report.get("jobs") or []
    if (report.get("run_id") != dispatch_id or report.get("project_root") != str(root)
            or report.get("all_ok") is not (not failed) or not isinstance(jobs, list) or len(jobs) != 1
            or not isinstance(jobs[0], dict)):
        raise ValueError("Investigation requires one attributable terminal dispatch report.")
    job = jobs[0]
    job_id = job.get("job_id")
    if (not isinstance(job_id, str) or re.fullmatch(r"[A-Za-z0-9_-]+", job_id) is None
            or job.get("project_task_id") != task_id or job.get("role") != "research"
            or job.get("dispatch_run_id", dispatch_id) != dispatch_id
            or job.get("outcome") != ("needs_human" if failed else "ok")
            or failed and job.get("status") != "failed"
            or job.get("attribution_status") != "captured"
            or task.get("dispatch_job_id") != job_id or task.get("dispatch_run_id") != dispatch_id
            or "worker_id" in task and job.get("worker_id") != task["worker_id"]):
        raise ValueError("Investigation output belongs to another task or attempt.")
    captured = verify_dispatch_result(root, {**job, "dispatch_run_id": dispatch_id})
    if captured.get("changed_files") != []:
        raise ValueError("A read-only investigation changed project files; feedback cannot release the batch.")
    retained = prepare_dispatch_workspace(root, dispatch_id, None)
    # A report describes its captured source revision, not today's checkout.
    # Keep this immutable so later independent commits cannot rewrite its
    # provenance or invalidate an already retained observation.
    head = retained.source_head()
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise ValueError("The investigation source revision is missing or invalid.")
    _require_clean_checkout(root, "investigation source")
    # Never read a path supplied by the model or report. The bound job identity
    # selects the existing safe storage reader; oversized output is not truncated.
    raw = read_bytes(root / ".goodteam" / "workers" / "jobs" / job_id / "result.md", maximum=_MAX_REPORT_BYTES)
    # Missing or empty text is itself feedback, never a fabricated report.
    # Read errors, oversized data and malformed UTF-8 still fail closed.
    content = raw.decode("utf-8") if raw is not None else ""
    report_status = ("missing" if raw is None else "empty" if not content.strip()
                     else "partial" if failed else "present")
    receipt = {"schema_version": _SCHEMA, "continuation_id": value["id"],
               "authorization_digest": value["authorization_digest"], "project_key": project_key,
               "project_root": str(root), "project_head": head, "task_id": task_id,
               "task_intent_digest": _intent(task), "batch_run_id": run_id,
               "dispatch_run_id": dispatch_id, "job_id": job_id, "recovery_version": evidence["version"],
               **({"worker_id": task["worker_id"]} if "worker_id" in task else {}),
               "dispatch_report_digest": "sha256:" + sha256(report_raw).hexdigest(),
               "report_digest": "sha256:" + sha256(raw).hexdigest() if raw is not None else None, "report": content,
               "task_acceptance": "not_assessed", "review_status": "not_attested_by_observation",
               "delivery_verified": False}
    # Keep successful, nonempty L2 observations byte-compatible: a pending
    # reserved decision must not be invalidated merely by upgrading the host.
    if failed or report_status != "present":
        receipt.update(task_status=task_status, worker_status="failed" if failed else "completed",
                       worker_outcome=job["outcome"], report_status=report_status,
                       execution_summary=str(job.get("summary") or "")[:2000])
    receipt["receipt_id"] = _digest(receipt)
    return receipt


def observe_pending_investigation(*, workspace, project_key, root, project_config, value):
    """Atomically retain a report and the responsibility to judge it next.

    Called under the original continuation guard, not by GET/polling. Return
    None for other task types. Review/task state and all counters stay untouched.
    """
    from goodteam_os.goal_continuation import authority_snapshot
    from goodteam_os.project_outcome_discovery import _current
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore

    pending = value.get("pending_batch") or {}
    ids = pending.get("task_ids") or []
    if (not pending.get("run_id") or len(ids) != 1
            or (value.get("discovery") or {}).get("allow_investigation") is not True):
        return None
    task = next((row for row in LocalTaskStore(root, project_key).list_tasks() if row["id"] == ids[0]), {})
    if (task.get("role") != "research" or task.get("execution_policy") != "read_only"
            or ids[0] not in value.get("discovered_task_ids", [])):
        return None
    receipt = _read_observation(workspace=workspace, project_key=project_key, root=root,
                                value=value, task_id=ids[0], run_id=pending["run_id"])
    store = TaskAssistantSessionStore(workspace, project_key)

    def retain(session):
        current = _current(session, value)
        if (current.get("pending_batch") != pending or current.get("discovery_pending")
                or current["authority"] != authority_snapshot(workspace, project_key, Path(project_config), Path(root))):
            raise ValueError("Investigation continuation changed before its report could be recorded.")
        observations = dict(current.get("investigation_observations") or {})
        previous = observations.get(ids[0])
        if previous is not None and previous != receipt:
            raise ValueError("The retained investigation report changed; inspect the original observation.")
        observations[ids[0]] = receipt
        current.update(investigation_observations=observations, investigation_pending=ids[0],
                       pending_batch=None, reason="investigation_feedback_pending")
        return session

    return store._mutate(retain)["project_continuation"]


def pending_investigation_feedback(*, workspace, project_key, root, value):
    """Revalidate exact report bytes before/after a decision; no acceptance."""
    task_id = value.get("investigation_pending")
    if not task_id:
        return None
    receipt = (value.get("investigation_observations") or {}).get(task_id)
    if not _valid_receipt(value, receipt):
        raise ValueError("The pending investigation observation is missing or changed.")
    observed = _read_observation(workspace=workspace, project_key=project_key, root=root,
                                 value=value, task_id=task_id, run_id=receipt["batch_run_id"])
    if observed != receipt:
        raise ValueError("The investigation evidence changed after capture; preserve the original decision input.")
    return receipt
