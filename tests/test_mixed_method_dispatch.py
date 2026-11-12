"""Mixed Runtime capture uses original jobs and per-track immutable methods."""
import json

import pytest

from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.models import SkillConflict, canonical_json, content_digest
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import repository


@pytest.mark.parametrize("sibling_adapter,sibling_role", [("fake", "implementation"), ("pi", "architect")])
def test_mixed_batch_preserves_each_runtime_role_and_original_job_identity(tmp_path, monkeypatch, sibling_adapter, sibling_role):
    source = repository(tmp_path / 'source')
    workspace = tmp_path / 'workspace'
    config = workspace / 'projects/demo.local.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('project_key: DEMO\n')
    ProjectRegistry().register_project(config, root=source, key='DEMO')
    team = TeamControlPlaneStore(workspace, 'DEMO')
    team.load_or_migrate(legacy_target={})
    captured = []
    class BeforeGit(Exception):
        pass
    def stop(root, run_id, job_ids, **kwargs):
        captured.append((run_id, job_ids))
        raise BeforeGit
    monkeypatch.setattr('goodteam_os.dispatch_workspace.prepare_dispatch_workspace', stop)
    tracks = [dict(track_id='A', project_task_id='A', role='implementation', adapter='pi', model='fixture',
                   task='Implement bounded A', context='PRIVATE A', execution_policy='standard'),
              dict(track_id='B', project_task_id='B', role=sibling_role, adapter=sibling_adapter,
                   task='Implement bounded B', context='PRIVATE B', execution_policy='standard')]
    request = dict(project_root=source, logical_task_id='mixed-role', require_isolation=True, tracks=tracks)
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    store = WorkerJobStore(source)
    jobs = [json.loads((store.root / jid / 'job.json').read_text()) for jid in captured[-1][1]]
    a, b = jobs
    assert a['method_loading_mode'] == 'ordinary_pi_methods.v1'
    assert not b.get('task_skill_context') and not b.get('method_loading_mode')
    assert [(j['track_id'], j['project_task_id']) for j in jobs] == [('A', 'A'), ('B', 'B')]
    prompt = PiMethodInput(store, a['id']).prompt()
    assert 'bounded-work' in prompt and 'PRIVATE A' in prompt and 'PRIVATE B' not in prompt
    assert a['run_id'] == b['run_id'] and a['execution_policy'] == b['execution_policy'] == 'standard'
    saved = json.loads((dispatch_artifact_root(source, a['run_id']) / 'request.json').read_text())
    assert [r['mode'] for r in saved['track_methods']['tracks']] == ['ordinary', 'legacy']
    monkeypatch.setattr('goodteam_os.skills.role_packages.role_method_package',
                        lambda name: pytest.fail('latest methods on replay'))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    assert captured[-1][1] == [a['id'], b['id']]
    assert PiMethodInput(store, a['id']).prompt() == prompt
    with pytest.raises(SkillConflict):
        dispatch_tracks(**{**request, 'tracks': list(reversed(tracks))})
    # Pre-marker mixed requests stay legacy, even before the first job exists.
    old_id = 'old-mixed'
    old_run = 'dispatch-' + content_digest(old_id.encode())[7:]
    old_root = dispatch_artifact_root(source, old_run)
    old_root.mkdir(parents=True, exist_ok=True)
    old_request = {k: v for k, v in saved.items() if k != 'track_methods'}
    (old_root / 'request.json').write_text(canonical_json(old_request))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**{**request, 'logical_task_id': old_id})
    old_jobs = [json.loads((store.root / jid / 'job.json').read_text()) for jid in captured[-1][1]]
    assert all(not job.get('task_skill_context') for job in old_jobs)
    if sibling_adapter == 'pi':
        return  # The strict non-Pi compatibility case belongs to the other variant.
    # Explicit custom-empty remains a captured strict task for the non-Pi
    # sibling. Mixing must not erase it merely to permit isolated execution.
    current = team.read()
    team.execute([{'kind': 'set_role_skills', 'role': 'implementation', 'mode': 'custom',
        'bindings': [], 'expected_binding_revision': current['skill_bindings']['role:implementation']['binding_revision']}],
        actor='user', surface='team_ui', expected_revision=current['revision'], command_key='strict-empty')
    with pytest.raises(ValueError, match='protected Skill work'):
        dispatch_tracks(**{**request, 'logical_task_id': 'mixed-strict'})
    assert all(store.find_job_by_idempotency_key(f'direct-job:DEMO:mixed-strict:{i}') is None for i in range(2))
