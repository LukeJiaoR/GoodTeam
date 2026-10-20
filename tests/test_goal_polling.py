"""Continuation scans retain fail-closed history checks without N+1 reads."""
from contextlib import contextmanager
import sqlite3

import pytest

from goodteam_os.goals import GoalCorruptState, GoalStore
from test_goals import make_goal, start_work


def stopped_goal(root, project, *, running=False, delivered=True):
    store = GoalStore(root, project)
    goal = make_goal(store)
    if running:
        goal = start_work(store, goal)
    goal = store.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key='stop')
    if delivered:
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE outbox SET delivered=1 WHERE goal_id=?', (goal['id'],))
    return goal


def test_scan_reuses_validated_history_with_constant_query_count(tmp_path, monkeypatch):
    for i in range(20):
        stopped_goal(tmp_path, f'OLD{i}')
    active = make_goal(GoalStore(tmp_path, 'LIVE'))
    store = GoalStore(tmp_path)
    original = store._connection
    queries = []
    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(queries.append)
            yield db
    monkeypatch.setattr(store, '_connection', traced)
    snapshots = []
    original_snapshot = store._snapshot_rows
    def counted(*args):
        snapshots.append(args[0]['id'])
        return original_snapshot(*args)
    monkeypatch.setattr(store, '_snapshot_rows', counted)
    cache = {}
    assert [g['id'] for g in store.list_continuation_goals(validated_idle=cache)] == [active['id']]
    assert len(snapshots) == 21
    first_queries = len([q for q in queries if q.startswith('SELECT')])
    snapshots.clear()
    queries.clear()
    assert [g['id'] for g in store.list_continuation_goals(validated_idle=cache)] == [active['id']]
    assert snapshots == [active['id']]
    assert len([q for q in queries if q.startswith('SELECT')]) == first_queries == 4


def test_scan_keeps_terminal_reconciliation_and_project_scope(tmp_path):
    stopped_goal(tmp_path, 'IDLE')
    running = stopped_goal(tmp_path, 'RUNNING', running=True)
    pending = stopped_goal(tmp_path, 'OUTBOX', delivered=False)
    active = make_goal(GoalStore(tmp_path, 'LIVE'))
    store = GoalStore(tmp_path)
    cache = {}
    for _ in range(2):
        pending_ids = {x['goal_id'] for x in store.pending_outbox()}
        expected = [g for g in store.list_goals() if g['status'] == 'active' or g['active_work'] or g['id'] in pending_ids]
        assert store.list_continuation_goals(validated_idle=cache) == expected
        assert {g['id'] for g in expected} == {running['id'], pending['id'], active['id']}
    assert GoalStore(tmp_path, 'IDLE').list_continuation_goals(validated_idle={}) == []
    assert [g['id'] for g in GoalStore(tmp_path, 'RUNNING').list_continuation_goals(validated_idle={})] == [running['id']]


@pytest.mark.parametrize('corruption', ['goal', 'work', 'event', 'delete_work', 'delete_events', 'outbox', 'delete_outbox', 'mismatch_outbox'])
def test_cached_history_corruption_fails_closed_without_revision_change(tmp_path, corruption):
    old = stopped_goal(tmp_path, 'OLD')
    make_goal(GoalStore(tmp_path, 'LIVE'))
    store = GoalStore(tmp_path)
    cache = {}
    store.list_continuation_goals(validated_idle=cache)
    with sqlite3.connect(store.path) as db:
        if corruption in {'goal', 'work', 'event'}:
            table = {'goal': 'goals', 'work': 'work', 'event': 'events'}[corruption]
            key = 'id' if table == 'goals' else 'goal_id'
            db.execute(f'UPDATE {table} SET data=? WHERE {key}=?', ('{}', old['id']))
        elif corruption.startswith('delete_'):
            db.execute(f'DELETE FROM {corruption[7:]} WHERE goal_id=?', (old['id'],))
        elif corruption == 'outbox':
            db.execute('UPDATE outbox SET delivered=9 WHERE goal_id=?', (old['id'],))
        else:
            db.execute('UPDATE outbox SET revision=999 WHERE goal_id=?', (old['id'],))
    with pytest.raises(GoalCorruptState):
        store.list_continuation_goals(validated_idle=cache)
    with pytest.raises(GoalCorruptState):
        store.list_continuation_goals(validated_idle=cache)


def test_pending_outbox_change_invalidates_idle_cache(tmp_path):
    old = stopped_goal(tmp_path, 'OLD')
    store = GoalStore(tmp_path)
    cache = {}
    assert store.list_continuation_goals(validated_idle=cache) == []
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE outbox SET delivered=0 WHERE goal_id=?', (old['id'],))
    assert [g['id'] for g in store.list_continuation_goals(validated_idle=cache)] == [old['id']]


