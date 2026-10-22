from pathlib import Path

import pytest

from goodteam_os.graph import generate_artifact_via_worker, run_release_until_gate
from goodteam_os.reviewer_dispatch import dispatch_prepared_reviewer_jobs
from goodteam_os.state import create_initial_state
from goodteam_os.workers import WorkerJobStore
from goodteam_os.workflow_workspace import (
    WorkflowWorkspaceError,
    collect_workflow_workspace,
    prepare_workflow_workspace,
    workspace_for_workflow,
)
from goodteam_os.dispatch_workspace import dispatch_artifact_root
from test_workflow_workspace import git


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q')
    (root / 'code.txt').write_bytes(b'committed\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial')
    return root


def bound_state(source):
    state = create_initial_state('candidate-run', 'DEMO', 'Deliver the task changes')
    state.repository_baseline = prepare_workflow_workspace(source, project_key='DEMO', run_id=state.run_id)
    root = workspace_for_workflow(source, state.repository_baseline, project_key='DEMO', run_id=state.run_id)
    return state, root


def test_release_cannot_skip_delivery_and_qa_for_bound_repository(source, tmp_path):
    state, _ = bound_state(source)
    result = run_release_until_gate(state, tmp_path)
    assert result.next_node == 'blocked'
    assert any('no completed delivery and QA' in blocker for blocker in result.blockers)
    assert 'release_notes' not in result.artifacts
    assert not result.release_candidate


def test_release_publishes_the_retained_qa_candidate_and_leaves_source_intact(source, tmp_path):
    state, root = bound_state(source)
    original_head = git(source, 'rev-parse', 'HEAD')
    original_index = (source / '.git/index').read_bytes()
    (root / 'code.txt').write_bytes(b'verified task change\n')
    reviewed = collect_workflow_workspace(source, state.repository_baseline, project_key='DEMO', run_id=state.run_id)
    state.release_candidate['repository_result'] = reviewed
    result = run_release_until_gate(state, tmp_path)
    assert result.next_node == 'await_release_approval'
    retained = result.release_candidate['repository_result']
    assert retained['result_commit'] == reviewed['result_commit']
    assert retained['integration']['status'] == 'succeeded'
    candidate = Path(retained['integration']['candidate_path'])
    assert (candidate / 'code.txt').read_bytes() == b'verified task change\n'
    assert candidate != root
    assert git(source, 'rev-parse', 'HEAD') == original_head
    assert (source / '.git/index').read_bytes() == original_index
    assert (source / 'code.txt').read_bytes() == b'committed\n'


def test_release_blocks_task_bytes_changed_after_qa(source, tmp_path):
    state, root = bound_state(source)
    (root / 'code.txt').write_bytes(b'QA verified\n')
    reviewed = collect_workflow_workspace(source, state.repository_baseline, project_key='DEMO', run_id=state.run_id)
    state.release_candidate['repository_result'] = reviewed
    (root / 'code.txt').write_bytes(b'changed after QA\n')
    result = run_release_until_gate(state, tmp_path)
    assert result.next_node == 'blocked'
    assert any('changed after QA' in blocker for blocker in result.blockers)
    assert result.release_candidate['repository_result'] == reviewed
    assert 'release_notes' not in result.artifacts
    assert (root / 'code.txt').read_bytes() == b'changed after QA\n'


def test_missing_retained_artifact_input_cannot_fall_back_to_template(source, tmp_path, monkeypatch):
    state, _ = bound_state(source)
    config = tmp_path / 'project.yaml'
    config.write_text(f'key: DEMO\nrepos:\n  app: {source}\nworker_backend:\n  mode: codex\n')
    (dispatch_artifact_root(source, state.repository_baseline['execution_id']) / 'baseline.json').unlink()
    monkeypatch.setattr(WorkerJobStore, 'run_job', lambda *_a, **_k: pytest.fail('Runtime must remain closed'))
    with pytest.raises(WorkflowWorkspaceError):
        generate_artifact_via_worker(state=state, workspace=tmp_path, project_config=config,
                                     role='architect', artifact_key='rfc', task_text='Review retained source')
    assert not (source / 'docs/rfc.md').exists()


