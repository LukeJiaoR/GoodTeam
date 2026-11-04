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
    from goodteam_os.web import append_action

    workspace, repo = tmp_path / 'workspace', tmp_path / 'repo'
    repo.mkdir()
    state = create_initial_state('run-1', 'DEMO', 'Build an offline export tool')
    state.delivery_mode = 'custom'
    standard = 'PERSISTED_ACCEPTANCE: preserve original row ordering.'
    feedback = 'LATEST_RECORDED_CHANGE: write TSV rather than CSV.'
    state.delivery_standards = [standard]
    state.change_requests = {'prd': feedback}
    state.approvals.update(prd='approved', security='approved')
    decision = dict(run_id=state.run_id, project_key=state.project_key, actor='human',
                    action='approval_decided', gate='prd', decision='approved', artifact='prd',
                    artifact_version='sha256:prd-v1', note='PRD_SCOPE: use CNY, local Docker only.',
                    workflow_operation_id='prd-operation')
    append_action(workspace, **decision)
    append_action(workspace, **{**decision, 'run_id': 'another-run', 'note': 'UNRELATED_DECISION'})
    state.pending_transition = {
        'id': 'security-operation', 'status': 'interrupted',
        'action': {**decision, 'gate': 'security', 'artifact': 'rfc',
                   'artifact_version': 'sha256:rfc-v1', 'workflow_operation_id': 'security-operation',
                   'note': 'SECURITY_SCOPE: loopback ports only; public tunnels need separate approval.'},
    }
    path = StateStore(workspace).write_project_current(state)
    before = path.read_bytes()
    audit = workspace / 'state/projects/DEMO/action-log.jsonl'
    audit_before = audit.read_bytes()
    code = """
import json, sys
from pathlib import Path
from goodteam_os import graph, reviewer_dispatch, web
from goodteam_os.graph import implementation_worker_job_specs
from goodteam_os.projects import write_project_config
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore
from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission
workspace, repo = Path(sys.argv[1]), Path(sys.argv[2])
state = StateStore(workspace).read_project_current('DEMO')
workers = WorkerJobStore(repo)
result = {}
for spec in implementation_worker_job_specs(state, {'worker_backend': {'mode': 'human'}}, workspace=workspace):
    job = workers.create_job(**spec)
    root = workers.root / job['id']
    result[spec['role']] = {'context': (root / 'context.md').read_text(),
                            'prompt': workers._worker_prompt(root, job), 'status': job['status']}
config = write_project_config(workspace / 'projects', 'DEMO', 'Demo', repo_path=str(repo), worker_mode='human')
graph.dispatch_reviewer_jobs_for_gate = lambda **kwargs: kwargs['context']
result['reviewer_context'] = graph.dispatch_gate_reviewer_jobs(
    state=state, workspace=workspace, project_config=config, approval='staging',
    artifact_key='staging_report', artifact_path=workspace / 'staging.md')
# Stop native review at input selection: no valid admission, capture or runtime
# is manufactured by this probe, and the persisted Workflow stays unchanged.
state.next_node = 'await_staging_approval'
web.interrupt_transition = lambda *args: state
web._project_runtime_roots_for_state = lambda **kwargs: [repo]
def capture_native(**kwargs):
    result['native_reviewer_context'] = kwargs['context']
    return []
reviewer_dispatch.reviewer_job_specs_for_gate = capture_native
status, _ = web._run_queued_reviewer_jobs_for_current_run(workspace, {}, 'DEMO',
    {'codex_review_admission': object.__new__(TrustedCodexReviewAdmission)})
assert status == 409
print(json.dumps(result))
"""
    process = subprocess.run([sys.executable, '-c', code, str(workspace), str(repo)],
                             capture_output=True, text=True, check=True, timeout=20)
    observed = json.loads(process.stdout)
    reviewer_context = observed.pop('reviewer_context')
    assert decision['note'] in reviewer_context and 'sha256:prd-v1' in reviewer_context
    native_context = observed.pop('native_reviewer_context')
    assert decision['note'] in native_context and 'sha256:prd-v1' in native_context
    assert set(observed) == {'ui', 'implementation', 'qa'}
    for record in observed.values():
        assert record['status'] == 'queued'
        for value in (record['context'], record['prompt']):
            assert standard in value and feedback in value
            assert 'source: workflow_state' in value
            assert 'not proof of a task-start capture' in value
            assert value.index(standard) < value.index('Requested changes:') < value.index(feedback)
            assert decision['note'] in value
            assert 'SECURITY_SCOPE: loopback ports only; public tunnels need separate approval.' in value
            assert 'sha256:prd-v1' in value and 'sha256:rfc-v1' in value
            assert 'action-log.jsonl' in value and 'pending_transition.action' in value
            assert 'UNRELATED_DECISION' not in value
            assert 'An approved label alone does not authorize broader actions.' in value
    assert path.read_bytes() == before
    assert audit.read_bytes() == audit_before


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
    state.pending_transition = {'action': {
        'run_id': state.run_id, 'project_key': state.project_key, 'action': 'approval_decided',
        'gate': 'security', 'decision': 'approved', 'note': 'ORIGINAL_APPROVAL_SCOPE',
    }}
    spec = implementation_worker_job_specs(state, {'worker_backend': {'mode': 'human'}})[1]
    workers = WorkerJobStore(tmp_path)
    job = workers.create_job(**spec)
    root = workers.root / job['id']
    before = (root / 'context.md').read_bytes()
    state.delivery_standards = ['LATER_STATE_STANDARD']
    state.pending_transition['action']['note'] = 'LATER_APPROVAL_SCOPE'
    StateStore(tmp_path).write_project_current(state)
    prompt = WorkerJobStore(tmp_path)._worker_prompt(root, job)
    assert 'ORIGINAL_QUEUED_STANDARD' in prompt and 'LATER_STATE_STANDARD' not in prompt
    assert 'ORIGINAL_APPROVAL_SCOPE' in prompt and 'LATER_APPROVAL_SCOPE' not in prompt
    assert (root / 'context.md').read_bytes() == before
