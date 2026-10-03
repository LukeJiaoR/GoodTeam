#!/usr/bin/env python3
"""Bounded lifecycle for this repository's single, unprivileged Linux runner."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid


REPOSITORY = "LukeJiaoR/GoodTeam"
RUNNER = "goodteam-ci-01"
MARKER = ".owner.json"
DEFAULTS = {
    "cache_max_bytes": 4 * 1024**3,
    "cache_keep_days": 30,
    "evidence_keep_days": 14,
    "evidence_max_bytes": 1024**3,
    "logs_keep_days": 14,
    "logs_max_bytes": 512 * 1024**2,
    "stale_temp_hours": 24,
    "min_free_bytes": 8 * 1024**3,
    "min_free_percent": 15,
    "disk_check_seconds": 5,
}


def notice(message, level="warning"):
    print(f"::{level}::{message}", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"- CI runner {level}: {message}\n")


def owned(path):
    info = path.lstat()
    return info.st_uid == os.getuid() and not stat.S_ISLNK(info.st_mode) and path.resolve() == path


def read_marker(path, kind):
    if not owned(path) or not owned(path / MARKER):
        raise ValueError(f"Unowned or linked path: {path}")
    data = json.loads((path / MARKER).read_text())
    if (data.get("repository"), data.get("runner"), data.get("uid"), data.get("kind")) != (
        REPOSITORY, RUNNER, os.getuid(), kind,
    ):
        raise ValueError(f"Missing ownership proof: {path}")
    return data


def marked_directory(path, kind, **fields):
    # Never adopt a pre-existing unknown directory, even an empty one.
    path.mkdir(mode=0o700)
    data = dict(repository=REPOSITORY, runner=RUNNER, uid=os.getuid(), kind=kind, created=time.time(), **fields)
    (path / MARKER).write_text(json.dumps(data))
    return data


def process_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid, "start": fields[19],
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }, int(fields[1])


def worker_identity(root):
    pid = os.getppid()
    while pid > 1:
        identity, parent = process_identity(pid)
        if Path(f"/proc/{pid}/exe").resolve() == root / "actions-runner/bin/Runner.Worker":
            return identity
        pid = parent
    raise ValueError("Lifecycle hooks must be children of this runner's Runner.Worker")


def alive(identity):
    try:
        return process_identity(identity["pid"])[0] == identity
    except FileNotFoundError:
        return False


def references(processes=None):
    """An unreadable same-account process blocks deletion; disappearing PIDs do not."""
    paths = []
    ancestors = set()
    pid = os.getpid()
    while pid > 1:
        ancestors.add(pid)
        _, pid = process_identity(pid)
    for process in Path("/proc").iterdir() if processes is None else processes:
        if not process.name.isdigit():
            continue
        try:
            # /proc directory ownership can become root for a nondumpable
            # same-account process; use its actual credentials, not st_uid.
            uid_line = next(line for line in (process / "status").read_text().splitlines() if line.startswith("Uid:"))
            if str(os.getuid()) not in uid_line.split()[1:]:
                continue
            for link in [process / "cwd", process / "exe", *(process / "fd").iterdir()]:
                try:
                    target = os.readlink(link).removesuffix(" (deleted)")
                    if target.startswith("/"):
                        paths.append(Path(target))
                except FileNotFoundError:
                    pass
            for mapping in (process / "maps").read_text().splitlines():
                fields = mapping.split(maxsplit=5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    paths.append(Path(fields[5].removesuffix(" (deleted)")))
            if int(process.name) not in ancestors:
                # A surviving job child can use its temp/cache again even when
                # it currently has no fd there. Hook ancestors themselves inherit
                # these variables but are not consumers of the job's resources.
                for entry in (process / "environ").read_bytes().split(b"\0"):
                    key, _, value = entry.partition(b"=")
                    if key in (b"TMPDIR", b"PIP_CACHE_DIR", b"npm_config_cache", b"PLAYWRIGHT_BROWSERS_PATH"):
                        if value.startswith(b"/"):
                            paths.append(Path(os.fsdecode(value)))
        except FileNotFoundError:
            pass
    return paths


def remove_owned(path, used):
    used = [*used, *references()]  # Recheck immediately before each deletion.
    if any(p == path or path in p.parents for p in used):
        notice(f"Retaining in-use resource: {path}")
        return False
    if not owned(path):
        raise ValueError(f"Refusing linked/unowned deletion: {path}")
    device = path.stat().st_dev
    if path.is_dir():
        for directory, dirs, files in os.walk(path, followlinks=False):
            children = [Path(directory) / name for name in [*dirs, *files]]
            if any(p.lstat().st_uid != os.getuid() or p.lstat().st_dev != device or p.is_mount() for p in children):
                raise ValueError(f"Refusing foreign owner/filesystem: {path}")
    if path.is_mount():
        raise ValueError(f"Refusing mount deletion: {path}")
    if path.is_dir():
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError("A symlink-safe rmtree implementation is required")
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"Removed owned CI resource: {path}", flush=True)
    return True


def size(path):
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


class Lifecycle:
    def __init__(self, root):
        self.root = root.absolute()
        if not owned(self.root):
            raise ValueError("Runner home must be owned by the current account, without symlink components")
        registration = self.root / "actions-runner/.runner"
        if not owned(registration):
            raise ValueError("Runner registration must be owned by this account")
        runner = json.loads(registration.read_text())
        if runner.get("agentName") != RUNNER or runner.get("gitHubUrl", "").rstrip("/") != (
            f"https://github.com/{REPOSITORY}"
        ):
            raise ValueError("Runner registration does not belong to GoodTeam")
        self.policy = DEFAULTS.copy()
        policy_file = self.root / "hooks/cleanup-policy.json"
        if policy_file.exists():
            if policy_file.is_symlink():
                raise ValueError("Policy must not be a symlink")
            overrides = json.loads(policy_file.read_text())
            if any(k not in DEFAULTS or type(v) is not int or v <= 0 for k, v in overrides.items()):
                raise ValueError("Policy requires known keys with positive integer values")
            self.policy.update(overrides)
        if self.policy["min_free_percent"] > 100:
            raise ValueError("min_free_percent cannot exceed 100")
        self.state = self.root / ".ci-lifecycle"

    def initialize(self):
        if not self.state.exists():
            marked_directory(self.state, "namespace")
        read_marker(self.state, "namespace")
        for name, kind in (("jobs", "jobs"), ("cache", "caches")):
            path = self.state / name
            if not path.exists():
                marked_directory(path, kind)
            read_marker(path, kind)

    def disk_ok(self):
        usage = shutil.disk_usage(self.root)
        if usage.free < self.policy["min_free_bytes"] or usage.free * 100 < usage.total * self.policy["min_free_percent"]:
            notice(f"Low disk on {self.root}: {usage.free / 1024**3:.2f} GiB free. Stopping CI allocation.", "error")
            return False
        return True

    def sweep(self, current=None):
        used = references()
        now = time.time()
        active = False
        evidence_paths = []
        for job in (self.state / "jobs").iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", job.name):
                continue
            try:
                marker = read_marker(job, "job")
                evidence_paths.append(job / "evidence")
                finishing = marker["worker"] == current
                if finishing:
                    marker["finished"] = now
                    (job / MARKER).write_text(json.dumps(marker))
                if not finishing and alive(marker["worker"]):
                    active = True
                    continue
                if not finishing and not marker.get("finished") and (
                    now - marker["created"] < self.policy["stale_temp_hours"] * 3600
                ):
                    continue
                temporary = job / "tmp"
                if temporary.exists() and not remove_owned(temporary, used):
                    continue
                evidence = job / "evidence"
                if evidence.exists() and now - marker.get("finished", marker["created"]) >= (
                    self.policy["evidence_keep_days"] * 86400
                ):
                    if not remove_owned(evidence, used):
                        continue
                if not evidence.exists() or not any(evidence.iterdir()):
                    remove_owned(job, used)
            except (OSError, ValueError, KeyError) as error:
                active = True  # Unknown liveness must also protect shared caches.
                notice(f"Skipped job cleanup: {error}")
        caches = []
        for name in ("pip", "npm", "playwright"):
            cache = self.state / "cache" / name
            if not cache.exists():
                continue
            marker = read_marker(cache, "cache")
            # Whole-cache eviction preserves pip/npm/browser internal formats.
            caches.append((marker["created"], cache, size(cache)))
        total = sum(item[2] for item in caches)
        for created, cache, count in sorted(caches):
            if now - created > self.policy["cache_keep_days"] * 86400 or total > self.policy["cache_max_bytes"]:
                if not active and remove_owned(cache, used):
                    total -= count
        if total > self.policy["cache_max_bytes"]:
            notice("Cache cap exceeded by protected resources; refusing to allocate another job", "error")
            raise ValueError("Cannot safely meet cache cap")
        evidence_bytes = sum(size(path) for path in evidence_paths if path.exists() and owned(path))
        if evidence_bytes > self.policy["evidence_max_bytes"]:
            notice("Job evidence budget exceeded; retention and live-resource protection take priority")
        diagnostics = self.root / "actions-runner/_diag"
        if diagnostics.exists():
            if not owned(diagnostics):
                raise ValueError("Unowned diagnostic directory")
            for log in diagnostics.iterdir():
                if re.fullmatch(r"(?:Runner|Worker)_\d{8}-\d{6}-utc(?:_\d+)?\.log", log.name) and owned(log):
                    if now - log.stat().st_mtime > self.policy["logs_keep_days"] * 86400:
                        remove_owned(log, used)
            if size(diagnostics) > self.policy["logs_max_bytes"]:
                notice("Diagnostic log budget exceeded; recent, open, and unknown evidence is retained")

    def start(self):
        worker = worker_identity(self.root)
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise ValueError("Only GoodTeam jobs can allocate lifecycle resources")
        self.sweep()
        if not self.disk_ok():
            return 75
        job = self.state / "jobs" / uuid.uuid4().hex
        marked_directory(job, "job", worker=worker, run=os.environ.get("GITHUB_RUN_ID"),
                         attempt=os.environ.get("GITHUB_RUN_ATTEMPT"), job=os.environ.get("GITHUB_JOB"))
        for name in ("tmp", "evidence"):
            (job / name).mkdir(mode=0o700)
        env = {
            "GOODTEAM_CI_JOB_TEMP": job / "tmp", "GOODTEAM_CI_EVIDENCE": job / "evidence",
            "TMPDIR": job / "tmp",
        }
        for name, variable in (("pip", "PIP_CACHE_DIR"), ("npm", "npm_config_cache"),
                               ("playwright", "PLAYWRIGHT_BROWSERS_PATH")):
            cache = self.state / "cache" / name
            if not cache.exists():
                marked_directory(cache, "cache")
            read_marker(cache, "cache")
            (cache / "data").mkdir(mode=0o700, exist_ok=True)
            if not owned(cache / "data"):
                raise ValueError(f"Linked/unowned cache data: {cache}")
            env[variable] = cache / "data"
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in env.items())
        return 0

    def finish(self):
        self.sweep(current=worker_identity(self.root))
        self.disk_ok()  # Completion alerts without hiding the job's original result.
        return 0

    def run(self, command):
        if not command or not self.disk_ok():
            return 75
        interrupted = []
        previous = {s: signal.signal(s, lambda signum, frame: interrupted.append(signum))
                    for s in (signal.SIGINT, signal.SIGTERM)}
        child = None
        try:
            child = subprocess.Popen(command, start_new_session=True)
            while child.poll() is None:
                if interrupted or not self.disk_ok():
                    return 128 + interrupted[0] if interrupted else 75
                try:
                    child.wait(timeout=self.policy["disk_check_seconds"])
                except subprocess.TimeoutExpired:
                    pass
            if interrupted:
                return 128 + interrupted[0]
            return child.returncode if child.returncode >= 0 else 128 - child.returncode
        finally:
            if child is not None:
                # This wrapper created the group; it never signals an existing host process.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/data/goodteam-ci"), help="Dedicated runner home")
    parser.add_argument("phase", choices=("started", "completed", "run"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        lifecycle = Lifecycle(args.root)
        if args.phase == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            return lifecycle.run(command)
        lifecycle.initialize()
        lock = lifecycle.state / ".lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lifecycle.start() if args.phase == "started" else lifecycle.finish()
    except (OSError, ValueError, KeyError) as error:
        notice(f"Runner lifecycle refused unsafe or unavailable cleanup: {error}", "error")
        return 1 if args.phase != "completed" else 0


if __name__ == "__main__":
    sys.exit(main())
