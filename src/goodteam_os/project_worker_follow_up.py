"""Consume bounded Manager follow-up decisions through the original Pi queue."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from goodteam_os.project_running_observation import _binding, judgment_human_current


def consume_worker_follow_up(*, store, root, project_key, value, authority_check):
    from goodteam_os.manager_worker_control import _target
    from goodteam_os.project_outcome_discovery import _current
    from goodteam_os.project_iteration_continuation import _scope_tasks
    from goodteam_os.tasks import LocalTaskStore
    from goodteam_os.workers import WorkerJobStore

    limit = (value.get("discovery") or {}).get("worker_follow_up_limit", 0)
    if not limit:
        return value
    workers = WorkerJobStore(Path(root))
    # Reconcile acknowledgements from prior snapshots too. This never resends
    # even if new evidence has replaced the observation that asked the question.
    initial = _current(store.read(), value)
    for old_key, old_record in initial.get("running_judgments", {}).items():
        old = old_record.get("worker_control") or {}
        if old.get("status") not in {"reserved", "queued", "unconfirmed", "processing"}:
            continue
        try:
            existing = workers.find_project_session_control(old["control_id"], project_key=project_key)
            if existing is None:
                continue
            job_id, request = existing
            if (job_id != old["job_id"] or request.get("action") != "follow_up"
                    or request.get("message") != old["message"] or request.get("actor") != "manager"
                    or request.get("source") != "project_continuation"):
                continue
            observed = workers.session_control_result(job_id, old["control_id"], "follow_up", wait_for_ack=False)
        except (OSError, ValueError, TypeError):
            continue
        def refresh(session):
            current = session.get("project_continuation") or {}
            target = (current.get("running_judgments", {}).get(old_key) or {}).get("worker_control")
            if current.get("id") == value["id"] and target and target["control_id"] == old["control_id"]:
                target.update(status=str(observed.get("control_state") or "unconfirmed"),
                              rpc_accepted=observed.get("accepted") is True)
            return session
        store._mutate(refresh)
    selected = None
    def reserve(session):
        nonlocal selected
        current = _current(session, value)
        key = (current.get("running_observation") or {}).get("digest")
        record = current.get("running_judgments", {}).get(key) or {}
        command = (record.get("advice") or {}).get("follow_up")
        if not command or record.get("status") != "recorded":
            return session
        if record.get("worker_control"):
            selected = (key, deepcopy(record["worker_control"]), False)
            return session
        controls = [r["worker_control"] for r in current.get("running_judgments", {}).values() if r.get("worker_control")]
        used = len(controls)
        if (used >= limit or any(c["job_id"] == command["job_id"] for c in controls) or _binding(session, root) != record["binding"]
                or not judgment_human_current(current, record) or not authority_check()):
            return session
        control = {"control_id": "control-manager-" + sha256((value["id"] + key).encode()).hexdigest(),
                   "job_id": command["job_id"], "action": "follow_up", "status": "reserved",
                   "message": "Progress question within your original task only. Do not expand scope or permissions.\n" + command["message"],
                   "worker_reply_attributed": False, "execution_completed": False}
        record["worker_control"] = control
        selected = (key, deepcopy(control), True)
        return session
    latest = store._mutate(reserve).get("project_continuation") or value
    if selected is None:
        return latest
    key, control, fresh = selected
    result = {"control_state": "unconfirmed", "accepted": False}
    try:
        if fresh:
            tasks = LocalTaskStore(root, project_key)
            def enqueue(session):
                current = _current(session, value)
                record = current.get("running_judgments", {}).get(key) or {}
                if (record.get("worker_control") != control or _binding(session, root) != record.get("binding")
                        or not judgment_human_current(current, record) or not authority_check()):
                    raise ValueError("Original observation or authority changed before follow-up enqueue")
                known, _ = _scope_tasks(current, tasks.list_tasks())
                pending = current.get("pending_batch") or {}
                if any(known[identifier]["status"] == "canceled" for identifier in pending.get("task_ids", [])):
                    raise ValueError("The current batch was canceled; no automatic follow-up is authorized.")
                command = {"job_id": control["job_id"], "action": "follow_up", "message": control["message"], "reason": ""}
                _target(root, project_key, session, command, record["binding"]["dispatch_run_id"])
                # The durable reservation is already saved. A crash at/after
                # enqueue can only query the same control, never resend it.
                nonlocal result
                existing = workers.find_project_session_control(control["control_id"], project_key=project_key)
                if existing is not None:
                    job_id, request = existing
                    if (job_id != control["job_id"] or request.get("action") != "follow_up"
                            or request.get("message") != control["message"] or request.get("actor") != "manager"
                            or request.get("source") != "project_continuation"):
                        raise ValueError("Control identity is already bound to different instructions")
                    result = workers.session_control_result(job_id, control["control_id"], "follow_up", wait_for_ack=False)
                else:
                    result = workers.session_control(control["job_id"], "follow_up", message=control["message"],
                        actor="manager", source="project_continuation", control_id=control["control_id"],
                        audit_context={"project_key": project_key}, wait_for_ack=False)
                return session
            # Match the existing task-ledger -> session lock order. Cancellation
            # cannot commit between the final task read and this nonblocking send.
            with tasks._lock():
                store._mutate(enqueue)
        elif control["status"] in {"reserved", "queued", "unconfirmed", "processing"}:
            existing = workers.find_project_session_control(control["control_id"], project_key=project_key)
            if existing is not None:
                job_id, request = existing
                if (job_id != control["job_id"] or request.get("action") != "follow_up"
                        or request.get("message") != control["message"] or request.get("actor") != "manager"
                        or request.get("source") != "project_continuation"):
                    raise ValueError("Original follow-up identity conflicts with durable Runtime evidence")
                result = workers.session_control_result(job_id, control["control_id"], "follow_up", wait_for_ack=False)
        else:
            return latest
    except (OSError, ValueError, TypeError) as exc:
        # Includes a reserved request whose enqueue outcome cannot be proved.
        # Preserve it, consume its allowance, and never resend automatically.
        from goodteam_os.pi_rpc import redact_pi_text
        result = {"control_state": "unconfirmed", "accepted": False, "error": redact_pi_text(str(exc), limit=500)}
    def publish(session):
        current = session.get("project_continuation") or {}
        record = current.get("running_judgments", {}).get(key) or {}
        original = record.get("worker_control")
        if current.get("id") == value["id"] and original and original["control_id"] == control["control_id"]:
            original.update(status=str(result.get("control_state") or "unconfirmed"),
                            rpc_accepted=result.get("accepted") is True, error=str(result.get("error") or "")[:500],
                            worker_reply_attributed=False, execution_completed=False)
        return session
    return store._mutate(publish).get("project_continuation") or value
