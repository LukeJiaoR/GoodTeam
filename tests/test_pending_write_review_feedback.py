"""Real local grant/ledger/board route with terminal-evidence and model fixtures."""
from copy import deepcopy
import pytest
from goodteam_os import project_batch_feedback as feedback
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_planner as planner
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_human_feedback import question_board
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


@pytest.mark.parametrize('mode', ['authorized', 'not_authorized', 'mixed', 'unknown', 'mixed_research', 'mixed_planner', 'mixed_no_initial', 'mixed_no_role', 'mixed_unknown'])
def test_pending_write_review_returns_facts_without_accepting_or_retrying(tmp_path, monkeypatch, mode):
    mixed = mode.startswith('mixed')
    readonly_role = 'planner' if mode == 'mixed_planner' else 'research'
    permissions = {}
    if mixed and mode != 'mixed':
        if mode != 'mixed_no_initial':
            permissions['allow_initial_readonly_feedback'] = True
        if mode != 'mixed_no_role':
            permissions['allow_planning' if readonly_role == 'planner' else 'allow_investigation'] = True
    root = tmp_path / 'source'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    first = ledger.create_task(title='Document first endpoint', description='Document its approved behavior.', role='implementation',
        write_domains=['docs/first'], acceptance_criteria=['First endpoint behavior is documented.'])
    second = ledger.create_task(title='Document second endpoint', description='Document its approved behavior.', role=readonly_role if mixed else 'implementation',
        execution_policy='read_only' if mixed else 'workspace_write',
        write_domains=None if mixed else ['docs/second'], acceptance_criteria=['Second endpoint behavior is documented.'])
    ids = [first['id'], second['id']]
    request = dict(confirmed=True, billing_acknowledged=True, idempotency_key='review-feedback', task_ids=ids, max_batches=3,
        expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version'], discovery=dict(
            objective='Document the current API within the approved documentation scope.', allowed_write_domains=['docs'],
            max_new_tasks=2, max_planning_calls=4, **permissions, **({'allow_undelivered_feedback': True} if mode != 'not_authorized' else {})))
    grant = continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root, project_config=config, request=request)
    run_id = 'task-batch-' + 'a' * 64
    for identifier in ids:
        ledger.update_task(identifier, status='review', run_id=run_id)
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    batch = {'run_id': run_id, 'status': 'review', 'confirmed': True, 'expected_continuation_id': grant['id'],
             'project_root': str(root.resolve()), 'dispatch_run_id': 'dispatch-fixture'}
    store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'],
        'pending_batch': {'run_id': run_id, 'task_ids': ids}}, 'project_task_batches': {run_id: batch}})
    reads = []
    receipt = {'schema_version': 'project_batch_observation.v1', 'continuation_id': grant['id'],
        'authorization_digest': grant['authorization_digest'], 'project_key': 'DEMO', 'batch_run_id': run_id,
        'observation_key': run_id + ':dispatch-fixture', 'delivery_verified': False,
        'tasks': [{'task_id': identifier, 'task_status': 'review', 'report': 'Captured implementation result fixture.',
                   'delivery_verified': False} for identifier in ids]}
    receipt['receipt_id'] = feedback._digest(receipt)
    def read(**kwargs):
        reads.append(kwargs)
        if mode in {'unknown', 'mixed_unknown'}:
            raise ValueError('Every batch invocation must have attributable known terminal evidence before feedback releases scheduling.')
        return deepcopy(receipt)
    monkeypatch.setattr(feedback, '_read_observation', read)
    monkeypatch.setattr('goodteam_os.project_iteration_recovery.project_batch_recovery_evidence',
        lambda **k: {'unknown_task_ids': ids if mode in {'unknown', 'mixed_unknown'} else []})
    monkeypatch.setattr(discovery, '_head', lambda *a: 'b' * 40)
    monkeypatch.setattr(planner, '_project_evidence', lambda *a, **k: {'fixture': 'no real repository validation claimed'})
    calls = []
    def manager(**kwargs):
        packet = kwargs['context']
        calls.append(packet)
        assert packet['verified_outcome'] is None
        assert packet['batch_feedback']['tasks'] == receipt['tasks']
        assert packet['unresolved_batch_task_ids'] == ids
        return {'selection': {'new_task': None, 'rationale': 'The known implementation reports raise an owner decision while delivery remains unaccepted.',
            'human_question': {'question': 'Should the reviewed API examples target internal or external consumers?',
                               'why_human': 'Only the owner can decide the intended audience.'}}}
    def forbidden(**kwargs):
        pytest.fail('Reading settled feedback must not retry or dispatch the original batch.')
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key='DEMO', root=root, project_config=config,
            manager_runner=manager, batch_runner=forbidden)
    result = tick()
    assert all(ledger.get_task(identifier)['status'] == 'review' and not ledger.get_task(identifier).get('delivery_verified') for identifier in ids)
    assert store.read()['project_task_batches'][run_id] == batch
    if mode in {'not_authorized', 'mixed', 'mixed_no_initial', 'mixed_no_role'}:
        assert not reads and not calls and result['pending_batch']['run_id'] == run_id
    elif mode in {'unknown', 'mixed_unknown'}:
        assert reads and not calls and result['status'] == 'blocked' and result['pending_batch']['run_id'] == run_id
    else:
        assert len(calls) == 1 and result['manager_calls'] == 1 and result['pending_batch'] is None, result.get('reason')
        assert result['status'] == 'active' and result['reason'] == 'awaiting_human_decision'
        assert len(question_board(store.read())['items']) == 1
        assert tick()['manager_calls'] == 1 and len(calls) == 1


def test_readonly_scope_representation_does_not_expand_mixed_authority():
    value = {'seed_task_ids': ['read'], 'discovery': {'allow_undelivered_feedback': True,
        'allow_initial_readonly_feedback': True, 'allow_investigation': True}}
    write = {'id': 'write', 'status': 'review', 'execution_policy': 'workspace_write'}
    read = {'id': 'read', 'status': 'review', 'execution_policy': 'read_only', 'role': 'research'}
    # Ordinary create_task omits the empty domain field; create_task_once retains [].
    assert feedback.can_observe_pending_write_review(value, [write, read])
    assert feedback.can_observe_pending_write_review(value, [write, {**read, 'write_domains': []}])
    for invalid in [None, ['docs'], '', {}]:
        assert not feedback.can_observe_pending_write_review(value, [write, {**read, 'write_domains': invalid}])
    assert not feedback.can_observe_pending_write_review(value, [write, {**read, 'id': 'outside-seed'}])
    assert not feedback.can_observe_pending_write_review(value, [write, {**read, 'execution_policy': 'unknown'}])
