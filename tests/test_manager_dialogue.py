"""Offline continuation adapter with an in-memory checkpoint, not live-model acceptance."""
from copy import deepcopy
import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from goodteam_os import assistant_requests, manager_dialogue
from goodteam_os.projects import write_project_config
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_dispatch_isolation import repository


def test_chat_followup_reuses_only_current_active_continuation_outside_conversation_lock(tmp_path, monkeypatch):
    from goodteam_os import project_iteration_continuation
    from goodteam_os.assistant_requests import conversation_lock
    from goodteam_os.project_human_feedback import _question_id
    from goodteam_os.project_outcome_discovery import _digest

    root = repository(tmp_path / 'source')
    config = write_project_config(tmp_path / 'projects', 'DEMO', 'Demo', repo_path=str(root), worker_mode='fake')
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    grant = {'id': 'original', 'status': 'active', 'pending_batch': None, 'max_batches': 2, 'batches_started': 1,
             'authorization_digest': 'fixture-authority', 'discovery': {'objective': 'Document the approved API audience.'}}
    historical = {**grant, 'id': 'earlier', 'status': 'stopped'}
    def retained_question(origin):
        packet = {'project_key': 'DEMO', 'old_context': 'Frozen packet detail must stay in storage.'}
        packet['packet_digest'] = _digest(packet)
        pending = {'packet': packet, 'packet_digest': packet['packet_digest'],
                   'selection': {'new_task': None, 'rationale': 'The audience decision needs the owner.',
                       'human_question': {'question': 'Which API audience should this project support?',
                                          'why_human': 'The owner chooses the business audience.'}},
                   'manager_execution': {'job_id': 'fixture-manager-job'}}
        identifier = _question_id(origin, pending)
        return {'id': identifier, 'planning': pending, 'answer': None}
    for origin in (grant, historical):
        question = retained_question(origin)
        origin['human_questions'] = {question['id']: question}
    grant['discovery_pending'] = deepcopy(question['planning'])
    store._mutate(lambda value: {**value, 'project_continuation': grant,
                                'project_continuation_history': {historical['id']: historical}})
    original_snapshot = manager_dialogue.current_context(store, root)
    checkpoint = InMemorySaver()
    monkeypatch.setattr(manager_dialogue, 'database', lambda: SimpleNamespace(manager_checkpointer=lambda: checkpoint))
    monkeypatch.setattr(assistant_requests, 'database_configured', lambda: True)
    calls = []
    def tick(**kwargs):
        with conversation_lock(store):
            assert store.read()['project_continuation'] == grant
        calls.append(kwargs)
        return grant
    monkeypatch.setattr(project_iteration_continuation, 'tick_project_continuation', tick)
    contexts = []
    def manager_turn(**kwargs):
        contexts.append(kwargs['context'])
        return {'intent': 'project_operation', 'response': 'Following up.',
                'project_operation': {'kind': 'advance_continuation', 'target_id': 'original'}}
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=manager_turn)
    request = dict(project_key='DEMO', root=root, text='Follow up unfinished work.', message_id='followup')
    service.send_message(**request)
    service.send_message(**request)
    assert len(calls) == 1
    assert set(calls[0]) == {'workspace', 'project_key', 'root', 'project_config'}
    assert manager_dialogue.current_context(store, root) == original_snapshot
    visible = json.loads(contexts[0].split('Fresh continuation/batch/attention (read-only, never authorization): ', 1)[1]
                        .split('\nPending conversation question', 1)[0])
    assert visible['version'] == original_snapshot['version']
    assert visible['continuation']['discovery'] == grant['discovery']
    assert visible['continuation']['discovery_pending']['selection'] == grant['discovery_pending']['selection']
    assert visible['continuation']['discovery_pending']['packet_digest'] == grant['discovery_pending']['packet_digest']
    assert {item['continuation_id'] for item in visible['question_board']['items']} == {'original', 'earlier'}
    assert 'Frozen packet detail must stay in storage.' not in contexts[0]
    assert 'human_questions' not in visible['continuation'] and 'packet' not in visible['continuation']['discovery_pending']
    store._mutate(lambda value: {**value, 'project_continuation': {**grant, 'status': 'stopped'}})
    service.send_message(**{**request, 'message_id': 'stopped'})
    assert len(calls) == 1
