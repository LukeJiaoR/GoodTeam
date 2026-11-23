"""[INPUT]: Goal 授权、任务账、批次交付证据。
[OUTPUT]: 项目身份核对、环境变更提示、批次推进与交付评估。
[POS]: Goal 服务同时扫描 Manager 外部输入。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from goodteam_os.goal_assessment import (
    CompletedAssessmentProtocolError, assess_goal, evidence_digest, verify_completed_assessment,
)
from goodteam_os.manager_execution import ManagerExecutionError, ManagerResourceBusy
from goodteam_os.goals import GoalStore
from goodteam_os.project_runtime import normalize_project_key
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.service_timeouts import GOAL_DRAIN_TIMEOUT_SECONDS


_LOG = logging.getLogger(__name__)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class GoalBusyError(RuntimeError):
    """The selected Goal guard is already held by another operation."""


@contextmanager
def goal_guard(workspace, project_key, *, goal_id=None, blocking=True):
    key = normalize_project_key(project_key)
    # The default scope retains existing project-continuation coordination.
    # Goal callers supply their own identity rather than taking that broad lock.
    suffix = "" if goal_id is None else "." + hashlib.sha256(str(goal_id).encode()).hexdigest()
    path = Path(workspace).resolve() / "state" / "goal-locks" / f"{key}{suffix}.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.Lock())
    if not lock.acquire(blocking=blocking):
        raise GoalBusyError("This Goal is busy.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise GoalBusyError("This Goal is busy.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


def continuation_project_identity(config, root, *, target=None):
    from goodteam_os.models import _parse_simple_yaml
    from goodteam_os.project_runtime import resolve_project_runtime_root

    config = Path(config)
    if target is None:
        target = _parse_simple_yaml(config)
    repos = target.get("repos") or {}
    configured = resolve_project_runtime_root(repos) if repos else Path(root)
    return {"project_root": str(configured.resolve()) if configured is not None else "",
            "project_config": str(config.resolve())}


def authority_snapshot(workspace, project_key, config, root):
    """Record project identity and the execution environment for comparison and notices."""
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

    batch_catalog = resolve_runtime_model_config(Path("models.yaml"))
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
    # Only a digest is persisted, so a Key rotation can be observed without exposing it.
    provider = ({key: value for key, value in connection.items() if key not in {"name", "revision"}}
                if connection else None)
    pi_connection = ProviderStore().active("pi")
    pi_provider = ({key: value for key, value in pi_connection.items() if key not in {"name", "revision"}}
                   if pi_connection else None)
    return {"schema_version": "execution_authority.v2",
            **({"pi_provider_sha256": digest(pi_provider)} if pi_provider else {}),
            **continuation_project_identity(config, root, target=target),
            "config_sha256": digest(target), "team_sha256": digest(team),
            "model_catalogs": {"batch": catalog(batch_catalog), "manager": catalog(manager_catalog)},
            "provider_sha256": digest(provider), "billing_acknowledged": True}


def same_continuation_project(expected, config, root):
    """Keep the authorized project identity independent of runtime settings."""
    if not isinstance(expected, dict):
        return False
    current = continuation_project_identity(config, root)
    return (current["project_root"] == str(Path(root).resolve())
            and all(expected.get(key) == value for key, value in current.items()))


def _continuation_environment_event(store, event_id, event, content):
    if not any(item.get("message_id") == event_id for item in store.read()["timeline"]):
        store.append_event(event_id, event, actor="system", content=content)


def notify_continuation_environment(store, identifier, expected, current):
    if expected != current:
        fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
        _continuation_environment_event(store, f"continuation-environment:{identifier}:{fingerprint}",
            "project_continuation_environment_changed",
            "The execution environment changed. Automatic continuation will use the current settings.")


def notify_continuation_environment_unavailable(store, identifier):
    _continuation_environment_event(store, f"continuation-environment-unavailable:{identifier}",
        "project_continuation_environment_unavailable",
        "GoodTeam could not read the current execution environment. Check the project and runtime settings.")


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
        with goal_guard(workspace, project_key, goal_id=request.get("goal_id"), blocking=False):
            return {"goal": stop(GoalStore(workspace, project_key))}
    except GoalBusyError:
        store = GoalStore(workspace, project_key)
        goal = store.get_goal(request.get("goal_id"))
        work = goal.get("active_work") or {}
        if work.get("execution"):
            return {"goal": stop(store)}
        with goal_guard(workspace, project_key, goal_id=request.get("goal_id")):
            return {"goal": stop(GoalStore(workspace, project_key))}


def handle_goal_action(workspace, project_key, root, request):
    from goodteam_os.web import _project_config_for_key
    from goodteam_os.team_control_plane import ensure_team_state

    workspace, root = Path(workspace), Path(root)
    project_key = normalize_project_key(project_key)
    command = request.get("idempotency_key")
    if not isinstance(command, str) or not command.strip() or len(command) > 256:
        raise ValueError("A stable Goal command identity is required.")
    action = request.get("action")
    goal_id = "create-goal" if action == "create_goal" else request.get("goal_id")
    with goal_guard(workspace, project_key, goal_id=goal_id):
        store = GoalStore(workspace, project_key)
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
                if not same_continuation_project(goal.get("authority_snapshot"), config, root):
                    raise ValueError("The original project source is unavailable for assessment retry.")
                execution = work.get("execution")
                if not execution:
                    raise ValueError("Only a delivered Goal batch can retry assessment.")
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
    """Read Goal progress from its own confirmed task batches."""
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    batches_by_project = {
        key: TaskAssistantSessionStore(workspace, key).read().get("project_task_batches", {})
        for key in {goal["project_key"] for goal in goals}
    }
    return [_goal_view(goal, batches_by_project[goal["project_key"]]) for goal in goals]


def _goal_view(goal, batches):
    result = deepcopy(goal)
    for work in result["work_units"]:
        if work.get("execution"):
            batch = batches.get(work["execution"]["run_id"], {})
            work["observed_execution_status"] = {
                "claimed": "in_progress", "running": "in_progress", "review": "awaiting_approval",
                "blocked": "blocked", "abandoned": "canceled", "delivered": "done",
            }.get(batch.get("status"), "queued" if work["status"] == "pending" else "unknown")
            work["observed_batch_run_id"] = work["execution"]["run_id"]
            work["observed_approval"] = "batch_review" if batch.get("status") == "review" else ""
    if result.get("active_work"):
        result["active_work"] = next(work for work in result["work_units"] if work["id"] == result["active_work"]["id"])
    return result


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


def tick_goal(workspace, project_key, goal_id, *, assessor=assess_goal, blocking=True):
    """One serialized decision/execution step; no polling reads call this."""
    from goodteam_os.web import _project_target_and_root, _project_config_for_key
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore

    workspace = Path(workspace)
    project_key = normalize_project_key(project_key)
    with goal_guard(workspace, project_key, goal_id=goal_id, blocking=blocking):
        store = GoalStore(workspace, project_key)
        goal = store.get_goal(goal_id)
        work = goal.get("active_work")
        if work and not work.get("execution"):
            return goal_view(workspace, goal)
        try:
            key, _target, root = _project_target_and_root(workspace, project_key)
            config = _project_config_for_key(workspace, key)
            project_matches = same_continuation_project(goal.get("authority_snapshot"), config, root)
        except Exception:
            # Keep the existing Goal available while its project configuration
            # is unreadable. A later scan can pick up the repaired environment.
            if goal["status"] == "active":
                notify_continuation_environment_unavailable(TaskAssistantSessionStore(workspace, project_key), goal_id)
            return goal
        if not project_matches:
            if goal["status"] == "active":
                return _block(store, goal, "The original project source changed. Inspect the project configuration.")
            return goal
        tasks = LocalTaskStore(root, key)
        store.sync_tasks(goal_id, tasks)
        if goal["status"] != "active":
            return goal_view(workspace, goal)
        session = TaskAssistantSessionStore(workspace, key)
        try:
            environment = authority_snapshot(workspace, key, config, root)
        except (OSError, ValueError):
            notify_continuation_environment_unavailable(session, goal_id)
        else:
            notify_continuation_environment(session, goal_id, goal["authority_snapshot"], environment)
        from goodteam_os.project_iteration_execution import ProjectBatchResourceBusy
        from goodteam_os.project_batch_lock import ProjectBatchBusyError
        from goodteam_os.state_documents import DocumentLockBusy

        try:
            goal = _tick_task_goal(workspace, key, root, config, store, goal, tasks, assessor)
        except (ManagerResourceBusy, ProjectBatchResourceBusy, ProjectBatchBusyError, DocumentLockBusy):
            # No new execution was admitted; retain the exact work for the
            # next scan rather than turning capacity contention into failure.
            goal = store.get_goal(goal_id)
        except Exception as exc:
            goal = store.get_goal(goal_id)
            if goal["status"] == "active":
                if isinstance(exc, ManagerExecutionError):
                    from goodteam_os.assistant_runtime import safe_failure

                    goal = store.pause_for_runtime(goal_id, reason=safe_failure(exc)["message"],
                        expected_revision=goal["revision"], idempotency_key=f"runtime-pause:{goal_id}:{goal['revision']}")
                else:
                    goal = _block(store, goal, f"Goal task continuation could not proceed: {exc}")
        store.sync_tasks(goal_id, tasks)
        return goal_view(workspace, goal)


class GoalServiceStop(threading.Event):
    """Stop scanning, then drain already admitted coordination ticks."""

    thread: threading.Thread

    def __init__(self):
        super().__init__()
        # Owned by the scanner; join reads it only after the scanner has exited.
        self.ticks = {}

    def join(self, timeout=GOAL_DRAIN_TIMEOUT_SECONDS):
        deadline = None if timeout is None else time.monotonic() + timeout

        def remaining():
            return None if deadline is None else max(0, deadline - time.monotonic())

        self.thread.join(remaining())
        if self.thread.is_alive():
            raise TimeoutError("Goal continuation scan has not stopped; inspect it before restarting.")
        for thread in self.ticks.values():
            thread.join(remaining())
        if any(thread.is_alive() for thread in self.ticks.values()):
            raise TimeoutError("Goal continuation has not drained its active ticks; inspect persisted work before restarting.")


def start_goal_service(workspace):
    """Scan Goals, authorized project continuations and pending Manager inputs."""
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
                stop.ticks = {key: thread for key, thread in stop.ticks.items() if thread.is_alive()}
                for goal in goals:
                    if stop.is_set():
                        break
                    identity = (goal["project_key"], goal["id"])
                    if identity in stop.ticks:
                        continue

                    def advance(project_key, goal_id):
                        if stop.is_set():
                            return
                        try:
                            # A second service observes the same Goal lock, not
                            # another queued tick behind a running assessment.
                            tick_goal(workspace, project_key, goal_id, blocking=False)
                        except GoalBusyError:
                            pass
                        except Exception:
                            _LOG.exception("Goal continuation tick failed for %s/%s", project_key, goal_id)

                    # Coordination only: actual work still enters the original
                    # batch runner and its shared process-capacity mechanism.
                    thread = threading.Thread(target=advance, args=identity,
                                              name=f"goodteam-goal-{goal['id']}", daemon=True)
                    thread.start()
                    stop.ticks[identity] = thread
            except Exception:
                validated_idle.clear()
                _LOG.exception("Goal continuation scan failed")

            if stop.is_set():
                break
            try:
                from goodteam_os.project_iteration_continuation import tick_authorized_project_continuations

                tick_authorized_project_continuations(workspace)
            except Exception:
                _LOG.exception("Project continuation scan failed")
                # Preserve continuation priority: an interrupted scan does not admit input on this tick.
                continue

            if stop.is_set():
                break
            try:
                # External inputs share this service, not a separate scheduler or Worker pool.
                from goodteam_os.operator_manager_intake import consume_manager_inputs
                consume_manager_inputs(workspace, stop=stop)
            except Exception:
                _LOG.exception("Manager input scan failed")
    stop.thread = threading.Thread(target=run, name="goodteam-goal-continuation", daemon=True)
    stop.thread.start()
    return stop
