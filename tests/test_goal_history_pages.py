"""Bounded reads retain complete evidence and isolate project cursors."""
import pytest

from goodteam_os.goals import GoalError, GoalStore
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.web import _goal_page_limit, build_goal_history
from goodteam_os.web import ProjectApiError
from test_goals import make_goal
from test_goal_session_reads import NoReadiness


def history(workspace, project='DEMO', count=7):
    store = GoalStore(workspace, project)
    goals = []
    for i in range(count):
        goal = make_goal(store, idempotency_key=f'create-{i}')
        goal = store.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key=f'stop-{i}')
        goals.append(goal)
    return store, goals


def test_pages_preserve_complete_records_and_stable_boundaries(tmp_path, monkeypatch):
    store, expected = history(tmp_path)
    decoded = []
    original = store._snapshot
    def record(db, goal_id):
        decoded.append(goal_id)
        return original(db, goal_id)
    monkeypatch.setattr(store, '_snapshot', record)
    first = store.page_goals(limit=2)
    assert first['goals'] == expected[-2:]
    assert len(decoded) == 2
    newer = make_goal(store, idempotency_key='new-during-traversal')
    cursor, seen = first['next_before'], first['goals']
    while cursor:
        page = store.page_goals(limit=2, before=cursor)
        seen = page['goals'] + seen
        cursor = page['next_before']
    assert seen == expected
    assert store.page_goals(limit=1)['goals'][0] == newer
    assert store.list_goals() == expected + [newer]

def test_cursor_page_seeks_from_rowid_boundary(tmp_path, monkeypatch):
    from contextlib import contextmanager

    store, goals = history(tmp_path, count=8)
    statements = []
    connect = store._connection

    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, '_connection', traced)
    page = store.page_goals(limit=2, before=goals[4]['id'])
    assert page['goals'] == goals[2:4]
    query, = [statement for statement in statements
              if statement.startswith("SELECT id FROM goals WHERE project_key=")]
    with connect() as db:
        plan = " ".join(row["detail"] for row in db.execute("EXPLAIN QUERY PLAN " + query))
    assert "SEARCH goals USING INTEGER PRIMARY KEY (rowid<?)" in plan


@pytest.mark.parametrize('limit', [0, -1, 51, True, 1.5, '2', None])
def test_invalid_limit(tmp_path, limit):
    store, _ = history(tmp_path, count=1)
    with pytest.raises(GoalError):
        store.page_goals(limit=limit)


def test_cursor_cannot_cross_projects_or_use_unknown_identity(tmp_path):
    a, goals = history(tmp_path, 'A', 2)
    b, _ = history(tmp_path, 'B', 2)
    for cursor in [goals[1]['id'], 'unknown', 1, 'x' * 129]:
        with pytest.raises(GoalError):
            b.page_goals(before=cursor)
    with pytest.raises(GoalError):
        GoalStore(tmp_path).page_goals()
    assert a.page_goals(before=goals[0]['id']) == {'goals': [], 'next_before': ''}


def test_session_opt_in_and_legacy_payload(tmp_path):
    store, expected = history(tmp_path)
    service = TaskAssistantService(tmp_path, readiness_executor=NoReadiness())
    legacy = service.session('DEMO', tmp_path)
    bounded = service.session('DEMO', tmp_path, goal_limit=1)
    assert legacy['goals'] == expected
    assert 'goal_page' not in legacy
    assert bounded['goals'] == expected[-1:]
    assert bounded['goal_page'] == {'next_before': expected[-1]['id']}
    assert service.session('DEMO', tmp_path)['goals'] == expected
    assert store.list_goals() == expected


def test_legacy_workspace_does_not_create_goal_database(tmp_path, monkeypatch):
    monkeypatch.setattr('goodteam_os.web._project_config_for_key', lambda *args: None)
    result = build_goal_history(tmp_path, 'DEMO')
    assert result == {'project_key': 'DEMO', 'goals': [], 'next_before': ''}
    assert not (tmp_path / 'state/goals.sqlite3').exists()
    service = TaskAssistantService(tmp_path, readiness_executor=NoReadiness())
    assert service.session('DEMO', tmp_path, goal_limit=1)['goal_page'] == {'next_before': ''}
    assert not (tmp_path / 'state/goals.sqlite3').exists()


