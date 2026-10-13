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
