"""Real grant/ledger/question/answer/queue path; Manager and capability are fixtures."""
from copy import deepcopy
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_planner as planner
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


@pytest.mark.parametrize("drift_during_call, followup", [(False, False), (True, False), (False, True), (True, True)])
def test_delivery_does_not_complete_remaining_backlog_or_hide_changed_facts(tmp_path, monkeypatch, drift_during_call, followup):
    root = tmp_path / 'project'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    delivered = ledger.create_task(title='Document first endpoint', description='Document the first endpoint.', role='implementation',
        write_domains=['docs/first'], acceptance_criteria=['First endpoint is documented.'])
    dependency = ledger.create_task(title='Confirm product audience', description='Record the owner audience decision.',
        role='implementation', write_domains=['docs/audience'], acceptance_criteria=['Audience is explicit.'])
    blocked = ledger.create_task(title='Clarify second endpoint', description='Document the second endpoint after its scope is clear.', role='implementation',
        write_domains=['docs/second'], dependencies=[dependency['id']], acceptance_criteria=['Second endpoint has clear scope.'])
    ledger.update_task(blocked['id'], status='blocked')
    request = dict(confirmed=True, billing_acknowledged=True, idempotency_key='delivery-backlog', task_ids=[delivered['id']] if followup else [delivered['id'], blocked['id']], max_batches=3,
        expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version'], discovery=dict(
            objective='Document the approved API endpoints within the existing scope.', allowed_write_domains=['docs'],
            max_new_tasks=2, max_planning_calls=6, allow_backlog_judgment=True))
    continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root, project_config=config, request=request)
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    if followup:
        # Reproduce a task previously published within this grant, not a new grant.
        def published(session):
            value = session['project_continuation']
            value['task_ids'].append(blocked['id'])
            value['task_intents'][blocked['id']] = continuation._intent(blocked)
            value['discovered_task_ids'].append(blocked['id'])
            value['new_tasks_created'] += 1
            return session
        store._mutate(published)
    # Acceptance is a fixture here; do not claim a real Git delivery check.
    ledger.update_task(delivered['id'], status='done', delivery_verified=True)
    outcome = {'batch_run_id': 'accepted-first', 'delivery': {'receipt_id': 'verified-first'}, 'quality': {}, 'tasks': []}
    monkeypatch.setattr(discovery, 'verified_outcome', lambda **k: deepcopy(outcome))
    monkeypatch.setattr(discovery, '_remember', lambda *a: None)
    monkeypatch.setattr(planner, '_project_evidence', lambda *a, **k: {'fixture': 'no repository inspection claimed'})
    calls = []
    def manager(**kwargs):
        packet = kwargs['context']
        calls.append(deepcopy(packet))
        if 'verified_outcome' in packet:
            assert packet['verified_outcome'] == outcome
            if 'authorized_waiting_task_ids' in packet:
                assert packet['authorized_waiting_task_ids'] == [blocked['id']]
            if drift_during_call:
                ledger.update_task(dependency['id'], status='review')
            return {'selection': {'new_task': None, 'rationale': 'The remaining task needs concrete scope facts before another useful action is justified.'}}
        return {'selection': {'selected_task_ids': [blocked['id']], 'compared_task_ids': [blocked['id']],
            'rationale': 'The original remaining task is ready and can proceed within the approved scope.'}}
    class BeforeExecution(Exception):
        pass
    def run(**kwargs):
        raise BeforeExecution
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key='DEMO', root=root, project_config=config,
            manager_runner=manager, batch_runner=run)
    first = tick()
    if drift_during_call:
        assert first['status'] == 'active' and first['reason'] == 'discovery_context_changed'
        assert first['discovery_pending'] is None and not first['discovery_consumed']
        assert len(calls) == 1
        second = tick()
        assert second['manager_calls'] == 2 and len(calls) == 2
        assert second['reason'] == 'backlog_judgment_waiting'
        assert tick()['manager_calls'] == 2 and len(calls) == 2
        return
    assert first['status'] == 'active' and first['reason'] == 'backlog_judgment_waiting'
    assert first['manager_calls'] == 1 and len(calls) == 1
    assert tick()['manager_calls'] == 1 and len(calls) == 1
    # Same delivery, new original task facts: exactly one fresh judgment.
    ledger.update_task(blocked['id'], status='review')
    second = tick()
    assert second['status'] == 'active' and second['manager_calls'] == 2 and len(calls) == 2
    assert calls[0]['authorized_backlog_context']['receipt_id'] != calls[1]['authorized_backlog_context']['receipt_id']
    assert tick()['manager_calls'] == 2 and len(calls) == 2
    # Dependency facts are a separate wake input even when this seed remains review.
    ledger.update_task(dependency['id'], status='done', delivery_verified=True)
    assert tick()['manager_calls'] == 3 and len(calls) == 3
    assert tick()['manager_calls'] == 3 and len(calls) == 3
    ledger.update_task(blocked['id'], status='todo')
    with pytest.raises(BeforeExecution):
        tick()
    assert store.read()['project_continuation']['pending_batch']['task_ids'] == [blocked['id']]
    assert len(calls) == 4
