"""Parent termination/revocation checks remain active after child pipe closure."""
import os
import sys
import time

import pytest

from goodteam_os.skills.models import SkillError
from goodteam_os.skills import review_process
from goodteam_os.skills.review_process import ReviewProcess, run_review_process


def test_revocation_stops_a_child_that_has_closed_all_streams(tmp_path):
    witness = tmp_path / 'child-pid'
    body = (
        'import os,sys,time\nfrom pathlib import Path\n'
        'sys.stdin.buffer.read()\n'
        "Path('child-pid').write_text(str(os.getpid()))\n"
        'os.close(0);os.close(1);os.close(2)\n'
        'time.sleep(5)\n'
    )
    started = time.monotonic()

    def current():
        if witness.exists() and time.monotonic() - started > 0.15:
            raise SkillError('version_revoked', 'Owned test revocation')

    spec = ReviewProcess((sys.executable, '-I', '-c', body), tmp_path, {}, 8)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b'input', check_current=current)
    elapsed = time.monotonic() - started
    assert error.value.code == 'version_revoked'
    assert elapsed < 2, 'Revocation was not checked while waiting for process exit'
    with pytest.raises(ProcessLookupError):
        os.kill(int(witness.read_text()), 0)


def test_timeout_after_closed_streams_does_not_leave_a_child(tmp_path, monkeypatch):
    witness = tmp_path / 'child-pid'
    ready = tmp_path / 'fixture-ready'
    body = (
        'import os,sys,time\nfrom pathlib import Path\n'
        "Path('fixture-ready').write_text('ready')\n"
        'sys.stdin.buffer.read()\n'
        'os.close(0);os.close(1);os.close(2)\n'
        "Path('child-pid').write_text(str(os.getpid()))\n"
        'time.sleep(5)\n'
    )
    original_popen = review_process.subprocess.Popen
    children = []

    def ready_child(*args, **kwargs):
        # Set up the closed-pipe scenario before the transport's unchanged
        # 0.3s execution budget. Interpreter/import startup is a separate case
        # below, not evidence that this child reached its pipe-close boundary.
        child = original_popen(*args, **kwargs)
        children.append(child)
        end = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(0.005)
        if not ready.exists():
            review_process._terminate(child)
            pytest.fail('The isolated closed-pipe fixture did not become ready')
        return child

    monkeypatch.setattr(review_process.subprocess, 'Popen', ready_child)
    spec = ReviewProcess((sys.executable, '-I', '-c', body), tmp_path, {}, 0.3)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b'input', check_current=lambda: None)
    assert error.value.code == 'review_timeout'
    assert len(children) == 1
    assert int(witness.read_text()) == children[0].pid, 'The child must actually close its streams'
    assert children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def test_timeout_during_child_startup_reaps_parent_observed_pid(tmp_path, monkeypatch):
    # Deterministically exercise the failure to reach child-written evidence:
    # timeout must still terminate the actual PID even when startup is slow.
    original_popen = review_process.subprocess.Popen
    children = []

    def record_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(review_process.subprocess, 'Popen', record_child)
    body = "import time; time.sleep(5); open('child-pid', 'w').write('too late')"
    spec = ReviewProcess((sys.executable, '-I', '-c', body), tmp_path, {}, 0.3)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b'input', check_current=lambda: None)
    assert error.value.code == 'review_timeout'
    assert not (tmp_path / 'child-pid').exists()
    assert len(children) == 1 and children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)
