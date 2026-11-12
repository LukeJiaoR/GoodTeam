"""Actual dispatch/Skill binding/Git workspace/Pi subprocess; no paid provider."""
import json
from pathlib import Path

import pytest
from test_dispatch_isolation import repository
from test_role_skill_input import invocation  # noqa: F401
from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.service import service_state_dir
from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.models import SkillError
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.workers import WorkerJobStore


def configured(tmp_path):
    source = repository(tmp_path / 'source')
    workspace = tmp_path / 'workspace'
    config = workspace / 'projects/demo.local.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('project_key: DEMO\n')
    ProjectRegistry().register_project(config, root=source, key='DEMO')
    team = TeamControlPlaneStore(workspace, 'DEMO')
    team.load_or_migrate(legacy_target={})
    package = tmp_path / 'method'
    package.mkdir()
    (package / 'SKILL.md').write_text('---\nname: task-method\ndescription: bounded instructions\n---\nORIGINAL ROLE METHOD\n')
    library = SkillLibraryService(service_state_dir())
    installed = library.install(library.preview_local(str(package), command_key='install')['proposal_id'])
    binding = {key: installed['installed'][key] for key in ('skill_id', 'version_id')}
    current = team.read()
    team.execute([{'kind': 'set_role_skills', 'role': 'implementation', 'mode': 'custom',
                   'expected_binding_revision': current['skill_bindings']['role:implementation']['binding_revision'],
                   'bindings': [{**binding, 'activation': 'required', 'enabled': True}]}],
                 actor='user', surface='team_ui', expected_revision=current['revision'], command_key='bind')
    return source, workspace


def test_new_role_method_dispatch_uses_original_isolated_workspace_and_replay(tmp_path, monkeypatch):
    source, _ = configured(tmp_path)
    fake = tmp_path / 'pi'
    script = (Path(__file__).parent / 'fixtures/fake_pi_rpc.py').read_text()
    script = script.replace('    global streaming\n', '    global streaming\n    Path("method-proof.json").write_text(json.dumps({"cwd":str(Path.cwd()), "input":json.loads(message)}))\n', 1)
    fake.write_text(script)
    fake.chmod(0o755)
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    # Only the HTTP listener is substituted; original broker/snapshots and
    # actual fake Pi subprocess/retained Git workspace are used.
    from goodteam_os.skills.pi_resources import PiResourceChannel
    monkeypatch.setattr(PiResourceChannel, 'start', lambda channel: (channel._check_context() or {}))
    request = dict(project_root=source, logical_task_id='role-method-dispatch', require_isolation=True,
                   tracks=[{'role':'implementation', 'adapter':'pi', 'task':'Return the assigned result.',
                            'context':'PRIVATE INVOCATION', 'execution_policy':'standard'}])
    from goodteam_os.repository_snapshot import RepositorySnapshotError
    try:
        report = dispatch_tracks(**request)
    except RepositorySnapshotError as exc:
        if str(exc) != 'Trusted Git executable was not available.':
            raise
        pytest.skip('NOT RUN: retained Git capture rejected this environment; original guard unchanged')
    assert report['all_ok'], report
    row = report['jobs'][0]
    target = Path(row['workspace_path'])
    assert target != source
    proof = json.loads((target/'method-proof.json').read_text())
    assert proof['cwd'] == str(target) == row['invocation_evidence']['runtime_cwd']
    assert proof['input']['context'] == 'PRIVATE INVOCATION'
    assert 'ORIGINAL ROLE METHOD' in proof['input']['required_methods'][0]['text']
    assert not (source/'method-proof.json').exists()
    assert row['changed_files'] == ['method-proof.json']
    assert dispatch_tracks(**request) == report
    workers = WorkerJobStore(source)
    method = PiMethodInput(workers, row['job_id'])
    with pytest.raises(SkillError, match='original dispatch workspace'):
        method.bind_workspace(None)
    with pytest.raises(SkillError):
        method.bind_workspace({'available': True, 'mode': 'dispatch_isolated',
             'workspace_path':str(source), 'execution_id':report['run_id'], 'sandbox_id':row['job_id']})


