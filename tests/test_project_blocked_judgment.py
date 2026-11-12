"""Bounded Manager judgment over blocked facts, not an execution bypass."""
from copy import deepcopy
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os.project_iteration import project_iteration_backlog, validate_iteration_choice
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.assistant_queue import queue_manager_iteration_choice


def test_blocked_context_is_judged_once_then_reconsidered_within_original_budget(tmp_path, monkeypatch):
    root=tmp_path/'project'
    root.mkdir()
    config=tmp_path/'project.yaml'
    config.write_text('key: APP\n')
    ledger=LocalTaskStore(root,'APP')
    task=ledger.create_task(title='Wait for evidence',description='Bounded work requires an unresolved dependency.',
        role='implementation',dependencies=['APP-missing'])
    store=TaskAssistantSessionStore(tmp_path,'APP')
    value=dict(id='grant',status='active',task_ids=[task['id']],task_intents={task['id']:continuation._intent(task)},
        authority={},max_batches=2,max_manager_calls=2,batches_started=0,manager_calls=0,pending_batch=None,
        planning_backlog_version='',reason='authorized')
    store._mutate(lambda session:{**session,'project_continuation':value})
    monkeypatch.setattr(continuation,'authority_snapshot',lambda *_:{})
    calls=[]
    def manager(**kwargs):
        calls.append(kwargs)
        assert kwargs['backlog']['allow_wait'] is True
        assert not any(x['eligible_to_plan'] for x in kwargs['backlog']['candidates'])
        return {'selection':{'selected_task_ids':[],'compared_task_ids':[task['id']],
            'rationale':'The task is waiting on an unresolved dependency; obtain its evidence before dispatch.'}}
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path,project_key='APP',root=root,
            project_config=config,manager_runner=manager,batch_runner=lambda **_:pytest.fail('No Worker authorized'))
    first=tick()
    assert first['status']=='active' and first['reason']=='awaiting_project_context', first
    assert first['manager_calls']==1 and first['batches_started']==0 and first['pending_batch'] is None
    assert tick()['manager_calls']==1 and len(calls)==1
    checkpoint = deepcopy(store.read())
    snapshot=project_iteration_backlog(root,'APP')
    # A new reserved/unknown attempt may NOT borrow an older completed waiting
    # result, even if the entire Context matches. Keep its identity on failure.
    reserved = {**checkpoint['project_continuation'], 'manager_calls': 2,
                'planning_backlog_version': snapshot['version'], 'planning_input_version': 3}
    store._mutate(lambda session: {**session, 'project_continuation': reserved})
    uncertain_calls = []
    def uncertain(**kwargs):
        uncertain_calls.append(kwargs['planning_attempt_id'])
        raise OSError('Unknown attempt outcome; inspect original invocation')
    unknown = continuation.tick_project_continuation(workspace=tmp_path,project_key='APP',root=root,
        project_config=config,manager_runner=uncertain)
    assert uncertain_calls == ['grant:2']
    assert unknown['planning_backlog_version'] == snapshot['version'] and unknown['manager_calls'] == 2
    store._mutate(lambda session: deepcopy(checkpoint))
    with pytest.raises(ValueError):
        queue_manager_iteration_choice(workspace=tmp_path,project_key='APP',root=root,
                                       expected_backlog_version=snapshot['version'])
    # A real new conversation fact causes another judgment, not restoration of
    # a stale plan or a new execution grant. Own recommendation events do not.
    store.append_message('new-fact','user','The dependency owner is investigating; do not start implementation yet.')
    second=tick()
    assert second['manager_calls']==2 and second['status']=='active' and len(calls)==2, second
    assert tick()['manager_calls']==2 and len(calls)==2
    store.append_message('another-fact','user','There is another relevant update to consider when budget is renewed.')
    assert tick()['status']=='exhausted' and len(calls)==2
    assert ledger.get_task(task['id'])['status']=='todo'
    assert not store.read().get('queued_task_proposals')
    # Empty advice is a captured protocol feature, never an absent/default parse.
    with pytest.raises(ValueError):
        validate_iteration_choice(snapshot,{'selected_task_ids':[],'compared_task_ids':[task['id']],
                                            'rationale':'Waiting for an actual dependency to become available.'})

    # A result arriving after authority changes is not activated as current advice.
    store._mutate(lambda session: deepcopy(checkpoint))
    store.append_message('authority-change', 'user', 'A new fact needs judgment under the existing authorization.')
    before_choices = sum(e.get('event') == 'project_iteration_choice_recorded' for e in store.read()['timeline'])
    def changed_authority(**kwargs):
        response = manager(**kwargs)
        monkeypatch.setattr(continuation, 'authority_snapshot', lambda *_: {'changed': True})
        return response
    rejected = continuation.tick_project_continuation(workspace=tmp_path,project_key='APP',root=root,
        project_config=config,manager_runner=changed_authority)
    assert rejected['status'] == 'blocked' and rejected['planning_backlog_version']
    assert sum(e.get('event') == 'project_iteration_choice_recorded' for e in store.read()['timeline']) == before_choices

    store._mutate(lambda session: deepcopy(checkpoint))
    monkeypatch.setattr(continuation, 'authority_snapshot', lambda *_: {})
    store.append_message('stop-during-call', 'user', 'Consider new facts, with Stop still authoritative.')
    def stopped_during_call(**kwargs):
        response = manager(**kwargs)
        continuation.stop_project_continuation(workspace=tmp_path, project_key='APP', continuation_id='grant')
        return response
    stopped = continuation.tick_project_continuation(workspace=tmp_path,project_key='APP',root=root,
        project_config=config,manager_runner=stopped_during_call)
    assert stopped['status'] == 'stopped' and stopped['planning_backlog_version']
