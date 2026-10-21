"""Revision regressions using persisted state and the built-in model-free backend."""
import json
from pathlib import Path

import pytest

from goodteam_os.artifact_revisions import capture_proposal, current_job_artifact, invalidate_artifact
from goodteam_os.artifacts import ArtifactStore
from goodteam_os.graph import accept_refinement, discard_refinement, refine_artifact
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import decide_approval


def binding_args(binding):
    return dict(expected_run_id=binding['run_id'], expected_artifact_key=binding['artifact_key'],
                expected_artifact_version=binding['base_version'], expected_proposal_id=binding['proposal_id'],
                expected_proposal_version=binding['proposal_version'])


def seed(tmp_path, monkeypatch, runner='legacy'):
    monkeypatch.setenv('GOODTEAM_STATE_DIR', str(tmp_path / 'operator'))
    monkeypatch.setenv('GOODTEAM_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('GOODTEAM_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    monkeypatch.setenv('GOODTEAM_DISABLE_TG_NETWORK', '1')
    repo = tmp_path / 'repo'
    repo.mkdir()
    config = tmp_path / 'demo.yaml'
    runner_line = 'workflow_runner: topo_v2\n' if runner == 'topo_v2' else ''
    config.write_text(f'name: Demo\nkey: DEMO\n{runner_line}repos:\n  app: {repo}\nworker_backend:\n  mode: fake\n')
    state = create_initial_state('run-1', 'DEMO', 'Thing')
    state.source_ref = str(config)
    state.source_type = 'existing_project'
    state.approvals.update(prd='approved', rfc='approved', security='approved', staging='approved', release='approved')
    state.current_node = 'release_review_gate'
    state.next_node = 'await_release_approval'
    store = ArtifactStore(tmp_path)
    for key in ['takeover', 'prd', 'rfc', 'task_breakdown', 'ui_spec', 'implementation_handoff', 'qa_report', 'staging_report', 'release_notes']:
        state.artifacts[key] = str(store.write_markdown(state.run_id, key, key + '.md', '# Old ' + key + '\n'))
    proposed = store.write_proposed_markdown(state.run_id, 'prd', 'prd.md', '# New PRD\n')
    state.artifacts['prd_proposed'] = str(proposed)
    binding = capture_proposal(state, 'prd', proposed)
    StateStore(tmp_path).write_project_current(state)
    return state, binding, repo


@pytest.mark.parametrize('runner', ['legacy', 'topo_v2'])
def test_upstream_acceptance_requires_fresh_rfc_and_security_before_execution(tmp_path, monkeypatch, runner):
    state, binding, repo = seed(tmp_path, monkeypatch, runner)
    previous = dict(state.artifacts)
    accepted = accept_refinement(workspace=tmp_path, project_key='DEMO', artifact_key='prd', **binding_args(binding))
    assert all(accepted['approvals'][gate] == 'pending' for gate in ('prd', 'rfc', 'security', 'staging', 'release'))
    assert accepted['artifacts']['takeover'] == previous['takeover']
    assert 'rfc' not in accepted['artifacts']
    assert Path(previous['prd']).read_text() == '# Old prd\n'
    assert Path(previous['prd_proposed']).read_text() == '# New PRD\n'
    approved = decide_approval(workspace=tmp_path, project_key='DEMO', approval='prd', decision='approved',
                               note='Model-free acceptance: continue with simulated evidence to verify the workflow contract.')
    assert approved['next_node'] == 'await_rfc_approval'
    assert approved['approvals']['security'] == 'pending'
    assert approved['artifacts']['rfc'] != previous['rfc']
    roles = [json.loads(p.read_text())['role'] for p in repo.glob('.goodteam/workers/jobs/*/job.json')]
    assert not {'implementation', 'ui_design', 'qa'}.intersection(roles)


@pytest.mark.parametrize('action', [accept_refinement, discard_refinement])
@pytest.mark.parametrize('field', ['run_id', 'artifact_key', 'base_version', 'proposal_id', 'proposal_version'])
def test_proposal_identity_mismatch_has_no_mutation(tmp_path, monkeypatch, action, field):
    state, binding, _ = seed(tmp_path, monkeypatch)
    original = StateStore(tmp_path).read_project_current('DEMO')
    binding[field] = 'stale'
    with pytest.raises(ValueError, match='proposal changed'):
        action(workspace=tmp_path, project_key='DEMO', artifact_key='prd', **binding_args(binding))
    assert StateStore(tmp_path).read_project_current('DEMO') == original
    assert Path(state.artifacts['prd_proposed']).exists()


def test_new_proposal_same_bytes_invalidates_old_identity(tmp_path, monkeypatch):
    state, old, _ = seed(tmp_path, monkeypatch)
    capture_proposal(state, 'prd', state.artifacts['prd_proposed'])
    StateStore(tmp_path).write_project_current(state)
    with pytest.raises(ValueError, match='proposal changed'):
        accept_refinement(workspace=tmp_path, project_key='DEMO', artifact_key='prd', **binding_args(old))


def test_refine_builtin_result_wins_over_old_mirror(tmp_path, monkeypatch):
    state, _, repo = seed(tmp_path, monkeypatch)
    (repo / 'docs').mkdir()
    (repo / 'docs' / 'prd.md').write_text('# Stale mirror\n')
    result = refine_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='prd', content='# Human base\n')
    assert Path(result['proposed_path']).read_text() != '# Human base\n'
    assert Path(result['proposed_path']).read_text() != '# Stale mirror\n'
    assert result['proposal_binding']['proposal_id']
    assert StateStore(tmp_path).read_project_current('DEMO').approvals['rfc'] == 'pending'


