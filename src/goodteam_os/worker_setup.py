"""Run the project's configured setup inside the existing Worker process lifetime."""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import tempfile


def _network_access(value):
    """Accept only explicit booleans, including this project's YAML scalars."""
    if type(value) is bool:
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
    value["network_access"] = _network_access(value.get("network_access", False))
    return value


def setup_timeout_seconds(setup, *, default=1800.0):
    """Installation wall-clock allowance, independent of Pi RPC request time."""
    import math

    raw = setup.get("timeout_seconds", default)
    if isinstance(raw, bool):
        raise ValueError("worker_setup.timeout_seconds must be a positive finite number.")
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker_setup.timeout_seconds must be a positive finite number.") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("worker_setup.timeout_seconds must be a positive finite number.")
    return seconds


def with_setup(command, *, setup, job_root, cwd, binary, reuse_completed=False, timeout_seconds=None,
               project_environment=None, workspace=None):
    """Run setup before the agent; the wrapper owns installer cancellation."""
    path = job_root / "setup-input.json"
    value = {"setup": setup, "cwd": str(cwd), "binary": binary}
    if timeout_seconds is not None:
        value["timeout_seconds"] = timeout_seconds
    if project_environment is not None:
        value.update(project_environment=project_environment, workspace=str(workspace))
    if reuse_completed and path.exists() and (job_root / "setup-result.json").exists():
        if (json.loads(path.read_text(encoding="utf-8")) == value
                and json.loads((job_root / "setup-result.json").read_text(encoding="utf-8")).get("exit_code") == 0):
            _bind_prepared_environment(value)
            return command
    # A retried wrapper must not observe a previous attempt's receipt.
    (job_root / "setup-result.json").unlink(missing_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return [sys.executable, str(Path(__file__).resolve()), str(path), *command]


# Do not inherit the host's provider/authentication environment. Explicit project
# installation variables are configuration owned by Planner, not a fixed list
# of package managers. Credential provisioning still follows its own authority.

def _setup_command(value, private):
    setup = value["setup"]
    cwd = Path(value["cwd"]).resolve()
    homes = {Path.home().resolve()}
    # Worker HOME may already be isolated; retain the OS account home boundary.
    try:
        import pwd
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())
    except (ImportError, KeyError):
        pass
    credentials = [home / name for home in homes for name in (
        ".codex", ".aws", ".ssh", ".config", ".azure", ".kube", ".cargo",
        ".docker", ".gnupg", ".npmrc", ".pypirc", ".netrc", ".git-credentials")]
    if os.environ.get("CODEX_HOME"):
        credentials.append(Path(os.environ["CODEX_HOME"]).expanduser().resolve())
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
                   if key in os.environ}
    configured = setup.get("env", {})
    if not isinstance(configured, dict) or any(not isinstance(key, str) or not key or "=" in key or "\0" in key
                                             or not isinstance(val, str) or "\0" in val
                                             for key, val in configured.items()):
        raise ValueError("worker_setup.env requires valid environment names and string values.")
    environment.update(configured)
    # Fresh for every invocation, not a shared HOME retained from another job.
    home, temporary = private / "home", private / "tmp"
    home.mkdir()
    temporary.mkdir()
    environment.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(temporary),
                       TMP=str(temporary), TEMP=str(temporary), CODEX_HOME=str(home / ".codex"))
    raw_paths = setup.get("cache_paths", [])
    if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
        raise ValueError("worker_setup.cache_paths must be a list of paths.")
    caches = [Path(item).expanduser().resolve() for item in raw_paths]
    for cache in caches:
        if (cache == Path(cache.anchor) or any(cache == home or cache in home.parents for home in homes)
                or cache == cwd or cache in cwd.parents
                or any(cache == secret or cache in secret.parents or secret in cache.parents
                       for secret in credentials)):
            raise ValueError("worker_setup cache overlaps a source root or credential directory.")
        cache.mkdir(parents=True, exist_ok=True)
    access = {":root": "deny", ":minimal": "read", ":tmpdir": "write", ":slash_tmp": "deny",
              str(cwd): "write", str(home): "write", str(temporary): "write"}
    access.update({str(cache): "write" for cache in caches})
    # Explicitly preserve credential exclusions even inside runtime/minimal paths.
    access.update({str(secret): "deny" for secret in credentials})
    filesystem = ", ".join(json.dumps(key) + "=" + json.dumps(mode) for key, mode in access.items())
    network = json.dumps(_network_access(setup.get("network_access", False)))
    policy = 'permissions.goodteam-setup={extends=":workspace", filesystem={' + filesystem + '}, network={enabled=' + network + '}}'
    command = [value["binary"], "sandbox", "-P", "goodteam-setup", "-C", str(cwd),
               "-c", policy, "--", *setup["command"]]
    return command, environment


def _bind_prepared_environment(value):
    if value.get("project_environment") is not None:
        # The wrapper is also invoked as a script, outside the package module path.
        if not __package__:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from goodteam_os.project_environment import bind_environment
        bind_environment(Path(value["workspace"]), value["project_environment"])


def _bounded_setup(command, *, cwd, env, stdout, stderr, timeout):
    """Own the installer group while preserving wrapper Stop/cancellation."""
    process = None
    previous = {}

    def interrupted(signum, _frame):
        raise InterruptedError("Project setup interrupted by signal " + str(signum))

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, interrupted)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        return process.wait(timeout=timeout)
    finally:
        # Include subprocesses even if the installer parent has already exited.
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    path = Path(sys.argv[1])
    value = json.loads(path.read_text(encoding="utf-8"))
    setup = value["setup"]
    started = time.time()
    record = {"command": setup["command"], "cwd": value["cwd"], "started_at": started,
              "stdout_path": str(path.parent / "setup.stdout.txt"),
              "stderr_path": str(path.parent / "setup.stderr.txt")}
    try:
        with tempfile.TemporaryDirectory(prefix="setup-", dir=path.parent) as private:
            command, environment = _setup_command(value, Path(private))
            with Path(record["stdout_path"]).open("w") as stdout, Path(record["stderr_path"]).open("w") as stderr:
                if value.get("timeout_seconds") is not None:
                    code = _bounded_setup(command, cwd=value["cwd"], env=environment,
                                          stdout=stdout, stderr=stderr, timeout=value["timeout_seconds"])
                else:
                    code = subprocess.run(command, cwd=value["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                          stdout=stdout, stderr=stderr, check=False).returncode
        if code == 0:
            _bind_prepared_environment(value)
        record["exit_code"] = code
    except subprocess.TimeoutExpired:
        record.update(exit_code=124, error="Project setup exceeded its installation time budget.")
    except (OSError, ValueError) as exc:
        record.update(exit_code=1, error=str(exc))
    record["seconds"] = round(time.time() - started, 3)
    receipt = path.parent / "setup-result.json"
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(receipt)
    if record["exit_code"]:
        print("Project setup failed; see setup-result.json and setup.stderr.txt.", file=sys.stderr)
        return record["exit_code"]
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == "__main__":
    sys.exit(main())


