from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from goodteam_os.frontend_assets import resolve_frontend_dist

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

DEFAULT_PORT = 21001
START_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0


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
    pid = int(state.get("pid", 0))
    if pid > 0 and _managed_process_is_running(pid):
        return {"running": True, **state}
    _state_path().unlink(missing_ok=True)
    return {"running": False, "stale_pid": pid or None}


def restart_service(workspace: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> dict[str, Any]:
    frontend_dist = resolve_frontend_dist()
    with _service_lock():
        return _restart_service_unlocked(workspace, host=host, port=port, frontend_dist=frontend_dist)


def _restart_service_unlocked(workspace: Path, *, host: str, port: int, frontend_dist: Path) -> dict[str, Any]:
    _stop_service_unlocked()
    state_dir = service_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
    ready_path = state_dir / "server.ready"
    ready_token = secrets.token_urlsafe(24)
    ready_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "goodteam_os.cli",
        "serve",
        "--workspace",
        str(workspace.resolve()),
        "--host",
        host,
        "--port",
        str(port),
        "--frontend-dist",
        str(frontend_dist),
        "--ready-file",
        str(ready_path),
        "--ready-token",
        ready_token,
    ]
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    state = {
        "pid": process.pid,
        "host": host,
        "port": port,
        "workspace": str(workspace.resolve()),
        "frontend_dist": str(frontend_dist),
        "log": str(log_path),
        "started_at": time.time(),
    }
    try:
        _write_state(state)
        if not _wait_until_ready(process, ready_path, ready_token):
            raise RuntimeError(f"server failed to start; see {log_path}")
        return {"running": True, **state}
    except BaseException:
        _terminate_process(process)
        _state_path().unlink(missing_ok=True)
        raise
    finally:
        ready_path.unlink(missing_ok=True)


def stop_service() -> dict[str, Any]:
    with _service_lock():
        return _stop_service_unlocked()


def _stop_service_unlocked() -> dict[str, Any]:
    state = _read_state()
    if not state:
        return {"stopped": False, "reason": "not running"}
    pid = int(state.get("pid", 0))
    if pid <= 0 or not _managed_process_is_running(pid):
        _state_path().unlink(missing_ok=True)
        return {"stopped": False, "reason": "not running"}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and _managed_process_is_running(pid):
        time.sleep(0.05)
    if _managed_process_is_running(pid):
        os.kill(pid, signal.SIGKILL)
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


def _managed_process_is_running(pid: int) -> bool:
    if not _process_is_running(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return False
    command = result.stdout.strip()
    return result.returncode == 0 and "goodteam_os.cli serve" in command


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
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)
