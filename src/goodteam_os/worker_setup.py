"""Read optional project setup context for ordinary Planner/Worker tasks."""
from __future__ import annotations

from pathlib import Path


def load_worker_setup(config_path):
    from goodteam_os.models import _parse_simple_yaml

    value = _parse_simple_yaml(Path(config_path)).get("worker_setup")
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("worker_setup must be a mapping.")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("worker_setup.command must be a nonempty argument list.")
    return value


def configured_setup_tool(job_root, context, *, workspace):
    """Describe an optional command using this task's captured setup context."""
    import json
    import sys
    from goodteam_os.memory_storage import atomic_write

    setup = context.get("worker_setup")
    environment = context.get("project_environment")
    if not setup or not environment:
        return None
    path = Path(job_root) / "configured-setup.json"
    atomic_write(path, json.dumps({"setup": setup, "cwd": str(Path(workspace).resolve())}).encode())
    return [sys.executable, str(Path(__file__).resolve()), str(path)]


def run_configured_setup(path):
    """Execute only when selected by an ordinary Worker task, never at startup.

    Installation-only variables do not change the agent runtime. This command
    uses the invoking Worker's process lifetime and actual permissions.
    """
    import json
    import os
    import subprocess

    context = json.loads(Path(path).read_text(encoding="utf-8"))
    setup = context["setup"]
    from goodteam_os.providers import PROVIDER_KEY_ENV

    # This value is injected by GoodTeam for model authentication, not part of
    # the project installation environment. Keep all actual host/project values.
    environment = dict(os.environ)
    environment.pop(PROVIDER_KEY_ENV, None)
    environment.update(setup.get("env", {}))
    return subprocess.run(setup["command"], cwd=context["cwd"],
                          env=environment, check=False).returncode


if __name__ == "__main__":
    import sys
    raise SystemExit(run_configured_setup(sys.argv[1]))
