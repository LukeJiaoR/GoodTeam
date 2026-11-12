"""Real grant/ledger/question/answer/queue path; Manager and capability are fixtures."""
from copy import deepcopy
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_planner as planner
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_human_feedback import question_board
from goodteam_os.assistant_requests import execute_message
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_backlog_question_answer_and_authorized_investigation_use_original_outbox(tmp_path, monkeypatch):
    root=tmp_path/'project'
    root.mkdir()
    config=tmp_path/'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger=LocalTaskStore(root,'DEMO')
    seed=ledger.create_task(title='Clarify document audience', description='Document the bounded API after its audience is decided.',
        role='implementation',write_domains=['docs'],acceptance_criteria=['Audience is explicit in the documentation.'])
    ledger.update_task(seed['id'],status='blocked')
    request=dict(confirmed=True,billing_acknowledged=True,idempotency_key='backlog',task_ids=[seed['id']],max_batches=3,
        expected_backlog_version=project_iteration_backlog(root,'DEMO')['version'],discovery=dict(
            objective='Clarify the audience and improve the API documentation within the existing scope.',
            allowed_write_domains=['docs'],max_new_tasks=2,max_planning_calls=4,allow_investigation=True))
    with pytest.raises(ValueError,match='available project backlog'):
        continuation.configure_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
            project_config=config,request=request)
    assert 'allow_backlog_judgment' not in discovery.normalize_discovery(request['discovery'])
    request['discovery']['allow_backlog_judgment']=True
    grant=continuation.configure_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
        project_config=config,request=request)
    store=TaskAssistantSessionStore(tmp_path,'DEMO')
    monkeypatch.setattr(planner,'_project_evidence',lambda *a,**k:{'fixture':'no repository inspection claimed'})
    monkeypatch.setattr('goodteam_os.team_control_plane.team_view_for_config',lambda *a,**k:{'roles':[
        {'role':'research','state':'enabled','capability':'ready','workers':[{'dispatchable':True}]}]})
    calls=[]
    def manager(**kwargs):
        packet=kwargs['context']
        calls.append(deepcopy(packet))
        assert packet['verified_outcome'] is None and 'undelivered_source' not in packet
        source=packet['backlog_judgment_source']
        assert source['delivery_verified'] is False and source['tasks'][0]['status']=='blocked'
        if len(calls)==1:
            return {'selection':{'new_task':None,
                'human_question':{'question':'Should this API document serve internal users or external customers?',
                    'why_human':'Only the owner can choose the intended product audience.'},
                'rationale':'Resolve the audience decision before undertaking investigation or changing documentation.'}}
        assert packet['human_answers'][0]['text']=='External customers, without expanding the write scope.'
        return {'selection':{'new_task':{'task_type':'investigation','title':'Investigate external audience needs',
            'description':'Research the documentation facts needed for external customers within the approved objective.',
            'acceptance_criteria':['Return evidence and remaining uncertainty about external audience needs.'],
            'write_domains':[],'dependencies':[],
            'source_refs':[source['receipt_id'],packet['human_answers'][0]['receipt_id']]},
            'rationale':'An authorized read-only investigation can resolve the missing facts without bypassing the blocked task.'}}
    class BeforeExecution(Exception):
        pass
    def no_runtime(**kwargs):
        raise BeforeExecution
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
            project_config=config,manager_runner=manager,batch_runner=no_runtime)
    waiting=tick()
    assert waiting['reason']=='awaiting_human_decision', waiting
    assert waiting['manager_calls']==1 and waiting['batches_started']==0
    assert tick()['manager_calls']==1 and len(calls)==1
    board=question_board(store.read())['items']
    assert len(board)==1 and board[0]['can_answer']
    service=TaskAssistantService(tmp_path,project_config=config)
    execute_message(service,project_key='DEMO',root=root,text=f"/answer {board[0]['id']} External customers, without expanding the write scope.",message_id='answer-1')
    with pytest.raises(BeforeExecution):
        tick()
    current=store.read()['project_continuation']
    assert current['manager_calls']==2 and current['new_tasks_created']==1 and len(calls)==2
    task_id=current['pending_batch']['task_ids'][0]
    task=ledger.get_task(task_id)
    assert task['role']=='research' and task['execution_policy']=='read_only' and task['write_domains']==[]
    assert ledger.get_task(seed['id'])['status']=='blocked' and not ledger.get_task(seed['id']).get('delivery_verified')
    assert len(store.read()['queued_task_proposals'])==1
    with pytest.raises(BeforeExecution):
        tick()
    assert len(calls)==2 and len(ledger.list_tasks())==2
    # The new source does not add investigation permission to any grant.
    packet=deepcopy(calls[-1])
    packet['discovery'].pop('allow_investigation')
    with pytest.raises(ValueError):
        discovery.validate_discovered_task(packet,manager(context=calls[-1])['selection'])
    assert grant['authorization_digest']==current['authorization_digest']


