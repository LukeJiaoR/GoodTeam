"""Goal waiting is a projection of existing approval authority, never new authority."""
import pytest

from goodteam_os.goal_continuation import goal_view, tick_goal
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.tasks import LocalTaskStore
from test_goal_continuation import setup  # noqa: F401

# Pytest fixture arguments intentionally share the imported fixture name.
# ruff: noqa: F811


def seed_waiting(setup, gate='prd'):
    workspace, root, _, store, create = setup
    goal = create()
    goal = store.next_work(goal['id'], expected_revision=goal['revision'], idempotency_key='claim')
    work = goal['active_work']
    state = create_initial_state(work['run_id'], 'DEMO', work['task'])
    state.current_node = f'{gate}_review_gate'
    state.next_node = f'await_{gate}_approval'
    state.approvals[gate] = ApprovalDecision.PENDING
    if gate == 'fix_plan':
        state.load_profile = 'fix'
    StateStore(workspace).write_project_current(state)
    return workspace, root, store, goal, state


@pytest.mark.parametrize('gate', ['prd', 'rfc', 'security', 'staging', 'release', 'takeover', 'fix_plan'])
def test_valid_pending_gate_projects_waiting_without_dispatch(setup, gate):
    workspace, root, store, goal, state = seed_waiting(setup, gate)
    for _ in range(3):
        result = tick_goal(workspace, 'DEMO', goal['id'], starter=lambda **kw: pytest.fail('waiting dispatched'))
        view = goal_view(workspace, result)
        assert view['status'] == 'active'
        assert view['active_work']['observed_execution_status'] == 'awaiting_approval'
        assert view['active_work']['observed_approval'] == gate
        card, = LocalTaskStore(root, 'DEMO').list_tasks()
        assert card['status'] == 'review'
        assert card['observed_execution_status'] == 'awaiting_approval'
        assert card['observed_approval'] == gate
    assert len(store.get_goal(goal['id'])['work_units']) == 1
    assert StateStore(workspace).read_project_current('DEMO').approvals[gate] == 'pending'


@pytest.mark.parametrize('invalid', ['blocker', 'not_pending', 'unknown_gate', 'wrong_task', 'interrupted'])
def test_invalid_wait_never_projects_actionable_gate(setup, invalid):
    workspace, _, _, goal, state = seed_waiting(setup)
    if invalid == 'blocker':
        state.blockers = ['Real execution failure']
    elif invalid == 'not_pending':
        state.approvals['prd'] = ApprovalDecision.APPROVED
    elif invalid == 'unknown_gate':
        state.next_node = 'await_unknown_approval'
        state.approvals['unknown'] = ApprovalDecision.PENDING
    elif invalid == 'wrong_task':
        state.idea = 'Different task'
    else:
        state.pending_transition = {'status': 'interrupted'}
    StateStore(workspace).write_project_current(state)
    view = goal_view(workspace, goal)
    assert view['active_work']['observed_execution_status'] != 'awaiting_approval'
    assert not view['active_work'].get('observed_approval')


@pytest.mark.parametrize('transition', ['recording', 'executing'])
def test_inflight_decision_never_advertises_approval_again(setup, transition):
    workspace, _, _, goal, state = seed_waiting(setup)
    state.pending_transition = {'status': transition}
    StateStore(workspace).write_project_current(state)
    work = goal_view(workspace, goal)['active_work']
    assert work['observed_execution_status'] == 'in_progress'
    assert not work['observed_approval']


def test_resumed_work_clears_old_gate_observation_without_revision_change(setup):
    workspace, root, _, goal, state = seed_waiting(setup)
    tick_goal(workspace, 'DEMO', goal['id'])
    card, = LocalTaskStore(root, 'DEMO').list_tasks()
    assert card['observed_approval'] == 'prd'
    state.approvals['prd'] = ApprovalDecision.APPROVED
    state.current_node = 'architect'
    state.next_node = 'security'
    StateStore(workspace).write_project_current(state)
    tick_goal(workspace, 'DEMO', goal['id'])
    card, = LocalTaskStore(root, 'DEMO').list_tasks()
    assert card['status'] == 'in_progress'
    assert card['observed_execution_status'] == 'in_progress'
    assert card['observed_approval'] == ''
