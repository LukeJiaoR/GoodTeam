"""Run the project's configured setup inside the existing Worker process lifetime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _network_access(value):
    """Accept only explicit booleans, including this project's YAML scalars."""
    if type(value) is bool:
        value["network_access"] = _network_access(value.get("network_access", False))
    return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("worker_setup.network_access must be true or false.")


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


def with_setup(command, *, setup, job_root, cwd, binary):
    """Keep setup and the agent in one process group, so existing Stop applies."""
    path = job_root / "setup-input.json"
    path.write_text(json.dumps({"setup": setup, "cwd": str(cwd), "binary": binary}), encoding="utf-8")
    return [sys.executable, str(Path(__file__).resolve()), str(path), *command]


def main():
    path = Path(sys.argv[1])
    value = json.loads(path.read_text(encoding="utf-8"))
    setup = value["setup"]
    environment = dict(os.environ)
    # Installation does not need the agent's model credentials.
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "GOODTEAM_CODEX_PROVIDER_API_KEY"):
        environment.pop(key, None)
    environment.update(setup.get("env", {}))
    cache_paths = [str(Path(item).expanduser().resolve()) for item in setup.get("cache_paths", [])]
    for item in cache_paths:
        Path(item).mkdir(parents=True, exist_ok=True)
    filesystem = ", ".join(json.dumps(item) + '="write"' for item in cache_paths)
    network = json.dumps(_network_access(setup.get("network_access", False)))
    policy = 'permissions.goodteam-setup={extends=":workspace", filesystem={' + filesystem + '}, network={enabled=' + network + '}}'
    command = [value["binary"], "sandbox", "-P", "goodteam-setup", "-C", value["cwd"],
               "-c", policy, "--", *setup["command"]]
    started = time.time()
    record = {"command": setup["command"], "cwd": value["cwd"], "started_at": started,
              "stdout_path": str(path.parent / "setup.stdout.txt"),
              "stderr_path": str(path.parent / "setup.stderr.txt")}
    try:
        with Path(record["stdout_path"]).open("w") as stdout, Path(record["stderr_path"]).open("w") as stderr:
            result = subprocess.run(command, cwd=value["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, check=False)
        record["exit_code"] = result.returncode
    except OSError as exc:
        record.update(exit_code=1, error=str(exc))
    record["seconds"] = round(time.time() - started, 3)
    (path.parent / "setup-result.json").write_text(json.dumps(record), encoding="utf-8")
    if record["exit_code"]:
        print("Project setup failed; see setup-result.json and setup.stderr.txt.", file=sys.stderr)
        return record["exit_code"]
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == "__main__":
    sys.exit(main())
