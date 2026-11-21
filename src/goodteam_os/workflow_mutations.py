"""[INPUT]: 项目身份和原有锁文件。
[OUTPUT]: 跨线程和进程串行化项目状态变更。
[POS]: 批次确认、恢复和审查共用的锁。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path

from goodteam_os.state import normalize_project_key


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class WorkflowBusyError(ValueError):
    pass


@contextmanager
def workflow_guard(workspace, project_key):
    directory = Path(workspace).resolve() / "state" / "workflow-locks"
    path = directory / f"{normalize_project_key(project_key)}.lock"
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path.resolve()), threading.Lock())
    if not lock.acquire(blocking=False):
        raise WorkflowBusyError("This project's workflow is busy. Refresh before retrying.")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkflowBusyError("This project's workflow is busy. Refresh before retrying.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()
