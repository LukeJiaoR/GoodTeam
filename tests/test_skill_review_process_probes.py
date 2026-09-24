"""Permission-denied probes must neither mask a timeout nor confirm cleanup.

Deterministic probe failures exercise the platform-independent control flow.
The integration case owns a real bounded child; none of these tests runs Codex,
Docker or a provider, or establishes native macOS conformance.
"""
import errno
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from goodteam_os.skills import review_process
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_process import ReviewProcess, run_review_process


@pytest.mark.parametrize("probe_error,expected", [
    (None, True),
    (ProcessLookupError(errno.ESRCH, "gone"), False),
    (PermissionError(errno.EPERM, "denied probe"), True),
])
def test_group_probe_requires_absence_not_permission_denial(monkeypatch, probe_error, expected):
    calls = []

    def killpg(pid, sig):
        calls.append((pid, sig))
        if probe_error is not None:
            raise probe_error

    monkeypatch.setattr(review_process, "os", SimpleNamespace(killpg=killpg))
    assert review_process._group_exists(123456) is expected
    assert calls == [(123456, 0)]


def test_group_probe_does_not_hide_unexpected_os_error(monkeypatch):
    failure = OSError(errno.EINVAL, "invalid probe")

    def killpg(_pid, _sig):
        raise failure

    monkeypatch.setattr(review_process, "os", SimpleNamespace(killpg=killpg))
    with pytest.raises(OSError) as error:
        review_process._group_exists(123456)
    assert error.value is failure


def test_persistent_probe_denial_exhausts_bounded_wait_without_confirming_cleanup(monkeypatch):
    clock = [0.0]
    calls, polls = [], []

    def killpg(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            raise PermissionError(errno.EPERM, "probe remains unavailable")

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(review_process, "os", SimpleNamespace(killpg=killpg))
    monkeypatch.setattr(review_process, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    process = SimpleNamespace(pid=123456, poll=lambda: polls.append(True))
    with pytest.raises(SkillError) as error:
        review_process._terminate(process, grace=0.1)
    assert error.value.code == "review_termination_unknown"
    assert [sig for _pid, sig in calls if sig] == [signal.SIGTERM, signal.SIGKILL]
    assert all(pid == process.pid for pid, _sig in calls)
    assert len(polls) > 2 and 0.2 <= clock[0] <= 0.25


def test_permission_denied_termination_signal_is_still_unknown(monkeypatch):
    calls = []
    failure = PermissionError(errno.EPERM, "cannot terminate")

    def killpg(pid, sig):
        calls.append((pid, sig))
        raise failure

    monkeypatch.setattr(review_process, "os", SimpleNamespace(killpg=killpg))
    process = SimpleNamespace(pid=123456, poll=lambda: pytest.fail("A denied signal cannot confirm cleanup"))
    with pytest.raises(SkillError) as error:
        review_process._terminate(process)
    assert error.value.code == "review_termination_unknown"
    assert error.value.__cause__ is failure
    assert calls == [(process.pid, signal.SIGTERM)]


def test_transient_probe_denial_preserves_timeout_after_actual_child_reap(tmp_path, monkeypatch):
    children, calls = [], []
    original_popen = review_process.subprocess.Popen
    original_killpg = os.killpg
    denied = []

    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    def killpg(pid, sig):
        assert children and pid == children[0].pid, "Only the owned group may be probed or signalled"
        calls.append((pid, sig))
        if sig == 0 and not denied:
            denied.append(True)
            raise PermissionError(errno.EPERM, "transient probe denial")
        return original_killpg(pid, sig)

    monkeypatch.setattr(review_process.subprocess, "Popen", popen)
    monkeypatch.setattr(review_process.os, "killpg", killpg)
    spec = ReviewProcess((sys.executable, "-I", "-c", "import time; time.sleep(30)"), tmp_path, {}, 0.2)
    try:
        with pytest.raises(SkillError) as error:
            run_review_process(spec, b"input", check_current=lambda: None)
        assert error.value.code == "review_timeout"
        assert denied == [True] and len(children) == 1
        assert children[0].returncode is not None
        assert sum(sig == 0 for _pid, sig in calls) >= 2
        with pytest.raises(ProcessLookupError):
            os.kill(children[0].pid, 0)
    finally:
        # Test cleanup never relies on the injected probe result.
        for child in children:
            try:
                original_killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)
