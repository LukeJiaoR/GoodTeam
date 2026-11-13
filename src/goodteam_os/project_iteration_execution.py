"""Task Assistant batch adapter over the existing isolated dispatch pool.

The queue owns confirmation, LocalTaskStore owns project tasks, and dispatch
owns Worker identity, retained workspaces and result receipts. This module
does not implement another scheduler or turn Worker completion into delivery.
"""
from __future__ import annotations

from contextlib import nullcontext
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
from goodteam_os.tasks import LocalTaskStore, project_task_intent, task_evidence_digest
from goodteam_os.tasks import TASK_INTENT_FIELDS as TASK_INTENT_FIELDS
from goodteam_os.workflow_mutations import workflow_guard, WorkflowBusyError


_BACKGROUND_THREADS = {}
_BACKGROUND_LOCK = threading.Lock()


def active_project_batches(workspace, project_key):
    """Derive occupied execution from the original batches; unknown is occupied."""
    batches = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {})
    result = []
    for batch in batches.values():
        if batch.get("status") in {"claimed", "running"}:
            result.append(batch)
        elif batch.get("status") in {"blocked", "review"}:
            from goodteam_os.project_iteration_recovery import project_batch_recovery_evidence
            try:
                evidence = project_batch_recovery_evidence(workspace=workspace, project_key=project_key,
                    root=Path(batch["project_root"]), run_id=batch["run_id"])
                if evidence["unknown_task_ids"]:
                    result.append(batch)
            except (OSError, ValueError, KeyError, TypeError):
                result.append(batch)
    return result


def active_project_batch(workspace, project_key):
    return next(iter(active_project_batches(workspace, project_key)), None)


class ProjectBatchResourceBusy(ValueError):
    """Valid separate batches temporarily overlap an occupied write domain."""


def _require_execution_slot(workspace, project_key, run_id, *, continuation_id=None, recovery=None, task_ids=None):
    require_no_active_project_goal(workspace, project_key)
    from goodteam_os.workflow_recovery import linked_parent
    parent = linked_parent(workspace, project_key, continuation_id=continuation_id, admitted=recovery)
    if active_project_run(workspace, project_key) and parent is None:
        raise ValueError("Finish or reconcile the active Workflow before starting another batch.")
    others = [b for b in active_project_batches(workspace, project_key) if b["run_id"] != run_id]
    if not others:
        return
    if (not continuation_id or recovery or not task_ids
            or any(b.get("expected_continuation_id") != continuation_id
                   or b.get("workflow_recovery") or b.get("current_recovery_attempt")
                   or b.get("status") not in {"claimed", "running"} for b in others)):
        raise ValueError("Finish or reconcile the active project execution before starting another batch.")
    # Other batches reserve their immutable task identities even before jobs
    # start. Shared capacity determines when a Worker actually receives a slot.
    root = Path(others[0]["project_root"])
    ledger = LocalTaskStore(root, project_key)
    all_tasks = {t["id"]: t for t in ledger.list_tasks()}
    occupied = []
    for batch in others:
        if Path(batch["project_root"]).resolve() != root.resolve():
            raise ValueError("Concurrent batch sources differ.")
        if (not batch.get("confirmed") or not batch.get("task_ids")
                or {b.get("task_id") for b in batch.get("task_bindings", [])} != set(batch["task_ids"])):
            raise ValueError("Occupied batch task attribution is incomplete.")
        for binding in batch.get("task_bindings", []):
            task = all_tasks.get(binding["task_id"])
            if task is None or project_task_intent(task) != binding["task_intent"]:
                raise ValueError("An occupied task's original intent changed.")
            occupied.append(task["id"])
        if set(occupied) & set(task_ids):
            raise ValueError("A task is already reserved by another batch.")
    snapshot = rank_project_backlog(list(all_tasks.values()), project_key, limit=None)
    candidates = {t["task_id"]: t for t in snapshot["candidates"]}
    ids = set(occupied) | set(task_ids)
    if not ids.issubset(candidates):
        raise ValueError("Concurrent task scope is unavailable.")
    # Invalid or internally conflicting batches are not a temporary resource
    # wait. Only collision between two individually valid scopes may defer.
    _validate_parallel_group([candidates[i] for i in occupied])
    _validate_parallel_group([candidates[i] for i in task_ids])
    try:
        _validate_parallel_group([candidates[i] for i in ids])
    except ValueError as exc:
        raise ProjectBatchResourceBusy(str(exc)) from exc


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