def test_qa_edit_preserves_sibling_code_review_evidence(tmp_path, monkeypatch):
    state, _, _ = seed(tmp_path, monkeypatch)
    state.artifacts['code_review'] = 'unchanged-code-review'
    invalidate_artifact(state, 'qa_report')
    assert state.artifacts['code_review'] == 'unchanged-code-review'
    assert state.approvals['rfc'] == 'approved'
    assert state.approvals['security'] == 'approved'
    assert state.approvals['staging'] == 'pending'
    assert 'release_notes' not in state.artifacts


def test_current_job_requires_owned_deliverable(tmp_path):
    root = tmp_path / 'job'
    root.mkdir()
    result = root / 'result.md'
    result.write_text('# Fresh result\n')
    outside = tmp_path / 'prd.md'
    outside.write_text('# Stale shared file\n')
    def read(path):
        return current_job_artifact(root, {'result_path': str(path)}, artifact_key='prd', filename='prd.md')
    assert read(result) == '# Fresh result\n'
    assert read(outside) is None
    result.write_text('# Worker Result\n\nStatus: completed\n\nSee stdout.txt and stderr.txt.\n')
    assert read(result) is None
    result.write_text('Changed files:\nprd.md\n')
    assert read(result) is None
    (root / 'prd.md').write_text('# Job deliverable\n')
    assert read(result) == '# Job deliverable\n'
    (root / 'prd.md').unlink()
    (root / 'prd.md').symlink_to(outside)
    assert read(result) is None


def test_product_http_binding_missing_stale_and_matching(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_skill_review_http import post, server_url

    _, binding, _ = seed(tmp_path, monkeypatch)
    ctx = SimpleNamespace(workspace=tmp_path, root=tmp_path)
    with server_url(ctx, monkeypatch, 'product') as base:
        for path in ['/api/refine-artifact', '/api/artifact', '/api/answer-open-questions']:
            code, result = post(base, path, {'project_key': 'DEMO', 'artifact': 'prd', 'content': '# stale'})
            assert code == 400, result
            assert result['code'] == 'artifact_binding_required'
        code, result = post(base, '/api/accept-refinement', {'project_key': 'DEMO', 'artifact': 'prd'})
        assert code == 400, result
        assert 'binding is required' in result['message']
        stale = {**binding_args(binding), 'expected_run_id': 'old-run'}
        code, result = post(base, '/api/accept-refinement', {'project_key': 'DEMO', 'artifact': 'prd', **stale})
        assert code == 400, result
        assert 'proposal changed' in result['message']
        code, result = post(base, '/api/accept-refinement', {'project_key': 'DEMO', 'artifact': 'prd', **binding_args(binding)})
        assert code == 200, result
        assert result['accepted']
        assert result['approvals']['rfc'] == 'pending'


def test_intermediate_edit_blocks_old_downstream_gate(tmp_path, monkeypatch):
    from goodteam_os.web import save_artifact

    state, _, _ = seed(tmp_path, monkeypatch)
    saved = save_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='qa_report', content='# Revised QA\n')
    current = StateStore(tmp_path).read_project_current('DEMO')
    assert current.next_node == 'blocked'
    assert 'automatic resume is unavailable' in current.blockers[-1]
    assert Path(current.artifacts['qa_report']).read_text() == '# Revised QA\n'
    assert Path(state.artifacts['qa_report']).read_text() == '# Old qa_report\n'
    assert saved['path'] == current.artifacts['qa_report']
    with pytest.raises(ValueError, match='not currently actionable'):
        decide_approval(workspace=tmp_path, project_key='DEMO', approval='release', decision='approved')


