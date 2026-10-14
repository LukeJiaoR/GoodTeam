from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from goodteam_os.frontend_assets import resolve_frontend_dist
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.service_timeouts import SERVICE_STOP_TIMEOUT_SECONDS

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

DEFAULT_PORT = 21001
START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 8.0


def service_state_dir() -> Path:
    override = os.environ.get("GOODTEAM_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "goodteam"


def service_status() -> dict[str, Any]:
    with _service_lock():
        return _service_status_unlocked()


def _service_status_unlocked() -> dict[str, Any]:
    state = _read_state()
    if not state:
        return {"running": False}
    pid = int(state.get("pid", 0) or 0)
    identity = str(state.get("identity", "") or "")
    if pid > 0 and _state_process_is_running(pid, identity):
        state["running"] = True
        try:
            state["queue"] = DurableOperatorQueue(service_state_dir()).summary()
        except Exception as exc:
            state["queue"] = {"error": f"{exc.__class__.__name__}: {exc}"}
        return state
    _state_path().unlink(missing_ok=True)
    result = {"running": False, "stale_pid": pid or None}
    if pid and identity and _process_is_running(pid):
        result["identity_mismatch"] = True
    return result


def restart_service(workspace: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT, *,
                    operator_review_config=None, local_codex_policy=None, trusted_codex_review_policy=None,
                    operator_review_config_sha256="") -> dict[str, Any]:
    from goodteam_os.operator_startup import startup_arguments
    operator_args = startup_arguments(operator_review_config=operator_review_config,
        local_codex_policy=local_codex_policy, trusted_codex_review_policy=trusted_codex_review_policy,
        operator_review_config_sha256=operator_review_config_sha256)
    frontend_dist = resolve_frontend_dist()
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise RuntimeError(f"workspace does not exist: {workspace}")
    with _service_lock():
        return _restart_service_unlocked(workspace, host=host, port=port, frontend_dist=frontend_dist,
                                         operator_args=operator_args)


def _restart_service_unlocked(workspace: Path, *, host: str, port: int, frontend_dist: Path, operator_args=()) -> dict[str, Any]:
    old_state = _read_state()
    _preflight_port(host, port, old_state=old_state)
    _stop_service_unlocked(set_stopped=False)

    state_dir = service_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
    ready_path = state_dir / "supervisor.ready"
    ready_token = secrets.token_urlsafe(24)
    identity = secrets.token_urlsafe(18)
    ready_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "supervise",
        "--workspace",
        str(workspace),
        "--host",
        host,
        "--port",
        str(port),
        "--frontend-dist",
        str(frontend_dist),
        "--state-dir",
        str(state_dir),
        f"--identity={identity}",
        "--ready-file",
        str(ready_path),
        f"--ready-token={ready_token}",
        *operator_args,
    ]
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    boot_state = {
        "schema_version": 2,
        "pid": process.pid,
        "identity": identity,
        "host": host,
        "port": port,
        "workspace": str(workspace),
        "frontend_dist": str(frontend_dist),
        "log": str(log_path),
        "started_at": time.time(),
        "desired_state": "running",
        "supervisor": {"status": "starting", "ready": False},
        "components": {},
    }
    try:
        _write_state(boot_state)
        if not _wait_until_ready(process, ready_path, ready_token):
            failure = _read_state().get("supervisor", {}).get("last_error", "")
            suffix = f": {failure}" if failure else ""
            raise RuntimeError(f"supervisor failed to start{suffix}; see {log_path}")
        payload = _read_state()
        return {"running": True, **payload}
    except BaseException:
        _terminate_process_group(process)
        current = _read_state()
        if int(current.get("pid", 0) or 0) == process.pid:
            _state_path().unlink(missing_ok=True)
        raise
    finally:
        ready_path.unlink(missing_ok=True)


def stop_service() -> dict[str, Any]:
    with _service_lock():
        return _stop_service_unlocked()


def _stop_service_unlocked(*, set_stopped: bool = True) -> dict[str, Any]:
    state = _read_state()
    if not state:
        if set_stopped:
            DurableOperatorQueue(service_state_dir()).set_desired_state("stopped")
        return {"stopped": False, "reason": "not running"}
    pid = int(state.get("pid", 0) or 0)
    identity = str(state.get("identity", "") or "")
    if pid <= 0 or not _state_process_is_running(pid, identity):
        mismatch = pid > 0 and _process_is_running(pid)
        _state_path().unlink(missing_ok=True)
        if set_stopped:
            DurableOperatorQueue(service_state_dir()).set_desired_state("stopped")
        return {
            "stopped": False,
            "reason": "identity mismatch" if mismatch else "not running",
            "pid": pid or None,
        }

    queue = DurableOperatorQueue(service_state_dir())
    queue.set_desired_state("draining")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + SERVICE_STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and _state_process_is_running(pid, identity):
        time.sleep(0.05)
    if _state_process_is_running(pid, identity):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if set_stopped:
        queue.set_desired_state("stopped")
    _state_path().unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


@contextmanager
def _service_lock() -> Iterator[None]:
    state_dir = service_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "server.lock").open("a+b") as lock_file:
        if os.name == "nt":
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _wait_until_ready(process: subprocess.Popen[bytes], ready_path: Path, ready_token: str) -> bool:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if ready_path.read_text(encoding="utf-8") == ready_token:
                return True
        except (FileNotFoundError, OSError):
            pass
        time.sleep(0.05)
    return False


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _preflight_port(host: str, port: int, *, old_state: dict[str, Any]) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise RuntimeError(f"invalid port: {port}")
    old_pid = int(old_state.get("pid", 0) or 0)
    old_identity = str(old_state.get("identity", "") or "")
    same_managed_listener = (
        old_pid
        and _state_process_is_running(old_pid, old_identity)
        and str(old_state.get("host", "")) == host
        and int(old_state.get("port", 0) or 0) == port
    )
    if same_managed_listener:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except PermissionError:
        # Some restricted test/container environments prohibit bind probes.
        # The web component readiness handshake remains the final authority.
        return
    except OSError as exc:
        raise RuntimeError(f"configured port {host}:{port} is occupied by an unmanaged process") from exc


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return True
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


def _managed_process_is_running(pid: int, *, identity: str = "") -> bool:
    if not _process_is_running(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return False
    command = result.stdout.strip()
    expected = "goodteam_os.cli supervise"
    return result.returncode == 0 and expected in command and (not identity or identity in command)


def _state_process_is_running(pid: int, identity: str) -> bool:
    if identity:
        return _managed_process_is_running(pid, identity=identity)
    return _managed_process_is_running(pid)


def _state_path() -> Path:
    return service_state_dir() / "server.json"


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
