"""Real Manager choice -> queue -> batch -> Role method capture, before Git execution."""
import json
import pytest
from test_dispatch_isolation import repository
from goodteam_os.assistant_queue import queue_manager_iteration_choice
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_execution import run_queued_iteration_batch
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.project_registry import ProjectRegistry
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.team_control_plane import TeamControlPlaneStore
from goodteam_os.workers import WorkerJobStore


def test_manager_batch_uses_versioned_role_packages_without_fixed_worker(tmp_path, monkeypatch):
    source = repository(tmp_path / 'source')
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {source}\nworker_backend:\n  mode: pi\n')
    ProjectRegistry().register_project(config, root=source, key='DEMO')
    from goodteam_os.projects import load_project_target
    TeamControlPlaneStore(tmp_path, 'DEMO').load_or_migrate(legacy_target=load_project_target(config))
    # Only capability discovery is a fixture; no live Pi/provider is claimed.
    monkeypatch.setattr('goodteam_os.team_control_plane.worker_runtime_eligible', lambda *a, **k: True)
    ledger = LocalTaskStore(source, 'DEMO')
    tasks = [ledger.create_task(title=f'PRIVATE_TASK_{i}', description=f'Only document private endpoint {i}.',
        role='implementation', write_domains=[f'docs/{i}'], acceptance_criteria=[f'PRIVATE_ACCEPTANCE_{i} is documented.']) for i in range(2)]
    ids = [task['id'] for task in tasks]
    snapshot = project_iteration_backlog(source, 'DEMO')
    plan_next_iteration(workspace=tmp_path, project_key='DEMO', root=source, project_config=config,
        expected_backlog_version=snapshot['version'], confirmed=True, manager_runner=lambda **_: {'selection': {
            'selected_task_ids': ids, 'compared_task_ids': ids,
            'rationale': 'The approved tasks have separate explicit paths and independent completion conditions.'}})
    queued = queue_manager_iteration_choice(workspace=tmp_path, project_key='DEMO', root=source,
        expected_backlog_version=snapshot['version'])
    from goodteam_os.skills.task_start import registered_project_scope
    assert registered_project_scope(source) == (tmp_path, 'DEMO')
    captured = []
    class BeforeGit(Exception):
        pass
    def stop(root, run_id, job_ids, **kwargs):
        assert registered_project_scope(source) == (tmp_path, 'DEMO'), 'scope lost in original batch path'
        captured.extend(job_ids)
        raise BeforeGit('Inspection checkpoint before original trusted Git workspace preparation')
    monkeypatch.setattr('goodteam_os.dispatch_workspace.prepare_dispatch_workspace', stop)
    with pytest.raises(BeforeGit):
        run_queued_iteration_batch(workspace=tmp_path, project_key='DEMO', root=source, project_config=config,
            proposal_ids=queued['queued_proposal_ids'], expected_revisions={key: 1 for key in queued['queued_proposal_ids']}, confirmed=True)
    assert len(captured) == 2 and len(set(captured)) == 2
    workers = WorkerJobStore(source)
    payloads = []
    for i, job_id in enumerate(captured):
        job = workers._read_json(workers.root / job_id / 'job.json')
        assert job['role'] == 'implementation' and job['adapter'] == 'pi'
        assert job['project_task_id'] == ids[i] and job['track_id'] == ids[i]
        assert job['execution_policy'] == 'standard'
        assert job['method_loading_mode'] == 'ordinary_pi_methods.v1'
        assert job['method_workspace_mode'] == 'dispatch_isolated'
        method = PiMethodInput(workers, job_id)
        payload = json.loads(method.prompt())
        payloads.append(payload)
        assert f'PRIVATE_ACCEPTANCE_{i}' in method.prompt()
        assert f'PRIVATE_TASK_{1-i}' not in method.prompt()
        assert f'PRIVATE_ACCEPTANCE_{1-i}' not in method.prompt()
        assert 'bounded-work' in method.prompt()
        assert not ledger.get_task(ids[i]).get('delivery_verified')
    assert payloads[0]['required_methods'] == payloads[1]['required_methods']
