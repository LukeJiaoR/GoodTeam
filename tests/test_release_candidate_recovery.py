"""Public release correction recovery with synthetic process transports only."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import web
from goodteam_os.state import StateStore
from test_artifact_revision_authority import seed
from test_workers import install_fake_popen


CORRECTED = b'# Corrected release\r\n\r\nRollback: revert only the patch. Browser coverage remains unverified.\r\n'


def setup_delivery(tmp_path, monkeypatch, runner, *, produce):
    state, _, repo = seed(tmp_path, monkeypatch, runner)
    from test_graph_sample import prepare_workflow_fixture
    prepare_workflow_fixture(state, Path(state.source_ref))
    state.approvals['release'] = 'pending'
    StateStore(tmp_path).write_project_current(state)
    config = Path(state.source_ref)
    config.write_text(config.read_text().replace('mode: fake', 'mode: anthropic'))
    calls = []

    def output():
        if not produce['enabled']:
            return
        for metadata in repo.glob('.goodteam/workers/jobs/*/job.json'):
            job = json.loads(metadata.read_text())
            if job['role'] == 'implementation' and job['status'] == 'running':
                (metadata.parent / 'release-notes.md').write_bytes(CORRECTED)

    real_popen = subprocess.Popen
    install_fake_popen(monkeypatch, stdout_text='# Verification\nStatus: passed\n',
                       snapshot=output, calls=calls)
    worker_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda command, *args, **kwargs: (
        worker_popen(command, *args, **kwargs) if str(command[0]).startswith('/fake/bin/')
        else real_popen(command, *args, **kwargs)
    ))
    return state, repo, calls


def retry(workspace):
    state = StateStore(workspace).read_project_current('DEMO')
    target = web.workflow_retry_target(workspace, state)
    assert target['available'], target
    return web.retry_blocked_workflow(workspace=workspace, project_key='DEMO',
                                     expected_run_id=state.run_id,
                                     expected_retry_version=target['version'])


@pytest.mark.parametrize('runner', ['legacy', 'topo_v2'])
def test_missing_release_output_recovers_through_bound_public_retry(tmp_path, monkeypatch, runner):
    produce = {'enabled': False}
    state, repo, calls = setup_delivery(tmp_path, monkeypatch, runner, produce=produce)
    old_path = Path(state.artifacts['release_notes'])
    old_content = old_path.read_bytes()
    result = web.decide_approval(workspace=tmp_path, project_key='DEMO', approval='release',
                                decision='changes_requested', note='Correct rollback and retain the browser gap.')
    assert result['decision'] == 'changes_requested'
    interrupted = StateStore(tmp_path).read_project_current('DEMO')
    assert interrupted.next_node in {'blocked', 'failure_triage'}
    assert interrupted.pending_transition['status'] == 'completed'
    assert any('missing or stale' in message for message in interrupted.blockers)
    assert len(calls) == 2  # Missing output stops before QA.
    produce['enabled'] = True
    recovered = retry(tmp_path)
    assert recovered['next_node'] == 'await_release_approval'
    assert not any('missing or stale' in message for message in recovered['blockers'])
    assert recovered['pending_transition']['status'] == 'completed'
    assert Path(recovered['artifacts']['release_notes']).read_bytes() == CORRECTED
    assert Path(recovered['release_candidate']['path']).read_bytes() == CORRECTED
    assert old_path.read_bytes() == old_content
    jobs = [json.loads(path.read_text()) for path in repo.glob('.goodteam/workers/jobs/*/job.json')]
    assert all(job['role'] != 'release' for job in jobs)
    assert len(calls) == 5


@pytest.mark.parametrize('runner', ['legacy', 'topo_v2'])
def test_unattested_staging_retry_restart_retains_correction_without_new_workers(tmp_path, monkeypatch, runner):
    state, repo, calls = setup_delivery(tmp_path, monkeypatch, runner, produce={'enabled': True})
    config = Path(state.source_ref)
    config.write_text(config.read_text() + '''deployment_backend:
  mode: ssh_compose
  staging_ssh_target: synthetic@example.invalid
  staging_remote_dir: /isolated-test
  staging_deploy_command: echo synthetic-deploy
  staging_healthcheck_command: echo synthetic-healthcheck
''')
    ssh_calls = []
    real_run = subprocess.run

    def synthetic_run(command, **kwargs):
        if command == ['/fake/bin/claude', '--version']:
            return SimpleNamespace(returncode=0, stdout='synthetic claude version', stderr='')
        if command[0] == 'ssh':
            ssh_calls.append(command)
            raise AssertionError('Workflow staging must attest the retained candidate before SSH.')
        return real_run(command, **kwargs)

    monkeypatch.setattr('goodteam_os.staging.subprocess.run', synthetic_run)
    blocked = web.decide_approval(workspace=tmp_path, project_key='DEMO', approval='release',
                                  decision='changes_requested', note='Correct rollback and retain the browser gap.')
    assert blocked['next_node'] == 'blocked'
    assert ssh_calls == []
    candidate = dict(blocked['release_candidate'])
    assert candidate['inputs']
    # Alter the original worker document, then use freshly loaded state via the
    # public retry. Only the controller snapshot may survive into release.
    job_path = repo / '.goodteam/workers/jobs' / candidate['job_id'] / 'release-notes.md'
    job_path.write_text('# Stale mutable source\n')
    retried = retry(tmp_path)
    assert retried['next_node'] == 'blocked'
    assert retried['release_candidate'] == candidate
    assert len(calls) == 3
    assert Path(retried['release_candidate']['path']).read_bytes() == CORRECTED
    assert ssh_calls == []
