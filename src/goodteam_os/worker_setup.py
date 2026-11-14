"""Run the project's configured setup inside the existing Worker process lifetime."""
from __future__ import annotations

import json
import os
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


def with_setup(command, *, setup, job_root, cwd, binary, reuse_completed=False):
    """Keep setup and the agent in one process group, so existing Stop applies."""
    path = job_root / "setup-input.json"
    value = {"setup": setup, "cwd": str(cwd), "binary": binary}
    if reuse_completed and path.exists() and (job_root / "setup-result.json").exists():
        if (json.loads(path.read_text(encoding="utf-8")) == value
                and json.loads((job_root / "setup-result.json").read_text(encoding="utf-8")).get("exit_code") == 0):
            return command
    path.write_text(json.dumps(value), encoding="utf-8")
    return [sys.executable, str(Path(__file__).resolve()), str(path), *command]


# Setup never needs the Worker's model/provider environment. Keep this list
# explicit: arbitrary setup.env values can reintroduce credential variables.
_SETUP_ENV = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "CI",
              "npm_config_cache", "PIP_CACHE_DIR", "YARN_CACHE_FOLDER",
              "PNPM_HOME", "CARGO_HOME", "RUSTUP_HOME"}


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
    if not isinstance(configured, dict) or any(key not in _SETUP_ENV or not isinstance(val, str)
                                             for key, val in configured.items()):
        raise ValueError("worker_setup.env contains unsupported installation variables.")
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
                result = subprocess.run(command, cwd=value["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr, check=False)
        record["exit_code"] = result.returncode
    except (OSError, ValueError) as exc:
        record.update(exit_code=1, error=str(exc))
    record["seconds"] = round(time.time() - started, 3)
    (path.parent / "setup-result.json").write_text(json.dumps(record), encoding="utf-8")
    if record["exit_code"]:
        print("Project setup failed; see setup-result.json and setup.stderr.txt.", file=sys.stderr)
        return record["exit_code"]
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == "__main__":
    sys.exit(main())


