"""Real Manager/queue/claim/thread locks; execution output is an offline seam."""
from threading import Event
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_execution as execution
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


@pytest.mark.parametrize("stop_while_waiting", [False, True])
def test_original_followup_waits_for_conflicting_batch_then_starts_once(tmp_path, monkeypatch, stop_while_waiting):
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
    entered = {a['id']: Event(), b['id']: Event(), conflict['id']: Event()}
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
        # Use the original saved-choice -> queue handoff, as discovery does.
        # A is still executing; C is already inside this user's exact grant.
        from goodteam_os.project_iteration import validate_iteration_choice
        from goodteam_os.assistant_queue import queue_manager_iteration_choice
        snapshot = project_iteration_backlog(root, 'DEMO', limit=None)
        choice = validate_iteration_choice(snapshot, {'selected_task_ids': [conflict['id']],
            'compared_task_ids': [conflict['id']], 'rationale': 'Follow-up for the approved same document.'})
        choice['source_event_id'] = 'fixture-original-followup'
        store.append_event(choice['source_event_id'], 'project_iteration_choice_recorded', details={'choice': choice})
        queued = queue_manager_iteration_choice(workspace=tmp_path, project_key='DEMO', root=root,
            expected_backlog_version=snapshot['version'], expected_task_ids=[conflict['id']],
            expected_choice_event_id=choice['source_event_id'])
        pending = dict(task_ids=[conflict['id']], proposal_ids=queued['queued_proposal_ids'],
            expected_revisions={i: 1 for i in queued['queued_proposal_ids']}, run_id='')
        def reserve(session):
            session['project_continuation'].update(pending_batch=pending, batches_started=2)
            return session
        store._mutate(reserve)
        before = store.read()['project_continuation']
        for _ in range(2):
            waiting = tick()
            assert waiting['status'] == 'active' and waiting['reason'] == 'awaiting_batch_resources'
            assert waiting['pending_batch'] == pending
            assert waiting['manager_calls'] == before['manager_calls']
            assert waiting['batches_started'] == 2
            assert executions == [a['id']] and len(calls) == 1
            assert len(store.read()['project_task_batches']) == 1
        if stop_while_waiting:
            continuation.stop_project_continuation(workspace=tmp_path, project_key='DEMO', continuation_id=grant['id'])
        release.set()
        for thread in list(execution._BACKGROUND_THREADS.values()):
            thread.join(5)
        # The offline operation emitted no real subprocess; its known terminal
        # evidence is supplied, not inferred from a merely failed status.
        monkeypatch.setattr('goodteam_os.project_iteration_recovery.project_batch_recovery_evidence',
            lambda **_: {'unknown_task_ids': []})
        resumed = tick()
        if stop_while_waiting:
            assert resumed['status'] == 'stopped'
            assert executions == [a['id']] and len(calls) == 1
            assert not entered[conflict['id']].is_set()
            return
        assert entered[conflict['id']].wait(3), resumed
        for thread in list(execution._BACKGROUND_THREADS.values()):
            thread.join(5)
        assert executions == [a['id'], conflict['id']] and len(calls) == 1
        assert resumed['pending_batch']['run_id'] and resumed['pending_batch']['task_ids'] == pending['task_ids']
        assert len(store.read()['project_task_batches']) == 2
        continuation.stop_project_continuation(workspace=tmp_path, project_key='DEMO', continuation_id=grant['id'])
        assert tick()['status'] == 'stopped'
    finally:
        release.set()
        # Complete the actual background threads before tmp_path cleanup.
        for thread in list(execution._BACKGROUND_THREADS.values()):
            thread.join(5)
