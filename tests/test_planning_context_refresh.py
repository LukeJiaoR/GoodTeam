"""Real grant/queue/planning consumer with deterministic Manager output, no Worker."""
from copy import deepcopy
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_planner as planner
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def scenario(tmp_path, monkeypatch, mode='backlog'):
    root = tmp_path / 'project'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    task = ledger.create_task(title='Document endpoint', description='Document the approved endpoint.',
        role='implementation', write_domains=['docs/api'], acceptance_criteria=['Endpoint behavior is documented.'])
    continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
        project_config=config, request=dict(confirmed=True, billing_acknowledged=True, idempotency_key='refresh',
            task_ids=[task['id']], max_batches=3, expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version']))
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    monkeypatch.setattr(planner, '_project_evidence', lambda *a, **k: {'fixture': 'no live repository inspection'})
    calls = []
    def manager(**kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            if mode == 'conversation':
                store.append_message('context-update', 'user', 'The endpoint audience is internal engineers; keep the same scope.')
            else:
                ledger.create_task(title='Separate backlog idea', description='Not included in this grant.', role='implementation')
        return {'selection': {'selected_task_ids': [task['id']], 'compared_task_ids': [task['id']],
            'rationale': 'This approved documentation task is the ready work in the original scope.'},
            'manager_execution': {'fixture': 'known completed decision'}}
    class BeforeWorker(Exception):
        pass
    def no_worker(**kwargs):
        raise BeforeWorker
    def tick(runner=None):
        return continuation.tick_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
            project_config=config, manager_runner=runner or manager, batch_runner=no_worker)
    return ledger, task, store, calls, tick, BeforeWorker


@pytest.mark.parametrize('mode', ['backlog', 'conversation', 'lost_ack'])
def test_completed_stale_decision_is_saved_then_rejudged_not_dispatched(tmp_path, monkeypatch, mode):
    ledger, task, store, calls, tick, BeforeWorker = scenario(tmp_path, monkeypatch, mode)
    if mode == 'lost_ack':
        original = TaskAssistantSessionStore._mutate
        lost = []
        def write(self, mutate):
            before = self.read()
            result = original(self, mutate)
            old = [e for e in before['timeline'] if e.get('event') == 'project_iteration_choice_superseded']
            new = [e for e in result['timeline'] if e.get('event') == 'project_iteration_choice_superseded']
            if not old and new and not lost:
                lost.append(True)
                raise OSError('Committed settlement acknowledgement lost')
            return result
        monkeypatch.setattr(TaskAssistantSessionStore, '_mutate', write)
    first = tick()
    assert first['status'] == 'active' and first['reason'] == 'planning_context_changed', first.get('reason')
    assert first['manager_calls'] == 1 and first['planning_backlog_version'] == ''
    assert not store.read().get('queued_task_proposals')
    events = [e for e in store.read()['timeline'] if e.get('event') == 'project_iteration_choice_superseded']
    assert len(events) == 1 and events[0]['details']['choice']['status'] == 'stale'
    assert events[0]['details']['context'] == calls[0]['context']
    assert events[0]['details']['choice']['planning_attempt_id'] == first['id'] + ':1'
    with pytest.raises(BeforeWorker):
        tick()
    current = store.read()['project_continuation']
    assert len(calls) == 2 and current['manager_calls'] == 2
    assert calls[1]['planning_attempt_id'] == first['id'] + ':2'
    assert calls[1]['context'] != calls[0]['context']
    assert current['pending_batch']['task_ids'] == [task['id']]
    assert len([e for e in store.read()['timeline'] if e.get('event') == 'project_iteration_choice_superseded']) == 1
