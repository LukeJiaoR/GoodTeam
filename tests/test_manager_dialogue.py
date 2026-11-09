"""Offline continuation adapter with an in-memory checkpoint, not live-model acceptance."""
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

    root = repository(tmp_path / 'source')
    config = write_project_config(tmp_path / 'projects', 'DEMO', 'Demo', repo_path=str(root), worker_mode='fake')
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    grant = {'id': 'original', 'status': 'active', 'pending_batch': None, 'max_batches': 2, 'batches_started': 1}
    store._mutate(lambda value: {**value, 'project_continuation': grant})
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
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=lambda **_: {
        'intent': 'project_operation', 'response': 'Following up.',
        'project_operation': {'kind': 'advance_continuation', 'target_id': 'original'}})
    request = dict(project_key='DEMO', root=root, text='Follow up unfinished work.', message_id='followup')
    service.send_message(**request)
    service.send_message(**request)
    assert len(calls) == 1
    assert set(calls[0]) == {'workspace', 'project_key', 'root', 'project_config'}
    store._mutate(lambda value: {**value, 'project_continuation': {**grant, 'status': 'stopped'}})
    service.send_message(**{**request, 'message_id': 'stopped'})
    assert len(calls) == 1
