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
import stat
import sys

BINARY = "/runtime/bin/codex"
BINARY_SHA256 = "298d3d73d0bbc1367e58a370df5b6216fe30ce0a92e8b6b0afb0377a958dc335"
# Exact version's system/config + repo/ancestor/user roots. All must be absent
# before CLI startup; bundled skills are separately disabled in strict config.
# SKILL.md underneath sources/{base,head}/ is deliberately NOT removed.
FORBIDDEN_ROOTS = (
    "/etc/codex", "/.codex", "/.agents", "/AGENTS.md", "/AGENTS.override.md",
    "/review/.git", "/review/.codex", "/review/.agents", "/review/AGENTS.md", "/review/AGENTS.override.md",
    "/home/worker/.codex", "/home/worker/.agents", "/codex-state/skills",
    "/codex-state/config.toml", "/codex-state/auth.json", "/codex-state/managed_config.toml",
)


def verify():
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
    if digest.hexdigest() != BINARY_SHA256:
        raise ValueError("native executable digest mismatch")
    if os.environ != {"PATH": "/runtime/bin:/usr/bin:/bin", "HOME": "/home/worker", "CODEX_HOME": "/codex-state",
                      "TMPDIR": "/tmp", "LANG": "C.UTF-8"}:
        raise ValueError("unexpected native environment")


def main():
    try:
        verify()
        if len(sys.argv) < 3 or sys.argv[1:3] != [BINARY, "exec"]:
            raise ValueError("unexpected native command")
        os.execve(BINARY, sys.argv[1:], dict(os.environ))
    except (OSError, ValueError):
        # Never dump config, raw daemon details, paths or provider responses.
        sys.stderr.write("goodteam: pinned Codex pre-exec guard failed\n")
        return 78


def source_bytes():
    # Host-side adapter helper only; source is hashed into the context contract.
    return Path(__file__).read_bytes()


if __name__ == "__main__":
    sys.exit(main())
