"""Read-only Role dispatch producer, exact methods and unchanged old contracts."""
import json

import pytest

from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.models import SkillError, canonical_json, content_digest
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.worker_requests import _request
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.workers import WorkerJobStore
from test_dispatch_isolation import repository


def test_readonly_research_and_planner_capture_original_methods_without_write_or_search(tmp_path, monkeypatch):
    root = repository(tmp_path / 'source')
    workspace = tmp_path / 'workspace'
    config = workspace / 'projects/demo.local.yaml'
    config.parent.mkdir(parents=True)
    config.write_text('project_key: DEMO\n')
    ProjectRegistry().register_project(config, root=root, key='DEMO')
    TeamControlPlaneStore(workspace, 'DEMO').load_or_migrate(legacy_target={})
    captured = []
    class BeforeGit(Exception):
        pass
    def stop(root, run_id, job_ids, **kwargs):
        captured.append((run_id, job_ids))
        raise BeforeGit
    monkeypatch.setattr('goodteam_os.dispatch_workspace.prepare_dispatch_workspace', stop)
    tracks = [dict(track_id=role, project_task_id='task-' + role, role=role, adapter='pi', model='fixture',
                   execution_policy='read_only', task='Return bounded ' + role, context='PRIVATE ' + role)
              for role in ('research', 'planner')]
    request = dict(project_root=root, logical_task_id='readonly-roles', require_isolation=True, tracks=tracks)
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    store = WorkerJobStore(root)
    jobs = [json.loads((store.root / jid / 'job.json').read_text()) for jid in captured[-1][1]]
    for job, other in zip(jobs, reversed(jobs)):
        assert job['method_loading_mode'] == 'readonly_pi_methods.v1'
        assert job['execution_policy'] == 'read_only'
        method = PiMethodInput(store, job['id'])
        assert method.readonly_tools and not method.manager_tools
        prompt = method.prompt()
        assert 'PRIVATE ' + job['role'] in prompt and 'PRIVATE ' + other['role'] not in prompt
        assert ('evidence-research' if job['role'] == 'research' else 'planning-handoff') in prompt
        with pytest.raises(SkillError, match='policy witness'):
            method.resource_channel(active=lambda: True)
        for change in ({'execution_policy': 'standard'}, {'role': 'implementation'}, {'role': 'orchestrator'},
                       {'review_target': {}}, {'session_policy': 'reuse'},
                       {'worker_session_ref': 'other'}, {'method_workspace_mode': ''}):
            with pytest.raises(SkillError):
                _request({**method.request, **change})
    with pytest.raises((SkillError, ValueError)):
        _request({**method.request, 'web_search': {}})  # Malformed authorization remains rejected.
    saved = json.loads((dispatch_artifact_root(root, jobs[0]['run_id']) / 'request.json').read_text())
    assert saved['track_methods']['schema_version'] == 'dispatch_track_methods.v2'
    monkeypatch.setattr('goodteam_os.skills.role_packages.role_method_package',
                        lambda name: pytest.fail('latest package on replay'))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**request)
    assert captured[-1][1] == [j['id'] for j in jobs]
    # Old confirmed read-only requests retain their original unsupported path.
    old_id = 'old-readonly'
    old_root = dispatch_artifact_root(root, 'dispatch-' + content_digest(old_id.encode())[7:])
    old_root.mkdir(parents=True, exist_ok=True)
    (old_root / 'request.json').write_text(canonical_json({k: v for k, v in saved.items() if k != 'track_methods'}))
    with pytest.raises(BeforeGit):
        dispatch_tracks(**{**request, 'logical_task_id': old_id})
    for jid in captured[-1][1]:
        job = json.loads((store.root / jid / 'job.json').read_text())
        assert not job.get('method_loading_mode') and job['execution_policy'] == 'read_only'
        assert store.run_job(jid)['status'] == 'failed'
