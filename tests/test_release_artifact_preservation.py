from pathlib import Path

import pytest

from goodteam_os.graph import accept_refinement, refine_artifact
from goodteam_os.web import decide_approval
from test_artifact_revision_authority import seed, binding_args


@pytest.mark.parametrize('runner', ['legacy', 'topo_v2'])
def test_refined_release_survives_approval(tmp_path, monkeypatch, runner):
    state, _, _ = seed(tmp_path, monkeypatch, runner)
    old_path = Path(state.artifacts['release_notes'])
    original = old_path.read_text()
    proposal = refine_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='release_notes',
                               content='# Corrected release\n\nKeep the verified rollback instruction.\n')
    proposed_path = Path(proposal['proposed_path'])
    expected = proposed_path.read_text()
    accepted = accept_refinement(workspace=tmp_path, project_key='DEMO', artifact_key='release_notes',
                                 **binding_args(proposal['proposal_binding']))
    current = accepted['path']
    approved = decide_approval(workspace=tmp_path, project_key='DEMO', approval='release', decision='approved',
        note='Model-free acceptance: continue with simulated evidence to verify the workflow contract.')
    assert approved['artifacts']['release_notes'] == current
    assert Path(current).read_text() == expected
    assert old_path.read_text() == original
    assert proposed_path.read_text() == expected
    assert approved['current_node'] == 'complete'


@pytest.mark.parametrize('runner', ['legacy', 'topo_v2'])
def test_release_change_request_has_owned_document_producer(tmp_path, monkeypatch, runner):
    import json
    state, _, repo = seed(tmp_path, monkeypatch, runner)
    from goodteam_os.state import StateStore
    state.approvals['release'] = 'pending'
    StateStore(tmp_path).write_project_current(state)
    from test_workers import install_fake_popen
    config = Path(state.source_ref)
    config.write_text(config.read_text().replace('mode: fake', 'mode: anthropic'))
    corrected = '# Corrected release notes\n\nRollback: revert only the test patch.\nBrowser coverage remains unverified.\n'
    def subprocess_output():
        for metadata in repo.glob('.goodteam/workers/jobs/*/job.json'):
            job = json.loads(metadata.read_text())
            if job['role'] == 'implementation' and job['status'] == 'running':
                (metadata.parent / 'release-notes.md').write_text(corrected)
    calls = []
    install_fake_popen(monkeypatch, stdout_text='# Verification\nStatus: passed\n',
                       snapshot=subprocess_output, calls=calls)
    original_path = Path(state.artifacts['release_notes'])
    original = original_path.read_text()
    request = 'Remove SQLite rollback; retain the known browser coverage gap and the verified patch instructions.'
    revised = decide_approval(workspace=tmp_path, project_key='DEMO', approval='release',
                               decision='changes_requested', note=request)
    jobs = [json.loads(p.read_text()) for p in repo.glob('.goodteam/workers/jobs/*/job.json')]
    assert sorted(job['role'] for job in jobs) == ['implementation', 'qa', 'ui']
    implementation = next(job for job in jobs if job['role'] == 'implementation')
    job_root = repo / '.goodteam/workers/jobs' / implementation['id']
    assert request in (job_root / 'context.md').read_text()
    assert Path(revised['artifacts']['release_notes']).read_text() == corrected
    assert str(job_root / 'release-notes.md') in calls[1]['input']
    assert revised['release_candidate']['job_id'] == implementation['id']
    assert revised['next_node'] == 'await_release_approval'
    assert revised['approvals']['release'] == 'pending'
    assert original_path.read_text() == original


def candidate_state(tmp_path, monkeypatch):
    from goodteam_os.workers import WorkerJobStore
    from goodteam_os.release_artifacts import capture_release_candidate, bind_release_candidate
    state, _, repo = seed(tmp_path, monkeypatch)
    state.change_requests['release'] = 'Preserve the verified rollback and browser gap.'
    store = WorkerJobStore(repo)
    job = store.create_job(run_id=state.run_id, role='implementation', model='test', adapter='fake',
                           task='Correct release document', context='', constraints=[],
                           expected_artifacts=['release-notes.md'])
    result = store.run_job(job['id'])
    source = store.root / job['id'] / 'release-notes.md'
    source.write_text('# Corrected release\n\nQA and staging still require review.\n')
    capture_release_candidate(state, tmp_path, Path(state.source_ref), [result])
    bind_release_candidate(state)
    return state, source


def test_candidate_survives_staging_retry_restart_without_reading_mutable_worker_file(tmp_path, monkeypatch):
    from goodteam_os.artifact_revisions import invalidate_artifact
    from goodteam_os.release_artifacts import validated_release_candidate
    from goodteam_os.state import StateStore
    state, source = candidate_state(tmp_path, monkeypatch)
    expected = source.read_text()
    source.write_text('# Changed after capture by another job\n')
    invalidate_artifact(state, 'staging_report')
    StateStore(tmp_path).write_project_current(state)
    restored = StateStore(tmp_path).read_project_current('DEMO')
    assert validated_release_candidate(restored, tmp_path) == expected
    assert restored.approvals['release'] == 'pending'


@pytest.mark.parametrize('corruption', ['run', 'request', 'upstream', 'path', 'content', 'missing'])
def test_stale_candidate_is_never_replaced_with_template(tmp_path, monkeypatch, corruption):
    from goodteam_os.graph import run_release_until_gate
    state, _ = candidate_state(tmp_path, monkeypatch)
    previous = dict(state.artifacts)
    if corruption == 'run':
        state.run_id = 'another-run'
    elif corruption == 'request':
        state.change_requests['release'] = 'Different correction'
    elif corruption == 'upstream':
        Path(state.artifacts['qa_report']).write_text('# Different QA evidence\n')
    elif corruption == 'path':
        state.release_candidate['path'] = state.artifacts['prd']
    elif corruption == 'content':
        Path(state.release_candidate['path']).write_text('# Tampered\n')
    else:
        Path(state.release_candidate['path']).unlink()
    with pytest.raises(ValueError, match='missing or stale'):
        run_release_until_gate(state, tmp_path, Path(state.source_ref))
    assert state.artifacts == previous


def test_release_rejection_does_not_dispatch_or_consume_candidate(tmp_path, monkeypatch):
    from goodteam_os.state import StateStore
    state, _ = candidate_state(tmp_path, monkeypatch)
    state.approvals['release'] = 'pending'
    StateStore(tmp_path).write_project_current(state)
    before = dict(state.release_candidate)
    result = decide_approval(workspace=tmp_path, project_key='DEMO', approval='release', decision='rejected', note='The correction is not acceptable.')
    assert result['next_node'] == 'rejected'
    assert result['release_candidate'] == before


def test_missing_owned_correction_preserves_history_and_blocks(tmp_path, monkeypatch):
    from goodteam_os.state import StateStore
    state, _, repo = seed(tmp_path, monkeypatch)
    state.approvals['release'] = 'pending'
    StateStore(tmp_path).write_project_current(state)
    original = Path(state.artifacts['release_notes'])
    content = original.read_text()
    # Builtin implementation returns only a generic result, not the requested
    # job-local release document. It must not silently satisfy this contract.
    with pytest.raises(ValueError, match='missing or stale'):
        decide_approval(workspace=tmp_path, project_key='DEMO', approval='release',
                        decision='changes_requested', note='Correct the rollback instructions.')
    current = StateStore(tmp_path).read_project_current('DEMO')
    assert current.next_node == 'blocked'
    assert current.pending_transition['status'] == 'interrupted'
    assert current.approvals['release'] != 'approved'
    assert original.read_text() == content
