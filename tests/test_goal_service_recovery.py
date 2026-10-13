"""Real background/HTTP consumers, seeded crash snapshots; no model calls."""
import subprocess
import sys
import time

import pytest

from goodteam_os.goal_continuation import authority_snapshot
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.team_control_plane import ensure_team_state
from workflow_http_helpers import RecoveryServer, wait_until


def seed(root):
    workspace = root / 'workspace'
    config = workspace / 'projects/recovery.local.yaml'
    config.parent.mkdir(parents=True)
    target = root / 'target'
    target.mkdir()
    config.write_text(f'key: RECOVERY\nname: Recovery\nrepos:\n  root: {target}\nworker_backend:\n  mode: fake\n')
    ensure_team_state(workspace, 'RECOVERY', config)
    store = GoalStore(workspace, 'RECOVERY')
    goal = store.create_goal(objective='Recover this isolated fixture', acceptance_criteria=['Check works'],
        allowed_scope=['Only this fixture'], max_work_units=1, confirmed=True, idempotency_key='create',
        authority_snapshot=authority_snapshot(workspace, 'RECOVERY', config, target))
    goal = store.next_work(goal['id'], expected_revision=goal['revision'], idempotency_key='claim')
    work = goal['active_work']
    state = create_initial_state(work['run_id'], 'RECOVERY', work['task'])
    state.current_node = 'security'
    state.next_node = 'await_security_approval'
    state.pending_transition = {'id': 'crashed', 'status': 'executing', 'action': {}}
    StateStore(workspace).write_project_current(state)
    return workspace, config, store, goal


def test_service_recovers_abandoned_transition_without_dashboard_read(tmp_path):
    workspace, _, store, goal = seed(tmp_path)
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'blocked')
        assert StateStore(workspace).read_project_current('RECOVERY').pending_transition['status'] == 'interrupted'
        assert len(store.get_goal(goal['id'])['work_units']) == 1
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()


def test_service_preserves_live_cross_process_transition(tmp_path):
    workspace, _, store, goal = seed(tmp_path)
    marker = tmp_path / 'locked'
    release = tmp_path / 'release-lock'
    script = "from pathlib import Path; import time; from goodteam_os.workflow_mutations import workflow_guard\nwith workflow_guard(Path(%r), 'RECOVERY'):\n Path(%r).touch()\n while not Path(%r).exists(): time.sleep(.02)" % (str(workspace), str(marker), str(release))
    process = subprocess.Popen([sys.executable, '-c', script])
    server = RecoveryServer(tmp_path)
    try:
        wait_until(marker.exists)
        server.start()
        time.sleep(1.5)
        assert store.get_goal(goal['id'])['status'] == 'active'
        assert StateStore(workspace).read_project_current('RECOVERY').pending_transition['status'] == 'executing'
        release.touch()
        process.wait(timeout=5)
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'blocked')
    finally:
        release.touch()
        process.wait(timeout=5)
        server.stop()


@pytest.mark.parametrize('damage', ['missing', 'invalid_root'])
def test_http_stop_survives_configuration_failure(tmp_path, damage):
    workspace, config, store, goal = seed(tmp_path)
    state = StateStore(workspace).read_project_current('RECOVERY')
    state.pending_transition = {}
    StateStore(workspace).write_project_current(state)
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        if damage == 'missing':
            config.unlink()
        else:
            config.write_text('key: RECOVERY\nrepos: broken\n')
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'blocked')
        current = store.get_goal(goal['id'])
        assert 'configuration or authorization snapshot is unavailable' in current['stop_reason']
        request = dict(action='stop_goal', goal_id=goal['id'], expected_revision=current['revision'], idempotency_key='stop')
        status, result = server.request('/api/projects/RECOVERY/task-assistant/actions', request)
        assert status == 200, result
        assert result['goal']['status'] == 'stopped'
        assert server.request('/api/projects/RECOVERY/task-assistant/actions', request)[0] == 200
        assert server.request('/api/projects/RECOVERY/task-assistant/actions', {**request, 'idempotency_key': 'stale'})[0] == 409
        time.sleep(1.2)
        assert store.get_goal(goal['id'])['status'] == 'stopped'
        assert len(store.get_goal(goal['id'])['work_units']) == 1
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()