@pytest.mark.parametrize('adapter', ['codex', 'anthropic', 'gemini', 'pi'])
def test_real_adapter_prompt_requests_owned_markdown_not_generic_summary(tmp_path, adapter):
    from goodteam_os.workers import WorkerJobStore

    store = WorkerJobStore(tmp_path)
    job = store.create_job(run_id='run', role='product_requirement', adapter=adapter, model='test',
                           task='Write a PRD', context='', constraints=['Output markdown only.'],
                           expected_artifacts=['prd.md'])
    prompt = store._worker_prompt(store.root / job['id'], job)
    assert 'Return the complete requested markdown artifact as your final assistant response' in prompt
    assert str(store.root / job['id']) in prompt
    assert 'At the end, summarize changed files' not in prompt


@pytest.mark.parametrize('adapter', ['anthropic', 'gemini'])
def test_stdout_adapter_output_adopted_without_shared_file_fallback(tmp_path, monkeypatch, adapter):
    from goodteam_os.graph import generate_artifact_via_worker
    from test_workers import install_fake_popen

    state, _, repo = seed(tmp_path, monkeypatch)
    # Only transport is synthetic; real job creation, adapter result capture,
    # provenance selection and workflow artifact generation all execute.
    config = Path(state.source_ref)
    config.write_text(config.read_text().replace('mode: fake', f'mode: {adapter}'))
    monkeypatch.setattr('goodteam_os.workers.which', lambda name: f'/fake/bin/{name}')
    calls = []
    install_fake_popen(monkeypatch, stdout_text='# Current PRD\n\nAcceptance criteria\n', stderr_text='', calls=calls)
    (repo / 'docs').mkdir()
    (repo / 'docs' / 'prd.md').write_text('# Stale PRD\n')
    content = generate_artifact_via_worker(state=state, workspace=tmp_path, project_config=config,
                                          role='product_requirement', artifact_key='prd', task_text='Write PRD')
    assert content == '# Current PRD\n\nAcceptance criteria\n'
    sent_prompt = calls[0]['input'] or ' '.join(calls[0]['command'])
    assert 'Return the complete requested markdown artifact' in sent_prompt


def test_fix_profile_handoff_revision_rewinds_code_review_not_plan(tmp_path, monkeypatch):
    state, _, _ = seed(tmp_path, monkeypatch)
    state.load_profile = 'fix'
    state.approvals = dict(fix_plan='approved', code_review='approved', qa='approved')
    state.artifacts['fix_plan'] = str(ArtifactStore(tmp_path).write_markdown(state.run_id, 'fix_plan', 'fix-plan.md', '# Fix plan'))
    state.artifacts['implementation_handoff_proposed'] = str(ArtifactStore(tmp_path).write_proposed_markdown(
        state.run_id, 'implementation_handoff', 'implementation-handoff.md', '# Revised handoff'))
    binding = capture_proposal(state, 'implementation_handoff', state.artifacts['implementation_handoff_proposed'])
    StateStore(tmp_path).write_project_current(state)
    result = accept_refinement(workspace=tmp_path, project_key='DEMO', artifact_key='implementation_handoff', **binding_args(binding))
    assert result['approvals'] == dict(fix_plan='approved', code_review='pending', qa='pending')
    assert result['next_node'] == 'await_code_review_approval'
    assert 'qa_report' not in result['artifacts']
    assert result['artifacts']['fix_plan'] == state.artifacts['fix_plan']


def test_builtin_rfc_revision_preserves_history_and_invalidates_delivery(tmp_path, monkeypatch):
    from goodteam_os.web import regenerate_rfc_review_gate

    state, _, _ = seed(tmp_path, monkeypatch)
    original = state.artifacts['rfc']
    revised = regenerate_rfc_review_gate(state=state, workspace=tmp_path, project_config=Path(state.source_ref))
    assert revised.artifacts['rfc'] != original
    assert Path(original).read_text() == '# Old rfc\n'
    assert revised.approvals['prd'] == 'approved'
    assert all(revised.approvals[gate] == 'pending' for gate in ('rfc', 'security', 'staging', 'release'))
    assert 'implementation_handoff' not in revised.artifacts
    assert revised.next_node == 'await_rfc_approval'


def test_takeover_regeneration_retains_source_packet_history_without_mirroring(tmp_path, monkeypatch):
    from goodteam_os.graph import run_adoption_until_next_gate

    state, _, repo = seed(tmp_path, monkeypatch)
    config = Path(state.source_ref)
    state = run_adoption_until_next_gate(state, tmp_path, config)
    old = Path(state.artifacts['takeover_context'])
    content = old.read_text()
    state.idea = 'A changed project request'
    state = run_adoption_until_next_gate(state, tmp_path, config)
    assert Path(state.artifacts['takeover_context']) != old
    assert old.read_text() == content
    assert not (repo / 'docs' / 'takeover-context.md').exists()
