from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from goodteam_os.operator_queue import DurableOperatorQueue


HEARTBEAT_SECONDS = 0.5
COMPONENT_HEARTBEAT_TIMEOUT_SECONDS = float(os.environ.get("GOODTEAM_COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", "30"))
COMPONENT_START_TIMEOUT_SECONDS = 8.0
MAX_COMPONENT_RESTARTS = 3


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


class ComponentHeartbeat:
    """Child-owned progress heartbeat with generation and PID binding."""

    def __init__(self, path: Path, token: str):
        self.path = Path(path)
        self.token = token
        self.sequence = 0

    def beat(self) -> None:
        self.sequence += 1
        payload = {
            "schema_version": 1,
            "token": self.token,
            "pid": os.getpid(),
            "sequence": self.sequence,
            "updated_at": time.time(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


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
) -> int:
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
) -> int:
    from goodteam_os.operator import OperatorService

    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    service = OperatorService(workspace=workspace, state_dir=state_dir)
    service.recover()
    heartbeat = ComponentHeartbeat(heartbeat_file, heartbeat_token)
    heartbeat.beat()
    _write_ready(ready_file, ready_token)
    while not stop_requested:
        service.tick()
        heartbeat.beat()
        active = service.queue.active_item()
        phase = str(active.get("phase", "")) if active else ""
        if phase == "observing_ci":
            time.sleep(15.0)
        elif phase == "waiting_telegram" or active is None:
            time.sleep(1.0)
        else:
            time.sleep(0.25)
    return 0


def _component_specs(
    *,
    workspace: Path,
    host: str,
    port: int,
    frontend_dist: Path,
    state_dir: Path,
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
                "--ready-token",
                token,
                "--heartbeat-file",
                str(heartbeat),
                "--heartbeat-token",
                heartbeat_token,
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
            ["_operator-component", "--workspace", str(workspace), "--state-dir", str(state_dir)],
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
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


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
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if token != component.heartbeat_token or pid != process.pid or sequence <= 0 or updated_at <= 0:
        return False
    component.last_heartbeat = updated_at
    component.heartbeat_sequence = sequence
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