def test_serve_shutdown_drains_inflight_tick_and_starts_no_later_goal(tmp_path, monkeypatch):
    import threading
    from goodteam_os import web, goal_continuation

    workspace, _, _, _ = seed(tmp_path)
    GoalStore(workspace, 'SECOND').create_goal(
        objective='Second isolated Goal', acceptance_criteria=['Check works'], allowed_scope=['Fixture only'],
        max_work_units=1, confirmed=True, idempotency_key='second', authority_snapshot={})
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def tick(*args):
        calls.append(args)
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(goal_continuation, 'tick_goal', tick)
    class Server:
        def __init__(self, *_args):
            pass
        def serve_forever(self):
            assert entered.wait(5)
        def server_close(self):
            closed.set()
    monkeypatch.setattr(web, 'ThreadingHTTPServer', Server)
    thread = threading.Thread(target=web.serve, kwargs={'workspace': workspace})
    thread.start()
    try:
        assert entered.wait(5)
        assert closed.wait(5)
        time.sleep(.1)
        assert thread.is_alive(), 'serve returned while continuation tick was still in flight'
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(calls) == 1


def test_shutdown_timeout_is_observable_without_replaying_work():
    import threading
    from goodteam_os.goal_continuation import GoalServiceStop

    release = threading.Event()
    stop = GoalServiceStop()
    stop.thread = threading.Thread(target=release.wait, daemon=True)
    stop.thread.start()
    try:
        stop.set()
        with pytest.raises(TimeoutError, match="shutdown checkpoint"):
            stop.join(timeout=.01)
    finally:
        release.set()
        stop.join()


@pytest.mark.parametrize('condition', ['stopped', 'missing_config'])
def test_service_recovers_bound_transition_even_when_goal_cannot_continue(tmp_path, condition):
    workspace, config, store, goal = seed(tmp_path)
    if condition == 'stopped':
        store.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key='stop-first')
    else:
        config.unlink()
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        wait_until(lambda: StateStore(workspace).read_project_current('RECOVERY').pending_transition['status'] == 'interrupted')
        wait_until(lambda: store.get_goal(goal['id'])['status'] == ('stopped' if condition == 'stopped' else 'blocked'))
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()


def test_real_cli_supervisor_signal_drains_tick_and_preserves_worker(tmp_path):
    import json
    import os
    import threading
    from pathlib import Path
    from goodteam_os.supervisor import Component, _stop_component

    seed(tmp_path)
    control = tmp_path / 'cli-control'
    control.mkdir()
    (tmp_path / '.recovery-fixture').touch()
    repository = Path(__file__).resolve().parents[1]
    with (control / 'log').open('w') as log:
        process = subprocess.Popen([sys.executable, str(repository / 'tests/fixtures/goal_shutdown_cli.py'),
            str(tmp_path), str(control)], cwd=repository, start_new_session=True,
            env={**os.environ, 'PYTHONPATH': str(repository / 'src')}, stdout=log, stderr=log)
        component = Component('web', [], control / 'ready', '', control / 'heartbeat', '', process=process)
        stopper = None
        try:
            wait_until(lambda: (control / 'entered').exists() or process.poll() is not None)
            assert process.poll() is None, (control / 'log').read_text()
            stopper = threading.Thread(target=_stop_component, args=(component,))
            stopper.start()
            time.sleep(5.5)  # exceeds the former component kill deadline
            assert process.poll() is None, 'supervisor killed web before its Goal drain window'
            assert stopper.is_alive()
            import signal
            os.kill(process.pid, signal.SIGTERM)  # repeated stop must not interrupt drain
            time.sleep(.1)
            assert process.poll() is None
            (control / 'release').touch()
            stopper.join(5)
            assert not stopper.is_alive()
            assert process.returncode == 0, (control / 'log').read_text()
            assert json.loads((control / 'finished').read_text())['worker_alive'] is True
        finally:
            (control / 'release').touch()
            if stopper:
                stopper.join(5)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize('stopped', [False, True])
def test_service_never_recovers_a_different_run_for_an_old_goal(tmp_path, stopped):
    workspace, _, store, goal = seed(tmp_path)
    if stopped:
        store.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key='stop-first')
    state = create_initial_state('different-run', 'RECOVERY', 'An unrelated request')
    state.current_node = 'security'
    state.next_node = 'await_security_approval'
    state.pending_transition = {'id': 'unrelated', 'status': 'executing', 'action': {}}
    StateStore(workspace).write_project_current(state)
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        time.sleep(1.3)
        assert StateStore(workspace).read_project_current('RECOVERY').pending_transition['status'] == 'executing'
        assert store.get_goal(goal['id'])['status'] == ('stopped' if stopped else 'blocked')
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()


