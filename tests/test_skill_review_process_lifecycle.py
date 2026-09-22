"""Parent termination/revocation checks remain active after child pipe closure."""
import os
import sys
import time

import pytest

from goodteam_os.skills.models import SkillError
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


def test_timeout_after_closed_streams_does_not_leave_a_child(tmp_path):
    witness = tmp_path / 'child-pid'
    body = (
        'import os,sys,time\nfrom pathlib import Path\n'
        'sys.stdin.buffer.read()\n'
        "Path('child-pid').write_text(str(os.getpid()))\n"
        'os.close(0);os.close(1);os.close(2)\n'
        'time.sleep(5)\n'
    )
    spec = ReviewProcess((sys.executable, '-I', '-c', body), tmp_path, {}, 0.3)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b'input', check_current=lambda: None)
    assert error.value.code == 'review_timeout'
    with pytest.raises(ProcessLookupError):
        os.kill(int(witness.read_text()), 0)
