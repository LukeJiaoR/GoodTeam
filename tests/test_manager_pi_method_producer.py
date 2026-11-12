"""Original producer/capture/store/Role assets; no Runtime or model invocation."""
from copy import deepcopy
import json
import pytest
from goodteam_os.manager_execution import _create_manager_job
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.models import SkillError
from goodteam_os.workers import WorkerJobStore
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.project_registry import ProjectRegistry


def setup(tmp_path):
    root=tmp_path/'source'
    root.mkdir()
    workspace=tmp_path/'workspace'
    config=workspace/'projects/demo.local.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('project_key: DEMO\n')
    ProjectRegistry().register_project(config,root=root,key='DEMO')
    team=TeamControlPlaneStore(workspace,'DEMO')
    team.load_or_migrate(legacy_target={})
    return root,config,team,WorkerJobStore(root)


def spec(name='one'):
    return dict(run_id='task-assistant:DEMO',role='orchestrator',adapter='pi',model='fixture',
                task='Choose coordination only from supplied facts.',context='PRIVATE '+name,
                constraints=['No independent investigation'],expected_artifacts=[],
                project_key='DEMO',phase='task_assistant_chat',execution_policy='read_only',
                idempotency_key='turn:'+name)


def create(root,config,store,name='one',**updates):
    return _create_manager_job(store,project_config=config,project_root=root,project_key='DEMO',
        logical_id='message:'+name,spec={**spec(name),**updates})


def test_new_manager_pi_captures_role_methods_and_replays_original(tmp_path,monkeypatch):
    root,config,team,store=setup(tmp_path)
    job,created=create(root,config,store)
    assert created and job['method_loading_mode']=='manager_pi_methods.v1'
    assert job['execution_policy']=='read_only'
    method=PiMethodInput(store,job['id'])
    assert method.manager_tools
    prompt=method.prompt()
    payload=json.loads(prompt)
    assert len(payload['required_methods'])==2
    assert 'manager-coordination' in prompt and 'PRIVATE one' in prompt
    with pytest.raises(SkillError,match='tool policy witness'):
        method.resource_channel(active=lambda:True)
    other,_=create(root,config,store,'two')
    other_input=PiMethodInput(store,other['id'])
    assert 'PRIVATE one' not in other_input.prompt() and 'PRIVATE two' not in prompt
    monkeypatch.setattr('goodteam_os.skills.role_packages.role_method_package',
        lambda name:pytest.fail('Original task must not reread latest methods'))
    replay,new=create(root,config,store)
    assert not new and replay['id']==job['id']
    assert PiMethodInput(store,replay['id']).prompt()==prompt


def test_manager_method_request_cannot_expand_policy_or_impersonate_role():
    from goodteam_os.skills.worker_requests import _request
    original={**spec(), 'session_policy':'new','method_loading_mode':'manager_pi_methods.v1'}
    assert _request(original)['method_loading_mode']=='manager_pi_methods.v1'
    for change in ({'role':'research'},{'execution_policy':'standard'}, {'adapter':'codex'},
                   {'session_policy':'reuse'}, {'worker_session_ref':'other'},
                   {'web_search':{}},{'method_workspace_mode':'dispatch_isolated'},
                   {'review_target':{}},{'project_task_id':'task-1'}):
        with pytest.raises(SkillError):
            _request({**deepcopy(original),**change})
    legacy={**original}
    legacy.pop('method_loading_mode')
    assert 'method_loading_mode' not in _request(legacy)


def test_existing_legacy_manager_job_is_not_upgraded(tmp_path):
    root,config,team,store=setup(tmp_path)
    old=store.create_job(**spec())
    replay,created=create(root,config,store)
    assert not created and replay['id']==old['id']
    assert 'method_loading_mode' not in replay


def test_captured_old_manager_task_stays_strict_and_new_custom_empty_stays_empty(tmp_path):
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.task_locks import TaskSkillIdentity
    root,config,team,store=setup(tmp_path)
    current=team.read()
    team.execute([{'kind':'set_role_skills','subject':'manager:orchestrator','mode':'custom','bindings':[],
        'expected_binding_revision':current['skill_bindings']['manager:orchestrator']['binding_revision']}],
        actor='user',surface='team_ui',expected_revision=current['revision'],command_key='empty-manager')
    original=spec('old-lock')
    capture_task_start(workspace=config.parent.parent,project_key='DEMO',worker_root=root,
        task=TaskSkillIdentity('manager_message','DEMO','message:old-lock'),command_key='manager-start:DEMO:message:old-lock',
        confirmed_input={k:original[k] for k in ('run_id','phase','task','context','constraints','expected_artifacts')},
        subjects=('manager:orchestrator',))
    old,_=create(root,config,store,'old-lock')
    assert 'method_loading_mode' not in old
    with pytest.raises(SkillError):
        PiMethodInput(store,old['id'])
    fresh,_=create(root,config,store,'empty-new')
    assert fresh['method_loading_mode']=='manager_pi_methods.v1'
    with pytest.raises(SkillError) as error:
        PiMethodInput(store,fresh['id'])
    assert error.value.code=='methods_blocked'  # Explicit custom-empty never gains defaults.
