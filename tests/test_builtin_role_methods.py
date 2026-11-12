"""Actual first-party content through existing snapshots/Pi input; no model call."""
import json
import pytest
from goodteam_os.skills.role_packages import publish_role_methods, role_method_package
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.availability import SkillAvailabilityStore
from goodteam_os.skills.bindings import default_binding
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillLockStore, TaskSkillPlan
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.models import SkillError
from goodteam_os.workers import WorkerJobStore
from goodteam_os.service import service_state_dir


def test_role_packages_load_shared_versions_and_separate_invocation_context(tmp_path, monkeypatch):
    from pathlib import Path
    root=tmp_path/'worker'
    root.mkdir()
    operator=Path(service_state_dir())
    library=SkillLibraryService(operator,worker_roots=(root,))
    subjects=('role:research','role:planner')
    items=publish_role_methods(library.store,subjects)
    assert items[subjects[0]][0]==items[subjects[1]][0]
    assert items==publish_role_methods(library.store,subjects)
    SkillAvailabilityStore(operator/'skills/availability',library.store,worker_roots=(root,)).initialize()
    locks=TaskSkillLockStore(operator/'skills/task-locks',library.store,worker_roots=(root,))
    task=TaskSkillIdentity('direct_task','project','real-methods')
    bindings={s:default_binding(s,project_key='project') for s in subjects}
    contracts={s:{'ref':b['role_contract_ref'],'subject':s} for s,b in bindings.items()}
    ref=locks.begin(task,command_key='start',task_input={},
                    resolve_plan=lambda:TaskSkillPlan(1,bindings,contracts,items))
    workers=WorkerJobStore(root)
    methods=[]
    for role in ('research','planner'):
        job=workers.create_job(run_id='run',role=role,adapter='pi',model='fixture',task='Bounded '+role,
            context=role+' PRIVATE',project_key='project',idempotency_key=role,
            task_skill_context=task_skill_context(task,ref),skill_subject='role:'+role,
            method_loading_mode='ordinary_pi_methods.v1')
        methods.append(PiMethodInput(workers,job['id']))
    research,planner=methods
    payload=json.loads(research.prompt())
    assert len(payload['required_methods'])==2
    assert 'not the entire private project Context' in research.prompt()
    assert 'PRIVATE' in research.prompt() and 'planner PRIVATE' not in research.prompt()
    assert 'planning-handoff' in planner.prompt() and 'research PRIVATE' not in planner.prompt()
    assert b'No mandatory search stage' in research.provider.read_resource(research.ref,
        skill_id=items['role:research'][1]['skill_id'],relative='references/evidence.md',call_id='evidence-read')
    # No prompt lookup: independent package assets remain functional without it.
    monkeypatch.setattr('goodteam_os.roles.stable_role_prompt',lambda role:pytest.fail('legacy prompt lookup'))
    assert role_method_package('manager-coordination')[0].resource('SKILL.md')
    with pytest.raises(SkillError,match='No reviewed default'):
        publish_role_methods(library.store,('role:unknown',))


def test_new_default_dispatch_loads_real_packages_but_old_requests_remain_legacy(tmp_path, monkeypatch):
    from test_dispatch_isolation import repository
    from goodteam_os.dispatch import dispatch_tracks
    from goodteam_os.project_registry import ProjectRegistry
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from goodteam_os.dispatch_workspace import dispatch_artifact_root
    from goodteam_os.skills.models import canonical_json, content_digest
    source=repository(tmp_path/'source')
    workspace=tmp_path/'workspace'
    config=workspace/'projects/demo.local.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('project_key: DEMO\n')
    ProjectRegistry().register_project(config,root=source,key='DEMO')
    TeamControlPlaneStore(workspace,'DEMO').load_or_migrate(legacy_target={})
    captured=[]
    class BeforeGit(Exception):
        pass
    def stop(_root,run_id,job_ids,**kwargs):
        captured.append((run_id,job_ids))
        raise BeforeGit
    monkeypatch.setattr('goodteam_os.dispatch_workspace.prepare_dispatch_workspace',stop)
    request=dict(project_root=source,logical_task_id='default-new',require_isolation=True,
                 tracks=[{'role':'planner','adapter':'pi','model':'fixture',
                          'task':'Make a bounded plan','context':'PRIVATE PLAN'}])
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    workers=WorkerJobStore(source)
    job_id=captured[-1][1][0]
    first=PiMethodInput(workers,job_id).prompt()
    assert 'planning-handoff' in first and 'PRIVATE PLAN' in first
    saved=dispatch_artifact_root(source,captured[-1][0])/'request.json'
    assert json.loads(saved.read_text())['default_role_methods']=='role-methods.v1'
    # Once captured, replay must not resolve current package sources again.
    monkeypatch.setattr('goodteam_os.skills.role_packages.role_method_package',
                        lambda name:pytest.fail('latest package lookup on replay'))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    assert captured[-1][1]==[job_id] and PiMethodInput(workers,job_id).prompt()==first
    oldid='legacy-before-job'
    run='dispatch-'+content_digest(oldid.encode())[7:]
    oldroot=dispatch_artifact_root(source,run)
    oldroot.mkdir(parents=True,exist_ok=True)
    old_request=json.loads(saved.read_text())
    old_request.pop('default_role_methods')
    (oldroot/'request.json').write_text(canonical_json(old_request))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**{**request,'logical_task_id':oldid})
    assert not captured[-1][1][0].startswith('job-skill-')


def test_explicit_default_capture_requires_team_and_keeps_custom_empty(tmp_path):
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    workspace=tmp_path/'workspace'
    worker=tmp_path/'worker'
    worker.mkdir()
    task=TaskSkillIdentity('direct_task','DEMO','missing-team')
    args=dict(workspace=workspace,project_key='DEMO',worker_root=worker,task=task,command_key='capture',
              confirmed_input={'default_role_methods':'role-methods.v1','method_loading_mode':'ordinary_pi_methods.v1'},
              subjects=('role:planner',),default_role_methods=True)
    with pytest.raises(SkillError) as exc:
        capture_task_start(**args)
    assert exc.value.code=='team_unavailable'
    team=TeamControlPlaneStore(workspace,'DEMO')
    team.load_or_migrate(legacy_target={})
    current=team.read()
    team.execute([{'kind':'set_role_skills','role':'planner','mode':'custom','bindings':[],
        'expected_binding_revision':current['skill_bindings']['role:planner']['binding_revision']}],
        actor='user',surface='team_ui',expected_revision=current['revision'],command_key='empty')
    captured=capture_task_start(**args)
    from goodteam_os.skills.task_service import ProjectTaskSkillService
    service=ProjectTaskSkillService(workspace,'DEMO',worker_roots=(worker,))
    saved=service.locks.find_task(task=task,command_key='capture')
    assert captured and saved['lock']['start_input']['request']['default_items']=={}
