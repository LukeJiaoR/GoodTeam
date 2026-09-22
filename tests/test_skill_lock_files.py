"""Actual lock creation contention and unsafe-file rejection, without a model."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import multiprocessing
import os
from pathlib import Path
from threading import Barrier

import pytest

from goodteam_os.skills.filesystem import directory, open_lock_file
from goodteam_os.skills.models import SkillError


def test_simultaneous_first_opens_share_one_inode_without_losing_callers(tmp_path):
    # Fresh names exercise creation contention, not just reopening a warm lock.
    with directory(tmp_path.resolve()) as parent, ThreadPoolExecutor(max_workers=4) as executor:
        for number in range(16):
            ready = Barrier(4)

            def enter(_):
                ready.wait(timeout=5)
                lock = open_lock_file(parent, f"{number}.lock")
                try:
                    info = os.fstat(lock)
                    return info.st_dev, info.st_ino
                finally:
                    os.close(lock)

            assert len(set(executor.map(enter, range(4)))) == 1


def _competing_process(root, messages):
    with directory(Path(root)) as parent:
        lock = open_lock_file(parent, ".lock")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                messages.send("blocked")
            else:
                messages.send("entered_while_parent_held_lock")
                return
            fcntl.flock(lock, fcntl.LOCK_EX)
            info = os.fstat(lock)
            messages.send((info.st_dev, info.st_ino))
        finally:
            os.close(lock)
            messages.close()


def test_opened_lock_preserves_cross_process_exclusion_and_handoff(tmp_path):
    context = multiprocessing.get_context("spawn")
    parent_messages, child_messages = context.Pipe()
    with directory(tmp_path.resolve()) as parent:
        lock = open_lock_file(parent, ".lock")
        process = context.Process(target=_competing_process, args=(str(tmp_path.resolve()), child_messages))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            info = os.fstat(lock)
            process.start()
            child_messages.close()
            assert parent_messages.poll(10), "Child did not attempt the held lock"
            assert parent_messages.recv() == "blocked"
            fcntl.flock(lock, fcntl.LOCK_UN)
            assert parent_messages.poll(10), "Child did not acquire the released lock"
            assert parent_messages.recv() == (info.st_dev, info.st_ino)
            process.join(timeout=10)
            assert process.exitcode == 0
        finally:
            os.close(lock)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            parent_messages.close()
            child_messages.close()


@pytest.mark.parametrize("create", [True, False])
@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "hardlink", "fifo", "directory"])
def test_lock_open_rejects_unsafe_entries_without_modifying_targets(tmp_path, kind, create):
    target = tmp_path / "target"
    target.write_bytes(b"keep original bytes")
    lock = tmp_path / ".lock"
    if kind == "symlink":
        lock.symlink_to(target)
    elif kind == "dangling_symlink":
        lock.symlink_to(tmp_path / "missing")
    elif kind == "hardlink":
        os.link(target, lock)
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        lock.mkdir()
    with directory(tmp_path.resolve()) as parent:
        with pytest.raises((OSError, SkillError)):
            open_lock_file(parent, ".lock", create=create)
    assert target.read_bytes() == b"keep original bytes"
    assert not (tmp_path / "missing").exists()


def test_read_path_never_creates_a_missing_lock(tmp_path):
    with directory(tmp_path.resolve()) as parent:
        with pytest.raises(FileNotFoundError):
            open_lock_file(parent, ".lock", create=False)
    assert not (tmp_path / ".lock").exists()


def test_disappearing_lock_is_not_recreated_on_another_inode(tmp_path, monkeypatch):
    lock = tmp_path / ".lock"
    lock.touch()
    original_open = os.open

    def removed_by_peer(name, flags, *args, **kwargs):
        if name == ".lock" and flags & os.O_EXCL:
            lock.unlink()
            raise FileExistsError("A peer created then removed the lock")
        return original_open(name, flags, *args, **kwargs)

    with directory(tmp_path.resolve()) as parent:
        monkeypatch.setattr(os, "open", removed_by_peer)
        with pytest.raises(FileNotFoundError):
            open_lock_file(parent, ".lock")
    assert not lock.exists()
