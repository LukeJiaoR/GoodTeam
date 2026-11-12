"""Bounded atomic input writes; only local offline subprocesses are allowed."""

import threading
from pathlib import Path
import pytest
from goodteam_os.pi_rpc import PiRpcProcess, PiRpcTimeout

FAKE = Path(__file__).parent / "fixtures/fake_pi_rpc.py"


def make(tmp_path, authorizer, **kw):
    r = PiRpcProcess(
        [str(FAKE), "--mode", "rpc", "--session-dir", str(tmp_path / "sessions")],
        cwd=tmp_path,
        stderr_path=tmp_path / "err",
        prompt_authorizer=authorizer,
        **kw,
    )
    r.start()
    return r


def test_stop_denial_closes_idle_and_sends_no_prompt(tmp_path):
    def deny(send):
        raise ValueError("Stop")

    records = []
    r = make(tmp_path, deny, on_record=records.append)
    with pytest.raises(ValueError, match="Stop"):
        r.request({"type": "prompt", "message": "never"})
    assert not r.alive
    assert not r._pending
    assert not any(x.get("type") == "agent_start" for x in records)


def test_write_then_commit_failure_closes_without_lock_deadlock(tmp_path):
    def fail_commit(send):
        send()
        raise ValueError("commit failure")

    r = make(tmp_path, fail_commit)
    errors = []

    def run():
        try:
            r.request({"type": "prompt", "message": "one"}, timeout=0.2)
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(8)
    assert not t.is_alive(), "cleanup deadlocked"
    assert not r.alive and not r._pending
    assert isinstance(errors[0], ValueError)


def test_authority_released_before_response_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TIMEOUT_COMMAND", "prompt")
    lock = threading.Lock()
    sent = threading.Event()

    def authorize(send):
        with lock:
            result = send()
        sent.set()
        return result

    r = make(tmp_path, authorize)
    errors = []

    def run():
        try:
            r.request({"type": "prompt", "message": "one"}, timeout=0.2)
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    assert sent.wait(2)
    assert lock.acquire(timeout=0.05)
    lock.release()
    t.join(2)
    r.close(abort=True)
    assert isinstance(errors[0], PiRpcTimeout)


def test_stalled_pipe_releases_authority_and_cleanup_is_bounded(tmp_path):
    import sys

    locked = threading.Lock()

    def authorize(send):
        with locked:
            return send()

    r = PiRpcProcess(
        [sys.executable, "-c", "import time;time.sleep(30)"],
        cwd=tmp_path,
        stderr_path=tmp_path / "stallerr",
        prompt_authorizer=authorize,
    )
    r.start()
    errors = []

    def run():
        try:
            r.request({"type": "prompt", "message": "x" * 2000000}, timeout=0.05)
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        t.join(6)
        assert not t.is_alive(), "cleanup abort blocked on full pipe"
        assert locked.acquire(timeout=0.1)
        locked.release()
        assert not r.alive and isinstance(errors[0], PiRpcTimeout)
    finally:
        if r.alive:
            r.process.kill()
            r.process.wait(timeout=2)
        t.join(2)


def test_authorized_short_writes_send_one_complete_request(tmp_path, monkeypatch):
    import os

    count = []
    r = make(tmp_path, lambda send: send())
    original = os.write

    def short(fd, data):
        count.append(len(data))
        return original(fd, data[:17])

    monkeypatch.setattr("goodteam_os.pi_rpc.os.write", short)
    try:
        result = r.request({"type": "prompt", "message": "a" * 500}, timeout=2)
        assert result["success"] is True
        assert len(count) > 10
        assert not r._pending
    finally:
        r.close(abort=True)