@pytest.mark.parametrize('raw', ['0', '51', '-1', '1.5', 'true', '１', '999999999999999999999999'])
def test_http_rejects_invalid_limits(raw):
    with pytest.raises(ProjectApiError):
        _goal_page_limit({'goal_limit': [raw]})


def test_history_projects_snapshots_without_advancing(tmp_path, monkeypatch):
    store, expected = history(tmp_path)
    monkeypatch.setattr('goodteam_os.web._project_config_for_key', lambda *args: None)
    page = build_goal_history(tmp_path, 'DEMO', limit=3)
    assert page['goals'] == expected[-3:]
    assert page['next_before'] == expected[-3]['id']
    assert store.list_goals() == expected


def test_latest_page_contains_the_only_unfinished_goal(tmp_path):
    from goodteam_os.goals import GoalConflict
    store, _ = history(tmp_path)
    current = make_goal(store, idempotency_key='active')
    with pytest.raises(GoalConflict):
        make_goal(store, idempotency_key='cannot-hide-active')
    assert store.page_goals(limit=1)['goals'] == [current]


def test_real_http_history_and_legacy_state_contract(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from goodteam_os import web

    _, expected = history(tmp_path)
    server_ready = threading.Event()
    servers = []
    class Server(ThreadingHTTPServer):
        def __init__(self, address, handler):
            super().__init__(address, handler)
            servers.append(self)
            server_ready.set()
    monkeypatch.setattr(web, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr('goodteam_os.goal_continuation.start_goal_service', lambda workspace: None)
    monkeypatch.setattr(web, '_project_config_for_key', lambda *args: None)
    calls = []
    def state(workspace, project_key, **kwargs):
        calls.append(kwargs)
        service = TaskAssistantService(workspace, readiness_executor=NoReadiness())
        return {'task_assistant_session': service.session(project_key, workspace, **kwargs)}
    monkeypatch.setattr(web, 'build_project_work_state', state)
    thread = threading.Thread(target=web.serve, args=(tmp_path,), kwargs={'port': 0}, daemon=True)
    thread.start()
    assert server_ready.wait(5)
    server = servers[0]
    base = f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urlopen(base + path, timeout=5) as response:
            return json.load(response)
    try:
        assert get('/api/state?project=DEMO')['task_assistant_session']['goals'] == expected
        assert get('/api/state?project=DEMO&goal_limit=1')['task_assistant_session']['goals'] == expected[-1:]
        assert calls == [{}, {'goal_limit': 1}]
        page = get('/api/goal-history?project=DEMO&goal_limit=2')
        assert page['goals'] == expected[-2:]
        assert get(f'/api/goal-history?project=DEMO&goal_limit=2&before={page["next_before"]}')['goals'] == expected[-4:-2]
        for path in ['/api/state?project=DEMO&goal_limit=0', '/api/goal-history?project=DEMO&before=unknown']:
            with pytest.raises(HTTPError) as error:
                get(path)
            assert error.value.code == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_readiness_event_replacement_keeps_page_metadata(tmp_path, monkeypatch):
    _, expected = history(tmp_path)
    class Readiness:
        def status(self, project_key, root):
            return {}
    service = TaskAssistantService(tmp_path, readiness_executor=Readiness())
    def replacement(store, session, *args):
        return {key: value for key, value in session.items() if key != 'goal_page'}
    monkeypatch.setattr(service, '_append_completed_understanding', replacement)
    monkeypatch.setattr(service, '_with_readiness_attention', lambda session, readiness: session)
    result = service.session('DEMO', tmp_path, goal_limit=1)
    assert result['goal_page'] == {'next_before': expected[-1]['id']}
    assert result['goals'] == expected[-1:]
