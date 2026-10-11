"""One explicitly requested local command; bounded capture, no inherited secrets.

This is an Operator CLI action, not model/issue-derived permission or isolation.
Only output digests and sizes leave the process observer; no raw logs are saved.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_process import _terminate

MAX_OUTPUT_BYTES = 1024 * 1024
MAX_SECONDS = 120


def now() -> str:
    return datetime.now(UTC).isoformat()


def run_explicit_command(argv: list[str], root: Path, *, seconds: int) -> dict:
    started = now()
    result = {"status": "unknown", "reason": "validation_spawn_failed", "exit_code": None,
              "started_at": started, "finished_at": "", "producer": "local_operator_process"}
    hashes = {name: hashlib.sha256() for name in ("stdout", "stderr")}
    sizes = {name: 0 for name in hashes}
    with tempfile.TemporaryDirectory(prefix="goodteam-validation-home-") as home:
        env = {"PATH": os.defpath, "HOME": home, "TMPDIR": home, "LC_ALL": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0"}
        try:
            process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True, bufsize=0)
        except OSError:
            process = None
        if process is not None:
            deadline = time.monotonic() + seconds
            try:
                with selectors.DefaultSelector() as selector:
                    for name in hashes:
                        stream = getattr(process, name)
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, name)
                    while selector.get_map() or process.poll() is None:
                        if time.monotonic() >= deadline:
                            result["reason"] = "validation_timeout"
                            break
                        for key, _events in selector.select(timeout=0.05):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                sizes[key.data] += len(chunk)
                                hashes[key.data].update(chunk)
                        if sum(sizes.values()) > MAX_OUTPUT_BYTES:
                            result["reason"] = "validation_output_limit"
                            break
                    else:
                        result.update(status="passed" if process.returncode == 0 else "failed",
                                      reason="" if process.returncode == 0 else "validation_command_failed",
                                      exit_code=process.returncode)
            finally:
                try:
                    _terminate(process)
                except SkillError:
                    result.update(status="unknown", reason="validation_termination_unknown", exit_code=None)
                process.stdout.close()
                process.stderr.close()
    result.update(finished_at=now(), output_complete=result["status"] in {"passed", "failed"},
                  output={name: {"sha256": hashes[name].hexdigest(), "bytes": sizes[name]} for name in hashes})
    return result
