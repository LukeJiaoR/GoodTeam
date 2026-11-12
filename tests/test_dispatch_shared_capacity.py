"""Actual shared locking/threads/process exit; Worker terminal bytes are fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time

import pytest
from goodteam_os.dispatch_capacity import available_dispatch_slots, run_with_project_capacity, DispatchCapacityUnknown


def job(root, number, *, run='dispatch-A'):
    value = dict(id=f'job-{number}', run_id=run, role='implementation', adapter='fake', status='queued')
    save(root, value, 'queued')
    return value


def save(root, value, status):
    directory = Path(root) / '.goodteam/workers/jobs' / value['id']
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'job.json').write_text(json.dumps({**value, 'status': status}))
    (directory / 'status.json').write_text(json.dumps({**value, 'job_id': value['id'], 'status': status,
        'process_alive': status == 'running', 'exit_code': 0 if status == 'completed' else None}))


def test_two_batches_share_ten_slots_and_late_stop_does_not_launch(tmp_path):
    release = threading.Event()
    lock = threading.Lock()
    started = []
    active = [0, 0]
    values = [job(tmp_path, i, run='dispatch-A' if i < 10 else 'dispatch-B') for i in range(11)]
    def execute(value):
        def work():
            save(tmp_path, value, 'running')
            with lock:
                started.append(value['id'])
                active[0] += 1
                active[1] = max(active)
            assert release.wait(5)
            with lock:
                active[0] -= 1
            save(tmp_path, value, 'completed')
        return run_with_project_capacity(tmp_path, value, work)
    with ThreadPoolExecutor(max_workers=11) as pool:
        futures = [pool.submit(execute, value) for value in values[:10]]
        deadline = time.monotonic() + 5
        while len(started) < 10 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(started) == 10 and available_dispatch_slots(tmp_path) == 0
        later = pool.submit(execute, values[-1])
        time.sleep(.1)
        assert not later.done() and len(started) == 10
        release.set()
        for f in futures + [later]:
            f.result(timeout=5)
    assert active[1] == 10 and len(started) == 11 and available_dispatch_slots(tmp_path) == 10
    stop_job = job(tmp_path, 'stopped')
    calls = [0]
    def check():
        calls[0] += 1
        if calls[0] == 2:
            raise ValueError('Stopped after capacity claim')
    with pytest.raises(ValueError, match='Stopped'):
        run_with_project_capacity(tmp_path, stop_job, lambda: pytest.fail('No execution after Stop'), check_start=check)
    assert available_dispatch_slots(tmp_path) == 10


def _crash(root, value):
    def work():
        save(root, value, 'running')
        os._exit(3)
    run_with_project_capacity(root, value, work)


def test_owner_crash_keeps_unknown_slot_until_original_terminal_evidence(tmp_path):
    value = job(tmp_path, 'orphan')
    process = multiprocessing.get_context('fork').Process(target=_crash, args=(tmp_path, value))
    process.start()
    process.join(5)
    assert process.exitcode == 3
    assert available_dispatch_slots(tmp_path) == 9
    with pytest.raises(DispatchCapacityUnknown):
        run_with_project_capacity(tmp_path, value, lambda: pytest.fail('Unknown invocation must not relaunch'))
    save(tmp_path, value, 'completed')
    assert available_dispatch_slots(tmp_path) == 10


def test_existing_dispatch_process_is_counted_without_new_resource_record(tmp_path):
    value = job(tmp_path, 'legacy')
    save(tmp_path, value, 'running')
    assert available_dispatch_slots(tmp_path) == 9
    save(tmp_path, value, 'completed')
    assert available_dispatch_slots(tmp_path) == 10


def test_upgrade_counts_failed_started_timeouts_without_observed_exit(tmp_path):
    values = []
    for number in range(10):
        value = job(tmp_path, f'old-timeout-{number}')
        value.update(adapter='codex', started_at='2026-11-12T00:00:00Z',
                     invocation_evidence={'external_invocation': True})
        save(tmp_path, value, 'failed')  # exit_code=None, not observed termination
        values.append(value)
    assert available_dispatch_slots(tmp_path) == 0
    value = values[0]
    directory = tmp_path / '.goodteam/workers/jobs' / value['id']
    status = json.loads((directory / 'status.json').read_text())
    status['exit_code'] = 1  # original nonzero host exit, not an inferred PID death
    (directory / 'status.json').write_text(json.dumps(status))
    assert available_dispatch_slots(tmp_path) == 1
    # Proven non-invocation failure (no attempted/start evidence) is not counted.
    missing = job(tmp_path, 'unavailable-runtime')
    missing.update(adapter='codex', invocation_evidence={'external_invocation': False})
    save(tmp_path, missing, 'failed')
    assert available_dispatch_slots(tmp_path) == 1
