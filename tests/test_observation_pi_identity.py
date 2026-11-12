"""Actual Manager producer and method snapshots; model transport is an explicit double."""
import json
import pytest
from test_manager_pi_runtime import reject_nonfixture_transports  # noqa: F401
from test_manager_pi_method_producer import setup
from goodteam_os import graph, projects, manager_execution as manager
from goodteam_os.project_observation_runtime import judge_with_production_runtime, reconcile_completed_observations, _launch_under_authority
from goodteam_os.project_running_observation import _binding
from goodteam_os.project_outcome_discovery import authorization_digest
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workers import WorkerJobStore


def observation(tmp_path,monkeypatch):
    root,config,team,workers=setup(tmp_path)
    store=TaskAssistantSessionStore(config.parent.parent,'DEMO')
    value=dict(id='grant',status='active',authority='fixture',manager_calls=0,max_manager_calls=4,
        max_batches=3,seed_task_ids=['task'],seed_task_intents={'task':'intent'},approved_backlog_version='version',
        pending_batch={'run_id':'batch','task_ids':['task']},discovery={'allow_running_observation':True})
    value['authorization_digest']=authorization_digest(value)
    batch=dict(confirmed=True,expected_continuation_id='grant',project_root=str(root.resolve()),status='running',dispatch_run_id='attempt')
    store._mutate(lambda s:{**s,'project_continuation':value,'project_task_batches':{'batch':batch}})
    packet={'binding':_binding(store.read(),root),'workers':[{'job_id':'worker','status':'running'}]}
    store._mutate(lambda s:{**s,'project_continuation':{**s['project_continuation'],
        'running_observation':{'digest':'one','packet':packet}}})
    monkeypatch.setattr(projects,'load_project_target',lambda p:{})
    monkeypatch.setattr(graph,'resolve_project_runtime_root',lambda r:root)
    monkeypatch.setattr(graph,'resolve_role_adapter',lambda *a:'pi')
    monkeypatch.setattr(manager,'_configured_manager_model',lambda t:'fixture')
    monkeypatch.setattr(manager,'_manager_settings',lambda t:{})
    return root,config,store,packet


@pytest.mark.parametrize('mode',['recover','tampered_request','stopped'])
def test_captured_pi_completed_observation_recovers_without_second_prompt(tmp_path,monkeypatch,mode):
    root,config,store,packet=observation(tmp_path,monkeypatch)
    calls=[]
    errors=[]
    from goodteam_os import project_observation_runtime as runtime
    original_run=runtime._run
    def debug_run(**kwargs):
        try:
            return original_run(**kwargs)
        except Exception as exc:
            errors.append(str(exc))
            raise
    monkeypatch.setattr(runtime,'_run',debug_run)
    def transport(workers,job_id):
        calls.append(job_id)
        job_root=workers.root/job_id
        job=json.loads((job_root/'job.json').read_text())
        assert job['method_loading_mode']=='manager_pi_methods.v1'
        assert job['status']=='queued'
        handle=object()
        assert workers._launch_authorizer(job,lambda:handle,cleanup=lambda _:None) is handle
        (job_root/'result.md').write_text(json.dumps({'assessment':'wait',
            'rationale':'The original Worker is still executing the authorized task.','observed_job_ids':['worker']}))
        # Explicit session-evidence double; this test does not claim a real RPC.
        from hashlib import sha256
        from goodteam_os.skills.pi_methods import PiMethodInput
        from goodteam_os.pi_manager_tools import PiManagerTools
        method=PiMethodInput(workers,job_id)
        policy=PiManagerTools()
        metadata=dict(status='settled',job_id=job_id,execution_id=job['run_id'],project_key=job['project_key'],
            session_id='fixture-session',session_file=str(job_root/'fixture-session.jsonl'),
            manager_tool_contract=dict(version=policy.version,tools=list(policy.tools),
                guarantee='tool-level; not an OS sandbox'),
            active_operation=dict(id='fixture-operation',kind='initial_prompt',state='settled',
                prompt_sha256=sha256(method.prompt().encode()).hexdigest()),
            settled_operation_id='fixture-operation')
        method.session_binding(metadata,create=True)
        (job_root/'pi_session.json').write_text(json.dumps(metadata))
        workers._write_status(job_root,job,'completed','Fixture transport complete; response lost.')
        assert json.loads((job_root/'job.json').read_text())['status']=='queued'
        raise TimeoutError('Fixture lost completion acknowledgement')
    monkeypatch.setattr(WorkerJobStore,'run_job',transport)
    def judge():
        return judge_with_production_runtime(store=store,root=root,project_config=config,project_key='DEMO',
            value=store.read()['project_continuation'],authority_check=lambda:True)
    first=judge()
    assert first['running_judgments']['one']['status']=='unconfirmed'
    assert len(calls)==1,errors
    record=first['running_judgments']['one']
    assert record['transport_started']
    assert record['manager_invocation']['idempotency_key'].startswith('manager-job:DEMO:running-observation:')
    if mode=='tampered_request':
        path=WorkerJobStore(root).root/calls[0]/'job.json'
        job=json.loads(path.read_text())
        job['model']='different-model'
        path.write_text(json.dumps(job))
    if mode=='stopped':
        store._mutate(lambda s:{**s,'project_continuation':{**s['project_continuation'],'status':'stopped'}})
    def recover():
        return reconcile_completed_observations(store=store,root=root,project_key='DEMO',
            value=store.read()['project_continuation'],authority_check=lambda:True)
    recovered=recover()
    expected={'recover':'recorded','tampered_request':'unconfirmed','stopped':'stale_result'}[mode]
    assert recovered['running_judgments']['one']['status']==expected
    recover()
    assert len(calls)==1 and recovered['manager_calls']==1


def test_rpc_cleanup_after_launch_commit_failure_keeps_transport_single(tmp_path,monkeypatch):
    root,config,store,packet=observation(tmp_path,monkeypatch)
    record={'status':'reserved','manager_invocation':{'job_id':'fixture-job'}}
    store._mutate(lambda s:{**s,'project_continuation':{**s['project_continuation'],'running_judgments':{'one':record}}})
    real_mutate=store._mutate
    def lost_ack(operation):
        real_mutate(operation)
        raise OSError('Fixture acknowledgement lost after committed launch')
    monkeypatch.setattr(store,'_mutate',lost_ack)
    handle=object()
    cleaned=[]
    launched=[]
    def launch():
        launched.append(handle)
        return handle
    with pytest.raises(OSError):
        _launch_under_authority(store=store,root=root,observation=packet,observation_id='one',
            continuation_id='grant',job_id='fixture-job',authority_check=lambda:True,launch=launch,
            cleanup=lambda rpc:cleaned.append(rpc))
    assert launched==cleaned==[handle]
    assert store.read()['project_continuation']['running_judgments']['one']['transport_started']
    monkeypatch.setattr(store,'_mutate',real_mutate)
    with pytest.raises(manager.ManagerExecutionError):
        _launch_under_authority(store=store,root=root,observation=packet,observation_id='one',
            continuation_id='grant',job_id='fixture-job',authority_check=lambda:True,launch=launch,
            cleanup=lambda rpc:cleaned.append(rpc))
    assert launched==cleaned==[handle]