@pytest.mark.parametrize("question", [False, True])
def test_independent_null_and_ready(tmp_path, monkeypatch, question):
    monkeypatch.setenv('GOODTEAM_STATE_DIR',str(tmp_path/'operator'))
    monkeypatch.setenv('GOODTEAM_HOME',str(tmp_path/'home'))
    monkeypatch.setenv('GOODTEAM_PROJECTS_REGISTRY',str(tmp_path/'registry.json'))
    root=tmp_path/'project'
    root.mkdir()
    config=tmp_path/'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger=LocalTaskStore(root,'DEMO')
    seed=ledger.create_task(title='Clarify document audience', description='Document the bounded API after its audience is decided.',
        role='implementation',write_domains=['docs'],acceptance_criteria=['Audience is explicit in the documentation.'])
    ledger.update_task(seed['id'],status='blocked')
    request=dict(confirmed=True,billing_acknowledged=True,idempotency_key='backlog',task_ids=[seed['id']],max_batches=3,
        expected_backlog_version=project_iteration_backlog(root,'DEMO')['version'],discovery=dict(
            objective='Clarify the audience and improve the API documentation within the existing scope.',
            allowed_write_domains=['docs'],max_new_tasks=2,max_planning_calls=4,allow_investigation=True))
    with pytest.raises(ValueError,match='available project backlog'):
        continuation.configure_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
            project_config=config,request=request)
    assert 'allow_backlog_judgment' not in discovery.normalize_discovery(request['discovery'])
    request['discovery']['allow_backlog_judgment']=True
    continuation.configure_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
        project_config=config,request=request)
    store=TaskAssistantSessionStore(tmp_path,'DEMO')
    monkeypatch.setattr(planner,'_project_evidence',lambda *a,**k:{'fixture':'no repository inspection claimed'})
    monkeypatch.setattr('goodteam_os.team_control_plane.team_view_for_config',lambda *a,**k:{'roles':[
        {'role':'research','state':'enabled','capability':'ready','workers':[{'dispatchable':True}]}]})
    calls=[]
    def manager(**kwargs):
        calls.append(kwargs)
        if 'backlog_judgment_source' in kwargs['context']:
            return {'selection':{'new_task':None,'rationale':'No useful in-scope action is justified until missing facts change.', **({'human_question':{'question':'Which audience should the API documentation serve?', 'why_human':'Only the owner can decide the intended product audience.'}} if question else {})}}
        return {'selection':{'selected_task_ids':[seed['id']],'compared_task_ids':[seed['id']],
            'rationale':'The original task is now ready and can proceed within its original approved scope.'}}
    class BeforeExecution(Exception):
        pass
    def execute(**kwargs):raise BeforeExecution
    def tick():return continuation.tick_project_continuation(workspace=tmp_path,project_key='DEMO',root=root,
        project_config=config,manager_runner=manager,batch_runner=execute)
    first=tick()
    assert first['status']=='active' and first['reason']==('awaiting_human_decision' if question else 'backlog_judgment_waiting'),first
    assert len(calls)==1 and first['batches_started']==0
    assert tick()['manager_calls']==1 and len(calls)==1
    ledger.update_task(seed['id'],status='todo')
    with pytest.raises(BeforeExecution):
        tick()
    assert len(calls)==2
    assert store.read()['project_continuation']['pending_batch']['task_ids']==[seed['id']]
    if question:
        board=question_board(store.read())['items']
        assert len(board)==1 and board[0]['can_answer']
