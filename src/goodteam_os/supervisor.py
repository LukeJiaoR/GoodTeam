from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.service_timeouts import COMPONENT_STOP_TIMEOUT_SECONDS, WEB_STOP_TIMEOUT_SECONDS


HEARTBEAT_SECONDS = 0.5
COMPONENT_HEARTBEAT_TIMEOUT_SECONDS = float(os.environ.get("GOODTEAM_COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", "30"))
COMPONENT_START_TIMEOUT_SECONDS = 8.0
MAX_COMPONENT_RESTARTS = 3
OPERATOR_HEARTBEAT_SECONDS = 0.5
OPERATOR_IDLE_DEADLINE_SECONDS = float(os.environ.get("GOODTEAM_OPERATOR_IDLE_DEADLINE_SECONDS", "60"))
OPERATOR_SHORT_DEADLINE_SECONDS = float(os.environ.get("GOODTEAM_OPERATOR_SHORT_DEADLINE_SECONDS", "120"))
OPERATOR_LONG_DEADLINE_SECONDS = float(os.environ.get("GOODTEAM_OPERATOR_LONG_DEADLINE_SECONDS", "1860"))
OPERATOR_LONG_PHASES = {"executing", "review_ready", "fixing"}
OPERATOR_PHASE_KINDS = {
    "hydrating": "hydrate",
    "preflight": "preflight",
    "executing": "execute",
    "pr_ready": "ensure_pr",
    "observing_ci": "observe_ci",
    "review_ready": "review",
    "fixing": "fix",
    "waiting_telegram": "send_approval",
    "merging": "merge",
}


@dataclass
class Component:
    name: str
    command: list[str]
    ready_path: Path
    ready_token: str
    heartbeat_path: Path
    heartbeat_token: str
    process: subprocess.Popen[bytes] | None = None
    ready: bool = False
    status: str = "starting"
    last_error: str = ""
    restart_count: int = 0
    started_at: float = 0.0
    last_heartbeat: float = 0.0
    heartbeat_sequence: int = 0
    heartbeat_details: dict[str, Any] | None = None