def test_legacy_code_reviewer_runs_against_same_retained_task_directory(source):
    state, root = bound_state(source)
    (root / 'code.txt').write_bytes(b'implementation and QA output\n')
    (source / 'code.txt').write_bytes(b'later human edit\n')
    store = WorkerJobStore(source)
    jobs = dispatch_prepared_reviewer_jobs(store=store, state=state, specs=[{
        'run_id': state.run_id, 'role': 'code_review', 'adapter': 'codex', 'task': 'Review delivery',
        'context': '', 'idempotency_key': 'retained-review', 'execution_policy': 'read_only',
    }])
    job = jobs[0]
    assert job['workflow_workspace'] == state.repository_baseline
    assert job['sandbox_context']['workspace_path'] == str(root)
    assert job['sandbox_context']['job_id'] == job['id']
    assert (Path(job['sandbox_context']['workspace_path']) / 'code.txt').read_bytes() == b'implementation and QA output\n'
    assert (store.root / job['id'] / 'job.json').is_file()


@pytest.mark.parametrize('change', ['missing-qa', 'changed-after-qa', 'verified'])
def test_fix_close_requires_and_retains_the_qa_repository_candidate(source, tmp_path, change):
    from goodteam_os.graph import run_fix_close

    state, root = bound_state(source)
    state.current_node = 'qa_review_gate'
    state.approvals['qa'] = 'approved'
    (root / 'code.txt').write_bytes(b'QA verified fix\n')
    if change != 'missing-qa':
        state.release_candidate['repository_result'] = collect_workflow_workspace(
            source, state.repository_baseline, project_key='DEMO', run_id=state.run_id,
        )
    if change == 'changed-after-qa':
        (root / 'code.txt').write_bytes(b'late unverified change\n')
    result = run_fix_close(state, tmp_path)
    if change == 'verified':
        assert result.current_node == 'complete'
        candidate = result.release_candidate['repository_result']['integration']
        assert candidate['status'] == 'succeeded'
        assert (Path(candidate['candidate_path']) / 'code.txt').read_bytes() == b'QA verified fix\n'
    else:
        assert result.current_node == 'fix_implement'
        assert result.next_node == 'blocked'
        expected = 'changed after QA' if change == 'changed-after-qa' else 'no completed delivery and QA'
        assert any(expected in blocker for blocker in result.blockers)
        assert 'integration' not in result.release_candidate.get('repository_result', {})
    assert (source / 'code.txt').read_bytes() == b'committed\n'


def test_fix_prompt_uses_retained_docs_after_source_human_edits(source, tmp_path, monkeypatch):
    from goodteam_os import graph

    docs = source / 'docs'
    docs.mkdir()
    (docs / 'architecture.md').write_text('Frozen architecture: use the original backend.\n')
    state, root = bound_state(source)
    (docs / 'architecture.md').write_text('LATE HUMAN EDIT: replace the backend with unrelated source.\n')
    config = tmp_path / 'project.yaml'
    config.write_text(f'key: DEMO\nrepos:\n  app: {source}\nworker_backend:\n  mode: codex\n')
    captured = {}

    def capture_prompt(**kwargs):
        captured.update(kwargs)
        return '# Fix Plan\n\nKeep the frozen architecture.\n'

    monkeypatch.setattr(graph, 'generate_artifact_via_worker', capture_prompt)
    result = graph.run_fix_until_next_gate(state, tmp_path, config)
    assert result.current_node == 'fix_plan_review_gate'
    assert 'Frozen architecture: use the original backend.' in captured['task_text']
    assert 'LATE HUMAN EDIT' not in captured['task_text']
    packets = graph.collect_project_context(config, state)
    assert all(Path(packet['path']).is_relative_to(root) for packet in packets)
    assert (docs / 'architecture.md').read_text().startswith('LATE HUMAN EDIT')