def test_dispatch_selects_only_new_standard_role_tasks_and_retains_old_confirmation(tmp_path, monkeypatch):
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.task_locks import TaskSkillIdentity
    from goodteam_os.skills.task_service import ProjectTaskSkillService
    import goodteam_os.dispatch_workspace as workspaces
    source, workspace = configured(tmp_path)
    calls = []
    class BeforeGit(Exception):
        pass
    def before_git(_root, run_id, job_ids, **kwargs):
        calls.append((run_id, job_ids))
        raise BeforeGit
    monkeypatch.setattr(workspaces, 'prepare_dispatch_workspace', before_git)
    tracks = [{'role':'implementation', 'adapter':'pi', 'task':'Original work', 'context':'private'}]
    request = dict(project_root=source, tracks=tracks, logical_task_id='new', require_isolation=True)
    for _ in range(2):
        with pytest.raises(BeforeGit):
            dispatch_tracks(**request)
    assert calls[0] == calls[1]
    workers = WorkerJobStore(source)
    job = workers._read_json(workers.root / calls[0][1][0] / 'job.json')
    assert job['method_loading_mode'] == 'ordinary_pi_methods.v1'
    assert job['method_workspace_mode'] == 'dispatch_isolated'
    assert job['execution_policy'] == 'standard'
    saved = ProjectTaskSkillService(workspace, 'DEMO', worker_roots=(source,)).locks.find_task(
        task=TaskSkillIdentity('direct_task','DEMO','new'), command_key='direct-start:DEMO:new')
    assert saved['lock']['start_input']['request']['confirmed_input']['method_loading_mode'] == 'ordinary_pi_methods.v1'
    # A historical confirmed task, even without a published job, cannot silently
    # change its strict meaning after the new producer code is installed.
    capture_task_start(workspace=workspace, project_key='DEMO', worker_root=source,
        task=TaskSkillIdentity('direct_task','DEMO','old'), command_key='direct-start:DEMO:old',
        subjects=('role:implementation',), confirmed_input={'tracks':tracks,'constraints':[],
            'verify_commands':{},'constraints_source':'','research_report_source':''})
    with pytest.raises(ValueError, match='protected Skill work keeps its original workflow'):
        dispatch_tracks(**{**request,'logical_task_id':'old'})
    with pytest.raises(ValueError, match='protected Skill work keeps its original workflow'):
        dispatch_tracks(**{**request,'logical_task_id':'readonly',
            'tracks':[{**tracks[0],'execution_policy':'read_only'}]})
    assert len(calls) == 2 and len(workers.list_jobs()) == 1


def test_method_workspace_mapping_receipt_and_runtime_entry_are_exact(invocation, tmp_path, monkeypatch):  # noqa: F811
    """Stored workspace mapping fixture, not proof of real retained Git capture."""
    from types import SimpleNamespace
    from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionRuntime, PiSessionMismatch
    create, _, _ = invocation
    provider, _, job = create('developer', 'pi', method_loading_mode='ordinary_pi_methods.v1',
                              method_workspace_mode='dispatch_isolated')
    workers = provider.workers
    target = repository(tmp_path / 'mapped-worktree')
    import goodteam_os.dispatch_workspace as workspaces
    selected = [target]
    def mapping(root, run_id, job_ids):
        assert root == workers.project_root and run_id == job['run_id'] and job_ids is None
        return SimpleNamespace(workspace=lambda job_id: selected[0] if job_id == job['id'] else None)
    monkeypatch.setattr(workspaces, 'prepare_dispatch_workspace', mapping)
    method = PiMethodInput(workers, job['id'])
    context = {'available':True, 'mode':'dispatch_isolated', 'workspace_path':str(target),
               'execution_id':job['run_id'], 'sandbox_id':job['id']}
    method.bind_workspace(context)
    assert method.runtime_context().runtime_cwd == target
    assert method.runtime_context().workspace_cwd_fallback is False
    for change in ({'execution_id':'foreign'}, {'sandbox_id':'other'}, {'workspace_path':str(workers.project_root)}):
        with pytest.raises(ValueError):
            method.bind_workspace({**context, **change})
    sessions = tmp_path / 'sessions-never-started'
    runtime = PiSessionRuntime(project_root=workers.project_root, job_root=workers.root/job['id'], job=job,
        runtime_cwd=workers.project_root, config=PiRuntimeConfig('unused','','','',sessions),
        stderr_path=tmp_path/'stderr', method_input=method)
    with pytest.raises(PiSessionMismatch, match='cwd differs'):
        runtime.start_or_resume()
    assert runtime.rpc is None and not sessions.exists()
    # Even a changed retained mapping may not move an already bound invocation.
    selected[0] = repository(tmp_path / 'replacement-worktree')
    with pytest.raises(SkillError, match='cannot change on replay'):
        method.bind_workspace({**context, 'workspace_path':str(selected[0])})
