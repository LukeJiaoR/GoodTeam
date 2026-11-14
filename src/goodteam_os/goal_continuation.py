"""Goal intent over the existing task queue and batch execution.

Already-started Workflow history retains its original evidence and controls.
New work binds ordinary tasks; Goal projection never owns their execution state.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path

from goodteam_os.goal_assessment import (
    CompletedAssessmentProtocolError, assess_goal, evidence_digest, run_evidence, verify_completed_assessment,
)
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, normalize_project_key
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.service_timeouts import GOAL_DRAIN_TIMEOUT_SECONDS
from goodteam_os.workflow_table import resolve_profile


_LOG = logging.getLogger(__name__)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class GoalBusyError(RuntimeError):
    """The project Goal guard is already held by another operation."""


@contextmanager
def goal_guard(workspace, project_key, *, blocking=True):
    key = normalize_project_key(project_key)
    path = Path(workspace).resolve() / "state" / "goal-locks" / f"{key}.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.Lock())
    if not lock.acquire(blocking=blocking):
        raise GoalBusyError("The project's Goal is busy.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise GoalBusyError("The project's Goal is busy.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


def authority_snapshot(workspace, project_key, config, root):
    """Bind execution semantics, not display labels or serialization/audit churn.

    Unknown project settings remain bound. Old byte-based snapshots are not
    migrated: their scope cannot be reconstructed safely from their hashes.
    """
    from goodteam_os.models import _parse_simple_yaml, load_model_profile
    from goodteam_os.providers import ProviderStore
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from goodteam_os.web import resolve_runtime_model_config

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    config = Path(config)
    target = _parse_simple_yaml(config)
    # Only this documented display field is excluded. Retain execution settings,
    # unknown extensions and legacy fallback blocks, even with canonical Team.
    target.pop("name", None)
    profile = str(target.get("model_profile", "") or "balanced")

    def catalog(path):
        path = Path(path).expanduser().resolve()
        # Preserve missing-catalog fallback, but malformed selected profiles fail
        # closed. Other profiles and YAML comments cannot affect this grant.
        return {"path": str(path), "profile": profile,
                "sha256": digest(load_model_profile(path, profile)) if path.is_file() else None}

    workflow_catalog = resolve_runtime_model_config(Path("models.yaml"))
    manager_catalog = os.getenv("GOODTEAM_MODEL_CONFIG", "").strip() or Path(__file__).resolve().parents[2] / "models.yaml"
    team = TeamControlPlaneStore(workspace, project_key).read()
    if team is not None:
        team = {
            "schema_version": team["schema_version"], "project_key": team["project_key"],
            "roles": {role: {"state": value["state"],
                **({"runtime_source_worker_id": value["runtime_source_worker_id"]}
                   if value.get("runtime_source_worker_id") else {})} for role, value in team["roles"].items()},
            "manager": {"runtime": team["manager"]["runtime"]},
            # created_at affects deterministic Worker selection. Keep it and
            # list ordering; only labels and non-execution timestamps are noise.
            "workers": [{key: value for key, value in worker.items()
                         if key not in {"name", "updated_at", "released_at", "deleted_at"}}
                        for worker in team["workers"]],
            "skill_bindings": team["skill_bindings"],
        }
    connection = ProviderStore().active()
    # The actual endpoint/account/model must stay bound even when catalog text
    # is unchanged. Only a digest is persisted, never provider credentials.
    provider = ({key: value for key, value in connection.items() if key not in {"name", "revision"}}
                if connection else None)
    return {"schema_version": "execution_authority.v2",
            "project_root": str(Path(root).resolve()), "project_config": str(config.resolve()),
            "config_sha256": digest(target), "team_sha256": digest(team),
            "model_catalogs": {"workflow": catalog(workflow_catalog), "manager": catalog(manager_catalog)},
            "provider_sha256": digest(provider), "billing_acknowledged": True}


def stop_goal_action(workspace, project_key, request):
    """Revoke continuation from its ledger even if project configuration is lost."""
    command = request.get("idempotency_key")
    if not isinstance(command, str) or not command.strip() or len(command) > 256:
        raise ValueError("A stable Goal command identity is required.")
    def stop(store):
        return store.stop_goal(
            request.get("goal_id"), expected_revision=request.get("expected_revision"),
            idempotency_key=command, status=request.get("status", "stopped"),
            reason="User stopped automatic continuation; an already started run keeps its existing controls.")

    try:
        with goal_guard(workspace, project_key, blocking=False):
            return {"goal": stop(GoalStore(workspace, project_key))}
    except GoalBusyError:
        # A synchronously starting workflow can hold the Goal guard while its
        # Worker runs. Revocation may proceed through the original transactional
        # ledger only after the exact claimed workflow has a durable checkpoint.
        # Before that checkpoint, retain the serialized claim/start boundary.
        store = GoalStore(workspace, project_key)
        goal = store.get_goal(request.get("goal_id"))
        work = goal.get("active_work") or {}
        if work.get("execution"):
            return {"goal": stop(store)}
        current = StateStore(workspace).read_project_current(project_key)
        if (goal["status"] == "active" and work.get("status") == "dispatching"
                and current is not None and current.run_id == work.get("run_id")
                and current.idea == work.get("task")):
            return {"goal": stop(store)}
        with goal_guard(workspace, project_key):
            return {"goal": stop(GoalStore(workspace, project_key))}


def handle_goal_action(workspace, project_key, root, request):
    from goodteam_os.web import _project_config_for_key
    from goodteam_os.team_control_plane import ensure_team_state

    workspace, root = Path(workspace), Path(root)
    project_key = normalize_project_key(project_key)
    command = request.get("idempotency_key")
    if not isinstance(command, str) or not command.strip() or len(command) > 256:
        raise ValueError("A stable Goal command identity is required.")
    from goodteam_os.workflow_mutations import workflow_guard

    with goal_guard(workspace, project_key), (
        workflow_guard(workspace, project_key) if request.get("action") == "create_goal" else nullcontext()
    ):
        store = GoalStore(workspace, project_key)
        action = request.get("action")
        if action == "create_goal":
            from goodteam_os.project_iteration_execution import active_project_batch

            if active_project_batch(workspace, project_key):
                raise ValueError("Finish or reconcile the active project batch before creating a Goal.")
            if request.get("confirmed") is not True or request.get("billing_acknowledged") is not True:
                raise ValueError("Confirm the exact Goal, automatic continuation and configured provider charges first.")
            replay = store.replay_goal_creation(idempotency_key=command, objective=request.get("objective"),
                acceptance_criteria=request.get("acceptance_criteria"), allowed_scope=request.get("allowed_scope"),
                max_work_units=request.get("max_work_units"), confirmed=True,
                max_assessment_attempts=request.get("max_assessment_attempts"))
            if replay is not None:
                store.sync_tasks(replay["id"], LocalTaskStore(root, project_key))
                return {"goal": replay}
            current = StateStore(workspace).read_project_current(project_key)
            if current and current.current_node not in {"complete", "rejected"} and current.next_node != "rejected":
                raise ValueError("Finish the existing workflow before confirming a new Goal.")
            config = _project_config_for_key(workspace, project_key)
            ensure_team_state(workspace, project_key, config)
            goal = store.create_goal(
                objective=request.get("objective"), acceptance_criteria=request.get("acceptance_criteria"),
                allowed_scope=request.get("allowed_scope"), max_work_units=request.get("max_work_units"),
                confirmed=True, idempotency_key=command,
                authority_snapshot=authority_snapshot(workspace, project_key, config, root),
                max_assessment_attempts=request.get("max_assessment_attempts"),
            )
        elif action == "decide_goal_gate":
            goal = store.decide_gate(request.get("goal_id"), gate_id=request.get("gate_id"),
                                     decision=request.get("decision"), expected_revision=request.get("expected_revision"),
                                     idempotency_key=command)
        elif action == "retry_goal_assessment":
            if request.get("confirmed") is not True:
                raise ValueError("Confirm the exact assessment retry and its potential provider charge.")
            def validate_retry(goal, work, failure):
                config = _project_config_for_key(workspace, project_key)
                if goal.get("authority_snapshot") != authority_snapshot(workspace, project_key, config, root):
                    raise ValueError("Goal configuration or model catalog changed; retry is blocked.")
                execution = work.get("execution")
                if execution is not None:
                    from goodteam_os.goal_assessment import batch_run_evidence
                    from goodteam_os.project_iteration_quality import _batch

                    batch = _batch(workspace, project_key, root, execution["run_id"])
                    if (batch.get("status") != "delivered"
                            or batch.get("proposal_ids") != execution["proposal_ids"]
                            or batch.get("expected_revisions") != execution["expected_revisions"]
                            or batch.get("goal_source") != {"goal_id": goal["id"], "work_id": work["id"]}):
                        raise ValueError("The original delivered Goal batch cannot be proven; retry is blocked.")
                    evidence = batch_run_evidence(workspace, project_root=root, project_key=project_key,
                        run_id=execution["run_id"], task_id=execution["project_task_id"])
                else:
                    current = StateStore(workspace).read_project_current(project_key)
                    if (not current or current.run_id != work["run_id"] or current.idea != work["task"]
                            or current.current_node != "complete"
                            or current.pending_transition.get("status") in {"recording", "executing", "interrupted"}):
                        raise ValueError("The same completed workflow cannot be proven; retry is blocked.")
                    evidence = run_evidence(workspace, current, project_root=root)
                if evidence_digest(evidence) != failure["evidence_digest"]:
                    raise ValueError("The original assessment evidence changed; retry is blocked.")
                verify_completed_assessment(root, goal, work, failure["attempt"], failure["job_id"], evidence)
            goal = store.retry_assessment(request.get("goal_id"), work_id=request.get("work_id"),
                run_id=request.get("run_id"), evidence_digest=request.get("evidence_digest"),
                failed_job_id=request.get("failed_job_id"), expected_revision=request.get("expected_revision"),
                idempotency_key=command, validate=validate_retry)
        elif action == "stop_goal":
            goal = store.stop_goal(request.get("goal_id"), expected_revision=request.get("expected_revision"),
                                   idempotency_key=command, status=request.get("status", "stopped"),
                                   reason="User stopped automatic continuation; an already started run keeps its existing controls.")
        else:
            raise ValueError("Unknown Goal action.")
        store.sync_tasks(goal["id"], LocalTaskStore(root, project_key))
        return {"goal": goal}


def goal_view(workspace, goal):
    """Read actual run facts without changing a Goal dispatch receipt."""
    return goal_views(workspace, [goal])[0]


def goal_views(workspace, goals):
    """Project a batch with fresh, request-local workflow reads per project.

    Archives stay lazy, and no snapshots survive this call. This is a read
    projection, not an atomic snapshot across the ledger and workflow files.
    """
    states = StateStore(Path(workspace))
    current_by_project, archived_by_project = {}, {}
    return [_goal_view(goal, states, current_by_project, archived_by_project, workspace=workspace) for goal in goals]


def _goal_view(goal, states, current_by_project, archived_by_project, *, workspace):
    from goodteam_os.web import current_approval_gate

    result = deepcopy(goal)
    project_key = goal["project_key"]
    if project_key not in current_by_project:
        current_by_project[project_key] = states.read_project_current(project_key)
    current = current_by_project[project_key]
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    batches = TaskAssistantSessionStore(workspace, project_key).read().get("project_task_batches", {})
    for work in result["work_units"]:
        if work.get("execution"):
            batch = batches.get(work["execution"]["run_id"], {})
            work["observed_execution_status"] = {
                "claimed": "in_progress", "running": "in_progress", "review": "awaiting_approval",
                "blocked": "blocked", "abandoned": "canceled", "delivered": "done",
            }.get(batch.get("status"), "queued" if work["status"] == "pending" else "unknown")
            work["observed_batch_run_id"] = work["execution"]["run_id"]
            work["observed_approval"] = "batch_review" if batch.get("status") == "review" else ""
            continue
        state = current if current and current.run_id == work["run_id"] else None
        if state is None and work["status"] in {"dispatching", "running"}:
            if project_key not in archived_by_project:
                archived_by_project[project_key] = {
                    item.run_id: item for _, item in states.list_project_archived(project_key)
                }
            state = archived_by_project[project_key].get(work["run_id"])
        if state is None:
            continue
        work["observed_approval"] = ""
        if state.idea != work["task"]:
            work["observed_execution_status"] = "unknown"
        elif state.current_node == "complete":
            work["observed_execution_status"] = "done"
        elif state.current_node == "rejected" or state.next_node == "rejected":
            work["observed_execution_status"] = "canceled"
        elif state.blockers or state.next_node == "blocked" or state.pending_transition.get("status") == "interrupted":
            work["observed_execution_status"] = "blocked"
        elif state.pending_transition.get("status") in {"recording", "executing"}:
            work["observed_execution_status"] = "in_progress"
        elif state.next_node.startswith("await_"):
            gate = current_approval_gate(state)
            try:
                supported = gate in resolve_profile(state.load_profile or "greenfield").gate_names()
            except ValueError:
                supported = False
            # Existing workflow facts determine waiting; this does not grant
            # approval authority. The UI separately binds the current review.
            work["observed_execution_status"] = "awaiting_approval" if supported else "blocked"
            if supported:
                work["observed_approval"] = gate
        else:
            work["observed_execution_status"] = "in_progress"
        work["observed_next_node"] = state.next_node
    if result.get("active_work"):
        result["active_work"] = next(work for work in result["work_units"] if work["id"] == result["active_work"]["id"])
    return result


def _sync_execution_projection(workspace, goal, tasks):
    view = goal_view(workspace, goal)
    cards = tasks.list_tasks()
    for work in view["work_units"]:
        if work.get("execution"):
            continue
        actual = work.get("observed_execution_status")
        if actual not in {"done", "canceled", "blocked", "in_progress", "awaiting_approval"} or work["status"] not in {"dispatching", "running"}:
            continue
        card = next((card for card in cards if card.get("goal_id") == goal["id"] and card.get("work_id") == work["id"]), None)
        status = "review" if actual == "awaiting_approval" else actual
        approval = work.get("observed_approval", "")
        if card and (card["status"] != status or card.get("observed_execution_status") != actual
                     or card.get("observed_approval", "") != approval):
            tasks.update_task(card["id"], status=status, observed_execution_status=actual, observed_approval=approval)
    return view


def _block(store, goal, reason):
    return store.block_goal(goal["id"], expected_revision=goal["revision"],
                            idempotency_key=f"block:{goal['id']}:{goal['revision']}", reason=reason)


def _tick_task_goal(workspace, key, root, config, store, goal, tasks, assessor):
    """Adapt Goal context to the existing task queue; it owns no execution state."""
    from datetime import UTC, datetime
    from goodteam_os.assistant_requests import conversation_lock
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import task_evidence_digest, project_task_intent
    from goodteam_os.project_iteration_execution import _identity, run_queued_iteration_batch
    from goodteam_os.goal_assessment import batch_run_evidence

    work = goal.get("active_work")
    if work is None:
        pending = [item for item in goal["work_units"] if item["status"] == "pending"]
        if not pending:
            return goal
        work = pending[0]
        if not work.get("execution"):
            task = next(item for item in tasks.list_tasks()
                        if f"goal:{goal['id']}:work:{work['id']}" in item.get("external_refs", []))
            task = tasks.update_task(task["id"], goal_execution="batch")
            proposal_id = hashlib.sha256(f"goal:{goal['id']}:{work['id']}".encode()).hexdigest()
            revisions = {proposal_id: 1}
            run_id, _ = _identity([proposal_id], revisions)
            goal = store.bind_execution(goal["id"], work_id=work["id"],
                execution={"kind": "batch", "run_id": run_id, "project_task_id": task["id"],
                           "proposal_ids": [proposal_id], "expected_revisions": revisions},
                expected_revision=goal["revision"], idempotency_key=f"bind:{goal['id']}:{work['id']}")
        goal = store.next_work(goal["id"], expected_revision=goal["revision"],
                               idempotency_key=f"claim:{goal['id']}:{goal['revision']}")
        work = goal["active_work"]
    execution = work["execution"]
    session_store = TaskAssistantSessionStore(workspace, key)
    batch = session_store.read().get("project_task_batches", {}).get(execution["run_id"])
    if batch is None:
        store.sync_tasks(goal["id"], tasks)
        task = tasks.get_task(execution["project_task_id"])
        proposal_id = execution["proposal_ids"][0]
        proposal = {"id": proposal_id, "revision": 1, "exact_request": task["description"],
            "source_message_ids": [], "source": "goal", "goal_source": {"goal_id": goal["id"], "work_id": work["id"]},
            "status": "queued", "confirmed": False, "run_id": "", "created_at": datetime.now(UTC).isoformat(),
            "project_task_id": task["id"], "project_task_digest": task_evidence_digest(task),
            "goal_task_intent": project_task_intent(task)}
        with conversation_lock(session_store):
            def enqueue(session):
                queue = session.setdefault("queued_task_proposals", [])
                old = next((item for item in queue if item["id"] == proposal_id), None)
                if old is None:
                    queue.append(proposal)
                elif old.get("goal_task_intent") != proposal["goal_task_intent"] or old.get("goal_source") != proposal["goal_source"]:
                    raise ValueError("Goal task changed after its queue binding.")
                elif not old.get("confirmed"):
                    old["project_task_digest"] = proposal["project_task_digest"]
                return session
            session_store._mutate(enqueue)
        run_queued_iteration_batch(workspace=workspace, project_key=key, root=root, project_config=config,
            proposal_ids=execution["proposal_ids"], expected_revisions=execution["expected_revisions"],
            confirmed=True, background=True, expected_authority=goal.get("authority_snapshot"))
        batch = session_store.read()["project_task_batches"][execution["run_id"]]
    elif batch.get("status") in {"claimed", "running"}:
        # Reuse the existing batch identity and its dispatch recovery; the batch
        # service observes an active thread instead of launching a duplicate.
        run_queued_iteration_batch(workspace=workspace, project_key=key, root=root, project_config=config,
            proposal_ids=execution["proposal_ids"], expected_revisions=execution["expected_revisions"],
            confirmed=True, background=True, expected_authority=goal.get("authority_snapshot"))
        batch = session_store.read()["project_task_batches"][execution["run_id"]]
    if work["status"] == "dispatching":
        goal = store.mark_running(goal["id"], expected_revision=goal["revision"],
            idempotency_key=f"running:{goal['id']}:{work['id']}", work_id=work["id"], run_id=work["run_id"])
        work = goal["active_work"]
    # Review and delivery remain facts owned by the existing batch services.
    # Worker completion alone neither accepts a Goal nor frees its dependencies.
    if batch.get("status") != "delivered":
        return goal
    evidence = batch_run_evidence(workspace, project_root=root, project_key=key,
                                  run_id=execution["run_id"], task_id=execution["project_task_id"])
    failures = work.get("assessment_failures", [])
    digest = evidence_digest(evidence)
    if failures and failures[0]["evidence_digest"] != digest:
        return _block(store, goal, "The original batch assessment evidence changed.")
    attempt = len(failures) + 1
    if attempt > work.get("assessment_retry_limit", goal.get("max_assessment_attempts", 1)):
        return _block(store, goal, "Assessment retry allowance exhausted.")
    try:
        assessment = assessor(workspace=workspace, project_config=config, project_root=root,
            goal=goal, work=work, execution_ref={"project_key": key, "run_id": execution["run_id"]},
            evidence=evidence, attempt=attempt)
        current_goal = store.get_goal(goal["id"])
        if current_goal["status"] != "active":
            return current_goal
        if not assessment.get("evidence_refs") or not set(assessment["evidence_refs"]).issubset(evidence):
            raise ValueError("Goal assessment cites evidence outside its original batch.")
        return store.complete_work(goal["id"], expected_revision=goal["revision"],
            idempotency_key=f"complete:{goal['id']}:{work['id']}", work_id=work["id"], **assessment)
    except CompletedAssessmentProtocolError as exc:
        verify_completed_assessment(root, goal, work, attempt, exc.job_id, evidence)
        return store.record_assessment_failure(goal["id"], work_id=work["id"], job_id=exc.job_id,
            code=exc.code, evidence_digest=digest, expected_revision=goal["revision"],
            idempotency_key=f"assessment-failure:{goal['id']}:{work['id']}:{attempt}")


def tick_goal(workspace, project_key, goal_id, *, assessor=assess_goal):
    """One serialized decision/execution step; no polling reads call this."""
    from goodteam_os.web import _project_target_and_root, _project_config_for_key

    workspace = Path(workspace)
    project_key = normalize_project_key(project_key)
    with goal_guard(workspace, project_key):
        store = GoalStore(workspace, project_key)
        goal = store.get_goal(goal_id)
        current = StateStore(workspace).read_project_current(project_key)
        work = goal.get("active_work")
        # Recovery is a workflow fact, independent of future Goal authority or
        # configuration availability. Never touch an unrelated/new project run.
        if (work and current and current.run_id == work["run_id"] and current.idea == work["task"]
                and current.pending_transition.get("status") in {"recording", "executing"}):
            from goodteam_os.workflow_mutations import recover_transition

            current = recover_transition(workspace, project_key,
                                         expected_run_id=work["run_id"], expected_idea=work["task"])
        try:
            key, target, root = _project_target_and_root(workspace, project_key)
            config = _project_config_for_key(workspace, key)
            authority = authority_snapshot(workspace, key, config, root)
        except Exception:
            # No target filesystem or Worker action is safe without resolving
            # the original configuration. The workspace ledger remains usable.
            if goal["status"] == "active":
                return _block(store, goal, "Project configuration or authorization snapshot is unavailable. Automatic continuation stopped.")
            return goal
        tasks = LocalTaskStore(root, key)
        store.sync_tasks(goal_id, tasks)
        if goal["status"] != "active":
            return _sync_execution_projection(workspace, goal, tasks)
        if goal.get("authority_snapshot") != authority:
            goal = _block(store, goal, "Project, Team or runtime configuration changed since Goal confirmation. Reconfirm a new Goal.")
            store.sync_tasks(goal_id, tasks)
            return goal
        if (work and work.get("execution")) or work is None:
            try:
                goal = _tick_task_goal(workspace, key, root, config, store, goal, tasks, assessor)
            except Exception as exc:
                goal = store.get_goal(goal_id)
                if goal["status"] == "active":
                    goal = _block(store, goal, f"Goal task continuation stopped: {exc}")
            store.sync_tasks(goal_id, tasks)
            return goal_view(workspace, goal)
        # Existing unbound active work remains attached to its original Workflow.
        # A workflow can publish its final node before its approval transaction
        # finishes. Wait for that existing authority boundary; do not assess or
        # claim a successor in the middle of an in-flight decision.
        if (work and current and current.run_id == work["run_id"] and current.idea == work["task"]
                and current.pending_transition.get("status") in {"recording", "executing"}):
            return goal
        if work and work["status"] in {"dispatching", "running"}:
            if current is None or current.run_id != work["run_id"]:
                goal = _block(store, goal, "Goal dispatch ownership cannot be proven from the current workflow. Automatic replay is blocked.")
            elif current.idea != work["task"]:
                goal = _block(store, goal, "Bound workflow input differs from the confirmed Goal work.")
            elif current.current_node == "start":
                goal = _block(store, goal, "Goal workflow start was interrupted before an execution checkpoint; inspect it before continuing.")
            elif current.next_node in {"blocked", "rejected"} or current.current_node == "rejected" or current.blockers:
                goal = _block(store, goal, "The bound workflow failed, was rejected or is blocked. Resolve its evidence before a new Goal.")
            else:
                if work["status"] == "dispatching":
                    goal = store.mark_running(goal_id, expected_revision=goal["revision"],
                                              idempotency_key=f"running:{goal_id}:{work['id']}", work_id=work["id"], run_id=work["run_id"])
                if current.current_node == "complete":
                    try:
                        evidence = run_evidence(workspace, current, project_root=root)
                        failures = work.get("assessment_failures", [])
                        digest = evidence_digest(evidence)
                        if failures and digest != failures[0]["evidence_digest"]:
                            raise ValueError("The original assessment evidence changed; retry is blocked.")
                        attempt = len(failures) + 1
                        if attempt > work.get("assessment_retry_limit", goal.get("max_assessment_attempts", 1)):
                            raise ValueError("Assessment retry allowance exhausted.")
                        if failures:
                            verify_completed_assessment(root, goal, work, failures[-1]["attempt"], failures[-1]["job_id"], evidence)
                        assessment = assessor(workspace=workspace, project_config=config, project_root=root,
                                              goal=goal, work=work, state=current, evidence=evidence, attempt=attempt)
                        # Only the validated assessment adapter returns criterion indices and refs;
                        # every ref is rechecked here against the current immutable evidence packet.
                        if not assessment.get("evidence_refs") or not set(assessment["evidence_refs"]).issubset(evidence):
                            raise ValueError("Goal assessment returned evidence outside the bound completed run.")
                        goal = store.complete_work(goal_id, expected_revision=goal["revision"],
                            idempotency_key=f"complete:{goal_id}:{work['id']}", work_id=work["id"], **assessment)
                    except CompletedAssessmentProtocolError as exc:
                        try:
                            verify_completed_assessment(root, goal, work, attempt, exc.job_id, evidence)
                            goal = store.record_assessment_failure(goal_id, work_id=work["id"], job_id=exc.job_id,
                                code=exc.code, evidence_digest=digest, expected_revision=goal["revision"],
                                idempotency_key=f"assessment-failure:{goal_id}:{work['id']}:{attempt}")
                        except Exception as proof_error:
                            goal = _block(store, store.get_goal(goal_id), f"Assessment retry could not be admitted: {proof_error}")
                    except Exception as exc:
                        goal = _block(store, store.get_goal(goal_id), f"Goal assessment stopped: {exc}")
            store.sync_tasks(goal_id, tasks)
            return _sync_execution_projection(workspace, goal, tasks)
        return goal


class GoalServiceStop(threading.Event):
    """Stop scanning, then explicitly drain the current bounded tick."""

    thread: threading.Thread

    def join(self, timeout=GOAL_DRAIN_TIMEOUT_SECONDS):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError("Goal continuation did not reach a shutdown checkpoint within 30 seconds; inspect the persisted run before restarting.")


def start_goal_service(workspace):
    """Resume only explicitly authorized Goals; one bounded tick per scan."""
    workspace = Path(workspace)
    stop = GoalServiceStop()

    def run():
        validated_idle = {}
        while not stop.wait(1):
            try:
                from goodteam_os.goals import goal_store_exists

                if goal_store_exists(workspace):
                    store = GoalStore(workspace)
                    goals = store.list_continuation_goals(validated_idle=validated_idle)
                else:
                    validated_idle.clear()
                    goals = []
                for goal in goals:
                    if stop.is_set():
                        break
                    try:
                        tick_goal(workspace, goal["project_key"], goal["id"])
                    except Exception:
                        _LOG.exception("Goal continuation stopped before dispatch")
                if not stop.is_set():
                    from goodteam_os.project_iteration_continuation import tick_authorized_project_continuations

                    tick_authorized_project_continuations(workspace)
                    # External input receipt processing shares this service; it
                    # creates no execution scheduler or additional Worker pool.
                    from goodteam_os.operator_manager_intake import consume_manager_inputs
                    from goodteam_os.operator_queue import DurableOperatorQueue
                    from goodteam_os.service import service_state_dir
                    if not stop.is_set() and DurableOperatorQueue(service_state_dir()).summary()["desired_state"] == "running":
                        consume_manager_inputs(workspace, stop=stop)
            except Exception:
                validated_idle.clear()
                _LOG.exception("Goal continuation ledger unavailable; no work dispatched")
    stop.thread = threading.Thread(target=run, name="goodteam-goal-continuation", daemon=True)
    stop.thread.start()
    return stop


