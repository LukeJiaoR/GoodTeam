from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
    state = _read_state()
    if not state:
        return {"running": False}
    pid = int(state.get("pid", 0))
    if pid > 0 and _managed_process_is_running(pid):
        return {"running": True, **state}
    _state_path().unlink(missing_ok=True)
    return {"running": False, "stale_pid": pid or None}


def restart_service(workspace: Path, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> dict[str, Any]:
    stop_service()
    state_dir = service_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
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
        "log": str(log_path),
        "started_at": time.time(),
    }
    _write_state(state)
    if _wait_until_ready(process, host, port):
        return {"running": True, **state}
    _state_path().unlink(missing_ok=True)
    raise RuntimeError(f"server failed to start; see {log_path}")


def stop_service() -> dict[str, Any]:
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


def _wait_until_ready(process: subprocess.Popen[bytes], host: str, port: int) -> bool:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((connect_host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


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