def dispatch_policy(task):
    """Translate a task's stored policy to the existing dispatch vocabulary."""
    policy = task.get("execution_policy", "workspace_write")
    if policy == "read_only":
        return "read_only"
    if policy == "workspace_write":
        return "standard"
    raise ValueError("Unrecognized project task execution policy.")


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
    from goodteam_os.task_delegation import render_project_task

    tracks = [{
        "track_id": task["id"], "project_task_id": task["id"], "role": task["role"],
        **_worker_job_fields(target, str(task["role"]), state,
                             **({"worker_id": task["worker_id"]} if "worker_id" in task else {})),
        "task": render_project_task(task), "task_source": "task_assistant_queue",
        **({"web_search": task["web_search"]} if "web_search" in task else {}),
        "context": "Execute only this confirmed project task in the assigned isolated repository. "
                   "Do not merge, deploy or change shared project state. Results require independent review.",
        "execution_policy": dispatch_policy(task),
        **({"write_domains": task["write_domains"]} if task.get("write_domains") else {}),
        "constraints": ["Do not write outside the declared task paths: " + ", ".join(task.get("write_domains") or [])],
    } for task, proposal in zip(tasks, proposals)]
    from goodteam_os.graph import SESSION_BACKENDS
    from goodteam_os.web_search import search_policy

    for track in tracks:
        if "web_search" in track:
            track["web_search"] = search_policy(track["web_search"])
            if track["web_search"] is not None and (
                    track["adapter"] not in SESSION_BACKENDS or not (track["execution_policy"] == "standard"
                    or track["execution_policy"] == "read_only" and track["role"] in {"research", "planner"})):
                raise ValueError("Task Web Search requires an explicitly authorized supported Pi Worker.")
    return tracks


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
                        or project_task_intent(task) != binding["task_intent"]):
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
                 expected_authority=None, expected_continuation_id=None, selected_proposal_id=None, expected_team_revision=None):
    if confirmed is not True:
        raise ValueError("Confirm starting these queued tasks; configured Workers may use model credits.")
    run_id, dispatch_id = _identity(proposal_ids, expected_revisions)
    session_store = TaskAssistantSessionStore(workspace, project_key)
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from goodteam_os.skills.models import SkillConflict, SkillError

    if expected_team_revision is not None and (type(expected_team_revision) is not int or expected_team_revision < 0):
        raise SkillError("invalid_revision", "Task confirmation requires a valid Team revision.")
    team_store = TeamControlPlaneStore(workspace, project_key)
    with workflow_guard(workspace, project_key), conversation_lock(session_store), (team_store._locked() if expected_team_revision is not None else nullcontext()):
        session = session_store.read()
        batch = session.get("project_task_batches", {}).get(run_id)
        if batch:
            if expected_team_revision is not None and batch.get("expected_team_revision") != expected_team_revision:
                raise SkillConflict("team_revision_changed", "The batch was confirmed under a different Team revision.")
            if selected_proposal_id is not None and batch.get("selected_proposal_id") != selected_proposal_id:
                raise ValueError("The batch belongs to a different selected task.")
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
                _require_execution_slot(workspace, project_key, run_id,
                    continuation_id=batch.get("expected_continuation_id"), recovery=batch.get("workflow_recovery"),
                    task_ids=batch.get("task_ids"))
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
        if expected_team_revision is not None:
            team = team_store.read()
            if team is None:
                raise SkillError("team_unavailable", "The confirmed project Team is unavailable.")
            if team["revision"] != expected_team_revision:
                raise SkillConflict("team_revision_changed", "Team changed after confirmation; review before starting.")
        _require_authority(workspace, project_key, root, project_config, expected_authority)
        _require_continuation(session, expected_continuation_id)
        proposal = session.get("proposed_task")
        selected_draft = None
        if selected_proposal_id is not None:
            if (proposal_ids != [selected_proposal_id] or not isinstance(proposal, dict)
                    or proposal.get("id") != selected_proposal_id
                    or proposal.get("revision") != expected_revisions[selected_proposal_id]
                    or proposal.get("status") not in {"draft", "confirmed"}
                    or proposal.get("pending_change") or proposal.get("run_id")
                    or not proposal.get("project_task_id")):
                raise ValueError("The selected project task draft changed before confirmation.")
            selected_draft = proposal
        if proposal and proposal.get("status") != "cancelled" and selected_draft is None:
            from goodteam_os.workflow_recovery import linked_parent

            if linked_parent(workspace, project_key, continuation_id=expected_continuation_id) is None:
                raise ValueError("Resolve the current task draft before starting a batch.")
        queue = {item["id"]: item for item in session.get("queued_task_proposals", [])}
        proposals = [queue.get(identifier) for identifier in proposal_ids]
        if any(item is None or item.get("status") != ("selected" if selected_draft else "queued") or item.get("confirmed")
               or item.get("revision") != expected_revisions[item["id"]]
               or not item.get("project_task_id") for item in proposals):
            raise ValueError("Queued project tasks changed; refresh before confirming the batch.")
        if selected_draft and any(selected_draft.get(key) != proposals[0].get(key)
                                  for key in ("project_task_id", "project_task_digest", "exact_request")):
            raise ValueError("The selected draft differs from its queued project task.")
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
        _require_execution_slot(workspace, project_key, run_id, continuation_id=expected_continuation_id,
                                task_ids=[task["id"] for task in tasks])
        if len(tasks) > 1:
            _validate_parallel_group([candidates[task["id"]] for task in tasks])
        from goodteam_os.project_candidate_handoff import candidate_for_tasks

        candidate_source = candidate_for_tasks(workspace=workspace, project_key=project_key, root=root,
            session=session, tasks=tasks, continuation_id=expected_continuation_id)
        from goodteam_os.models import _parse_simple_yaml
        from goodteam_os.project_worker_environment import load_environment

        environment_ref = _parse_simple_yaml(Path(project_config)).get("worker_environment_ref")
        environment = None
        if environment_ref:
            source = (Path(candidate_source["source"]["integration"]["candidate_path"])
                      if candidate_source is not None else Path(root))
            environment = load_environment(project_root=root, ref=environment_ref, source_root=source)
        tracks = _runtime_tracks(workspace, project_config, project_key, root, tasks, proposals)
        if environment is not None:
            for track in tracks:
                track["context"] += ("\nThe project has provided a private Worker copy of its locally prepared "
                    "dependency environment: " + ", ".join(environment["artifacts"]) + ". "
                    "Its preparation command was not independently verified. Existing task scope and sandbox apply.")
        _require_authority(workspace, project_key, root, project_config, expected_authority)
        batch = {"run_id": run_id, "dispatch_run_id": dispatch_id, "proposal_ids": list(proposal_ids),
            "task_ids": [task["id"] for task in tasks], "expected_revisions": expected_revisions,
            "task_bindings": [{"task_id": task["id"], "digest": item["project_task_digest"],
                               "task_intent": project_task_intent(task)}
                              for task, item in zip(tasks, proposals)],
            "project_root": str(Path(root).resolve()), "project_config": str(Path(project_config).resolve()),
            "expected_authority": expected_authority,
            "expected_continuation_id": expected_continuation_id,
            **({"workflow_recovery": session["project_continuation"]["workflow_recovery"]}
               if expected_continuation_id and session["project_continuation"].get("workflow_recovery") else {}),
            "selected_proposal_id": selected_proposal_id, "expected_team_revision": expected_team_revision,
            **({"candidate_source": candidate_source} if candidate_source is not None else {}),
            **({"worker_environment_ref": environment_ref} if environment is not None else {}),
            "tracks": tracks, "status": "claimed", "confirmed": True,
            "created_at": datetime.now(UTC).isoformat()}

        def persist(current):
            # Stop and admission share this short session mutation lock. A
            # stopped authorization cannot launch work after a slow preview.
            _require_continuation(current, expected_continuation_id)
            if selected_draft is not None:
                if current.get("proposed_task") != selected_draft:
                    raise ValueError("Selected draft changed during execution admission.")
                current["proposed_task"] = None
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
            intent_matches = project_task_intent(task) == bindings[task["id"]]["task_intent"]
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
    _publish_batch_result(workspace, project_key, root, batch, report, status)
    return {"run_id": batch["run_id"], "dispatch_run_id": effective_dispatch_id(batch), "status": status,
            "workflow_started": False, "execution_started": True, "report": report,
            "task_assistant_session": TaskAssistantSessionStore(workspace, project_key).read()}


