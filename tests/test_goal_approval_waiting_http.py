"""Real approval endpoint and background continuation; seeded release, synthetic assessment."""
import time

import pytest

from goodteam_os.state import ApprovalDecision, StateStore
from goodteam_os.tasks import LocalTaskStore
from test_goal_service_recovery import seed
from workflow_http_helpers import RecoveryServer, approval_body, wait_until


def release_goal(root):
    workspace, _, store, goal = seed(root)
    state = StateStore(workspace).read_project_current('RECOVERY')
    state.current_node = 'release_review_gate'
    state.next_node = 'await_release_approval'
    state.pending_transition = {}
    state.approvals = {key: ApprovalDecision.APPROVED for key in state.approvals}
    state.approvals['release'] = ApprovalDecision.PENDING
    artifact = workspace / 'artifacts/runs' / state.run_id / 'release_notes/release-notes.md'
    artifact.parent.mkdir(parents=True)
    artifact.write_text('# Release fixture\nThe isolated check works.\n')
    qa = artifact.parent.parent / 'qa/result.md'
    qa.parent.mkdir()
    qa.write_text('The isolated check works; this is synthetic fixture evidence.')
    state.artifacts = {'release_notes': str(artifact), 'qa': str(qa),
                       'task_ledger': str(root / 'target/.goodteam/tasks/tasks.json')}
    StateStore(workspace).write_project_current(state)
    return workspace, store, goal, state


def goal_card(data, goal_id):
    return next(card for cards in data['agile_board']['columns'].values()
                for card in cards if card.get('goal_id') == goal_id)


@pytest.mark.parametrize('decision', ['approved', 'rejected'])
def test_real_gate_decision_and_duplicate_preserve_same_goal(tmp_path, decision):
    workspace, store, goal, state = release_goal(tmp_path)
    server = RecoveryServer(tmp_path, fixture='goal_approval_waiting_server.py')
    try:
        server.start()
        tasks = LocalTaskStore(tmp_path / 'target', 'RECOVERY')
        wait_until(lambda: any(card.get('observed_execution_status') == 'awaiting_approval' for card in tasks.list_tasks()))
        before = server.state()
        view = before['task_assistant_session']['goals'][-1]
        assert view['id'] == goal['id']
        assert view['active_work']['observed_approval'] == 'release'
        card = goal_card(before, goal['id'])
        assert card['status'] == 'review' and card['observed_execution_status'] == 'awaiting_approval'
        assert card in before['agile_board']['columns']['doing']
        assert not list(tmp_path.rglob('job.json'))
        server.stop()
        server.start()
        time.sleep(1.2)
        assert not list(tmp_path.rglob('job.json'))
        binding = {**approval_body(server.state(), 'release'), 'decision': decision}
        assert server.ok('/api/approve', binding)['run_id'] == state.run_id
        assert server.ok('/api/approve', binding)['replayed'] is True
        expected = 'complete' if decision == 'approved' else 'blocked'
        wait_until(lambda: store.get_goal(goal['id'])['status'] == expected)
        final = store.get_goal(goal['id'])
        assert len(final['work_units']) == 1
        assert final['work_units'][0]['run_id'] == state.run_id
        final_view = server.state()
        final_card = next(card for card in tasks.list_tasks() if card.get('goal_id') == goal['id'])
        assert not final_card.get('observed_approval')
        jobs = list(tmp_path.rglob('job.json'))
        assert len(jobs) == (1 if decision == 'approved' else 0)
        if decision == 'approved':
            assert final_card['status'] == 'done'
            assert final['work_units'][0]['assessment_checks']
        else:
            assert final_card['status'] == 'canceled'
            assert final_card['observed_execution_status'] == 'canceled'
            assert final_view['task_assistant_session']['goals'][-1]['active_work']['observed_execution_status'] == 'canceled'
        actions = [item for item in server.state()['action_items'] if item['action'] == 'approval_decided']
        assert len(actions) == 1
    finally:
        server.stop()


@pytest.mark.parametrize('status', ['stopped', 'canceled'])
def test_stop_or_cancel_waiting_goal_never_resumes_continuation(tmp_path, status):
    _, store, goal, _ = release_goal(tmp_path)
    server = RecoveryServer(tmp_path, fixture='goal_approval_waiting_server.py')
    try:
        server.start()
        wait_until(lambda: store.get_goal(goal['id'])['active_work']['status'] == 'running')
        current = store.get_goal(goal['id'])
        binding = approval_body(server.state(), 'release')
        request = dict(action='stop_goal', status=status, goal_id=goal['id'], expected_revision=current['revision'], idempotency_key='stop')
        route = '/api/projects/RECOVERY/task-assistant/actions'
        assert server.ok(route, request)['goal']['status'] == status
        assert server.ok(route, request)['goal']['status'] == status
        # Stop revokes future Goal continuation, not existing workflow controls.
        server.ok('/api/approve', binding)
        time.sleep(1.2)
        assert store.get_goal(goal['id'])['status'] == status
        assert len(store.get_goal(goal['id'])['work_units']) == 1
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()


def test_real_blocker_is_not_presented_as_normal_approval_wait(tmp_path):
    workspace, store, goal, state = release_goal(tmp_path)
    state.blockers = ['Synthetic execution failure requiring inspection']
    state.next_node = 'blocked'
    StateStore(workspace).write_project_current(state)
    server = RecoveryServer(tmp_path, fixture='goal_approval_waiting_server.py')
    try:
        server.start()
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'blocked')
        data = server.state()
        view = data['task_assistant_session']['goals'][-1]
        assert view['active_work']['observed_execution_status'] == 'blocked'
        assert not view['active_work'].get('observed_approval')
        card = goal_card(data, goal['id'])
        assert card['status'] == 'blocked'
        assert card in data['agile_board']['columns']['blocked']
        assert not list(tmp_path.rglob('job.json'))
    finally:
        server.stop()
