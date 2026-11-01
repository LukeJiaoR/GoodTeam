"""Bounded project continuation through the existing Assistant queue and dispatch.

The Assistant session retains the owner's scope/budget and pending queue IDs.
LocalTaskStore remains task truth; dispatch receipts remain execution truth.
Neither a Worker exit nor a Manager statement is a delivery acceptance.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from goodteam_os.assistant_queue import active_project_run, queue_manager_iteration_choice, require_no_active_project_goal
from goodteam_os.goal_continuation import authority_snapshot, goal_guard
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.project_iteration import _internal, project_iteration_backlog, rank_project_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, task_evidence_digest


_FIELD = "project_continuation"
_INTENT_FIELDS = ("id", "title", "description", "role", "dependencies", "acceptance_criteria",
                  "write_domains", "execution_policy", "external_refs")


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _intent(task):
    return _digest({key: task.get(key) for key in _INTENT_FIELDS})


def _save(store, value):
    retained = dict(value)
    def write(session):
        current = session.get(_FIELD) or {}
        # Stop uses the short session mutation lock, even while a Manager call
        # holds the continuation guard. A late result must not reactivate it.
        if current.get("id") == value.get("id") and current.get("status") == "stopped":
            retained.update(status="stopped", reason="owner_stopped")
        return {**session, _FIELD: retained}
    store._mutate(write)
    return retained


def _event(store, identifier, event, details):
    store.append_event(identifier, event, actor="system", details=details)


def configure_project_continuation(*, workspace, project_key, root, project_config, request):
    """Confirm existing task identities and finite iteration/model-call limits."""
    from goodteam_os.project_import import read_bound_project_readiness
    from goodteam_os.project_readiness import build_capabilities
    from goodteam_os.team_control_plane import ensure_team_state
    from goodteam_os.project_outcome_discovery import authorization_digest, normalize_discovery

    if request.get("confirmed") is not True or request.get("billing_acknowledged") is not True:
        raise ValueError("Confirm automatic project continuation and configured provider charges.")
    key = request.get("idempotency_key")
    ids = request.get("task_ids")
    maximum = request.get("max_batches")
    discovery = normalize_discovery(request.get("discovery"))
    if not isinstance(key, str) or not 1 <= len(key) <= 256:
        raise ValueError("A stable continuation command identity is required.")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 128
            or any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids)):
        raise ValueError("Select 1–128 distinct existing project tasks.")
    if type(maximum) is not int or not 1 <= maximum <= 50:
        raise ValueError("Authorize between 1 and 50 project batches.")
    workspace, root, project_config = Path(workspace), Path(root), Path(project_config)
    store = TaskAssistantSessionStore(workspace, project_key)
    identity = sha256(key.encode()).hexdigest()[:32]
    with goal_guard(workspace, project_key):
        current = store.read().get(_FIELD) or {}
        if current.get("id") == identity:
            if (current.get("seed_task_ids", current.get("task_ids")) != ids or current.get("max_batches") != maximum
                    or current.get("discovery") != discovery
                    or current.get("approved_backlog_version") != request.get("expected_backlog_version")):
                raise ValueError("Continuation command was already used with different scope or limits.")
            return current
        pending = current.get("pending_batch")
        if pending:
            known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
            settled = all(known.get(identifier, {}).get("status") == "done"
                          and known[identifier].get("delivery_verified") for identifier in pending["task_ids"])
            admitted = any(set(batch.get("proposal_ids", [])) & set(pending["proposal_ids"])
                           for batch in store.read().get("project_task_batches", {}).values())
            unclaimed = (not pending.get("run_id") and not admitted
                         and all(known.get(identifier, {}).get("status") == "todo"
                                 and not known[identifier].get("run_id") for identifier in pending["task_ids"]))
            if settled or unclaimed:
                pending = None
        if current.get("status") == "active" or pending:
            raise ValueError("Stop the existing continuation and settle its current batch first.")
        if active_project_run(workspace, project_key):
            raise ValueError("Finish the active workflow before starting project continuation.")
        require_no_active_project_goal(workspace, project_key)
        try:
            ready = read_bound_project_readiness(workspace, project_key, root)
        except ValueError as exc:
            # Retain the existing Workflow/batch compatibility for configured
            # legacy projects. A present readiness record is never bypassed.
            if not str(exc).startswith("No readiness found for project:"):
                raise
        else:
            if not build_capabilities(ready)["start_workflow"]:
                raise ValueError("Complete project preparation before starting project continuation.")
        ensure_team_state(workspace, project_key, project_config)
        tasks = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
        version = rank_project_backlog(list(tasks.values()), project_key, limit=None)["version"]
        if request.get("expected_backlog_version") != version:
            raise ValueError("The displayed project backlog changed; review its current task scope before authorizing.")
        if any(identifier not in tasks or _internal(tasks[identifier])
               or tasks[identifier]["status"] not in {"todo", "done"} for identifier in ids):
            raise ValueError("Continuation scope must contain available project backlog tasks.")
        if discovery and not any(tasks[identifier]["status"] == "todo" for identifier in ids):
            raise ValueError("Begin outcome discovery with at least one existing seed task to deliver.")
        if discovery and any(tasks[identifier]["status"] == "done" and tasks[identifier].get("delivery_verified") is not True
                             for identifier in ids):
            raise ValueError("Discovery seed dependencies marked done need verified delivery evidence.")
        value = {
            "schema_version": "project_continuation.v1", "id": identity, "status": "active",
            "task_ids": ids, "task_intents": {identifier: _intent(tasks[identifier]) for identifier in ids},
            "approved_backlog_version": version,
            "max_batches": maximum, "max_manager_calls": maximum,
            "batches_started": 0, "manager_calls": 0, "pending_batch": None,
            "planning_backlog_version": "", "reason": "authorized",
            "authority": authority_snapshot(workspace, project_key, project_config, root),
            "created_at": datetime.now(UTC).isoformat(),
        }
        if discovery:
            value.update(discovery=discovery, seed_task_ids=list(ids), discovered_task_ids=[], new_tasks_created=0,
                         seed_task_intents={identifier: value["task_intents"][identifier] for identifier in ids},
                         discovery_pending=None, discovery_consumed=[], max_manager_calls=discovery["max_planning_calls"])
            value["authorization_digest"] = authorization_digest(value)
        _save(store, value)
        _event(store, f"project-continuation:{identity}", "project_continuation_authorized",
               {"task_ids": ids, "max_batches": maximum, "max_manager_calls": value["max_manager_calls"],
                **({"discovery": discovery, "authorization_digest": value["authorization_digest"]} if discovery else {})})
        return value


def stop_project_continuation(*, workspace, project_key, continuation_id):
    """Revoke future dispatch even if the project directory/config is unavailable."""
    store = TaskAssistantSessionStore(workspace, project_key)
    def stop(session):
        value = session.get(_FIELD) or {}
        if not value or value.get("id") != continuation_id:
            raise ValueError("Project continuation changed; reload before stopping it.")
        return {**session, _FIELD: {**value, "status": "stopped", "reason": "owner_stopped"}}
    value = store._mutate(stop)[_FIELD]
    _event(store, f"project-continuation-stop:{continuation_id}", "project_continuation_stopped", {})
    return value


def _scope_tasks(value, tasks):
    known = {task["id"]: task for task in tasks}
    selected = [known.get(identifier) for identifier in value["task_ids"]]
    if any(task is None or _intent(task) != value["task_intents"][task["id"]] for task in selected):
        raise ValueError("An authorized task's scope, dependencies or acceptance criteria changed.")
    return known, selected


def tick_project_continuation(*, workspace, project_key, root, project_config,
                              manager_runner=None, batch_runner=None):
    """One bounded automatic step; retries retain the same queued batch identity."""
    from goodteam_os.project_iteration_execution import run_queued_iteration_batch
    from goodteam_os.project_outcome_discovery import DiscoveryPublicationPending, discover_next_iteration

    workspace, root, project_config = Path(workspace), Path(root), Path(project_config)
    store = TaskAssistantSessionStore(workspace, project_key)
    with goal_guard(workspace, project_key):
        value = store.read().get(_FIELD) or {}
        if value.get("status") != "active":
            return value
        try:
            if value["authority"] != authority_snapshot(workspace, project_key, project_config, root):
                raise ValueError("Project configuration, Team or model settings changed; authorization is stale.")
            require_no_active_project_goal(workspace, project_key)
            if active_project_run(workspace, project_key):
                raise ValueError("Another Workflow owns the project; continuation is paused.")
            known, selected = _scope_tasks(value, LocalTaskStore(root, project_key).list_tasks())
            pending = value.get("pending_batch")
            if pending and pending.get("run_id"):
                batch_tasks = [known[identifier] for identifier in pending["task_ids"]]
                saved_batch = store.read().get("project_task_batches", {}).get(pending["run_id"], {})
                recovering = (saved_batch.get("status") in {"claimed", "running"}
                              and bool(saved_batch.get("current_recovery_attempt")))
                if any(task.get("run_id") not in {None, "", pending["run_id"]} for task in batch_tasks):
                    raise ValueError("The active project batch no longer owns its original tasks.")
                if all(task["status"] == "done" and task.get("delivery_verified") is True for task in batch_tasks):
                    value = _save(store, {**value, "pending_batch": None, "reason": "delivery_verified"})
                    pending = None
                elif (any(task["status"] == "canceled" for task in batch_tasks)
                      or not recovering and any(task["status"] == "blocked" for task in batch_tasks)):
                    raise ValueError("The current batch is blocked; inspect its retained execution result.")
                elif all(task["status"] == "review" for task in batch_tasks):
                    return value
            if (value.get("discovery") and pending is None and (value.get("discovery_pending")
                    or all(task["status"] == "done" and task.get("delivery_verified") is True for task in selected))):
                value = discover_next_iteration(workspace=workspace, project_key=project_key, root=root,
                    project_config=project_config, value=value, manager_runner=manager_runner)
                pending = value.get("pending_batch")
                if value["status"] != "active" or pending is None:
                    return value
            if pending is None and all(task["status"] == "done" for task in selected):
                return _save(store, {**value, "status": "completed", "reason": "authorized_tasks_delivered"})
            if pending is None:
                if (value["batches_started"] >= value["max_batches"]
                        or value["manager_calls"] >= value["max_manager_calls"] and not value.get("planning_backlog_version")):
                    return _save(store, {**value, "status": "exhausted", "reason": "approved_iteration_budget_used"})
                snapshot = project_iteration_backlog(root, project_key, limit=None)
                eligible = [task for task in snapshot.get("candidates", []) if task["eligible_to_plan"]]
                if not any(task["task_id"] in value["task_ids"] for task in eligible):
                    return _save(store, {**value, "status": "blocked", "reason": "no_authorized_task_is_ready"})
                # Reserve before the potentially billable Manager call. Its own
                # backlog-bound Worker identity makes a lost reply replayable.
                planning = value.get("planning_backlog_version")
                if planning and planning != snapshot["version"]:
                    raise ValueError("The backlog changed after a reserved planning attempt; inspect before resuming.")
                if not planning:
                    value = _save(store, {**value, "planning_backlog_version": snapshot["version"],
                                         "manager_calls": value["manager_calls"] + 1})
                if value["status"] != "active":
                    return value
                result = plan_next_iteration(
                    workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                    expected_backlog_version=snapshot["version"], confirmed=True,
                    allowed_task_ids=value["task_ids"],
                    **({"manager_runner": manager_runner} if manager_runner is not None else {}),
                )
                chosen = result["iteration_choice"]["selected_task_ids"]
                if (store.read().get(_FIELD) or {}).get("status") != "active":
                    return store.read()[_FIELD]
                if not set(chosen).issubset(value["task_ids"]):
                    raise ValueError("Manager selected work outside the approved task scope; no Worker started.")
                queued = queue_manager_iteration_choice(workspace=workspace, project_key=project_key, root=root,
                    expected_backlog_version=snapshot["version"], expected_task_ids=chosen,
                    expected_choice_event_id=result["iteration_choice"].get("source_event_id")
                    or f"iteration-choice:{snapshot['version']}")
                if queued["queued_task_ids"] != chosen:
                    raise ValueError("The queued task identities differ from the approved Manager choice.")
                proposals = {item["id"]: item for item in queued["task_assistant_session"]["queued_task_proposals"]}
                ids = queued["queued_proposal_ids"]
                pending = {"task_ids": chosen, "proposal_ids": ids,
                           "expected_revisions": {identifier: proposals[identifier]["revision"] for identifier in ids},
                           "run_id": ""}
                value = _save(store, {**value, "pending_batch": pending, "planning_backlog_version": "",
                                     "batches_started": value["batches_started"] + 1, "reason": "batch_reserved"})
            # The adapter rechecks readiness, methods, task claims and isolation.
            # This is the same path as a user-confirmed batch, not a new executor.
            if (store.read().get(_FIELD) or {}).get("status") != "active":
                return store.read()[_FIELD]
            if value["authority"] != authority_snapshot(workspace, project_key, project_config, root):
                raise ValueError("Project configuration changed during planning; no Worker was authorized.")
            queued_by_id = {item["id"]: item for item in store.read().get("queued_task_proposals", [])}
            if (not set(pending["task_ids"]).issubset(value["task_ids"])
                    or [queued_by_id.get(identifier, {}).get("project_task_id") for identifier in pending["proposal_ids"]]
                    != pending["task_ids"]):
                raise ValueError("The retained queue reservation is outside the approved task scope.")
            result = (batch_runner or run_queued_iteration_batch)(
                workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                proposal_ids=pending["proposal_ids"], expected_revisions=pending["expected_revisions"], confirmed=True,
                background=True, expected_authority=value["authority"], expected_continuation_id=value["id"],
            )
            if not result.get("run_id"):
                raise ValueError("The batch did not return its durable execution identity.")
            value = _save(store, {**value, "pending_batch": {**pending, "run_id": result["run_id"]},
                                 "reason": "awaiting_verified_delivery"})
            _event(store, f"project-continuation-batch:{value['id']}:{value['batches_started']}",
                   "project_continuation_batch_started", {"run_id": result["run_id"], "task_ids": pending["task_ids"]})
            return value
        except DiscoveryPublicationPending:
            # The response and creation identity are already durable. Replaying
            # this outbox does not spend another planning call or reset a card.
            current = store.read().get(_FIELD) or value
            return _save(store, {**current, "reason": "discovery_publication_pending"})
        except (OSError, ValueError, ManagerExecutionError) as exc:
            # An uncertain outcome is inspectable and cannot silently spend a
            # second attempt or choose another task under the same reservation.
            if value.get("discovery"):
                current = store.read().get(_FIELD) or {}
                if current.get("id") == value["id"]:
                    value = current
            return _save(store, {**value, "status": "blocked", "reason": str(exc)})


def tick_authorized_project_continuations(workspace):
    """Called by the existing continuation service; GET requests never advance."""
    from goodteam_os.web import _project_config_for_key, _project_target_and_root

    workspace = Path(workspace)
    for path in sorted((workspace / "state" / "projects").glob("*/task-assistant-session.json")):
        key = path.parent.name
        store = TaskAssistantSessionStore(workspace, key)
        try:
            value = store.read().get(_FIELD) or {}
            if value.get("status") != "active":
                continue
            key, _target, root = _project_target_and_root(workspace, key)
            tick_project_continuation(workspace=workspace, project_key=key, root=root,
                                      project_config=_project_config_for_key(workspace, key))
        except (OSError, ValueError):
            # Do not mint replacement project identities or discard approval
            # when registry/configuration/evidence cannot be read.
            continue


def _commit(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        raise ValueError("An exact candidate and landed Git commit are required.")
    return value


def _tree(root, commit):
    from goodteam_os.dispatch_workspace import _read_git

    result = {}
    for item in _read_git(root, ["ls-tree", "-r", "-z", "--full-tree", commit]).split(b"\0"):
        if item:
            entry, path = item.split(b"\t", 1)
            result[path] = entry
    return result


def _require_clean_checkout(root, label):
    from goodteam_os.dispatch_workspace import _metadata_path, _read_git

    dirty = _read_git(root, ["diff", "--name-only", "--no-ext-diff", "-z", "HEAD", "--"])
    dirty += _read_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if any(not _metadata_path(path.decode()) for path in dirty.split(b"\0") if path):
        raise ValueError(f"The {label} contains uncommitted edits; review and commit the exact delivery first.")


def _verify_landed_batch(*, workspace, project_key, root, run_id, candidate_commit, landed_commit):
    from goodteam_os.dispatch_workspace import (
        _git, _read_git, dispatch_artifact_root, verify_dispatch_result,
    )
    from goodteam_os.project_iteration_execution import iteration_batch_report
    from goodteam_os.project_iteration_quality import accepted_batch_quality

    candidate_commit, landed_commit = _commit(candidate_commit), _commit(landed_commit)
    report = iteration_batch_report(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    integration = report.get("integration") or {}
    if report.get("all_ok") is not True or integration.get("status") != "succeeded":
        raise ValueError("The exact integrated candidate is not available for delivery acceptance.")
    jobs = report.get("jobs") or []
    if not jobs or any(job.get("outcome") != "ok" for job in jobs):
        raise ValueError("Every task needs a successful retained Worker result before delivery review.")
    dispatch_id = report.get("run_id")
    for job in jobs:
        captured = verify_dispatch_result(root, {"dispatch_run_id": dispatch_id, **job})
        if captured.get("result_commit") != job.get("result_commit"):
            raise ValueError("A Worker result changed after candidate integration.")
    candidate = Path(str(integration.get("candidate_path") or "")).absolute()
    artifact = dispatch_artifact_root(root, dispatch_id)
    if candidate.resolve() != candidate or candidate.parent.parent != artifact or candidate.name != "candidate":
        raise ValueError("The candidate is outside the retained dispatch workspace.")
    if json.loads((candidate.parent / "integration.json").read_text()) != integration:
        raise ValueError("The integration receipt changed.")
    if _read_git(candidate, ["rev-parse", "HEAD"]).strip().decode() != integration["candidate_commit"]:
        raise ValueError("The candidate revision changed after review.")
    _require_clean_checkout(candidate, "candidate")
    quality = accepted_batch_quality(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    effective_commit = quality["candidate_commit"] if quality else integration["candidate_commit"]
    if candidate_commit != effective_commit:
        raise ValueError("Delivery requires the exact independently reviewed candidate.")
    if quality:
        # The quality resolver verifies the correction's original input,
        # protected host seal, ancestry and write scope. Never rewrite the
        # initial dispatch integration receipt to impersonate this new result.
        candidate = Path(quality["candidate_path"])
        _require_clean_checkout(candidate, "reviewed candidate")
    if _read_git(root, ["rev-parse", "HEAD"]).strip().decode() != landed_commit:
        raise ValueError("The project is not at the exact confirmed landed commit.")
    if _git(root, ["merge-base", "--is-ancestor", candidate_commit, landed_commit], check=False).returncode != 0:
        raise ValueError("The reviewed candidate has not landed in the project's current Git history.")
    _require_clean_checkout(root, "project")
    changed = _read_git(candidate, ["diff", "--name-only", "--no-renames", "-z",
                                    integration["baseline_commit"], candidate_commit]).split(b"\0")
    expected, actual = _tree(candidate, candidate_commit), _tree(root, landed_commit)
    if any(expected.get(path) != actual.get(path) for path in changed if path):
        raise ValueError("The landed project content differs from the reviewed candidate.")
    return report, quality


def _record_delivery(store, run_id, receipt, task_ids):
    def settle(session):
        batch = session.get("project_task_batches", {}).get(run_id)
        if batch is not None:
            batch.update(status="delivered", delivery_receipt=receipt)
        for proposal in session.get("queued_task_proposals", []):
            if proposal.get("run_id") == run_id:
                proposal["status"] = "done"
        value = session.get(_FIELD)
        if value and (value.get("pending_batch") or {}).get("run_id") == run_id:
            # Settlement does not reactivate a stopped authorization.
            value.update(pending_batch=None, reason="delivery_verified")
        return session
    store._mutate(settle)
    _event(store, f"project-delivery:{receipt['receipt_id']}", "project_batch_delivery_verified",
           {**receipt, "task_ids": task_ids})
    return {"delivery": receipt, "task_ids": task_ids, "project_continuation": store.read().get(_FIELD)}


def accept_project_batch_delivery(*, workspace, project_key, root, request):
    """Human review plus observed landed Git evidence; never a model assertion."""
    if request.get("confirmed") is not True or request.get("reviewed") is not True:
        raise ValueError("Confirm independent review of the exact candidate and its verification evidence.")
    run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("task-batch-"):
        raise ValueError("An existing project batch identity is required.")
    workspace, root = Path(workspace), Path(root)
    store = TaskAssistantSessionStore(workspace, project_key)
    tasks = LocalTaskStore(root, project_key)
    with goal_guard(workspace, project_key):
        batch = store.read().get("project_task_batches", {}).get(run_id)
        if not batch or batch.get("project_root") != str(root.resolve()) or batch.get("status") == "abandoned":
            raise ValueError("The delivery does not belong to this project batch.")
        receipt = {"producer": "human_confirmed_candidate_delivery", "run_id": run_id,
                   "dispatch_run_id": batch.get("current_dispatch_run_id", batch["dispatch_run_id"]), "candidate_commit": _commit(request.get("candidate_commit")),
                   "landed_commit": _commit(request.get("landed_commit")), "reviewed": True,
                   "verification": "landed_git_ancestor_and_content"}
        receipt["receipt_id"] = _digest(receipt)
        with tasks._lock():
            before = [task for task in tasks._read()["tasks"] if task.get("run_id") == run_id]
            # A historical quality receipt remains the same receipt after a
            # later batch advances HEAD, without re-running old quality work.
            previous_quality = (before[0].get("delivery_receipt") or {}).get("quality") if before else None
            if previous_quality:
                receipt["quality"] = previous_quality
                receipt["receipt_id"] = _digest({key: value for key, value in receipt.items() if key != "receipt_id"})
            replay = (before and len(before) == len(batch["task_ids"])
                      and all(task["status"] == "done" and task.get("delivery_verified") is True
                              and task.get("delivery_receipt") == receipt for task in before))
            originals = {item["task_id"]: item.get("task_intent") for item in batch["task_bindings"]}
            if not replay and (not before or len(before) != len(originals)
                    or any({key: task.get(key) for key in _INTENT_FIELDS} != originals.get(task["id"])
                           for task in before)):
                raise ValueError("The original task scope changed after batch confirmation.")
            expected_tasks = {task["id"]: task_evidence_digest(task) for task in before}
        if replay:
            # Finish a potentially interrupted session projection without
            # requiring HEAD to remain at a historical accepted revision.
            return {**_record_delivery(store, run_id, receipt, [task["id"] for task in before]), "reused": True}
        report, quality = _verify_landed_batch(
            workspace=workspace, project_key=project_key, root=root, run_id=run_id,
            candidate_commit=request.get("candidate_commit"), landed_commit=request.get("landed_commit"),
        )
        if quality:
            receipt["quality"] = {key: quality[key] for key in (
                "input_digest", "execution_id", "validation_receipt_id", "review_receipt_id", "candidate_commit")}
            receipt["receipt_id"] = _digest({key: value for key, value in receipt.items() if key != "receipt_id"})
        with tasks._lock():
            from goodteam_os.dispatch_workspace import _read_git

            if _read_git(root, ["rev-parse", "HEAD"]).strip().decode() != receipt["landed_commit"]:
                raise ValueError("The project revision changed during delivery verification.")
            ledger = tasks._read()
            matched = [task for task in ledger["tasks"] if task.get("run_id") == run_id]
            jobs = {job["job_id"]: job for job in report["jobs"]}
            if (not matched or len(matched) != len(jobs)
                    or {task.get("dispatch_job_id") for task in matched} != set(jobs)
                    or any(task.get("execution_origin") != "task_assistant_batch"
                           or task.get("dispatch_run_id") != jobs[task["dispatch_job_id"]].get("dispatch_run_id", report["run_id"])
                           or jobs[task["dispatch_job_id"]].get("project_task_id") != task["id"]
                           or task_evidence_digest(task) != expected_tasks.get(task["id"])
                           or task["status"] not in {"review", "done"} for task in matched)):
                raise ValueError("The delivery result does not match every original project task.")
            for task in matched:
                if task["status"] == "done" and task.get("delivery_receipt") != receipt:
                    raise ValueError("This task already has a different delivery receipt.")
                task.update(status="done", delivery_verified=True, delivery_receipt=receipt, updated_at=tasks._now())
            tasks._write(ledger)
        # The existing service observes these same done cards and automatically
        # replans. The request itself does not launch or approve another run.
        return _record_delivery(store, run_id, receipt, [task["id"] for task in matched])