def test_outer_service_stop_allows_component_drain_deadlines(tmp_path, monkeypatch):
    from goodteam_os import service
    from goodteam_os.service_timeouts import WEB_STOP_TIMEOUT_SECONDS, COMPONENT_STOP_TIMEOUT_SECONDS

    monkeypatch.setenv('GOODTEAM_STATE_DIR', str(tmp_path / 'operator'))
    monkeypatch.setattr(service, '_read_state', lambda: {'pid': 12345, 'identity': 'fixture'})
    elapsed = [0.0]
    signals = []
    monkeypatch.setattr(service.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(service.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    required = WEB_STOP_TIMEOUT_SECONDS + 2 * COMPONENT_STOP_TIMEOUT_SECONDS
    monkeypatch.setattr(service, '_state_process_is_running', lambda *_args: elapsed[0] < required)
    monkeypatch.setattr(service.os, 'kill', lambda pid, sig: signals.append(('pid', pid, sig)))
    monkeypatch.setattr(service.os, 'killpg', lambda pid, sig: signals.append(('group', pid, sig)))
    assert service.stop_service()['stopped'] is True
    assert elapsed[0] >= required
    assert [target for target, _pid, _sig in signals] == ['pid']


@pytest.mark.parametrize('drain_timeout', [False, True])
def test_serve_restores_signal_handlers_even_on_drain_error(tmp_path, monkeypatch, drain_timeout):
    import signal
    from goodteam_os import web, goal_continuation

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    events = []
    class Server:
        def __init__(self, *_args):
            pass
        def serve_forever(self):
            assert signal.getsignal(signal.SIGTERM) != previous[signal.SIGTERM]
        def server_close(self):
            events.append('closed')
    class Stop:
        def set(self):
            events.append('stopping')
        def join(self):
            events.append('drained')
            if drain_timeout:
                raise TimeoutError('fixture timeout')
    monkeypatch.setattr(web, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(goal_continuation, 'start_goal_service', lambda _workspace: Stop())
    if drain_timeout:
        with pytest.raises(TimeoutError):
            web.serve(tmp_path)
    else:
        web.serve(tmp_path)
    assert events == ['stopping', 'closed', 'drained']
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


@pytest.mark.parametrize('status, expected_timeout', [('running', 35), ('restarting', 5)])
def test_supervisor_hard_kill_remains_bounded_after_web_drain(tmp_path, monkeypatch, status, expected_timeout):
    import signal
    from goodteam_os import supervisor
    signals, waits = [], []
    class Process:
        pid = 12345
        def poll(self):
            return None
        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired('owned-fixture', timeout)
    component = supervisor.Component('web', [], tmp_path / 'ready', '', tmp_path / 'heartbeat', '', process=Process(), status=status)
    monkeypatch.setattr(supervisor.os, 'kill', lambda pid, sig: signals.append(('pid', pid, sig)))
    monkeypatch.setattr(supervisor.os, 'killpg', lambda pid, sig: signals.append(('group', pid, sig)))
    supervisor._stop_component(component)
    assert signals == [('pid', 12345, signal.SIGTERM), ('group', 12345, signal.SIGKILL)]
    assert waits == [expected_timeout, None]
    supervisor._stop_component(component)
    assert len(signals) == 2  # consumed generation cannot signal a reused group ID


def test_real_web_drain_timeout_cleans_remaining_worker_group(tmp_path):
    import os
    from pathlib import Path
    from goodteam_os.supervisor import Component, _stop_component
    from goodteam_os.service import _process_is_running

    seed(tmp_path)
    control = tmp_path / 'timeout-control'
    control.mkdir()
    (tmp_path / '.recovery-fixture').touch()
    repository = Path(__file__).resolve().parents[1]
    with (control / 'log').open('w') as log:
        process = subprocess.Popen([sys.executable, str(repository / 'tests/fixtures/goal_shutdown_cli.py'),
            str(tmp_path), str(control)], cwd=repository, start_new_session=True,
            env={**os.environ, 'PYTHONPATH': str(repository / 'src'), 'GOODTEAM_TEST_SHORT_DRAIN': '1'},
            stdout=log, stderr=log)
        component = Component('web', [], control / 'ready', '', control / 'heartbeat', '', process=process)
        worker_pid = None
        try:
            wait_until(lambda: (control / 'entered').exists() or process.poll() is not None)
            assert process.poll() is None, (control / 'log').read_text()
            worker_pid = int((control / 'worker-pid').read_text())
            _stop_component(component)
            assert process.returncode != 0
            assert 'TimeoutError' in (control / 'log').read_text()
            wait_until(lambda: not _process_is_running(worker_pid))
        finally:
            import signal
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            # Owned test process group, including a child left by the red case.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
