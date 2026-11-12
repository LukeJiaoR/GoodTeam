"""Real Manager/queue/claim/thread locks; execution output is an offline seam."""
from threading import Event
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_execution as execution
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_manager_starts_independent_b_before_a_finishes_and_retains_both(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    a, b = [ledger.create_task(title=f'Work {label}', description=f'Only document {label}.', role='implementation',
        write_domains=[f'docs/{label}'], acceptance_criteria=[f'{label} documented']) for label in ('A', 'B')]
    conflict = ledger.create_task(title="Conflicting C", description="Change A path", role="implementation", write_domains=["docs/A"])
    grant = continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
        project_config=config, request=dict(confirmed=True, billing_acknowledged=True, idempotency_key='pool',
        task_ids=[a['id'], b['id'], conflict['id']], max_batches=4,
        expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version']))
    monkeypatch.setattr(execution, '_runtime_tracks', lambda workspace, config, key, root, tasks, proposals: [
        dict(project_task_id=t['id'], role=t['role'], execution_policy='standard') for t in tasks])
    entered = {a['id']: Event(), b['id']: Event()}
    release = Event()
    executions = []
    def run(**kwargs):
        # Both calls use the real original batch entry and per-run lock.
        kwargs['capacity_check_start']()
        task_id = kwargs['tracks'][0]['project_task_id']
        executions.append(task_id)
        entered[task_id].set()
        assert release.wait(10), 'fixture did not release execution'
        return {'all_ok': False, 'jobs': [], 'integration': {}}
    monkeypatch.setattr(execution, 'dispatch_tracks', run)
    calls = []
    def manager(**kwargs):
        selected = a if not calls else b
        if calls:
            candidates = {row['task_id']: row for row in kwargs['backlog']['candidates']}
            assert candidates[b['id']]['eligible_to_plan']
            assert not candidates[conflict['id']]['eligible_to_plan']
            assert candidates[conflict['id']]['reason'] == 'reserved_or_conflicting_with_inflight_work'
        calls.append(selected['id'])
        return {'selection': {'selected_task_ids': [selected['id']], 'compared_task_ids': [a['id'], b['id']],
            'rationale': 'Independent approved document paths can use free Worker resources.'}}
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
            project_config=config, manager_runner=manager)
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    try:
        first = tick()
        assert entered[a['id']].wait(3), first
        second = tick()
        assert entered[b['id']].wait(3), second
        assert not release.is_set() and executions == [a['id'], b['id']]
        session = store.read()
        assert len(session['project_task_batches']) == 2
        assert {tuple(x['task_ids']) for x in session['project_task_batches'].values()} == {(a['id'],), (b['id'],)}
        assert second['batches_started'] == second['manager_calls'] == 2
        tick()  # Revisit original A, never reserve or execute it again.
        assert executions == [a['id'], b['id']] and len(calls) == 2
        with pytest.raises(ValueError):
            execution._require_execution_slot(tmp_path, 'DEMO', 'new', continuation_id=grant['id'], task_ids=[conflict['id']])
        continuation.stop_project_continuation(workspace=tmp_path, project_key='DEMO', continuation_id=grant['id'])
        assert tick()['status'] == 'stopped' and len(calls) == 2
    finally:
        release.set()
        # Complete the actual background threads before tmp_path cleanup.
        for thread in list(execution._BACKGROUND_THREADS.values()):
            thread.join(5)
