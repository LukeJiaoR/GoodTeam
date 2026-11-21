"""Selected project drafts and batch buttons share admission, not greenfield.

Runtime execution is held at its boundary; this proves HTTP/queue ownership,
not live provider execution or isolation acceptance.
"""
import pytest

from goodteam_os import project_iteration_execution as execution, web
from goodteam_os.assistant_queue import handle_queue_action
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, task_evidence_digest


@pytest.mark.parametrize("prepare_first", [False, True])
def test_selected_role_draft_keeps_task_and_uses_same_batch_once(tmp_path, monkeypatch, prepare_first):
    root = tmp_path / 'source'
    root.mkdir()
    config = tmp_path / 'project.yaml'
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    task = LocalTaskStore(root, 'DEMO').create_task(title='Investigate failures', description='Read the known inputs.',
        role='research', execution_policy='read_only', acceptance_criteria=['Return source-backed findings.'])
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    proposal = {'id': 'selected-research', 'revision': 1, 'exact_request': 'Investigate failures',
        'project_task_id': task['id'], 'project_task_digest': task_evidence_digest(task),
        'source_message_ids': [], 'status': 'queued', 'confirmed': False, 'run_id': ''}
    store._mutate(lambda value: {**value, 'queued_task_proposals': [proposal]})
    service = TaskAssistantService(tmp_path, project_config=config)
    monkeypatch.setattr(service, '_readiness_state', lambda *_: {'lifecycle': 'ready', 'project_mode': 'existing_project'})
    monkeypatch.setattr(service, 'session', lambda *_: store.read())
    monkeypatch.setattr(service.readiness, 'submit_input', lambda *_, **__: {})
    monkeypatch.setattr(web, 'TaskAssistantService', lambda *_, **__: service)
    monkeypatch.setattr(web, '_project_target_and_root', lambda *_: ('DEMO', {}, root))
    monkeypatch.setattr(web, '_project_config_for_key', lambda *_: config)
    handle_queue_action(service, project_key='DEMO', root=root, request={
        'action': 'select_queued_task', 'proposal_id': proposal['id'], 'expected_revision': 1})
    assert store.read()['proposed_task']['project_task_id'] == task['id']
    # No model/Worker is started by this integration check. Admission still
    # resolves the actual configured Role and preserves the original task.
    observed = []
    monkeypatch.setattr(execution, '_execute_batch', lambda **kwargs: observed.append(kwargs['batch']))
    path = '/api/projects/DEMO/task-assistant/actions'
    request = {'action': 'confirm_task', 'proposal_id': proposal['id'], 'expected_revision': 1}
    with pytest.raises(web.ProjectApiError, match='latest selected task revision'):
        web.handle_project_api_post(tmp_path, path, {**request, 'expected_revision': 2})
    with monkeypatch.context() as team_patch:
        team_patch.setattr('goodteam_os.team_control_plane.TeamControlPlaneStore.read',
                           lambda _: {'revision': 2})
        with pytest.raises(web.ProjectApiError, match='Team changed after confirmation'):
            web.handle_project_api_post(tmp_path, path, {**request, 'expected_team_revision': 1})
        assert not store.read()['proposed_task'].get('confirmed')
    if prepare_first:
        # Resume a draft whose Team confirmation was already persisted.
        store.confirm_task(proposal['id'], expected_revision=1)
        store._mutate(lambda session: {**session, 'proposed_task': {
            **session['proposed_task'], 'selected_team_revision': None}})
        status, result = web.handle_project_api_post(tmp_path, path,
            {'action': 'continue_preparation', 'step_id': 'worker_preflight', 'values': {}})
    else:
        status, result = web.handle_project_api_post(tmp_path, path, request)
    assert status == 202 and result['execution_started'] is True
    run_id = result['run_id']
    batch = store.read()['project_task_batches'][run_id]
    assert batch['selected_proposal_id'] == proposal['id']
    assert batch['tracks'][0]['role'] == 'research'
    assert batch['tracks'][0]['execution_policy'] == 'read_only'
    assert batch['task_ids'] == [task['id']]
    assert not store.read()['proposed_task']
    # A repeated click observes/reuses the same admitted batch, not a fresh
    # workflow or task. The explicit bulk entry also shares that identity.
    repeated = web.handle_project_api_post(tmp_path, path, request)[1]
    assert repeated['run_id'] == run_id
    bulk = web.handle_project_api_post(tmp_path, path, {'action': 'start_queued_task_batch',
        'proposal_ids': [proposal['id']], 'expected_revisions': {proposal['id']: 1}, 'confirmed': True})[1]
    assert bulk["run_id"] == run_id and len(store.read()["project_task_batches"]) == 1


def test_incomplete_selected_confirmation_cannot_drop_team_guard(tmp_path):
    from goodteam_os.assistant_drafts import start_selected_project_task

    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    queued = {'id': 'interrupted-confirm', 'revision': 1, 'project_task_id': 'task-1',
              'status': 'selected', 'confirmed': False}
    # Simulate process exit after confirmed was saved but before its Team guard.
    store._mutate(lambda value: {**value, 'queued_task_proposals': [queued],
        'proposed_task': {**queued, 'confirmed': True}})
    service = TaskAssistantService(tmp_path)
    with pytest.raises(ValueError, match='confirmation is incomplete'):
        start_selected_project_task(service, project_key='DEMO', root=tmp_path,
            request={'action': 'continue_preparation', 'values': {}})
    assert not store.read().get('project_task_batches')