def _publish_batch_result(workspace, project_key, root, batch, report, status):
    """Return actual Worker output to chat without promoting it to delivery.

    Publication is replayable after settlement; reading/polling starts no work.
    Result paths are derived from bound job IDs, never trusted from a report.
    """
    from goodteam_os.memory_storage import read_bytes
    import re

    store = TaskAssistantSessionStore(workspace, project_key)
    event_id = "batch-result:" + batch["run_id"] + ":" + str(batch.get("current_recovery_attempt", "initial"))
    session = store.read()
    previous = next((item for item in session.get("timeline", []) if item.get("message_id") == event_id), None)
    if previous is not None:
        if previous.get("event") != "conversation_replied" or previous.get("details", {}).get("run_id") != batch["run_id"]:
            raise ValueError("The batch result event belongs to another publication.")
        old_evidence = previous.get("details", {}).get("source_evidence", {})
        old_results = old_evidence.get("worker_results")
        if ("schema_version" not in old_evidence and isinstance(old_results, list)
                and all(isinstance(item, dict) and "job_id" not in item for item in old_results)):
            # Only the pre-handle format retains its original bytes across this
            # upgrade. New publications still require an exact replay below.
            return
    proposals = [item for item in session.get("queued_task_proposals", [])
                 if item.get("run_id") == batch["run_id"]]
    chinese = any(re.search(r"[\u3400-\u9fff]", item["exact_request"]) for item in proposals)
    content = [("Worker 结果已返回，尚未完成独立审查与交付确认。" if chinese else
                "Worker results returned; independent review and delivery remain outstanding.") if status == "review" else
               ("任务未完成，执行已阻塞。保留的结果如下；没有自动增加重试或模型预算。" if chinese else
                "Work remains incomplete and execution is blocked. Retained results follow; no retry or model budget was added.")]
    evidence = []
    for job in (report or {}).get("jobs", []):
        job_id = str(job.get("job_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            continue
        raw = read_bytes(Path(root) / ".goodteam" / "workers" / "jobs" / job_id / "result.md",
                         maximum=2 * 1024 * 1024)
        result = raw.decode("utf-8", errors="replace").strip() if raw else ""
        label = f"{job.get('role', 'Worker')} · {job_id}"
        content.append(f"\n{label}\n\n" + (result[:16000] if result else (
            "未保留可读报告，不能确认成果。" if chinese else "No readable report was retained; results cannot be confirmed.")))
        if len(result) > 16000:
            content.append("报告较长，此处为节选。" if chinese else "The report above is an excerpt.")
        evidence.append({"job_id": job_id, "role": str(job.get("role", "Worker")),
                         "status": str(job.get("status", "unknown")), "summary": result,
                         **{key: job[key] for key in ("project_task_id", "dispatch_run_id") if job.get(key)}})
    if not (report or {}).get("jobs"):
        content.append("尚无可核实的 Worker 报告。" if chinese else "No verifiable Worker report is available yet.")
    store.append_event(event_id,
                       "conversation_replied", content="\n\n".join(content),
                       details={"run_id": batch["run_id"], "actions_applied": False,
                                "source_evidence": {"schema_version": "worker_invocation_results.v1",
                                                    "worker_results": evidence}})


def _execute_batch(*, workspace, project_key, root, batch):
    from goodteam_os.dispatch_workspace import dispatch_workspace_lock
    from goodteam_os.project_iteration_recovery import effective_dispatch_id

    with dispatch_workspace_lock(Path(root), effective_dispatch_id(batch)):
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
            try:
                with workflow_guard(workspace, project_key):
                    _require_execution_slot(workspace, project_key, batch["run_id"],
                        continuation_id=batch.get("expected_continuation_id"), recovery=batch.get("workflow_recovery"),
                        task_ids=batch.get("task_ids"))
                    _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
                    _require_continuation(TaskAssistantSessionStore(workspace, project_key).read(), batch.get("expected_continuation_id"))
                    _claim_tasks(root, project_key, batch)
            except WorkflowBusyError:
                # Only the short, not-yet-executed admission was busy.
                return {"run_id": batch["run_id"], "status": batch["status"], "execution_started": False}
            def check_start(*_):
                _require_continuation(TaskAssistantSessionStore(workspace, project_key).read(), batch.get("expected_continuation_id"))
                _require_authority(workspace, project_key, root, batch["project_config"], batch.get("expected_authority"))
                known = {t["id"]: t for t in LocalTaskStore(root, project_key).list_tasks()}
                if any(binding["task_id"] not in known
                       or project_task_intent(known[binding["task_id"]]) != binding["task_intent"]
                       for binding in batch["task_bindings"]):
                    raise ValueError("The original task scope changed before Worker launch.")
                from goodteam_os.project_candidate_handoff import verify_candidate_for_batch

                verify_candidate_for_batch(workspace=workspace, project_key=project_key, root=root, batch=batch)
            from goodteam_os.project_candidate_handoff import verify_candidate_for_batch

            candidate_source = verify_candidate_for_batch(workspace=workspace, project_key=project_key,
                                                          root=root, batch=batch)
            environment_ref = batch.get("worker_environment_ref")
            if environment_ref:
                from goodteam_os.models import _parse_simple_yaml
                if _parse_simple_yaml(Path(batch["project_config"])).get("worker_environment_ref") != environment_ref:
                    raise ValueError("The project Worker environment changed after batch confirmation.")
            report = dispatch_tracks(project_root=Path(root).resolve(), tracks=batch["tracks"],
                projects_dir=Path(workspace) / "projects", logical_task_id=batch["run_id"], require_isolation=True,
                capacity_check_start=check_start,
                **({"worker_environment_ref": environment_ref} if environment_ref else {}),
                **({"candidate_source": candidate_source} if candidate_source is not None else {}))
        except Exception as exc:
            _settle_batch(workspace, project_key, root, batch, error=str(exc))
            raise
        return _settle_batch(workspace, project_key, root, batch, report=report)


def run_queued_iteration_batch(*, workspace, project_key, root, project_config, proposal_ids,
                               expected_revisions, confirmed=False, background=False, expected_authority=None,
                               expected_continuation_id=None, selected_proposal_id=None, expected_team_revision=None):
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
                        or expected_continuation_id is not None and batch.get("expected_continuation_id") != expected_continuation_id
                        or selected_proposal_id is not None and batch.get("selected_proposal_id") != selected_proposal_id
                        or expected_team_revision is not None and (type(expected_team_revision) is not int
                            or batch.get("expected_team_revision") != expected_team_revision)):
                    raise ValueError("The running batch belongs to different confirmed inputs or authority.")
            else:
                batch = _claim_batch(workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                    proposal_ids=proposal_ids, expected_revisions=expected_revisions, confirmed=confirmed,
                    expected_authority=expected_authority, expected_continuation_id=expected_continuation_id,
                    selected_proposal_id=selected_proposal_id, expected_team_revision=expected_team_revision)
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
        expected_authority=expected_authority, expected_continuation_id=expected_continuation_id,
                    selected_proposal_id=selected_proposal_id, expected_team_revision=expected_team_revision)
    return _execute_batch(workspace=workspace, project_key=project_key, root=root, batch=batch)
