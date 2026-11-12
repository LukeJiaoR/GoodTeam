from copy import deepcopy
import pytest
from goodteam_os import project_scope_addition as addition
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_human_addition_preserves_grant_and_enables_original_next_planning(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    root.mkdir()
    config = tmp_path / 'project.yaml'
    config.write_text('key: APP\n')
    tasks = LocalTaskStore(root, 'APP')
    a = tasks.create_task(title='Waiting A', description='Needs dependency.', role='implementation', dependencies=['APP-missing'])
    b = tasks.create_task(title='Independent B', description='Document B.', role='implementation', write_domains=['docs'])
    store = TaskAssistantSessionStore(tmp_path, 'APP')
    value = dict(id='grant', status='active', task_ids=[a['id']], task_intents={a['id']: continuation._intent(a)},
                 authority={}, max_batches=3, max_manager_calls=3, batches_started=0, manager_calls=0,
                 pending_batch=None, planning_backlog_version='', reason='awaiting_project_context')
    store._mutate(lambda session: {**session, 'project_continuation': value})
    monkeypatch.setattr(addition, 'authority_snapshot', lambda *_: {})
    monkeypatch.setattr(continuation, 'authority_snapshot', lambda *_: {})
    request = dict(continuation_id='grant', task_ids=[b['id']], expected_backlog_version=project_iteration_backlog(root, 'APP')['version'],
                   confirmed=True, billing_acknowledged=True, idempotency_key='add-B')
    def add(**changes):
        return addition.add_project_continuation_tasks(workspace=tmp_path, project_key='APP', root=root,
            project_config=config, request={**request, **changes})
    before = deepcopy(store.read())
    with pytest.raises(ValueError, match='Explicit confirmation'):
        add(confirmed=False)
    assert store.read() == before
    # The real Worker execution guard is held for the entire running batch.
    # Scope confirmation must not acquire that long-lived execution lock.
    from goodteam_os.workflow_mutations import workflow_guard
    with workflow_guard(tmp_path, 'APP'):
        result = add()
    assert result['task_ids'] == [a['id'], b['id']]
    assert result['manager_calls'] == result['batches_started'] == 0
    assert result['max_manager_calls'] == result['max_batches'] == 3
    assert result['pending_batch'] is None
    assert not store.read().get('project_task_batches')
    retained = deepcopy(store.read())
    assert add() == result and store.read() == retained
    seen = []
    def manager(**kwargs):
        seen.append(kwargs)
        return {'selection': {'selected_task_ids': [b['id']], 'compared_task_ids': [a['id'], b['id']],
                'rationale': 'B is independent authorized work while A awaits its dependency.'}}
    def batch(**kwargs):
        assert len(kwargs['proposal_ids']) == 1
        return {'run_id': 'test-batch'}
    outcome = continuation.tick_project_continuation(workspace=tmp_path, project_key='APP', root=root,
        project_config=config, manager_runner=manager, batch_runner=batch)
    assert seen and outcome['pending_batch']['task_ids'] == [b['id']]
    continuation.stop_project_continuation(workspace=tmp_path, project_key='APP', continuation_id='grant')
    stopped = deepcopy(store.read())
    assert add()['status'] == 'stopped' and store.read() == stopped
    with pytest.raises(ValueError):
        add(idempotency_key='different')
