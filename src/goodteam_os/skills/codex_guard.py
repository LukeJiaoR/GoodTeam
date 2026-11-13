"""Standalone, model-free pre-exec guard copied into the read-only input mount.

This is trusted adapter code, not a Skill hook/runner. It never reads host paths,
credentials or user configuration; it checks the already isolated container and
execs only the pinned CLI. It emits no invented native events or PASS record.
Actual model/egress/discovery conformance remains independently required.
"""
import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys

BINARY = "/runtime/bin/codex"
# Exact version's system/config + repo/ancestor/user roots. All must be absent
# before CLI startup; bundled skills are separately disabled in strict config.
# SKILL.md underneath sources/{base,head}/ is deliberately NOT removed.
FORBIDDEN_ROOTS = (
    "/etc/codex", "/.codex", "/.agents", "/AGENTS.md", "/AGENTS.override.md",
    "/review/.git", "/review/.codex", "/review/.agents", "/review/AGENTS.md", "/review/AGENTS.override.md",
    "/home/worker/.codex", "/home/worker/.agents", "/codex-state/skills",
    "/codex-state/config.toml", "/codex-state/auth.json", "/codex-state/managed_config.toml",
)


def verify(expected_digest, expected_version):
    if (re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None
            or len(expected_version) > 128
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?", expected_version) is None):
        raise ValueError("invalid admitted native identity")
    if platform.system() != "Linux" or platform.machine() != "aarch64" or os.getuid() != 65534 or os.getgid() != 65534:
        raise ValueError("native platform or uid mismatch")
    if os.getcwd() != "/review":
        raise ValueError("unexpected native working directory")
    if any(os.path.lexists(path) for path in FORBIDDEN_ROOTS):
        raise ValueError("native discovery/config root is not empty")
    for root in ("/", "/runtime", "/review"):
        if not os.statvfs(root).f_flag & os.ST_RDONLY:
            raise ValueError("native root is not physically read-only")
    digest = hashlib.sha256()
    fd = os.open(BINARY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                or info.st_mode & 0o022 or not info.st_mode & 0o111 or info.st_size > 512 * 1024 * 1024):
            raise ValueError("unsafe native executable")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if "sha256:" + digest.hexdigest() != expected_digest:
        raise ValueError("native executable digest mismatch")
    if os.environ != {"PATH": "/runtime/bin:/usr/bin:/bin", "HOME": "/home/worker", "CODEX_HOME": "/codex-state",
                      "TMPDIR": "/tmp", "LANG": "C.UTF-8"}:
        raise ValueError("unexpected native environment")
    version = subprocess.run([BINARY, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=10, check=False)
    if version.returncode or version.stdout.strip() != ("codex-cli " + expected_version).encode("ascii"):
        raise ValueError("native executable version mismatch")


def main():
    try:
        if len(sys.argv) < 5 or sys.argv[3:5] != [BINARY, "exec"]:
            raise ValueError("unexpected native command")
        verify(sys.argv[1], sys.argv[2])
        os.execve(BINARY, sys.argv[3:], dict(os.environ))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        # Never dump config, raw daemon details, paths or provider responses.
        sys.stderr.write("goodteam: pinned Codex pre-exec guard failed\n")
        return 78


def source_bytes():
    # Host-side adapter helper only; source is hashed into the context contract.
    return Path(__file__).read_bytes()


if __name__ == "__main__":
    sys.exit(main())
