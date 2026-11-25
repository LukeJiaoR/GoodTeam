"""[INPUT]: 项目续跑设置、待办、Worker 结果、接受凭据和 Git 内容。
[OUTPUT]: 续跑状态及绑定已接受候选和落地提交的交付凭据。
[POS]: 复用任务队列。按项目路径纳入待办，核对候选与 Git 落地。
[PROTOCOL]: Update this header on change, then check AGENTS.md

Bounded project continuation through the existing Assistant queue and dispatch.

The Assistant session retains the owner's scope/budget and pending queue IDs.
LocalTaskStore remains task truth; dispatch receipts remain execution truth.
Delivery records both the acceptance source and the observed Git result.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from goodteam_os.assistant_queue import queue_manager_iteration_choice
from goodteam_os.goal_continuation import (
    authority_snapshot, goal_guard, notify_continuation_environment,
    notify_continuation_environment_unavailable, same_continuation_project,
)
from goodteam_os.manager_execution import ManagerExecutionError, ManagerResourceBusy
from goodteam_os.project_iteration import (
    _internal, normalize_write_domains, project_iteration_backlog, rank_project_backlog, write_domains_within_scope,
)
from goodteam_os.project_iteration_planner import (
    plan_next_iteration, ManagerPlanningBudgetExhausted, ManagerPlanningActivationChanged,
)
from goodteam_os.state_documents import DocumentLockBusy
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, project_task_intent, task_evidence_digest


_FIELD = "project_continuation"
_DELIVERY_VERIFICATION = "landed_git_content"
DELIVERY_VERIFICATIONS = (_DELIVERY_VERIFICATION, "landed_git_ancestor_and_content")
DELIVERY_PRODUCERS = ("human_confirmed_candidate_delivery", "independently_reviewed_candidate_delivery",
                      "manager_accepted_candidate_delivery")


class DeliveryNotLanded(ValueError):
    """当前 Git 内容尚不能证明候选已落地。"""


def _digest(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _intent(task):
    return _digest(project_task_intent(task))


def _save(store, value):
    retained = dict(value)
    def write(session):
        current = session.get(_FIELD) or {}
        if current.get("id") == value.get("id"):
            # Answers use the short session lock while planning holds the
            # continuation guard. A whole-grant checkpoint cannot overwrite them.
            if "human_questions" in current:
                retained["human_questions"] = current["human_questions"]
            if current.get("status") == "stopped":
                retained.update(status="stopped", reason="owner_stopped")
            elif current.get("status") == "active" and retained.get("status") == "completed":
                from goodteam_os.project_human_feedback import human_feedback

                if any(item["receipt_id"] not in retained.get("discovery_consumed", [])
                       for item in human_feedback(current)):
                    retained.update(status="active", reason="human_answer_received")
        if current.get("id") != value.get("id") and current.get("human_questions"):
            # Retain the original grant as history in the same session. Its
            # questions stay visible without carrying its authority forward.
            session.setdefault("project_continuation_history", {})[current["id"]] = current
        return {**session, _FIELD: retained}
    store._mutate(write)
    return retained


def _event(store, identifier, event, details):
    store.append_event(identifier, event, actor="system", details=details)


def pending_project_batch(session, root, project_key):
    """Keep uncertain/admitted work; release only settled or unclaimed intent."""
    value = session.get(_FIELD) or {}
    known = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
    from goodteam_os.project_parallel_dispatch import select_pending
    pending = select_pending(session, value, known)
    if not pending:
        return None
    settled = all(known.get(identifier, {}).get("status") == "done"
                  and known[identifier].get("delivery_verified") for identifier in pending["task_ids"])
    admitted = any(set(batch.get("proposal_ids", [])) & set(pending["proposal_ids"])
                   for batch in session.get("project_task_batches", {}).values())
    unclaimed = (not pending.get("run_id") and not admitted
                 and all(known.get(identifier, {}).get("status") == "todo"
                         and not known[identifier].get("run_id") for identifier in pending["task_ids"]))
    return None if settled or unclaimed else pending


def configure_project_continuation(*, workspace, project_key, root, project_config, request):
    """保存项目续跑范围、初始任务和可选用量上限。"""
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
    project_scope = bool(discovery and discovery.get("use_project_backlog"))
    if (not isinstance(ids, list) or not ids or not project_scope and len(ids) > 128
            or any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids)):
        raise ValueError("Supply distinct existing tasks. Fixed-list continuation supports up to 128 tasks.")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise ValueError("The optional project batch limit must be a positive integer.")
    workspace, root, project_config = Path(workspace), Path(root), Path(project_config)
    store = TaskAssistantSessionStore(workspace, project_key)
    identity = sha256(key.encode()).hexdigest()[:32]
    from goodteam_os.project_batch_lock import project_batch_guard

    with goal_guard(workspace, project_key), project_batch_guard(workspace, project_key):
        session = store.read()
        current = session.get(_FIELD) or {}
        original = current if current.get("id") == identity else (session.get("project_continuation_history") or {}).get(identity)
        if original:
            if (original.get("requested_task_ids", original.get("seed_task_ids", original.get("task_ids"))) != ids or original.get("max_batches") != maximum
                    or original.get("discovery") != discovery
                    or original.get("approved_backlog_version") != request.get("expected_backlog_version")):
                raise ValueError("Continuation command was already used with different scope or limits.")
            return original
        pending = pending_project_batch(session, root, project_key)
        if current.get("status") == "active" or pending:
            raise ValueError("Stop the existing continuation and settle its current batch first.")
        ensure_team_state(workspace, project_key, project_config)
        tasks = {task["id"]: task for task in LocalTaskStore(root, project_key).list_tasks()}
        version = rank_project_backlog(list(tasks.values()), project_key, limit=None)["version"]
        if request.get("expected_backlog_version") != version:
            raise ValueError("The displayed project backlog changed; review its current task scope before authorizing.")
        permitted_statuses = {"todo", "done", "blocked"} if discovery and discovery.get("allow_backlog_judgment") is True else {"todo", "done"}
        if any(identifier not in tasks or _internal(tasks[identifier])
               or tasks[identifier]["status"] not in permitted_statuses for identifier in ids):
            raise ValueError("Continuation scope must contain available project backlog tasks.")
        if discovery and not any(tasks[identifier]["status"] in (
                {"todo", "blocked"} if discovery.get("allow_backlog_judgment") is True else {"todo"}) for identifier in ids):
            raise ValueError("Begin outcome discovery with at least one existing seed task to deliver.")
        if discovery and any(tasks[identifier]["status"] == "done" and tasks[identifier].get("delivery_verified") is not True
                             for identifier in ids):
            raise ValueError("Discovery seed dependencies marked done need verified delivery evidence.")
        if project_scope:
            ids_in_scope = [identifier for identifier in ids if _within_project_paths(tasks[identifier], discovery)]
            if not any(tasks[identifier]["status"] != "done" for identifier in ids_in_scope):
                raise ValueError("Start with an existing read-only task or a task within the project write paths.")
        else:
            ids_in_scope = ids
        value = {
            "schema_version": "project_continuation.v1", "id": identity, "status": "active",
            "task_ids": ids_in_scope, "task_intents": {identifier: _intent(tasks[identifier]) for identifier in ids_in_scope},
            "approved_backlog_version": version,
            "max_batches": maximum, "max_manager_calls": maximum,
            "batches_started": 0, "manager_calls": 0, "pending_batch": None,
            "planning_backlog_version": "", "reason": "authorized",
            "authority": authority_snapshot(workspace, project_key, project_config, root),
            "created_at": datetime.now(UTC).isoformat(),
        }
        if discovery:
            value.update(discovery=discovery, seed_task_ids=list(ids_in_scope), discovered_task_ids=[], new_tasks_created=0,
                         seed_task_intents=dict(value["task_intents"]),
                         discovery_pending=None, discovery_consumed=[], max_manager_calls=discovery["max_planning_calls"])
            if project_scope:
                value["requested_task_ids"] = ids
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
    from goodteam_os.manager_app_server import stop_background_work
    # Revocation is durable before native interruption; never hold the session
    # lock while waiting for a provider acknowledgement.
    stop_background_work(workspace, project_key)
    return value


def _scope_tasks(value, tasks):
    known = {task["id"]: task for task in tasks}
    selected = [known.get(identifier) for identifier in value["task_ids"]]
    if any(task is None or _intent(task) != value["task_intents"][task["id"]] for task in selected):
        raise ValueError("An authorized task's scope, dependencies or acceptance criteria changed.")
    return known, selected


def _within_project_paths(task, discovery):
    if task.get("execution_policy") == "read_only":
        return True
    domains = normalize_write_domains(task.get("write_domains"))
    return bool(domains and write_domains_within_scope(domains, discovery["allowed_write_domains"]))


def _refresh_project_backlog(store, value, tasks):
    """将项目路径内的新待办交给 Manager 排序，保留已绑定的任务版本。"""
    discovery = value.get("discovery") or {}
    if (discovery.get("use_project_backlog") is not True or value.get("planning_backlog_version")
            or value.get("discovery_pending")):
        return value
    intents = value["task_intents"]
    added = {task["id"]: _intent(task) for task in tasks
             if task["id"] not in intents and task["status"] == "todo" and not task.get("run_id")
             and not task.get("goal_id") and not _internal(task) and _within_project_paths(task, discovery)}
    if not added:
        return value
    return _save(store, {**value, "task_ids": [*value["task_ids"], *added],
                         "task_intents": {**intents, **added}})


def tick_project_continuation(*, workspace, project_key, root, project_config,
                              manager_runner=None, batch_runner=None, observation_runner=None):
    """One bounded automatic step; retries retain the same queued batch identity."""
    from goodteam_os.project_iteration_execution import run_queued_iteration_batch, ProjectBatchResourceBusy
    from goodteam_os.project_outcome_discovery import DiscoveryPublicationPending, discover_next_iteration
    from goodteam_os.project_batch_feedback import can_observe_initial_readonly, can_observe_pending_write_review, can_observe_discovered_readonly, observe_pending_batch
    from goodteam_os.project_investigation_feedback import is_observed_investigation, observe_pending_investigation
    from goodteam_os.project_human_feedback import human_feedback

    workspace, root, project_config = Path(workspace), Path(root), Path(project_config)
    store = TaskAssistantSessionStore(workspace, project_key)
    with goal_guard(workspace, project_key):
        value = store.read().get(_FIELD) or {}
        if value.get("status") != "active":
            return value
        try:
            if not same_continuation_project(value["authority"], project_config, root):
                raise ValueError("The original continuation project source changed.")
            try:
                environment = authority_snapshot(workspace, project_key, project_config, root)
            except (OSError, ValueError):
                notify_continuation_environment_unavailable(store, value["id"])
            else:
                notify_continuation_environment(store, value["id"], value["authority"], environment)
            tasks = LocalTaskStore(root, project_key).list_tasks()
            value = _refresh_project_backlog(store, value, tasks)
            if value.get("status") != "active":
                return value
            known, selected = _scope_tasks(value, tasks)
            from goodteam_os.project_parallel_dispatch import select_pending, ready_parallel_tasks
            session = store.read()
            if ((value.get("discovery") or {}).get("use_project_backlog")
                    and not value.get("planning_backlog_version") and not value.get("discovery_pending")):
                for batch in session.get("project_task_batches", {}).values():
                    if (batch.get("expected_continuation_id") == value["id"] and batch.get("status") == "review"
                            and (batch.get("manager_acceptance") or
                                 (batch.get("quality") or {}).get("acceptance_status") == "accepted_by_review_policy")):
                        from goodteam_os.models import _parse_simple_yaml

                        branch = _parse_simple_yaml(project_config).get("local_delivery_branch")
                        delivered = _reconcile_accepted_delivery(workspace, project_key, root, batch, branch=branch)
                        if delivered is not None:
                            return delivered["project_continuation"]
            pending = select_pending(session, value, known)
            if pending != value.get("pending_batch"):
                value = _save(store, {**value, "pending_batch": pending})
            parallel_planning = bool(value.get("planning_backlog_version") and value.get("planning_input_version") == 5)
            parallel_eligible = value.get("planning_eligible_task_ids") if parallel_planning else None
            if parallel_planning:
                if (not isinstance(parallel_eligible, list) or not parallel_eligible
                        or not set(parallel_eligible).issubset(value["task_ids"])):
                    raise ValueError("The original concurrent planning inventory is unavailable.")
                # Reconcile the reserved call before moving its observation
                # cursor or consuming newly arrived sibling feedback.
                pending = None
            if pending and pending.get("run_id"):
                batch_tasks = [known[identifier] for identifier in pending["task_ids"]]
                if any(task["status"] == "canceled" for task in batch_tasks):
                    raise ValueError("The current batch was canceled; no automatic follow-up is authorized.")
                saved_batch = store.read().get("project_task_batches", {}).get(pending["run_id"], {})
                from goodteam_os.project_running_observation import observe_running_batch
                observed_value = observe_running_batch(store=store, root=root, project_key=project_key, value=value)
                if (observed_value.get("status") != "active" or observed_value.get("id") != value.get("id")
                        or observed_value.get("authority") != value.get("authority")
                        or observed_value.get("pending_batch") != pending):
                    return observed_value
                from goodteam_os.project_observation_judgment import judge_running_observation
                def authority_check():
                    return same_continuation_project(observed_value["authority"], project_config, root)
                if observation_runner is None:
                    from goodteam_os.project_observation_runtime import judge_with_production_runtime
                    judged = judge_with_production_runtime(store=store, root=root, project_key=project_key,
                        project_config=project_config, value=observed_value, authority_check=authority_check)
                else:
                    judged = judge_running_observation(store=store, root=root, value=observed_value,
                        runner=observation_runner, authority_check=authority_check)
                if (judged.get("status") != "active" or judged.get("id") != observed_value.get("id")
                        or judged.get("pending_batch") != pending):
                    return judged
                from goodteam_os.project_worker_follow_up import consume_worker_follow_up
                value = consume_worker_follow_up(store=store, root=root, project_key=project_key,
                    value=judged, authority_check=authority_check)
                if value.get("status") != "active" or value.get("pending_batch") != pending:
                    return value
                latest_batch = store.read().get("project_task_batches", {}).get(pending["run_id"], {})
                if ((latest_batch.get("current_dispatch_run_id") or latest_batch.get("dispatch_run_id"))
                        != (saved_batch.get("current_dispatch_run_id") or saved_batch.get("dispatch_run_id"))
                        or latest_batch.get("status") != saved_batch.get("status")):
                    return value
                recovering = (saved_batch.get("status") in {"claimed", "running"}
                              and bool(saved_batch.get("current_recovery_attempt")))
                if any(task.get("run_id") not in {None, "", pending["run_id"]} for task in batch_tasks):
                    raise ValueError("The active project batch no longer owns its original tasks.")
                if all(task["status"] == "done" and task.get("delivery_verified") is True for task in batch_tasks):
                    value = _save(store, {**value, "pending_batch": None, "reason": "delivery_verified"})
                    pending = None
                elif any(task["status"] == "canceled" for task in batch_tasks):
                    raise ValueError("The current batch was canceled; no automatic follow-up is authorized.")
                elif not recovering and all(task["status"] in {"review", "blocked"} for task in batch_tasks):
                    candidate_feedback = ((value.get("discovery") or {}).get("use_project_backlog")
                                          and saved_batch.get("candidate_source"))
                    observed = None if candidate_feedback else observe_pending_investigation(workspace=workspace, project_key=project_key,
                        root=root, project_config=project_config, value=value)
                    if observed is None and (candidate_feedback or any(task["status"] == "blocked" for task in batch_tasks)
                            or can_observe_discovered_readonly(value, batch_tasks)
                            or can_observe_initial_readonly(value, batch_tasks)
                            or can_observe_pending_write_review(value, batch_tasks)):
                        observed = observe_pending_batch(workspace=workspace, project_key=project_key,
                            root=root, project_config=project_config, value=value)
                    if observed is None:
                        if any(task["status"] == "blocked" for task in batch_tasks):
                            raise ValueError("The current batch is blocked; inspect its retained execution result.")
                    else:
                        value, pending = observed, None
                elif not recovering and any(task["status"] == "blocked" for task in batch_tasks):
                    raise ValueError("The current batch is blocked; inspect its retained execution result.")
            if (pending and pending.get("run_id") and value.get("discovery")
                    and not value.get("planning_backlog_version") and not value.get("discovery_pending")
                    and not any(record.get("status") in {"reserved", "unconfirmed"}
                                for record in value.get("running_judgments", {}).values())
                    and any(answer["receipt_id"] not in value.get("discovery_consumed", [])
                            and value["human_questions"][answer["question_id"]]["planning"].get("source_kind") != "running_observation"
                            for answer in human_feedback(value))):
                # An answer to the original discovery question must reach
                # Manager while independent Workers keep running. This moves
                # only the local observation cursor; original batches remain
                # in the ledger and all source/authority checks stay in discovery.
                pending = None
            if pending and pending.get("run_id"):
                parallel_eligible = (value.get("planning_eligible_task_ids") if value.get("planning_backlog_version")
                    else ready_parallel_tasks(workspace, project_key, root, store.read(), value, known))
            if (pending and pending.get("run_id") and (value.get("planning_backlog_version") or parallel_eligible)):
                # pending_batch is only the current observation/reservation cursor;
                # original project_task_batches retains every in-flight identity.
                pending = None
                parallel_planning = True
            if pending and pending.get("run_id") and saved_batch.get("status") in {"review", "blocked"}:
                return value
            ready_authorized = any(task["eligible_to_plan"] and task["task_id"] in value["task_ids"]
                for task in rank_project_backlog(list(known.values()), project_key, limit=None).get("candidates", []))
            if (value.get("discovery") and pending is None and not parallel_planning and (value.get("discovery_pending")
                    or value["discovery"].get("allow_backlog_judgment") is True and not ready_authorized
                    or value.get("investigation_pending") or value.get("batch_feedback_pending") or value.get("reason") == "awaiting_human_decision" and not ready_authorized
                    or any(item["receipt_id"] not in value.get("discovery_consumed", []) for item in human_feedback(value))
                    or all((task["status"] == "done" and task.get("delivery_verified") is True)
                           or is_observed_investigation(value, task) for task in selected))):
                value = discover_next_iteration(workspace=workspace, project_key=project_key, root=root,
                    project_config=project_config, value=value, manager_runner=manager_runner)
                pending = value.get("pending_batch")
                if value["status"] != "active" or pending is None:
                    return value
            if pending is None and all(task["status"] == "done" for task in selected):
                return _save(store, {**value, "status": "completed", "reason": "authorized_tasks_delivered"})
            if pending is None:
                if (value["max_batches"] is not None and value["batches_started"] >= value["max_batches"]
                        or value["max_manager_calls"] is not None and value["manager_calls"] >= value["max_manager_calls"]
                        and not value.get("planning_backlog_version") and not value.get("waiting_attempt_id")):
                    return _save(store, {**value, "status": "exhausted", "reason": "approved_iteration_budget_used"})
                from goodteam_os.manager_execution import manager_resource_slot

                with manager_resource_slot(root) as resource_slot:
                    snapshot = project_iteration_backlog(root, project_key, limit=None)
                    eligible = [task for task in snapshot.get("candidates", []) if task["eligible_to_plan"]]
                    if (value.get("planning_backlog_version") and value.get("planning_input_version") == 2
                            and not any(task["task_id"] in value["task_ids"] for task in eligible)):
                        return _save(store, {**value, "status": "blocked", "reason": "no_authorized_task_is_ready"})
                    # Reserve before the potentially billable Manager call. Its own
                    # backlog-bound Worker identity makes a lost reply replayable.
                    planning = value.get("planning_backlog_version")
                    if planning and planning != snapshot["version"]:
                        raise ValueError("The backlog changed after a reserved planning attempt; inspect before resuming.")
                    if planning and value.get("planning_input_version") not in {2, 3, 4, 5}:
                        raise ValueError("An older reserved planning attempt must be reconciled before continuing.")
                    planned_call = value["manager_calls"] + (0 if planning else 1)
                    def reserve_planning():
                        nonlocal value
                        # The planner now owns the conversation lock. Recheck
                        # Stop even when replaying an already reserved attempt.
                        if not same_continuation_project(value["authority"], project_config, root):
                            raise ValueError("The original project source changed before planning.")
                        def reserve(session):
                            current = session.get(_FIELD) or {}
                            if (current.get("id") != value["id"] or current.get("status") != "active"
                                    or current.get("authority") != value["authority"]):
                                raise ValueError("Continuation stopped or changed before planning.")
                            if (current.get("planning_backlog_version") != value.get("planning_backlog_version")
                                    or current.get("manager_calls") != value["manager_calls"]):
                                raise ValueError("Planning reservation changed before invocation.")
                            if not planning:
                                if current["max_manager_calls"] is not None and current["manager_calls"] >= current["max_manager_calls"]:
                                    raise ManagerPlanningBudgetExhausted("Approved Manager planning budget is exhausted.")
                                current.update(planning_backlog_version=snapshot["version"],
                                               planning_input_version=5 if parallel_planning else 4, manager_calls=planned_call)
                                if parallel_planning:
                                    current["planning_eligible_task_ids"] = list(parallel_eligible)
                                else:
                                    current.pop("planning_eligible_task_ids", None)
                            return session
                        value = store._mutate(reserve)[_FIELD]
                    def validate_planning_authority():
                        current = store.read().get(_FIELD) or {}
                        if (current.get("id") != value["id"] or current.get("status") != "active"
                                or current.get("authority") != value["authority"]
                                or not same_continuation_project(value["authority"], project_config, root)):
                            raise ManagerPlanningActivationChanged("Continuation stopped or authorization changed during planning.")
                    def settle_stale_planning(evidence):
                        # Retain the completed decision and settle its original
                        # reservation together in the existing session timeline.
                        validate_planning_authority()
                        identifier = evidence["choice"]["source_event_id"] + ":stale"
                        event = {"message_id": identifier, "actor": "system", "event": "project_iteration_choice_superseded",
                                 "run_id": "", "content": "Completed planning input changed; no task dispatched.",
                                 "details": evidence}
                        def settle(session):
                            current = session.get(_FIELD) or {}
                            if (current.get("id") != value["id"] or current.get("status") != "active"
                                    or current.get("authority") != value["authority"]):
                                raise ManagerPlanningActivationChanged("Continuation stopped or changed during planning.")
                            if (current.get("planning_backlog_version") != snapshot["version"]
                                    or current.get("manager_calls") != planned_call
                                    or current.get("planning_input_version") != value.get("planning_input_version")):
                                raise ValueError("The original planning reservation changed before settlement.")
                            _scope_tasks(current, LocalTaskStore(root, project_key).list_tasks())
                            timeline = session.setdefault("timeline", [])
                            if any(item.get("message_id") == identifier for item in timeline):
                                raise ValueError("This planning settlement was already recorded.")
                            timeline.append({**event, "created_at": datetime.now(UTC).isoformat()})
                            current.update(planning_backlog_version="", waiting_attempt_id="", reason="planning_context_changed")
                            return session
                        try:
                            store._mutate(settle)
                        except OSError:
                            saved = store.read()
                            record = next((item for item in saved.get("timeline", []) if item.get("message_id") == identifier), None)
                            current = saved.get(_FIELD) or {}
                            if (not record or any(record.get(key) != item for key, item in event.items())
                                    or current.get("id") != value["id"] or current.get("planning_backlog_version")
                                    or current.get("manager_calls") != planned_call):
                                raise

                    result = plan_next_iteration(
                        workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                        expected_backlog_version=snapshot["version"], confirmed=True,
                        allowed_task_ids=value["task_ids"],
                        **({"concurrent_continuation_id": value["id"], "eligible_task_ids": parallel_eligible} if parallel_planning else {}),
                        allow_wait=not planning or value.get("planning_input_version") in {3, 4, 5},
                        include_question_context=not planning or value.get("planning_input_version") in {4, 5},
                        completed_wait_attempt_id=value.get("waiting_attempt_id", "") if not planning else "",
                        validate_authority=validate_planning_authority, on_stale_result=settle_stale_planning,
                        resource_slot=resource_slot, reserve_planning=reserve_planning,
                        planning_attempt_id=f"{value['id']}:{planned_call}",
                        **({"manager_runner": manager_runner} if manager_runner is not None else {}),
                    )
                if result.get("context_changed"):
                    return store.read().get(_FIELD) or value
                chosen = result["iteration_choice"]["selected_task_ids"]
                if not chosen:
                    current = store.read().get(_FIELD) or value
                    if current.get("status") != "active" or current.get("id") != value["id"]:
                        return current
                    validate_planning_authority()
                    return _save(store, {**current, "planning_backlog_version": "",
                                         "waiting_attempt_id": result["iteration_choice"].get("planning_attempt_id", ""),
                                         "reason": "awaiting_project_context"})
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
            if not same_continuation_project(value["authority"], project_config, root):
                raise ValueError("The original project source changed during planning.")
            queued_by_id = {item["id"]: item for item in store.read().get("queued_task_proposals", [])}
            if (not set(pending["task_ids"]).issubset(value["task_ids"])
                    or [queued_by_id.get(identifier, {}).get("project_task_id") for identifier in pending["proposal_ids"]]
                    != pending["task_ids"]):
                raise ValueError("The retained queue reservation is outside the approved task scope.")
            new_reservation = not pending.get("run_id")
            result = (batch_runner or run_queued_iteration_batch)(
                workspace=workspace, project_key=project_key, root=root, project_config=project_config,
                proposal_ids=pending["proposal_ids"], expected_revisions=pending["expected_revisions"], confirmed=True,
                background=True, expected_authority=value["authority"], expected_continuation_id=value["id"],
            )
            if not result.get("run_id"):
                raise ValueError("The batch did not return its durable execution identity.")
            value = _save(store, {**value, "pending_batch": {**pending, "run_id": result["run_id"]},
                                 "reason": "awaiting_verified_delivery"})
            if new_reservation:
                _event(store, f"project-continuation-batch:{value['id']}:{value['batches_started']}",
                       "project_continuation_batch_started", {"run_id": result["run_id"], "task_ids": pending["task_ids"]})
            return value
        except ProjectBatchResourceBusy:
            # The original queue reservation has not claimed or started work.
            # Retry that exact reservation after the occupied domain is free;
            # no new Manager call, budget charge, or alternative task selection.
            def wait_for_resources(session):
                current = session.get(_FIELD) or {}
                if (current.get("id") == value.get("id") and current.get("status") == "active"
                        and current.get("authority") == value.get("authority")
                        and current.get("pending_batch") == value.get("pending_batch")):
                    current["reason"] = "awaiting_batch_resources"
                return session
            return store._mutate(wait_for_resources).get(_FIELD) or {}
        except ManagerPlanningActivationChanged as exc:
            current = store.read().get(_FIELD) or value
            if current.get("id") != value["id"] or current.get("status") != "active":
                return current
            return _save(store, {**current, "status": "blocked", "reason": str(exc)})
        except ManagerPlanningBudgetExhausted:
            current = store.read().get(_FIELD) or value
            return _save(store, {**current, "status": "exhausted", "reason": "approved_iteration_budget_used"})
        except DiscoveryPublicationPending:
            # The response and creation identity are already durable. Replaying
            # this outbox does not spend another planning call or reset a card.
            current = store.read().get(_FIELD) or value
            return _save(store, {**current, "reason": "discovery_publication_pending"})
        except (ManagerResourceBusy, DocumentLockBusy):
            # Slot contention happens before reservation/provider entry. Keep
            # prior uncertain reservations intact; never refund a spent call.
            current = store.read().get(_FIELD) or value
            return _save(store, {**current, "reason": "manager_resource_busy"})
        except (OSError, ValueError, ManagerExecutionError) as exc:
            # An uncertain outcome is inspectable and cannot silently spend a
            # second attempt or choose another task under the same reservation.
            current = store.read().get(_FIELD) or {}
            if current.get("id") != value.get("id"):
                return current  # A late old failure cannot replace a newer grant.
            if value.get("discovery"):
                value = current
            return _save(store, {**value, "status": "blocked", "reason": str(exc)})


def tick_authorized_project_continuations(workspace):
    """Called by the existing continuation service; GET requests never advance."""
    from goodteam_os.web import _project_config_for_key, _project_target_and_root

    workspace = Path(workspace)
    from goodteam_os.state_documents import paths

    for path in paths(workspace / "state" / "projects", "*/task-assistant-session.json"):
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
    for item in _read_git(root, ["ls-tree", "-r", "-t", "-z", "--full-tree", commit]).split(b"\0"):
        if item:
            entry, path = item.split(b"\t", 1)
            result[path] = entry
    return result


def _require_clean_checkout(root, label, *, paths=None):
    from goodteam_os.dispatch_workspace import _metadata_path, _read_git

    if paths is not None and not paths:
        return
    pathspecs = [":(top,literal)" + os.fsdecode(path) for path in paths or ()]
    status = _read_git(root, ["status", "--porcelain=v1", "--untracked-files=all",
        "--no-renames", "-z", "--", *pathspecs])
    if any(not _metadata_path(os.fsdecode(entry[3:])) for entry in status.split(b"\0") if entry):
        raise DeliveryNotLanded(f"The {label} contains uncommitted edits; review and commit the exact delivery first.")


def _delivery_candidate(*, workspace, project_key, root, run_id, candidate_commit):
    from goodteam_os.dispatch_workspace import _read_git, dispatch_artifact_root, verify_dispatch_results
    from goodteam_os.project_iteration_execution import iteration_batch_report
    from goodteam_os.project_iteration_quality import accepted_batch_quality

    candidate_commit = _commit(candidate_commit)
    report = iteration_batch_report(workspace=workspace, project_key=project_key, root=root, run_id=run_id)
    integration = report.get("integration") or {}
    if report.get("all_ok") is not True or integration.get("status") != "succeeded":
        raise ValueError("The exact integrated candidate is not available for delivery acceptance.")
    jobs = report.get("jobs") or []
    if not jobs or any(job.get("outcome") != "ok" for job in jobs):
        raise ValueError("Every task needs a successful retained Worker result before delivery review.")
    dispatch_id = report.get("run_id")
    results = verify_dispatch_results(root, ({"dispatch_run_id": dispatch_id, **job} for job in jobs))
    for job, captured in zip(jobs, results, strict=True):
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
        raise ValueError("Delivery requires the exact accepted candidate.")
    if quality:
        # The quality resolver verifies the correction's original input,
        # protected host seal, ancestry and write scope. Never rewrite the
        # initial dispatch integration receipt to impersonate this new result.
        candidate = Path(quality["candidate_path"])
        _require_clean_checkout(candidate, "reviewed candidate")
    return report, quality, candidate


def _verify_landed_batch(*, workspace, project_key, root, run_id, candidate_commit, landed_commit):
    from goodteam_os.dispatch_workspace import _git, _read_git

    landed_commit = _commit(landed_commit)
    report, quality, candidate = _delivery_candidate(workspace=workspace, project_key=project_key, root=root,
        run_id=run_id, candidate_commit=candidate_commit)
    integration = report["integration"]
    if _read_git(root, ["rev-parse", "HEAD"]).strip().decode() != landed_commit:
        raise DeliveryNotLanded("The project is not at the exact confirmed landed commit.")
    changed = [path for path in _read_git(candidate, ["diff", "--name-only", "--no-renames", "-z",
        integration["baseline_commit"], candidate_commit]).split(b"\0") if path]
    if not changed and _git(root, ["merge-base", "--is-ancestor", candidate_commit, landed_commit], check=False).returncode:
        raise DeliveryNotLanded("The unchanged candidate has not landed in the project's current Git history.")
    expected, actual = _tree(candidate, candidate_commit), _tree(root, landed_commit)
    if any(expected.get(path) != actual.get(path) for path in changed):
        raise DeliveryNotLanded("The landed project content differs from the reviewed candidate.")
    _require_clean_checkout(root, "project", paths=changed)
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
        raise ValueError("Confirm that you inspected the exact candidate and its verification evidence.")
    with goal_guard(workspace, project_key):
        return _accept_project_batch_delivery(workspace=workspace, project_key=project_key, root=root,
                                              request=request, producer=DELIVERY_PRODUCERS[0])


def _reconcile_accepted_delivery(workspace, project_key, root, batch, *, branch=None):
    """依据接受凭据和实际 Git 内容登记交付。调用方持有项目锁。"""
    from goodteam_os.dispatch_workspace import _read_git
    from goodteam_os.project_iteration_quality import project_batch_quality_evidence

    scope = dict(workspace=workspace, project_key=project_key, root=root, run_id=batch["run_id"])
    quality = project_batch_quality_evidence(**scope)
    if quality.get("acceptance_status") not in {"accepted_by_review_policy", "accepted_by_manager"}:
        return None
    head = quality["candidate_commit"]
    landed = _read_git(root, ["rev-parse", "HEAD"]).decode().strip()
    request = {"run_id": batch["run_id"], "candidate_commit": head, "landed_commit": landed}
    accept = dict(workspace=workspace, project_key=project_key, root=root,
                  producer=(DELIVERY_PRODUCERS[2] if quality.get("acceptance_status") == "accepted_by_manager"
                            else DELIVERY_PRODUCERS[1]))
    try:
        return _accept_project_batch_delivery(**accept, request=request)
    except DeliveryNotLanded:
        if not branch:
            return None
    try:
        if _land_accepted_candidate(scope, batch, head, landed, branch):
            return _accept_project_batch_delivery(**accept, request={**request, "landed_commit": head})
    except DeliveryNotLanded:
        pass
    return None


def _land_accepted_candidate(scope, batch, head, landed, branch):
    """将已接受候选快进到项目配置的本地分支。"""
    from goodteam_os.dispatch_workspace import _git, _import_retained_commit, _read_git

    root = scope["root"]
    ref = f"refs/heads/{branch}"
    if not isinstance(branch, str) or _git(root, ["check-ref-format", ref], check=False).returncode:
        raise ValueError("Set local_delivery_branch to a local Git branch name.")
    if _git(root, ["symbolic-ref", "-q", "HEAD"], check=False).stdout.strip().decode() != ref:
        return False
    report, quality, source = _delivery_candidate(**scope, candidate_commit=head)
    baseline = report["integration"]["baseline_commit"]
    # 只落地已接受的变更，排除派工前工作区中的未提交内容。
    if (not quality or _read_git(source, ["rev-parse", baseline + "^{tree}"])
            != _read_git(root, ["rev-parse", landed + "^{tree}"])):
        return False
    try:
        _require_clean_checkout(root, "project")
    except DeliveryNotLanded:
        return False
    _import_retained_commit(root, source, head)
    if _git(root, ["merge-base", "--is-ancestor", landed, head], check=False).returncode:
        return False
    store = TaskAssistantSessionStore(scope["workspace"], scope["project_key"])
    advanced = False

    def land(session):
        nonlocal advanced
        value = session.get(_FIELD) or {}
        if (batch.get("expected_continuation_id") != value.get("id") or value.get("status") != "active"
                or batch.get("expected_authority") != value.get("authority")
                or _git(root, ["symbolic-ref", "-q", "HEAD"], check=False).stdout.strip().decode() != ref
                or _read_git(root, ["rev-parse", "HEAD"]).strip().decode() != landed):
            return session
        _require_clean_checkout(root, "project")
        _git(root, ["merge", "--ff-only", head])
        advanced = True
        return session

    try:
        store._mutate(land)
    except DeliveryNotLanded:
        return False
    return advanced


def _delivery_quality_binding(quality):
    return {key: quality[key] for key in ("input_digest", "execution_id", "validation_receipt_id",
                                         "review_receipt_id", "reviewer_job_id", "decision_receipt_id", "candidate_commit") if key in quality}


def _accept_project_batch_delivery(*, workspace, project_key, root, request, producer):
    run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("task-batch-"):
        raise ValueError("An existing project batch identity is required.")
    workspace, root = Path(workspace), Path(root)
    store = TaskAssistantSessionStore(workspace, project_key)
    tasks = LocalTaskStore(root, project_key)
    batch = store.read().get("project_task_batches", {}).get(run_id, {})
    if not batch or batch.get("project_root") != str(root.resolve()) or batch.get("status") == "abandoned":
        raise ValueError("The delivery does not belong to this project batch.")
    receipt = {"producer": producer, "run_id": run_id,
               "dispatch_run_id": batch.get("current_dispatch_run_id", batch["dispatch_run_id"]), "candidate_commit": _commit(request.get("candidate_commit")),
               "landed_commit": _commit(request.get("landed_commit")), "reviewed": True,
               "verification": _DELIVERY_VERIFICATION}
    with tasks._lock():
        before = [task for task in tasks._read()["tasks"] if task.get("run_id") == run_id]
        # 保留原凭据的验证方式和质量记录，重放不依赖当前 HEAD。
        previous = (before[0].get("delivery_receipt") or {}) if before else {}
        replayed = {**receipt, "verification": previous.get("verification")}
        if previous.get("producer") in DELIVERY_PRODUCERS:
            replayed["producer"] = previous["producer"]
        if previous.get("quality"):
            replayed["quality"] = previous["quality"]
        replayed["receipt_id"] = _digest(replayed)
        replay = (before and len(before) == len(batch["task_ids"])
                  and replayed["verification"] in DELIVERY_VERIFICATIONS
                  and all(task["status"] == "done" and task.get("delivery_verified") is True
                          and task.get("delivery_receipt") == replayed for task in before))
        originals = {item["task_id"]: item.get("task_intent") for item in batch["task_bindings"]}
        if not replay and (not before or len(before) != len(originals)
                or any(project_task_intent(task) != originals.get(task["id"])
                       for task in before)):
            raise ValueError("The original task scope changed after batch confirmation.")
        expected_tasks = {task["id"]: task_evidence_digest(task) for task in before}
    if replay:
        # Finish a potentially interrupted session projection without
        # requiring HEAD to remain at a historical accepted revision.
        return {**_record_delivery(store, run_id, replayed, [task["id"] for task in before]), "reused": True}
    report, quality = _verify_landed_batch(
        workspace=workspace, project_key=project_key, root=root, run_id=run_id,
        candidate_commit=request.get("candidate_commit"), landed_commit=request.get("landed_commit"),
    )
    if producer != DELIVERY_PRODUCERS[0] and not quality:
        raise ValueError("Automatic delivery requires an acceptance receipt for this candidate.")
    if quality:
        receipt["quality"] = _delivery_quality_binding(quality)
    receipt["receipt_id"] = _digest(receipt)
    with tasks._lock():
        from goodteam_os.dispatch_workspace import _read_git

        if _read_git(root, ["rev-parse", "HEAD"]).strip().decode() != receipt["landed_commit"]:
            raise DeliveryNotLanded("The project revision changed during delivery verification.")
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

