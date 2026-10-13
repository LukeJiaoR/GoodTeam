"""Bounded Goal continuation over the existing workflow control plane.

Goal records own intent and continuation decisions. StateStore remains the only
workflow execution truth; this module never approves a workflow Gate. A durable
claim without a provable matching run is blocked, never automatically replayed.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from goodteam_os.goal_assessment import (
    CompletedAssessmentProtocolError, assess_goal, evidence_digest, run_evidence, verify_completed_assessment,
)
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, normalize_project_key
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.service_timeouts import GOAL_DRAIN_TIMEOUT_SECONDS


_LOG = logging.getLogger(__name__)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def goal_guard(workspace, project_key):
    key = normalize_project_key(project_key)
    path = Path(workspace).resolve() / "state" / "goal-locks" / f"{key}.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.Lock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def authority_snapshot(workspace, project_key, config, root):
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from goodteam_os.web import resolve_runtime_model_config

    def catalog(path):
        path = Path(path).expanduser().resolve()
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}

    # Workflow and Manager have distinct historical fallback resolution paths.
    # Bind both; unchanged Team fields do not freeze an empty-model fallback.
    workflow_catalog = resolve_runtime_model_config(Path("models.yaml"))
    manager_catalog = os.getenv("GOODTEAM_MODEL_CONFIG", "").strip() or Path(__file__).resolve().parents[2] / "models.yaml"
    team = TeamControlPlaneStore(workspace, project_key).read()
    return {"project_root": str(root.resolve()), "project_config": str(config.resolve()),
            "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "team_sha256": hashlib.sha256(json.dumps(team, sort_keys=True).encode()).hexdigest(),
            "model_catalogs": {"workflow": catalog(workflow_catalog), "manager": catalog(manager_catalog)},
            "billing_acknowledged": True}


def stop_goal_action(workspace, project_key, request):
    """Revoke continuation from its ledger even if project configuration is lost."""
    command = request.get("idempotency_key")
    if not isinstance(command, str) or not command.strip() or len(command) > 256:
        raise ValueError("A stable Goal command identity is required.")
    with goal_guard(workspace, project_key):
        goal = GoalStore(workspace, project_key).stop_goal(
            request.get("goal_id"), expected_revision=request.get("expected_revision"),
            idempotency_key=command, status=request.get("status", "stopped"),
            reason="User stopped automatic continuation; an already started run keeps its existing controls.")
        return {"goal": goal}


def handle_goal_action(workspace, project_key, root, request):
    from goodteam_os.web import _project_config_for_key
    from goodteam_os.project_readiness import build_capabilities
    from goodteam_os.project_readiness_executor import ProjectReadinessExecutor
    from goodteam_os.team_control_plane import ensure_team_state

    workspace, root = Path(workspace), Path(root)
    project_key = normalize_project_key(project_key)
    command = request.get("idempotency_key")
    if not isinstance(command, str) or not command.strip() or len(command) > 256:
        raise ValueError("A stable Goal command identity is required.")
    with goal_guard(workspace, project_key):
        store = GoalStore(workspace, project_key)
        action = request.get("action")
        if action == "create_goal":
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
            ready = ProjectReadinessExecutor(workspace).status(project_key, root)
            if not build_capabilities(ready)["start_workflow"]:
                raise ValueError("Complete project preparation before confirming a Goal.")
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
                current = StateStore(workspace).read_project_current(project_key)
                if (not current or current.run_id != work["run_id"] or current.idea != work["task"]
                        or current.current_node != "complete"
                        or current.pending_transition.get("status") in {"recording", "executing", "interrupted"}):
                    raise ValueError("The same completed workflow cannot be proven; retry is blocked.")
                evidence = run_evidence(workspace, current)
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
    result = deepcopy(goal)
    states = StateStore(Path(workspace))
    current = states.read_project_current(goal["project_key"])
    archived = None
    for work in result["work_units"]:
        state = current if current and current.run_id == work["run_id"] else None
        if state is None and work["status"] in {"dispatching", "running"}:
            if archived is None:
                archived = {item.run_id: item for _, item in states.list_project_archived(goal["project_key"])}
            state = archived.get(work["run_id"])
        if state is None:
            continue
        if state.idea != work["task"]:
            work["observed_execution_status"] = "unknown"
        elif state.current_node == "complete":
            work["observed_execution_status"] = "done"
        elif state.current_node == "rejected" or state.next_node == "rejected":
            work["observed_execution_status"] = "canceled"
        elif state.blockers or state.next_node == "blocked" or state.next_node.startswith("await_"):
            work["observed_execution_status"] = "blocked"
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
        actual = work.get("observed_execution_status")
        if actual not in {"done", "canceled", "blocked", "in_progress"} or work["status"] not in {"dispatching", "running"}:
            continue
        card = next((card for card in cards if card.get("goal_id") == goal["id"] and card.get("work_id") == work["id"]), None)
        if card and (card["status"] != actual or card.get("observed_execution_status") != actual):
            tasks.update_task(card["id"], status=actual, observed_execution_status=actual)
    return view


def _block(store, goal, reason):
    return store.block_goal(goal["id"], expected_revision=goal["revision"],
                            idempotency_key=f"block:{goal['id']}:{goal['revision']}", reason=reason)


def tick_goal(workspace, project_key, goal_id, *, assessor=assess_goal, starter=None):
    """One serialized decision/execution step; no polling reads call this."""
    from goodteam_os.web import _project_target_and_root, _project_config_for_key, start_workflow

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

            current = recover_transition(workspace, project_key)
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
                        evidence = run_evidence(workspace, current)
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
            return goal
        # Pending continuation may only claim when the prior workflow is terminal.
        if current and current.current_node not in {"complete", "rejected"} and current.next_node != "rejected":
            goal = _block(store, goal, "Another active workflow owns this project; Goal continuation did not interrupt it.")
            store.sync_tasks(goal_id, tasks)
            return goal
        goal = store.next_work(goal_id, expected_revision=goal["revision"],
                               idempotency_key=f"claim:{goal_id}:{goal['revision']}")
        work = goal.get("active_work")
        if not work or work["status"] != "dispatching":
            store.sync_tasks(goal_id, tasks)
            return goal
        # The project Goal lock spans claim and dispatch. Stop/Gate actions use
        # the same lock, so authority cannot be silently revoked between them.
        store.sync_tasks(goal_id, tasks)
        try:
            (starter or start_workflow)(workspace=workspace, idea=work["task"], project_key=key,
                model_config=Path("models.yaml"), model_profile=str(target.get("model_profile") or "balanced"),
                delivery_mode=str(target.get("delivery_mode") or "init_project"), source_type="existing_project",
                project_config=config, run_id=work["run_id"])
            current = StateStore(workspace).read_project_current(key)
            if current is None or current.run_id != work["run_id"] or current.idea != work["task"]:
                raise ValueError("Workflow dispatch did not persist the exact claimed run and input.")
            goal = store.mark_running(goal_id, expected_revision=goal["revision"],
                                      idempotency_key=f"running:{goal_id}:{work['id']}", work_id=work["id"], run_id=work["run_id"])
        except Exception as exc:
            # A lost response with a matching run is reconciled on the next
            # tick. An unknown outcome never re-enters start_workflow.
            current = StateStore(workspace).read_project_current(key)
            if current is None or current.run_id != work["run_id"]:
                goal = _block(store, store.get_goal(goal_id), f"Goal dispatch stopped without a provable run: {exc}")
        store.sync_tasks(goal_id, tasks)
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
        while not stop.wait(1):
            if not (workspace / "state" / "goals.sqlite3").is_file():
                continue
            try:
                store = GoalStore(workspace)
                pending = {item["goal_id"] for item in store.pending_outbox()}
                goals = [goal for goal in store.list_goals() if goal["status"] == "active" or goal.get("active_work") or goal["id"] in pending]
                for goal in goals:
                    if stop.is_set():
                        break
                    try:
                        tick_goal(workspace, goal["project_key"], goal["id"])
                    except Exception:
                        _LOG.exception("Goal continuation stopped before dispatch")
            except Exception:
                _LOG.exception("Goal continuation ledger unavailable; no work dispatched")
    stop.thread = threading.Thread(target=run, name="goodteam-goal-continuation", daemon=True)
    stop.thread.start()
    return stop
