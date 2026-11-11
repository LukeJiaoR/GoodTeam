"""Persisted assignment -> runtime track -> job evidence, without a live provider."""
import json
from copy import deepcopy

import pytest

from goodteam_os.dispatch import _create_track_job
from goodteam_os.manager_execution import _parse_chat_turn, ManagerExecutionError
from goodteam_os.project_iteration_execution import _runtime_tracks
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, project_task_intent
from goodteam_os.workers import WorkerJobStore


def test_delegated_contract_reaches_persisted_job_and_retains_source(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    action = {'kind': 'queue_task', 'role': 'research', 'execution_policy': 'read_only',
              'write_domains': [], 'acceptance_criteria': ['Identify the provider and cite its config.'],
              'delegation': {'purpose': 'Identify the configured provider.',
                             'context': 'Prior discussion: compare current setup; unknown deployment.',
                             'scope': 'Read config only, no deployment or paid calls.'}}
    turn = {'intent': 'work_request', 'response': 'Queued research.',
            'task_intake': {'ready': True, 'missing_decisions': [], 'source_message_ids': ['request']},
            'task_action': action}
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=lambda **_: _parse_chat_turn(json.dumps(turn)))
    monkeypatch.setattr(service, '_readiness_state', lambda *_: {'project_mode': 'existing_project', 'lifecycle': 'ready'})
    monkeypatch.setattr(service, 'session', lambda *_: TaskAssistantSessionStore(tmp_path, 'DEMO').read())
    request = dict(project_key='DEMO', root=root, text='Check the configured provider.', message_id='request')
    session = service.send_message(**request)['session']
    proposal = session['queued_task_proposals'][0]
    task = LocalTaskStore(root, 'DEMO').get_task(proposal['project_task_id'])
    assert proposal['exact_request'] == request['text'] == task['description']
    assert task['delegation']['source_messages'] == [{'message_id': 'request', 'content': request['text']}]
    assert project_task_intent(task)['delegation'] == task['delegation']
    assert proposal['assignment']['delegation'] == action['delegation']
    track = _runtime_tracks(tmp_path, config, 'DEMO', root, [task], [proposal])[0]
    assert track['role'] == 'research' and track['execution_policy'] == 'read_only'
    for part in [request['text'], *action['delegation'].values(), *action['acceptance_criteria']]:
        assert part in track['task']
    jobs = WorkerJobStore(root)
    job = _create_track_job(jobs, run_id='contract', track=track, constraints=[], project_key='DEMO', command_key='contract-1')
    assert job['role'] == 'research' and job['project_task_id'] == task['id']
    assert (jobs.root / job['id'] / 'task.md').read_text() == track['task']
    assert len(service.send_message(**request)['session']['queued_task_proposals']) == 1
    corrupted = deepcopy(turn)
    corrupted['task_action']['delegation']['unknown_authority'] = 'publish'
    with pytest.raises(ManagerExecutionError, match='delegation'):
        _parse_chat_turn(json.dumps(corrupted))

    legacy = deepcopy(turn)
    del legacy['task_action']['delegation']
    with pytest.raises(ManagerExecutionError, match='task action'):
        _parse_chat_turn(json.dumps(legacy))
    assert _parse_chat_turn(json.dumps(legacy), require_delegation=False)['task_action'] == legacy['task_action']
