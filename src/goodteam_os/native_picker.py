from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

PICKER_TIMEOUT_SECONDS = 60.0
CANCELLED_SENTINEL = "__GOODTEAM_CANCELLED__"
APPLESCRIPT = f"""
on run argv
  if (count of argv) is not 2 then error "GoodTeam picker requires mode and initial path."
  set pickerMode to item 1 of argv
  set initialPath to item 2 of argv
  try
    set initialLocation to POSIX file initialPath as alias
    if pickerMode is "directory" then
      set selectedItem to choose folder with prompt "Choose a directory for GoodTeam" default location initialLocation
    else if pickerMode is "file" then
      set selectedItem to choose file with prompt "Choose a file for GoodTeam" default location initialLocation
    else
      error "Unsupported GoodTeam picker mode."
    end if
    return POSIX path of selectedItem
  on error errorMessage number errorNumber
    if errorNumber is -128 then return "{CANCELLED_SENTINEL}"
    error errorMessage number errorNumber
  end try
end run
""".strip()

Runner = Callable[..., Any]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _initial_directory(initial_path: str) -> Path:
    candidate = Path(initial_path).expanduser().resolve() if initial_path.strip() else Path.home().resolve()
    if candidate.is_file():
        return candidate.parent
    if candidate.is_dir():
        return candidate
    for parent in candidate.parents:
        if parent.is_dir():
            return parent
    return Path.home().resolve()


def _unsupported_reason(*, host: str, platform: str, environ: Mapping[str, str]) -> str:
    if platform != "darwin":
        return "native_picker_unavailable"
    if not _is_loopback_host(host):
        return "non_loopback_host"
    if any(
        (
            _truthy(environ.get("CI")),
            _truthy(environ.get("GOODTEAM_HEADLESS")),
            _truthy(environ.get("GOODTEAM_DISABLE_NATIVE_PICKER")),
            bool(str(environ.get("SSH_CONNECTION", "")).strip()),
        )
    ):
        return "headless_environment"
    return ""


def choose_native_path(
    mode: str,
    initial_path: str = "",
    *,
    host: str,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Runner | None = None,
) -> dict[str, str]:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"file", "directory"}:
        raise ValueError("mode must be file or directory")

    runtime_platform = sys.platform if platform is None else platform
    runtime_environ = os.environ if environ is None else environ
    unsupported_reason = _unsupported_reason(
        host=host,
        platform=runtime_platform,
        environ=runtime_environ,
    )
    if unsupported_reason:
        return {"status": "unsupported", "reason": unsupported_reason}

    initial_directory = _initial_directory(initial_path)
    command: Sequence[str] = [
        "/usr/bin/osascript",
        "-e",
        APPLESCRIPT,
        normalized_mode,
        str(initial_directory),
    ]
    run = subprocess.run if runner is None else runner
    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=PICKER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "native_picker_timeout"}
    except OSError:
        return {"status": "error", "error": "native_picker_unavailable"}

    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if stdout == CANCELLED_SENTINEL or (completed.returncode != 0 and "-128" in stderr):
        return {"status": "cancelled", "path": ""}
    if completed.returncode != 0:
        return {"status": "error", "error": stderr or "native_picker_failed"}
    if not stdout:
        return {"status": "error", "error": "native_picker_returned_empty_path"}

    selected = Path(stdout).expanduser().resolve()
    if not selected.exists():
        return {"status": "error", "error": "selected_path_not_found"}
    if normalized_mode == "file" and not selected.is_file():
        return {"status": "error", "error": "selected_path_is_not_a_file"}
    if normalized_mode == "directory" and not selected.is_dir():
        return {"status": "error", "error": "selected_path_is_not_a_directory"}
    return {"status": "selected", "path": str(selected)}