class ComponentHeartbeat:
    """Child-owned progress heartbeat with generation and PID binding."""

    def __init__(self, path: Path, token: str):
        self.path = Path(path)
        self.token = token
        self.sequence = 0

    def beat(self, details: Mapping[str, Any] | None = None) -> None:
        self.sequence += 1
        payload = {
            "schema_version": 1,
            "token": self.token,
            "pid": os.getpid(),
            "sequence": self.sequence,
            "updated_at": time.time(),
            "details": dict(details or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


class OperatorLivenessHeartbeat:
    """Publish operator liveness while a bounded synchronous action is in flight.

    The main loop advances progress before and after every tick. A watchdog thread
    may keep the child-owned heartbeat fresh while that tick is blocked in a
    worker or remote operation, but only until the persisted operation-specific
    deadline. Once the deadline expires without main-loop progress, publication
    stops and the supervisor's normal stale-component recovery takes over.
    """

    def __init__(
        self,
        *,
        queue: DurableOperatorQueue,
        emitter: ComponentHeartbeat,
        heartbeat_interval: float = OPERATOR_HEARTBEAT_SECONDS,
        idle_deadline: float = OPERATOR_IDLE_DEADLINE_SECONDS,
        short_deadline: float = OPERATOR_SHORT_DEADLINE_SECONDS,
        long_deadline: float = OPERATOR_LONG_DEADLINE_SECONDS,
    ):
        self.queue = queue
        self.emitter = emitter
        self.heartbeat_interval = heartbeat_interval
        self.idle_deadline = idle_deadline
        self.short_deadline = short_deadline
        self.long_deadline = long_deadline
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._details: dict[str, Any] = {}
        self._operation_key = ""
        self._queue_revision = ""
        self._worker_revision = ""
        self._main_progress_sequence = 0
        self._deadline_at = 0.0
        self._deadline_expiration_published = False

    def start(self) -> None:
        self.progress("component_started")
        self._publish_once()
        self._thread = threading.Thread(target=self._run, name="goodteam-operator-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.heartbeat_interval * 3))

    def progress(self, event: str) -> None:
        item = self._active_item()
        now = time.time()
        with self._lock:
            self._main_progress_sequence += 1
            self._record_item(item, now=now, reset_deadline=True)
            self._details.update(
                {
                    "progress_event": event,
                    "main_progress_sequence": self._main_progress_sequence,
                    "main_progress_at": now,
                }
            )

    def _run(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            self._publish_once()

    def _publish_once(self) -> None:
        item = self._active_item()
        worker = _latest_worker_progress(item)
        now = time.time()
        with self._lock:
            self._record_item(item, now=now, reset_deadline=False)
            worker_revision = str(worker.get("heartbeat_at", ""))
            if worker_revision and worker_revision != self._worker_revision:
                self._worker_revision = worker_revision
                self._details["operation_progress_sequence"] = int(
                    self._details.get("operation_progress_sequence", 0)
                ) + 1
            self._details["worker"] = worker
            self._details["deadline_at"] = self._deadline_at
            expired = now > self._deadline_at
            self._details["deadline_expired"] = expired
            details = dict(self._details)
            publish = not expired or not self._deadline_expiration_published
            if expired:
                self._deadline_expiration_published = True
        if publish:
            self.emitter.beat(details)

    def _record_item(self, item: dict[str, Any] | None, *, now: float, reset_deadline: bool) -> None:
        phase = str(item.get("phase", "idle")) if item else "idle"
        operation = _latest_operator_operation(item)
        operation_id = str(operation.get("operation_id", "")) or _predicted_operator_operation(item, phase)
        operation_key = f"{item.get('id', '') if item else 'idle'}:{phase}:{operation_id}"
        queue_revision = str(item.get("updated_at", "")) if item else ""
        if reset_deadline or operation_key != self._operation_key:
            self._operation_key = operation_key
            deadline_seconds = self._deadline_for_phase(phase)
            self._deadline_at = now + deadline_seconds
            self._deadline_expiration_published = False
            self._details["operation_started_at"] = now
            self._details["deadline_seconds"] = deadline_seconds
            self._details["operation_progress_sequence"] = int(
                self._details.get("operation_progress_sequence", 0)
            ) + 1
        if queue_revision and queue_revision != self._queue_revision:
            self._queue_revision = queue_revision
            self._details["operation_progress_sequence"] = int(
                self._details.get("operation_progress_sequence", 0)
            ) + 1
        self._details.update(
            {
                "task_id": str(item.get("id", "")) if item else "",
                "phase": phase,
                "attempt": int(item.get("attempt", 0) or 0) if item else 0,
                "operation_id": operation_id,
                "operation_kind": str(operation.get("kind", "")),
                "operation_state": str(operation.get("state", "")),
                "queue_revision": queue_revision,
            }
        )

    def _deadline_for_phase(self, phase: str) -> float:
        if phase == "idle" or phase == "waiting_telegram":
            return self.idle_deadline
        if phase in OPERATOR_LONG_PHASES:
            return self.long_deadline
        return self.short_deadline

    def _active_item(self) -> dict[str, Any] | None:
        try:
            return self.queue.active_item()
        except Exception:
            return None


def _latest_operator_operation(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    operations = item.get("operations", {})
    if not isinstance(operations, Mapping):
        return {}
    attempt_prefix = f"{item.get('id', '')}:{int(item.get('attempt', 0) or 0)}:"
    phase = str(item.get("phase", ""))
    candidates = [
        dict(operation)
        for operation_id, operation in operations.items()
        if str(operation_id).startswith(attempt_prefix)
        and isinstance(operation, Mapping)
        and isinstance(operation.get("payload"), Mapping)
        and str(operation["payload"].get("phase", "")) == phase
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda operation: str(operation.get("updated_at", "")))


def _predicted_operator_operation(item: Mapping[str, Any] | None, phase: str) -> str:
    if not item:
        return "operator:idle"
    kind = OPERATOR_PHASE_KINDS.get(phase, phase or "idle")
    metadata = item.get("metadata", {})
    head = str(metadata.get("head_sha", "")) if isinstance(metadata, Mapping) else ""
    return f"{item.get('id', '')}:{int(item.get('attempt', 0) or 0)}:{kind}:{head or 'none'}"


def _latest_worker_progress(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    metadata = item.get("metadata", {})
    if not isinstance(metadata, Mapping):
        return {}
    project_root = str(metadata.get("project_root", "")).strip()
    if not project_root:
        return {}
    jobs_root = Path(project_root) / ".goodteam" / "workers" / "jobs"
    if not jobs_root.is_dir():
        return {}
    phase = str(item.get("phase", ""))
    expected_role = "qa" if phase == "review_ready" else "implementation" if phase in {"executing", "fixing"} else ""
    execution_id = str(item.get("execution_id", ""))
    candidates: list[dict[str, Any]] = []
    try:
        status_paths = list(jobs_root.glob("*/status.json"))
    except OSError:
        return {}
    for status_path in status_paths:
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "running":
            continue
        if expected_role and str(payload.get("role", "")) != expected_role:
            continue
        if execution_id and str(payload.get("run_id", "")) != execution_id:
            continue
        heartbeat_at = str(payload.get("heartbeat_at", ""))
        if not heartbeat_at:
            continue
        candidates.append(
            {
                "job_id": str(payload.get("job_id", "")),
                "role": str(payload.get("role", "")),
                "status": "running",
                "heartbeat_at": heartbeat_at,
            }
        )
    if not candidates:
        return {}
    return max(candidates, key=lambda candidate: candidate["heartbeat_at"])


def run_supervisor(
    *,
    workspace: Path,
    host: str,
    port: int,
    frontend_dist: Path,
    state_dir: Path,
    identity: str,
    ready_file: Path,
    ready_token: str,
    operator_review_config=None,
    local_codex_policy=None,
    trusted_codex_review_policy=None,
    operator_review_config_sha256="",
) -> int:
    from goodteam_os.operator_startup import startup_arguments
    operator_args = startup_arguments(operator_review_config=operator_review_config,
        local_codex_policy=local_codex_policy, trusted_codex_review_policy=trusted_codex_review_policy,
        operator_review_config_sha256=operator_review_config_sha256)
    workspace = workspace.resolve()
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    queue = DurableOperatorQueue(state_dir)
    queue.set_desired_state("running")
    components = _component_specs(
        workspace=workspace,
        host=host,
        port=port,
        frontend_dist=frontend_dist,
        state_dir=state_dir,
        operator_args=operator_args,
    )
    state_path = state_dir / "server.json"
    log_path = state_dir / "server.log"
    state = {
        "schema_version": 2,
        "pid": os.getpid(),
        "identity": identity,
        "host": host,
        "port": port,
        "workspace": str(workspace),
        "frontend_dist": str(frontend_dist),
        "log": str(log_path),
        "started_at": time.time(),
        "desired_state": "running",
        "supervisor": {"status": "starting", "ready": False, "last_heartbeat": time.time()},
        "components": {},
    }
    try:
        for component in components:
            _start_component(component, state_dir=state_dir)
            _wait_for_component(component)
            _write_projection(state_path, state, components, queue=queue)
        state["supervisor"] = {
            "status": "healthy",
            "ready": True,
            "last_heartbeat": time.time(),
        }
        _write_projection(state_path, state, components, queue=queue)
        _write_ready(ready_file, ready_token)

        while not stopping:
            health = "healthy"
            for component in components:
                failure = _component_failure(component)
                if failure:
                    restart_health = _restart_component(
                        component,
                        failure=failure,
                        state_dir=state_dir,
                        state_path=state_path,
                        state=state,
                        components=components,
                        queue=queue,
                    )
                    if restart_health == "failed":
                        health = "failed"
                    elif restart_health == "degraded" and health != "failed":
                        health = "degraded"
                    if restart_health != "running":
                        continue
                if component.status == "failed":
                    health = "failed"
                elif component.status != "running":
                    health = "degraded"
            state["supervisor"] = {
                "status": health,
                "ready": health != "failed",
                "last_heartbeat": time.time(),
            }
            _write_projection(state_path, state, components, queue=queue)
            time.sleep(HEARTBEAT_SECONDS)
        state["desired_state"] = "draining"
        queue.set_desired_state("draining")
        _write_projection(state_path, state, components, queue=queue)
        return 0
    except BaseException as exc:
        state["supervisor"] = {
            "status": "failed",
            "ready": False,
            "last_error": f"{exc.__class__.__name__}: {exc}",
            "last_heartbeat": time.time(),
        }
        _write_projection(state_path, state, components, queue=queue)
        return 1
    finally:
        ready_file.unlink(missing_ok=True)
        for component in reversed(components):
            _stop_component(component)
        for component in components:
            component.ready_path.unlink(missing_ok=True)
            component.heartbeat_path.unlink(missing_ok=True)
        try:
            queue.set_desired_state("stopped")
        except Exception:
            pass


def run_telegram_component(
    *,
    workspace: Path,
    ready_file: Path,
    ready_token: str,
    heartbeat_file: Path,
    heartbeat_token: str,
) -> int:
    from goodteam_os.telegram import poll_telegram_approvals, telegram_config_from_env

    _install_component_signal_handlers()
    heartbeat = ComponentHeartbeat(heartbeat_file, heartbeat_token)
    heartbeat.beat()
    _write_ready(ready_file, ready_token)
    if not telegram_config_from_env()["enabled"]:
        return _idle_until_signal(heartbeat=heartbeat.beat)
    poll_telegram_approvals(workspace=workspace, heartbeat=heartbeat.beat)
    return 0


def run_operator_component(
    *,
    workspace: Path,
    state_dir: Path,
    ready_file: Path,
    ready_token: str,
    heartbeat_file: Path,
    heartbeat_token: str,
    candidate_reviews=None,
    codex_review_admission=None,
) -> int:
    from goodteam_os.operator import OperatorService

    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    service = OperatorService(workspace=workspace, state_dir=state_dir,
                              candidate_reviews=candidate_reviews, codex_review_admission=codex_review_admission)
    service.recover()
    liveness = OperatorLivenessHeartbeat(
        queue=service.queue,
        emitter=ComponentHeartbeat(heartbeat_file, heartbeat_token),
    )
    liveness.start()
    _write_ready(ready_file, ready_token)
    try:
        while not stop_requested:
            liveness.progress("tick_started")
            service.tick()
            liveness.progress("tick_completed")
            active = service.queue.active_item()
            phase = str(active.get("phase", "")) if active else ""
            if phase == "observing_ci":
                time.sleep(15.0)
            elif phase == "waiting_telegram" or active is None:
                time.sleep(1.0)
            else:
                time.sleep(0.25)
    finally:
        liveness.stop()
    return 0


def _component_specs(
    *,
    workspace: Path,
    host: str,
    port: int,
    frontend_dist: Path,
    state_dir: Path,
    operator_args=(),
) -> list[Component]:
    runtime = state_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    def component(name: str, args: list[str]) -> Component:
        token = secrets.token_urlsafe(18)
        heartbeat_token = secrets.token_urlsafe(18)
        ready = runtime / f"{name}.ready"
        heartbeat = runtime / f"{name}.heartbeat.json"
        return Component(
            name=name,
            command=[
                sys.executable,
                "-m",
                "goodteam_os.cli",
                *args,
                "--ready-file",
                str(ready),
                f"--ready-token={token}",
                "--heartbeat-file",
                str(heartbeat),
                f"--heartbeat-token={heartbeat_token}",
            ],
            ready_path=ready,
            ready_token=token,
            heartbeat_path=heartbeat,
            heartbeat_token=heartbeat_token,
        )

    return [
        component(
            "web",
            [
                "serve",
                "--workspace",
                str(workspace),
                "--host",
                host,
                "--port",
                str(port),
                "--frontend-dist",
                str(frontend_dist),
            ],
        ),
        component("telegram", ["_telegram-component", "--workspace", str(workspace)]),
        component(
            "operator",
            ["_operator-component", "--workspace", str(workspace), "--state-dir", str(state_dir), *operator_args],
        ),
    ]


def _start_component(component: Component, *, state_dir: Path) -> None:
    component.ready_path.unlink(missing_ok=True)
    component.heartbeat_path.unlink(missing_ok=True)
    log_path = state_dir / f"{component.name}.log"
    with log_path.open("ab") as log_file:
        component.process = subprocess.Popen(
            component.command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    component.started_at = time.time()
    component.status = "starting"
    component.ready = False
    component.last_heartbeat = 0.0
    component.heartbeat_sequence = 0
    component.heartbeat_details = {}


def _wait_for_component(component: Component) -> None:
    deadline = time.monotonic() + COMPONENT_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if component.process is None or component.process.poll() is not None:
            raise RuntimeError(f"{component.name} component exited before readiness")
        try:
            ready = component.ready_path.read_text(encoding="utf-8") == component.ready_token
            if ready and _refresh_component_heartbeat(component):
                component.ready = True
                component.status = "running"
                component.last_error = ""
                return
        except (FileNotFoundError, OSError):
            pass
        time.sleep(0.05)
    raise RuntimeError(f"{component.name} component readiness timed out")


def _stop_component(component: Component) -> None:
    process = component.process
    if process is None:
        return

    def kill_owned_group():
        # _start_component creates this process as its session/group leader.
        # Its descendants retain that group after the leader exits, including
        # when web's bounded drain raises before our outer wait times out.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            return
        if component.name == "web":
            # Consume ownership once. A later stop must not signal a recycled
            # numeric group ID after this generation has been cleaned up.
            component.process = None

    if process.poll() is not None:
        if component.name == "web":
            kill_owned_group()
        return
    try:
        if component.name == "web":
            # Let web checkpoint its in-flight Goal/Worker before killing any
            # child process. Group-wide TERM would preempt the drain itself.
            os.kill(process.pid, signal.SIGTERM)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        if component.name == "web":
            kill_owned_group()
        return
    except PermissionError:
        return
    try:
        # A healthy explicit stop gets the Goal drain window. An unhealthy
        # component already selected for restart retains bounded fault recovery;
        # a SIGSTOPed web cannot run its signal handler or reach a checkpoint.
        timeout = (WEB_STOP_TIMEOUT_SECONDS if component.name == "web" and component.status != "restarting"
                   else COMPONENT_STOP_TIMEOUT_SECONDS)
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_owned_group()
        process.wait()
    else:
        if component.name == "web":
            # The leader's exit is not proof that every Worker has exited.
            # Reap remaining owned descendants before stop/restart returns.
            kill_owned_group()


def _component_exit_error(component: Component) -> str:
    code = component.process.poll() if component.process is not None else None
    return f"component exited with code {code}"


def _refresh_component_heartbeat(component: Component) -> bool:
    process = component.process
    if process is None or process.poll() is not None:
        return False
    try:
        payload = json.loads(component.heartbeat_path.read_text(encoding="utf-8"))
        token = str(payload.get("token", ""))
        pid = int(payload.get("pid", 0) or 0)
        sequence = int(payload.get("sequence", 0) or 0)
        updated_at = float(payload.get("updated_at", 0.0) or 0.0)
        details = payload.get("details", {})
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if (
        token != component.heartbeat_token
        or pid != process.pid
        or sequence <= 0
        or updated_at <= 0
        or not isinstance(details, dict)
    ):
        return False
    component.last_heartbeat = updated_at
    component.heartbeat_sequence = sequence
    component.heartbeat_details = dict(details)
    return True


def _component_failure(component: Component) -> str:
    process = component.process
    if process is None or process.poll() is not None:
        return _component_exit_error(component)
    _refresh_component_heartbeat(component)
    if component.status != "running" or not component.ready:
        return component.last_error or "component is alive but not ready"
    age = time.time() - component.last_heartbeat if component.last_heartbeat else float("inf")
    if age > COMPONENT_HEARTBEAT_TIMEOUT_SECONDS:
        return f"component heartbeat is stale ({age:.1f}s)"
    return ""


def _restart_component(
    component: Component,
    *,
    failure: str,
    state_dir: Path,
    state_path: Path,
    state: dict[str, Any],
    components: list[Component],
    queue: DurableOperatorQueue,
) -> str:
    if component.restart_count >= MAX_COMPONENT_RESTARTS:
        component.status = "failed"
        component.ready = False
        component.last_error = failure
        _pause_active_task_for_component_failure(queue, component)
        return "failed"
    component.restart_count += 1
    component.status = "restarting"
    component.ready = False
    component.last_error = failure
    _stop_component(component)
    _write_projection(state_path, state, components, queue=queue)
    _start_component(component, state_dir=state_dir)
    try:
        _wait_for_component(component)
    except RuntimeError as exc:
        component.last_error = str(exc)
        component.status = "restarting"
        component.ready = False
        _stop_component(component)
        return "degraded"
    return "running"


def _pause_active_task_for_component_failure(queue: DurableOperatorQueue, component: Component) -> None:
    try:
        item = queue.active_item()
        if item is None or item.get("status") == "paused":
            return
        queue.transition(
            str(item["id"]),
            status="paused",
            phase="paused",
            expected_phase=str(item["phase"]),
            updates={"last_failure_class": f"component_{component.name}_restart_exhausted"},
            event="component_failure_paused_task",
            payload={"component": component.name, "restart_count": component.restart_count},
        )
    except Exception:
        return


def _write_projection(
    state_path: Path,
    state: dict[str, Any],
    components: list[Component],
    *,
    queue: DurableOperatorQueue,
) -> None:
    state["components"] = {
        component.name: {
            "identity": f"{component.name}:{component.process.pid if component.process else 0}",
            "pid": component.process.pid if component.process else 0,
            "ready": component.ready,
            "status": component.status,
            "last_heartbeat": component.last_heartbeat,
            "heartbeat_sequence": component.heartbeat_sequence,
            "heartbeat_details": dict(component.heartbeat_details or {}),
            "last_error": component.last_error,
            "restart_count": component.restart_count,
            "terminal_failure": component.status == "failed",
        }
        for component in components
    }
    try:
        state["queue"] = queue.summary()
    except Exception as exc:
        state["queue"] = {"error": f"{exc.__class__.__name__}: {exc}"}
        state["supervisor"]["status"] = "failed"
        state["supervisor"]["ready"] = False
    temporary = state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(state_path)


def _write_ready(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")


def _install_component_signal_handlers() -> None:
    def stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def _idle_until_signal(*, heartbeat: Callable[[], None] | None = None) -> int:
    while True:
        if heartbeat is not None:
            heartbeat()
        time.sleep(1.0)
