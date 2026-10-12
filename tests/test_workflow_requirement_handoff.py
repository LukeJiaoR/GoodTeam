"""Controlled Worker-input probe; never invokes a provider or native runtime."""
import json
import subprocess
import sys

import pytest

from goodteam_os.graph import generate_artifact_via_worker, implementation_worker_job_specs, prd_worker_task, worker_context
from goodteam_os.projects import write_project_config
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore


def test_first_prd_worker_receives_persisted_delivery_standard(tmp_path, monkeypatch):
    workspace, repo = tmp_path / 'workspace', tmp_path / 'repo'
    repo.mkdir()
    config = write_project_config(workspace / 'projects', 'DEMO', 'Demo', repo_path=str(repo), worker_mode='human')
    state = create_initial_state('run-1', 'DEMO', 'Build an offline export tool')
    state.delivery_mode = 'custom'
    state.delivery_standards = ['ACCEPTANCE_STANDARD: exports must preserve all original row ordering.']
    state.change_requests = {'prd': 'CURRENT_FEEDBACK: preserve the public file format.'}
    states = StateStore(workspace)
    states.write_project_current(state)
    restored = StateStore(workspace).read_project_current('DEMO')
    observed = []

    def capture_input(self, job_id):
        root = self.root / job_id
        job = json.loads((root / 'job.json').read_text())
        observed.append(self._worker_prompt(root, job))
        # Inspect the materialized input only, without dispatching any backend.
        return {'job_id': job_id, 'status': 'failed', 'summary': 'Controlled input-only probe'}

    monkeypatch.setattr(WorkerJobStore, 'run_job', capture_input)
    generate_artifact_via_worker(state=restored, workspace=workspace, project_config=config,
                                role='product_requirement', artifact_key='prd', task_text=prd_worker_task(restored))
    assert len(observed) == 1
    assert restored.idea in observed[0]
    assert 'CURRENT_FEEDBACK' in observed[0]
    assert states.read_project_current('DEMO').delivery_standards == state.delivery_standards
    assert not restored.artifacts, 'The first PRD has no prior artifact from which to recover its standard.'
    assert 'ACCEPTANCE_STANDARD' in observed[0], 'Saved standard is omitted from actual materialized first-PRD Worker prompt.'


def test_fresh_process_materializes_saved_standard_and_feedback_for_all_delivery_roles(tmp_path):
    workspace, repo = tmp_path / 'workspace', tmp_path / 'repo'
    repo.mkdir()
    state = create_initial_state('run-1', 'DEMO', 'Build an offline export tool')
    state.delivery_mode = 'custom'
    standard = 'PERSISTED_ACCEPTANCE: preserve original row ordering.'
    feedback = 'LATEST_RECORDED_CHANGE: write TSV rather than CSV.'
    state.delivery_standards = [standard]
    state.change_requests = {'prd': feedback}
    path = StateStore(workspace).write_project_current(state)
    before = path.read_bytes()
    code = """
import json, sys
from pathlib import Path
from goodteam_os.graph import implementation_worker_job_specs
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore
workspace, repo = Path(sys.argv[1]), Path(sys.argv[2])
state = StateStore(workspace).read_project_current('DEMO')
workers = WorkerJobStore(repo)
result = {}
for spec in implementation_worker_job_specs(state, {'worker_backend': {'mode': 'human'}}, workspace=workspace):
    job = workers.create_job(**spec)
    root = workers.root / job['id']
    result[spec['role']] = {'context': (root / 'context.md').read_text(),
                            'prompt': workers._worker_prompt(root, job), 'status': job['status']}
print(json.dumps(result))
"""
    process = subprocess.run([sys.executable, '-c', code, str(workspace), str(repo)],
                             capture_output=True, text=True, check=True, timeout=20)
    observed = json.loads(process.stdout)
    assert set(observed) == {'ui', 'implementation', 'qa'}
    for record in observed.values():
        assert record['status'] == 'queued'
        for value in (record['context'], record['prompt']):
            assert standard in value and feedback in value
            assert 'source: workflow_state' in value
            assert 'not proof of a task-start capture' in value
            assert value.index(standard) < value.index('Requested changes:') < value.index(feedback)
    assert path.read_bytes() == before


def test_custom_standards_do_not_come_from_live_project_settings(tmp_path):
    state = create_initial_state('run-1', 'DEMO', 'Build an export')
    state.delivery_mode = 'custom'
    state.delivery_standards = ['SAVED_STATE_STANDARD']
    target = {'worker_backend': {'mode': 'human'}, 'delivery_standards': ['NEW_LIVE_CONFIG_STANDARD']}
    specs = implementation_worker_job_specs(state, target, workspace=tmp_path)
    for spec in specs:
        assert 'SAVED_STATE_STANDARD' in spec['context']
        assert 'NEW_LIVE_CONFIG_STANDARD' not in spec['context']
    assert state.delivery_standards == ['SAVED_STATE_STANDARD']


def test_missing_custom_standards_remain_explicitly_unknown():
    state = create_initial_state('run-1', 'DEMO', 'Build an export')
    state.delivery_mode = 'custom'
    context = worker_context(state)
    assert 'No custom standards recorded in this workflow state; do not invent them.' in context
    assert state.delivery_standards == []


@pytest.mark.parametrize('mode', ['init_project', 'mvp', 'multi_service'])
def test_noncustom_mode_does_not_revive_leftover_custom_standards(mode):
    state = create_initial_state('run-1', 'DEMO', 'Build an export')
    state.delivery_mode = mode
    state.delivery_standards = ['STALE_CUSTOM_STANDARD']
    context = worker_context(state)
    assert 'STALE_CUSTOM_STANDARD' not in context
    assert 'Recorded workflow delivery settings' not in context


def test_materialized_job_input_is_not_rewritten_when_workflow_state_changes(tmp_path):
    state = create_initial_state('run-1', 'DEMO', 'Build an export')
    state.delivery_mode = 'custom'
    state.delivery_standards = ['ORIGINAL_QUEUED_STANDARD']
    spec = implementation_worker_job_specs(state, {'worker_backend': {'mode': 'human'}})[1]
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(**spec)
    root = workers.root / job['id']
    before = (root / 'context.md').read_bytes()
    state.delivery_standards = ['LATER_STATE_STANDARD']
    StateStore(tmp_path).write_project_current(state)
    prompt = WorkerJobStore(tmp_path)._worker_prompt(root, job)
    assert 'ORIGINAL_QUEUED_STANDARD' in prompt and 'LATER_STATE_STANDARD' not in prompt
    assert (root / 'context.md').read_bytes() == before
