"""Task Assistant batch adapter over the existing isolated dispatch pool.

The queue owns confirmation, LocalTaskStore owns project tasks, and dispatch
owns Worker identity, retained workspaces and result receipts. This module
does not implement another scheduler or turn Worker completion into delivery.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import threading

from goodteam_os.assistant_queue import active_project_run, require_no_active_project_goal
from goodteam_os.assistant_requests import conversation_lock
from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.dispatch_workspace import dispatch_artifact_root, prepare_dispatch_workspace
from goodteam_os.project_iteration import _internal, _validate_parallel_group, rank_project_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, task_evidence_digest
from goodteam_os.workflow_mutations import workflow_guard


TASK_INTENT_FIELDS = ("id", "title", "description", "role", "dependencies", "acceptance_criteria",
                      "write_domains", "execution_policy", "external_refs")
_BACKGROUND_THREADS = {}
_BACKGROUND_LOCK = threading.Lock()


def active_project_batch(workspace, project_key):
    batches = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {})
    for batch in batches.values():
        if batch.get("status") in {"claimed", "running"}:
            return batch
        if batch.get("status") in {"blocked", "review"}:
            from goodteam_os.project_iteration_recovery import project_batch_recovery_evidence

            try:
                value = project_batch_recovery_evidence(workspace=workspace, project_key=project_key,
                    root=Path(batch["project_root"]), run_id=batch["run_id"])
                if value["unknown_task_ids"]:
                    return batch
            except (OSError, ValueError, KeyError, TypeError):
                return batch
    return None


def _require_execution_slot(workspace, project_key, run_id):
    require_no_active_project_goal(workspace, project_key)
    active = active_project_batch(workspace, project_key)
    if active_project_run(workspace, project_key) or (active and active["run_id"] != run_id):
        raise ValueError("Finish or reconcile the active project execution before starting another batch.")


def _require_authority(workspace, project_key, root, project_config, expected):
    if expected is None:
        return
    from goodteam_os.goal_continuation import authority_snapshot

    if expected != authority_snapshot(workspace, project_key, Path(project_config), root):
        raise ValueError("Project configuration, Team or model settings changed after automatic execution was authorized.")


def _require_continuation(session, expected_id):
    if expected_id is None:
        return
    current = session.get("project_continuation") or {}
    if current.get("id") != expected_id or current.get("status") != "active":
        raise ValueError("Automatic project continuation was stopped or replaced before batch admission.")


def _identity(proposal_ids, expected_revisions):
    if (not isinstance(proposal_ids, list) or not proposal_ids
            or any(not isinstance(item, str) or not item for item in proposal_ids)
            or len(set(proposal_ids)) != len(proposal_ids)
            or not isinstance(expected_revisions, dict)
            or set(expected_revisions) != set(proposal_ids)
            or any(type(value) is not int or value < 1 for value in expected_revisions.values())):
        raise ValueError("Confirm distinct queued tasks at their exact current revisions.")
    references = sorted((item, expected_revisions[item]) for item in proposal_ids)
    run_id = "task-batch-" + sha256(json.dumps(references).encode()).hexdigest()
    return run_id, "dispatch-" + sha256(run_id.encode()).hexdigest()


def _runtime_tracks(workspace, project_config, project_key, root, tasks, proposals):
    from goodteam_os.graph import _worker_job_fields, resolve_project_runtime_root
    from goodteam_os.projects import load_project_target
    from goodteam_os.state import create_initial_state
    from goodteam_os.web import resolve_project_model_profile, resolve_runtime_model_config
    from goodteam_os.project_import import read_bound_project_readiness

    target = load_project_target(Path(project_config))
    configured = resolve_project_runtime_root(dict(target.get("repos", {})))
    if configured is None or Path(configured).resolve() != Path(root).resolve():
        raise ValueError("Batch source differs from the configured project repository.")
    from goodteam_os.project_readiness import build_capabilities

    # Legacy projects may have no readiness record, like start_workflow.
    # A present record remains authoritative and cannot be bypassed.
    try:
        readiness = read_bound_project_readiness(Path(workspace), project_key, root)
    except ValueError as exc:
        if not str(exc).startswith("No readiness found for project:"):
            raise
    else:
        if not build_capabilities(readiness)["start_workflow"]:
            raise ValueError("Project readiness must be complete before starting a task batch.")
    state = create_initial_state(run_id="batch-runtime-preview", project_key=project_key, idea="Queued task batch")
    _profile, state.role_models = resolve_project_model_profile(
        resolve_runtime_model_config(Path("models.yaml")), target,
        explicit_profile=str(target.get("model_profile") or "balanced"), role_overrides=[],
    )
    return [{
        "track_id": task["id"], "project_task_id": task["id"], "role": task["role"],
        **_worker_job_fields(target, str(task["role"]), state),
        "task": proposal["exact_request"], "task_source": "task_assistant_queue",
        "context": "Execute only this confirmed project task in the assigned isolated repository. "
                   "Do not merge, deploy or change shared project state. Results require independent review.",
        "execution_policy": "read_only" if task.get("execution_policy") == "read_only" else "standard",
        **({"write_domains": task["write_domains"]} if task.get("write_domains") else {}),
        "constraints": ["Do not write outside the declared task paths: " + ", ".join(task.get("write_domains") or [])],
    } for task, proposal in zip(tasks, proposals)]


def _claim_tasks(root, project_key, batch):
    store = LocalTaskStore(root, project_key)
    with store._lock():
        ledger = store._read()
        known = {task["id"]: task for task in ledger["tasks"]}
        changes = {}
        for binding in batch["task_bindings"]:
            task = known.get(binding["task_id"])
            if task is None or _internal(task):
                raise ValueError("A selected project task is unavailable.")
            if task.get("run_id") == batch["run_id"]:
                if (task.get("batch_source_digest") != binding["digest"]
                        or {key: task.get(key) for key in TASK_INTENT_FIELDS} != binding["task_intent"]):
                    raise ValueError("The task belongs to a different batch confirmation.")
                continue
            if task.get("run_id") or task.get("status") != "todo" or task_evidence_digest(task) != binding["digest"]:
                raise ValueError("A selected task changed or was already claimed.")
            if any(known.get(dep, {}).get("status") != "done" for dep in task.get("dependencies", [])):
                raise ValueError("A selected task has unfinished dependencies.")
            changes[task["id"]] = {**task, "status": "in_progress", "run_id": batch["run_id"],
                "batch_run_id": batch["run_id"], "dispatch_run_id": batch["dispatch_run_id"],
                "batch_source_digest": binding["digest"], "execution_origin": "task_assistant_batch",
                "delivery_verified": False, "updated_at": datetime.now(UTC).isoformat()}
        if changes:
            ledger["tasks"] = [changes.get(task["id"], task) for task in ledger["tasks"]]
            store._write(ledger)


def _claim_batch(*, workspace, project_key, root, project_config, proposal_ids, expected_revisions, confirmed,
                 expected_authority=None, expected_continuation_id=None):
    if confirmed is not True:
        raise ValueError("Confirm starting these queued tasks; configured Workers may use model credits.")
    run_id, dispatch_id = _identity(proposal_ids, expected_revisions)
    session_store = TaskAssistantSessionStore(workspace, project_key)
    with workflow_guard(workspace, project_key), conversation_lock(session_store):
        session = session_store.read()
        batch = session.get("project_task_batches", {}).get(run_id)
        if batch:
            if batch["project_root"] != str(Path(root).resolve()) or batch["project_config"] != str(Path(project_config).resolve()):
                raise ValueError("A batch cannot be replayed against another project source.")
            if expected_authority is not None and batch.get("expected_authority") != expected_authority:
                raise ValueError("The batch was confirmed under different automatic execution authority.")
            if expected_continuation_id is not None and batch.get("expected_continuation_id") != expected_continuation_id:
                raise ValueError("The batch belongs to a different automatic continuation.")
            from goodteam_os.project_iteration_recovery import effective_dispatch_id, reconcile_recovery_tasks

            report_path = dispatch_artifact_root(root, effective_dispatch_id(batch)) / "report.json"
            if batch.get("status") not in {"delivered", "abandoned"} and not report_path.exists():
                # A blocked attempt may have released its slot before a report
                # existed. Resuming is execution, so re-admit against current
                # Workflow/Goal/batch authority, then reserve the same slot.
                _require_execution_slot(workspace, project_key, run_id)
                _require_authority(workspace, project_key, root, project_config, batch.get("expected_authority"))
                if batch.get("current_recovery_attempt"):
                    command = batch["recovery_commands"][batch["current_recovery_attempt"]]
                    if not command["started"]:
                        reconcile_recovery_tasks(root, project_key, batch, command)
                batch = {**batch, "status": "claimed"}
                def reclaim(current):
                    _require_continuation(current, expected_continuation_id)
                    return {**current, "project_task_batches": {
                        **current.get("project_task_batches", {}), run_id: batch}}
                session_store._mutate(reclaim)
            return batch
        _require_execution_slot(workspace, project_key, run_id)
        _require_authority(workspace, project_key, root, project_config, expected_authority)
        _require_continuation(session, expected_continuation_id)
        proposal = session.get("proposed_task")
        if proposal and proposal.get("status") != "cancelled":
            raise ValueError("Resolve the current task draft before starting a batch.")
        queue = {item["id"]: item for item in session.get("queued_task_proposals", [])}
        proposals = [queue.get(identifier) for identifier in proposal_ids]
        if any(item is None or item.get("status") != "queued" or item.get("confirmed")
               or item.get("revision") != expected_revisions[item["id"]]
               or not item.get("project_task_id") for item in proposals):
            raise ValueError("Queued project tasks changed; refresh before confirming the batch.")
        ledger = LocalTaskStore(root, project_key)
        tasks = [ledger.get_task(item["project_task_id"]) for item in proposals]
        if len({task["id"] for task in tasks}) != len(tasks):
            raise ValueError("The batch contains duplicate original project tasks.")
        snapshot = rank_project_backlog(ledger.list_tasks(), project_key, limit=None)
        candidates = {item["task_id"]: item for item in snapshot["candidates"]}
        if any(task["id"] not in candidates or not candidates[task["id"]]["eligible_to_plan"]
               or task_evidence_digest(task) != item.get("project_task_digest")
               for task, item in zip(tasks, proposals)):
            raise ValueError("Selected project task evidence changed before batch confirmation.")
        if len(tasks) > 1:
            _validate_parallel_group([candidates[task["id"]] for task in tasks])
        tracks = _runtime_tracks(workspace, project_config, project_key, root, tasks, proposals)
        _require_authority(workspace, project_key, root, project_config, expected_authority)
        batch = {"run_id": run_id, "dispatch_run_id": dispatch_id, "proposal_ids": list(proposal_ids),
            "task_ids": [task["id"] for task in tasks], "expected_revisions": expected_revisions,
            "task_bindings": [{"task_id": task["id"], "digest": item["project_task_digest"],
                               "task_intent": {key: task.get(key) for key in TASK_INTENT_FIELDS}}
                              for task, item in zip(tasks, proposals)],
            "project_root": str(Path(root).resolve()), "project_config": str(Path(project_config).resolve()),
            "expected_authority": expected_authority,
            "expected_continuation_id": expected_continuation_id,
            "tracks": tracks, "status": "claimed", "confirmed": True,
            "created_at": datetime.now(UTC).isoformat()}

        def persist(current):
            # Stop and admission share this short session mutation lock. A
            # stopped authorization cannot launch work after a slow preview.
            _require_continuation(current, expected_continuation_id)
            current.setdefault("project_task_batches", {})[run_id] = batch
            for item in current["queued_task_proposals"]:
                if item["id"] in proposal_ids:
                    item.update(confirmed=True, status="running", run_id=run_id, batch_run_id=run_id)
            current["timeline"].append({"message_id": "batch-confirmed:" + run_id,
                "actor": "system", "event": "queued_task_batch_confirmed", "created_at": batch["created_at"],
                "content": "Confirmed queued project tasks for isolated execution.",
                "details": {"run_id": run_id, "proposal_ids": proposal_ids, "task_ids": batch["task_ids"]}})
            return current

        # Save the immutable confirmation BEFORE claiming tasks or creating
        # Workers. A crash retries this exact request, never a fresh runtime.
        session_store._mutate(persist)
        return batch


def iteration_batch_report(*, workspace, project_key, root, run_id):
    from goodteam_os.project_iteration_recovery import effective_dispatch_id

    batch = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {}).get(run_id)
    if not batch or batch.get("project_root") != str(Path(root).resolve()):
        raise ValueError("The project batch is unavailable.")
    from goodteam_os.memory_storage import read_bytes

    dispatch_id = effective_dispatch_id(batch)
    raw = read_bytes(dispatch_artifact_root(root, dispatch_id) / "report.json")
    if raw is None:
        raise ValueError("The existing dispatch has not published a terminal result.")
    report = json.loads(raw)
    if report.get("run_id") != dispatch_id or report.get("project_root") != str(Path(root).resolve()):
        raise ValueError("Dispatch report belongs to another batch.")
    prepare_dispatch_workspace(root, dispatch_id, None)
    return report


def _settle_batch(workspace, project_key, root, batch, report=None, error=""):
    from goodteam_os.project_iteration_recovery import effective_dispatch_id

    tasks = LocalTaskStore(root, project_key)
    jobs = {item.get("project_task_id"): item for item in (report or {}).get("jobs", [])}
    bindings = {binding["task_id"]: binding for binding in batch["task_bindings"]}
    task_statuses = {}
    with tasks._lock():
        ledger = tasks._read()
        for task in ledger["tasks"]:
            if task.get("run_id") != batch["run_id"] or task.get("execution_origin") != "task_assistant_batch":
                continue
            if task["status"] == "done" and task.get("delivery_verified"):
                task_statuses[task["id"]] = "done"
                continue
            job = jobs.get(task["id"], {})
            if not job and batch.get("current_recovery_attempt"):
                selected = batch["recovery_commands"][batch["current_recovery_attempt"]]["request"]["retry_task_ids"]
                if task["id"] not in selected:
                    task_statuses[task["id"]] = task["status"]
                    continue
            # Successful isolated task output can be reviewed even when a
            # sibling failed. The batch's blocked fan-in still prevents delivery.
            intent_matches = {key: task.get(key) for key in TASK_INTENT_FIELDS} == bindings[task["id"]]["task_intent"]
            reviewable = intent_matches and job.get("outcome") == "ok" and job.get("attribution_status") == "captured"
            task.update(status="review" if reviewable else "blocked", dispatch_job_id=job.get("job_id", ""),
                        dispatch_run_id=job.get("dispatch_run_id", effective_dispatch_id(batch)),
                        observed_workflow_status="complete" if reviewable else "rejected", delivery_verified=False,
                        execution_error=error if intent_matches else "Project task intent changed during execution.",
                        updated_at=datetime.now(UTC).isoformat())
            task_statuses[task["id"]] = task["status"]
        tasks._write(ledger)
    status = "review" if (report or {}).get("all_ok") and all(
        task_statuses.get(identifier) in {"review", "done"} for identifier in batch["task_ids"]) else "blocked"

    def persist(session):
        saved = session["project_task_batches"][batch["run_id"]]
        if saved.get("status") in {"delivered", "abandoned"}:
            return session
        saved.update(status=status, error=error, report_path=str(dispatch_artifact_root(root, effective_dispatch_id(batch)) / "report.json"),
                     integration=(report or {}).get("integration", {}), updated_at=datetime.now(UTC).isoformat())
        for item in session["queued_task_proposals"]:
            if item.get("run_id") == batch["run_id"]:
                item["status"] = task_statuses.get(item.get("project_task_id"), "blocked")
        return session

    TaskAssistantSessionStore(workspace, project_key)._mutate(persist)
    return {"run_id": batch["run_id"], "dispatch_run_id": effective_dispatch_id(batch), "status": status,
            "workflow_started": False, "execution_started": True, "report": report,
            "task_assistant_session": TaskAssistantSessionStore(workspace, project_key).read()}


def _execute_batch(*, workspace, project_key, root, batch):
    with workflow_guard(workspace, project_key):
        batch = TaskAssistantSessionStore(workspace, project_key).read()["project_task_batches"][batch["run_id"]]
        if batch.get("status") in {"delivered", "abandoned"}:
            return {"run_id": batch["run_id"], "status": batch["status"], "execution_started": False}
        try:
            if batch.get("current_recovery_attempt"):
                from goodteam_os.project_iteration_recovery import execute_recovery

                return execute_recovery(workspace=workspace, project_key=project_key, root=root, batch=batch)
            report_path = dispatch_artifact_root(root, batch["dispatch_run_id"]) / "report.json"
            if report_path.exists():
                report = iteration_batch_report(workspace=workspace, project_key=project_key, root=root,
                                                run_id=batch["run_id"])
                return _settle_batch(workspace, project_key, root, batch, report=report)
            _require_execution_slot(workspace, project_key, batch["run_id"])
            _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
            _claim_tasks(root, project_key, batch)
            report = dispatch_tracks(project_root=Path(root).resolve(), tracks=batch["tracks"],
                projects_dir=Path(workspace) / "projects", logical_task_id=batch["run_id"], require_isolation=True)
        except Exception as exc:
            _settle_batch(workspace, project_key, root, batch, error=str(exc))
            raise
        return _settle_batch(workspace, project_key, root, batch, report=report)


def run_queued_iteration_batch(*, workspace, project_key, root, project_config, proposal_ids,
                               expected_revisions, confirmed=False, background=False, expected_authority=None,
                               expected_continuation_id=None):
    """Confirm exact queued work once and reuse the existing real Worker pool.

    Manual requests and authorized project continuation share this entry;
    background execution releases the caller after durable confirmation.
    """
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    if background:
        identity = (str(workspace), project_key, _identity(proposal_ids, expected_revisions)[0])
        with _BACKGROUND_LOCK:
            active = _BACKGROUND_THREADS.get(identity)
            if active is not None and active.is_alive():
                # The same process still owns execution. Merely observe it;
                # do not contend with its workflow_guard or create poll threads.
                batch = TaskAssistantSessionStore(workspace, project_key).read()["project_task_batches"][identity[2]]
                if (confirmed is not True or batch["project_root"] != str(root)
                        or batch["project_config"] != str(Path(project_config).resolve())
                        or expected_authority is not None and batch.get("expected_authority") != expected_authority
                        or expected_continuation_id is not None and batch.get("expected_continuation_id") != expected_continuation_id):
                    raise ValueError("The running batch belongs to different confirmed inputs or authority.")
            else:
                batch = _claim_batch(workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                    proposal_ids=proposal_ids, expected_revisions=expected_revisions, confirmed=confirmed,
                    expected_authority=expected_authority, expected_continuation_id=expected_continuation_id)
                def run():
                    try:
                        _execute_batch(workspace=workspace, project_key=project_key, root=root, batch=batch)
                    except Exception:
                        # Existing queue/card projections retain failure; no hidden retry.
                        pass
                    finally:
                        with _BACKGROUND_LOCK:
                            if _BACKGROUND_THREADS.get(identity) is threading.current_thread():
                                _BACKGROUND_THREADS.pop(identity, None)
                thread = threading.Thread(target=run, daemon=True)
                _BACKGROUND_THREADS[identity] = thread
                thread.start()
            return {"run_id": batch["run_id"], "dispatch_run_id": batch.get("current_dispatch_run_id", batch["dispatch_run_id"]),
                    "status": batch["status"], "execution_started": batch.get("status") not in {"delivered", "abandoned"},
                    "workflow_started": False,
                    "task_assistant_session": TaskAssistantSessionStore(workspace, project_key).read()}
    batch = _claim_batch(workspace=workspace, project_key=project_key, root=root, project_config=project_config,
        proposal_ids=proposal_ids, expected_revisions=expected_revisions, confirmed=confirmed,
        expected_authority=expected_authority, expected_continuation_id=expected_continuation_id)
    return _execute_batch(workspace=workspace, project_key=project_key, root=root, batch=batch)