@pytest.mark.parametrize('corrupt_after_first_scan', [False, True])
def test_service_reuses_cache_but_stops_whole_scan_on_history_corruption(tmp_path, monkeypatch, corrupt_after_first_scan):
    from goodteam_os import goal_continuation as service

    active = make_goal(GoalStore(tmp_path, 'LIVE'))
    old = stopped_goal(tmp_path, 'OLD')
    validated, ticks = [], []
    original = GoalStore._snapshot_rows
    def counted(self, row, works, events):
        validated.append(row['id'])
        return original(self, row, works, events)
    monkeypatch.setattr(GoalStore, '_snapshot_rows', counted)
    monkeypatch.setattr(service, 'tick_goal', lambda workspace, project, goal: ticks.append(goal))
    class TwoScans(service.GoalServiceStop):
        scans = 0
        def wait(self, timeout=None):
            self.scans += 1
            if self.scans == 2 and corrupt_after_first_scan:
                with sqlite3.connect(GoalStore(tmp_path).path) as db:
                    db.execute('UPDATE work SET data=? WHERE goal_id=?', ('{}', old['id']))
            return self.scans > 2
    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(service, 'GoalServiceStop', TwoScans)
    monkeypatch.setattr(service.threading, 'Thread', ImmediateThread)
    service.start_goal_service(tmp_path)
    assert ticks == [active['id']] * (1 if corrupt_after_first_scan else 2)
    assert validated.count(old['id']) == (2 if corrupt_after_first_scan else 1)
    assert validated.count(active['id']) == 2


def test_cache_is_bounded_and_removed_goals_are_evicted(tmp_path):
    for i in range(130):
        stopped_goal(tmp_path, f'OLD{i}')
    store = GoalStore(tmp_path)
    cache = {}
    assert store.list_continuation_goals(validated_idle=cache) == []
    assert len(cache['records']) == 128
    with sqlite3.connect(store.path) as db:
        db.execute('DELETE FROM goals')
    assert store.list_continuation_goals(validated_idle=cache) == []
    assert cache['records'] == {}


def test_replaced_ledger_revalidates_even_identical_records(tmp_path, monkeypatch):
    stopped_goal(tmp_path, 'OLD')
    store = GoalStore(tmp_path)
    cache = {}
    assert store.list_continuation_goals(validated_idle=cache) == []
    replacement = store.path.with_suffix('.replacement')
    replacement.write_bytes(store.path.read_bytes())
    replacement.replace(store.path)
    original = store._snapshot_rows
    seen = []
    def counted(*args):
        seen.append(args[0]['id'])
        return original(*args)
    monkeypatch.setattr(store, '_snapshot_rows', counted)
    assert store.list_continuation_goals(validated_idle=cache) == []
    assert len(seen) == 1


def test_oversized_idle_record_is_not_retained(tmp_path):
    store = GoalStore(tmp_path, 'LARGE')
    goal = make_goal(store, acceptance_criteria=['a' * 16000, 'b' * 16000])
    store.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key='stop')
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE outbox SET delivered=1')
    cache = {}
    assert store.list_continuation_goals(validated_idle=cache) == []
    assert cache['records'] == {}


@pytest.mark.parametrize('table', ['work', 'events'])
def test_added_historical_rows_invalidate_cache(tmp_path, table):
    old = stopped_goal(tmp_path, 'OLD')
    store = GoalStore(tmp_path)
    cache = {}
    store.list_continuation_goals(validated_idle=cache)
    with sqlite3.connect(store.path) as db:
        if table == 'work':
            db.execute('INSERT INTO work VALUES (?,?,?,?)', (old['id'], 'extra', 999, '{}'))
        else:
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', ('extra', old['id'], 999, 'extra', '{}'))
    with pytest.raises(GoalCorruptState):
        store.list_continuation_goals(validated_idle=cache)
    assert cache == {}


def test_cache_scope_cannot_hide_other_project_or_workspace(tmp_path, monkeypatch):
    stopped_goal(tmp_path, 'OLD')
    cache = {}
    store = GoalStore(tmp_path)
    assert store.list_continuation_goals(validated_idle=cache) == []
    original = GoalStore._snapshot_rows
    seen = []
    def counted(self, *args):
        seen.append(args[0]['id'])
        return original(self, *args)
    monkeypatch.setattr(GoalStore, '_snapshot_rows', counted)
    assert GoalStore(tmp_path, 'OLD').list_continuation_goals(validated_idle=cache) == []
    assert len(seen) == 1
    other = tmp_path / 'other' / 'state'
    other.mkdir(parents=True)
    (other / 'goals.sqlite3').write_bytes(store.path.read_bytes())
    assert GoalStore(other.parent, 'OLD').list_continuation_goals(validated_idle=cache) == []
    assert len(seen) == 2


def test_scan_uses_one_snapshot_when_a_writer_changes_history(tmp_path, monkeypatch):
    old = stopped_goal(tmp_path, 'OLD')
    active = make_goal(GoalStore(tmp_path, 'LIVE'))
    store = GoalStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    connect = store._connection
    changed = []
    def change_after_goals_read(sql):
        if sql.startswith('SELECT w.*') and not changed:
            with sqlite3.connect(store.path) as writer:
                writer.execute('UPDATE work SET data=? WHERE goal_id=?', ('{}', old['id']))
            changed.append(True)
    @contextmanager
    def concurrent_connection():
        with connect() as db:
            db.set_trace_callback(change_after_goals_read)
            yield db
    monkeypatch.setattr(store, '_connection', concurrent_connection)
    cache = {}
    assert [g['id'] for g in store.list_continuation_goals(validated_idle=cache)] == [active['id']]
    assert changed == [True]
    with pytest.raises(GoalCorruptState):
        store.list_continuation_goals(validated_idle=cache)
