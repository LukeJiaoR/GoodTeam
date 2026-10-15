"""Read-only projection contracts: original intent metadata, no authority changes."""
import json
from pathlib import Path

import pytest

from goodteam_os.execution.approved_plan_evidence import approved_plan_evidence
from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.review_workbench import ReviewWorkbenchService
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.task_snapshot import execution_key
from goodteam_os.state import StateStore
from goodteam_os.workers import WorkerJobStore
from test_approved_plan_binding import register
from test_task_input_snapshot import ctx as ctx_fixture, start  # noqa: F401


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def bound(request, monkeypatch):
    c = request.getfixturevalue('ctx_fixture')
    c.execution_id = start(c).execution_id
    c.binding = register(c)
    monkeypatch.setattr(WorkerJobStore, 'run_job', lambda *_a, **_kw: pytest.fail('Projection dispatched a Worker'))
    return c


def test_current_projection_is_bounded_metadata_and_read_only(bound):
    c = bound
    before = files(c.workspace.parent)
    for _ in range(2):
        value = approved_plan_evidence(c.project, c.execution_id)
        assert value['status'] == 'current'
        assert value['binding_id'] == c.binding['binding_id']
        assert value['input_snapshot'] == c.store.load(c.execution_id).reference
        assert [item['gate'] for item in value['approvals']] == ['rfc', 'security']
        assert value['derived_task']['human_approved'] is False
        assert value['semantic_compliance'] == 'not_independently_verified'
        encoded = json.dumps(value)
        assert 'No network or unrelated changes' not in encoded
        assert 'Preserve OLD_CONDITION' not in encoded
        assert str(c.workspace) not in encoded
        assert 'workflow_operation_id' not in encoded
    assert files(c.workspace.parent) == before


@pytest.mark.parametrize('mutation', ['revoked', 'artifact', 'breakdown', 'missing', 'marker', 'corrupt', 'other_run'])
def test_invalid_binding_never_becomes_current_or_leaks_content(bound, mutation):
    c = bound
    root = c.store.root / execution_key(c.execution_id)
    states = StateStore(c.workspace)
    state = states.read_project_current('DEMO')
    if mutation in {'revoked', 'other_run'}:
        if mutation == 'revoked':
            state.approvals['rfc'] = 'rejected'
        else:
            state.run_id = 'different-workflow'
        states.write_project_current(state)
    elif mutation in {'artifact', 'breakdown'}:
        Path(state.artifacts['rfc' if mutation == 'artifact' else 'task_breakdown']).write_text('SECRET changed artifact')
    elif mutation in {'missing', 'marker'}:
        (root / ('approved-plan.json' if mutation == 'missing' else 'approved-plan-required.json')).unlink()
    else:
        (root / 'approved-plan.json').write_text('SECRET corrupt JSON')
    before = files(c.workspace.parent)
    for _ in range(2):
        result = approved_plan_evidence(c.project, c.execution_id)
        assert result['status'] == 'unavailable'
        assert result['semantic_compliance'] == 'not_independently_verified'
        assert set(result) == {'status', 'reason', 'semantic_compliance'}
        assert 'SECRET' not in json.dumps(result)
    assert files(c.workspace.parent) == before


def test_legacy_and_unbound_reads_create_nothing(request):
    c = request.getfixturevalue('ctx_fixture')
    before = files(c.workspace.parent)
    assert approved_plan_evidence(c.project, 'legacy')['status'] == 'legacy_unverified'
    assert files(c.workspace.parent) == before
    execution = start(c).execution_id
    before = files(c.workspace.parent)
    assert approved_plan_evidence(c.project, execution)['status'] == 'unbound'
    assert files(c.workspace.parent) == before


def test_existing_evidence_and_workbench_share_projection_without_authority_change(bound):
    c = bound
    manifest = ExecutionEvidenceService(c.project).get_evidence(c.execution_id)
    assert manifest['approved_plan'] == approved_plan_evidence(c.project, c.execution_id)
    assert manifest['final_status'] != 'completed'
    review = ReviewWorkbenchService(c.project).get_review(c.execution_id)
    assert review['evidence']['approved_plan'] == manifest['approved_plan']
    assert review['evidence']['final_status'] == manifest['final_status']


def test_html_escapes_heading_and_exposes_no_action(bound):
    value = approved_plan_evidence(bound.project, bound.execution_id)
    value['derived_task']['heading'] = '<script>alert(1)</script>'
    html = render_review_workbench_html({'evidence': {'approved_plan': value}})
    section = html.split('id="approved-plan"', 1)[1].split('</section>', 1)[0]
    assert '<script>' not in section and '&lt;script&gt;' in section
    assert '<button' not in section and '<form' not in section and '<details open' not in section


def test_missing_lock_files_are_not_repaired_by_projection(bound):
    c = bound
    for lock in c.operator.rglob('.store.lock'):
        lock.unlink()
    before = files(c.workspace.parent)
    assert approved_plan_evidence(c.project, c.execution_id)['status'] == 'current'
    assert files(c.workspace.parent) == before


def test_malformed_workflow_state_stays_unavailable_without_breaking_evidence(bound):
    c = bound
    path = StateStore(c.workspace).project_state_dir('DEMO') / 'current.json'
    state = json.loads(path.read_text())
    state['approvals'] = []
    path.write_text(json.dumps(state))
    before = files(c.workspace.parent)
    result = approved_plan_evidence(c.project, c.execution_id)
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'approved_plan_unavailable'
    assert ExecutionEvidenceService(c.project).get_evidence(c.execution_id)['approved_plan'] == result
    assert files(c.workspace.parent) == before


def test_missing_snapshot_cannot_downgrade_retained_plan_to_legacy(bound):
    c = bound
    root = c.store.root / execution_key(c.execution_id)
    for name in ('snapshot.json', 'required.json'):
        (root / name).unlink()
    before = files(c.workspace.parent)
    value = approved_plan_evidence(c.project, c.execution_id)
    assert value['status'] == 'unavailable'
    assert value['reason'] == 'approved_plan_input_missing'
    assert files(c.workspace.parent) == before


def test_lost_registration_does_not_make_retained_binding_legacy(bound):
    c = bound
    c.registry.unlink()
    before = files(c.workspace.parent)
    assert approved_plan_evidence(c.project, c.execution_id)['status'] == 'unavailable'
    assert files(c.workspace.parent) == before


def test_binding_moving_during_projection_is_unavailable(bound, monkeypatch):
    from goodteam_os.execution import approved_plan_evidence as module
    c = bound
    original = module._capture
    root = c.store.root / execution_key(c.execution_id)
    def moving(*args, **kwargs):
        value = original(*args, **kwargs)
        (root / 'approved-plan-required.json').unlink()
        return value
    monkeypatch.setattr(module, '_capture', moving)
    result = approved_plan_evidence(c.project, c.execution_id)
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'approved_plan_binding_stale'
    assert not (root / 'approved-plan-required.json').exists()
