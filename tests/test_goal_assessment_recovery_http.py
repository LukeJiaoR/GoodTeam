"""Real HTTP/background recovery, deliberately seeded completed run; no paid models."""
from goodteam_os.goal_continuation import authority_snapshot
from goodteam_os.goals import GoalStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.team_control_plane import ensure_team_state
from workflow_http_helpers import RecoveryServer, wait_until


def test_http_explicit_retry_reuses_completed_run_and_exact_goal(tmp_path):
    workspace = tmp_path / 'workspace'
    config = workspace / 'projects/recovery.local.yaml'
    config.parent.mkdir(parents=True)
    root = tmp_path / 'target'
    root.mkdir()
    config.write_text(f'key: RECOVERY\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ensure_team_state(workspace, 'RECOVERY', config)
    store = GoalStore(workspace, 'RECOVERY')
    goal = store.create_goal(objective='Verify completed fixture', acceptance_criteria=['Feature works'],
        allowed_scope=['Read this isolated fixture only'], max_work_units=1, confirmed=True,
        idempotency_key='create', authority_snapshot=authority_snapshot(workspace, 'RECOVERY', config, root))
    goal = store.next_work(goal['id'], expected_revision=goal['revision'], idempotency_key='claim')
    work = goal['active_work']
    state = create_initial_state(work['run_id'], 'RECOVERY', work['task'])
    state.current_node = state.next_node = 'complete'
    artifact = workspace / 'artifacts/runs' / state.run_id / 'qa/result.md'
    artifact.parent.mkdir(parents=True)
    artifact.write_text('Feature works and its check passes.')
    state.artifacts = {'qa': str(artifact)}
    StateStore(workspace).write_project_current(state)
    server = RecoveryServer(tmp_path, fixture='goal_assessment_recovery_server.py')
    try:
        server.start()
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'blocked')
        blocked = store.get_goal(goal['id'])
        target = blocked['assessment_retry_target']
        request = dict(action='retry_goal_assessment', goal_id=goal['id'], expected_revision=blocked['revision'],
            work_id=target['work_id'], run_id=target['run_id'], evidence_digest=target['evidence_digest'],
            failed_job_id=target['job_id'], confirmed=True, idempotency_key='retry')
        path = '/api/projects/RECOVERY/task-assistant/actions'
        assert server.request(path, {**request, 'confirmed': False})[0] == 409
        assert server.ok(path, request)['goal']['id'] == goal['id']
        assert server.ok(path, request)['goal']['id'] == goal['id']
        wait_until(lambda: store.get_goal(goal['id'])['status'] == 'complete')
        final = store.get_goal(goal['id'])
        assert len(final['work_units']) == 1 and final['work_units'][0]['run_id'] == state.run_id
        assert len(list(root.rglob('job.json'))) == 2
        assert final['work_units'][0]['assessment_checks'][0]['quote'] == artifact.read_text()
        server.stop()
        server.start()
        assert store.get_goal(goal['id'])['status'] == 'complete'
        assert len(list(root.rglob('job.json'))) == 2
    finally:
        server.stop()
