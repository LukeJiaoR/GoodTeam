"""Real decision boundary tests; fixture work is not a real reviewer PASS."""
from copy import deepcopy

import pytest

from goodteam_os import web
from goodteam_os.state import StateStore
from test_workflow_recovery import binding_for, gate_state


def durable_files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
            if path.is_file() and path.suffix in {'.json', '.jsonl'}}


@pytest.mark.parametrize('origin', ['web', 'telegram', 'cli', 'manager_intent_commit', 'fake'])
def test_empty_override_never_writes_decision_or_starts_work(tmp_path, monkeypatch, origin):
    state = gate_state(tmp_path, 'release')
    review = next(item for item in web.build_approval_reviews(state, workspace=tmp_path)
                  if item['approval'] == 'release')
    assert review['recommendation']['verdict'] != 'approve'
    before = durable_files(tmp_path)
    executed = []
    monkeypatch.setattr(web, 'advance_approved_workflow', lambda **kw: executed.append(kw))
    with pytest.raises(web.ProjectApiError) as error:
        web.decide_approval(workspace=tmp_path, project_key='RECOVERY', approval='release',
                            decision='approved', note=' \n ', decision_origin=origin,
                            client_context={'recommendation': 'approve', 'reason_required': False},
                            **binding_for(state))
    assert error.value.code == 'approval_reason_required'
    assert durable_files(tmp_path) == before
    assert executed == []


def test_manual_reason_is_recorded_once_and_replay_never_redecides(tmp_path):
    state = gate_state(tmp_path, 'release')
    binding = binding_for(state)
    note = 'I accept the simulated recovery fixture solely to check the decision contract.'
    result = web.decide_approval(workspace=tmp_path, project_key='RECOVERY', approval='release',
                                decision='approved', note=note, **binding)
    assert result['current_node'] == 'complete'
    saved = StateStore(tmp_path).read_project_current('RECOVERY')
    first_action = deepcopy(saved.pending_transition['action'])
    assert first_action['note'] == note
    replay = web.decide_approval(workspace=tmp_path, project_key='RECOVERY', approval='release',
                                decision='approved', note='', **binding)
    assert replay['replayed'] is True
    decisions = [row for row in web.build_action_items(tmp_path, 'RECOVERY')
                 if row['action'] == 'approval_decided']
    assert len(decisions) == 1 and decisions[0]['note'] == note
    assert StateStore(tmp_path).read_project_current('RECOVERY').pending_transition['action'] == first_action
