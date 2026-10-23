"""Request-local Goal projection reads preserve complete history and run identity."""
from collections import Counter
from copy import deepcopy

from goodteam_os.goal_continuation import goal_view
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant import TaskAssistantService
from test_goals import make_goal


class NoReadiness:
    def status(self, project_key, root):
        raise ValueError(f"No readiness found for project: {project_key}")


def count_reads(monkeypatch):
    counts = Counter()
    for method in ('read_project_current', 'list_project_archived'):
        original = getattr(StateStore, method)
        def counted(self, project_key, _method=method, _original=original):
            counts[_method, project_key] += 1
            return _original(self, project_key)
        monkeypatch.setattr(StateStore, method, counted)
    return counts


def test_session_shares_reads_preserving_all_goal_history(tmp_path, monkeypatch):
    goals = GoalStore(tmp_path, 'DEMO')
    states = StateStore(tmp_path)
    for i in range(6):
        goal = make_goal(goals, idempotency_key=f'create-{i}')
        goal = goals.next_work(goal['id'], expected_revision=goal['revision'], idempotency_key=f'claim-{i}')
        work = goal['active_work']
        state = create_initial_state(work['run_id'], 'DEMO', work['task'])
        state.current_node = state.next_node = 'complete'
        states.write_project_current(state)
        if i < 5:
            states.archive_project_current('DEMO')
        goals.stop_goal(goal['id'], expected_revision=goal['revision'], idempotency_key=f'stop-{i}')
    expected = [goal_view(tmp_path, g) for g in goals.list_goals()]
    assert len(expected) == 6
    assert all(g['revision'] == 3 and g['work_units'] for g in expected)
    counts = count_reads(monkeypatch)
    service = TaskAssistantService(tmp_path, readiness_executor=NoReadiness())
    first = service.session('DEMO', tmp_path)
    assert first['goals'] == expected
    assert counts == {('read_project_current', 'DEMO'): 1, ('list_project_archived', 'DEMO'): 1}
    # Same service instance must re-read changed current and archived snapshots.
    state.next_node = 'blocked'
    state.current_node = 'implementation'
    states.write_project_current(state)
    archive = states.project_state_dir('DEMO') / 'archived'
    for path in archive.glob('*.json'):
        path.unlink()
    counts.clear()
    second = service.session('DEMO', tmp_path)
    assert second['goals'][-1]['active_work']['observed_execution_status'] == 'blocked'
    assert all('observed_execution_status' not in g['active_work'] for g in second['goals'][:-1])
    assert counts == {('read_project_current', 'DEMO'): 1, ('list_project_archived', 'DEMO'): 1}


def projection_goal(project, run, *, task='task', status='running'):
    work = dict(id='w1', run_id=run, task=task, status=status,
                evidence_refs=['retained/evidence'], assessment_failures=[{'attempt': 1}])
    return dict(project_key=project, work_units=[work], active_work=work,
                history=[{'detail': 'retain all history'}])


def test_batch_keeps_project_run_task_identity_and_current_precedence(tmp_path, monkeypatch):
    from goodteam_os.goal_continuation import goal_views

    states = StateStore(tmp_path)
    for project, node in [('A', 'complete'), ('B', 'rejected')]:
        state = create_initial_state('shared', project, 'task')
        state.current_node = state.next_node = node
        states.write_project_current(state)
        states.archive_project_current(project)
        state = create_initial_state('current', project, 'task')
        state.next_node = 'blocked' if project == 'A' else 'await_prd_approval'
        states.write_project_current(state)
    # Archive with the same run ID must not override current, even on task mismatch.
    state = create_initial_state('current', 'A', 'wrong-task')
    state.current_node = state.next_node = 'complete'
    states.write_project_current(state)
    states.archive_project_current('A')
    state.idea = 'task'
    state.current_node = 'implementation'
    state.next_node = 'blocked'
    states.write_project_current(state)
    goals = [projection_goal(p, r) for p in ['A', 'B'] for r in ['shared', 'current', 'absent']]
    goals += [projection_goal('A', 'current', task='wrong-task'),
              projection_goal('B', 'shared', task='wrong-task'),
              projection_goal('A', 'shared', status='done')]
    untouched = deepcopy(goals)
    expected = [goal_view(tmp_path, goal) for goal in goals]
    counts = count_reads(monkeypatch)
    actual = goal_views(tmp_path, goals)
    assert actual == expected
    assert goals == untouched
    assert counts == {(method, p): 1 for p in ['A', 'B']
                      for method in ['read_project_current', 'list_project_archived']}
    observed = [g['active_work'].get('observed_execution_status') for g in actual]
    assert observed == ['done', 'blocked', None, 'canceled', 'awaiting_approval', None, 'unknown', 'unknown', None]
    assert actual[4]['active_work']['observed_approval'] == 'prd'
    for result in actual:
        assert result['active_work'] is result['work_units'][0]
    actual[0]['active_work']['assessment_failures'][0]['attempt'] = 99
    assert goals == untouched


def test_archives_are_lazy_and_empty_results_are_shared(tmp_path, monkeypatch):
    from goodteam_os.goal_continuation import goal_views

    state = create_initial_state('current', 'A', 'task')
    StateStore(tmp_path).write_project_current(state)
    counts = count_reads(monkeypatch)
    goals = [projection_goal('A', 'current'), projection_goal('A', 'old', status='done'),
             projection_goal('EMPTY', 'missing', status='done')]
    goal_views(tmp_path, goals * 3)
    assert counts == {('read_project_current', 'A'): 1, ('read_project_current', 'EMPTY'): 1}
    counts.clear()
    assert goal_views(tmp_path, []) == []
    assert not counts
    result = goal_views(tmp_path, [projection_goal('EMPTY', 'missing')] * 3)
    assert all('observed_execution_status' not in g['active_work'] for g in result)
    assert counts == {('read_project_current', 'EMPTY'): 1, ('list_project_archived', 'EMPTY'): 1}


def test_direct_views_and_batches_do_not_reuse_other_calls_or_workspaces(tmp_path):
    from goodteam_os.goal_continuation import goal_views

    goal = projection_goal('A', 'shared')
    for workspace, node in [(tmp_path / 'one', 'complete'), (tmp_path / 'two', 'rejected')]:
        state = create_initial_state('shared', 'A', 'task')
        state.current_node = state.next_node = node
        StateStore(workspace).write_project_current(state)
    assert goal_view(tmp_path / 'one', goal)['active_work']['observed_execution_status'] == 'done'
    assert goal_views(tmp_path / 'two', [goal])[0]['active_work']['observed_execution_status'] == 'canceled'
    state.project_key = 'A'
    state.current_node = 'implementation'
    state.next_node = 'blocked'
    StateStore(tmp_path / 'one').write_project_current(state)
    assert goal_view(tmp_path / 'one', goal)['active_work']['observed_execution_status'] == 'blocked'
