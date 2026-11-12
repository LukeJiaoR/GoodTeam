"""Real grant/ledger/question/answer/queue path; Manager and capability are fixtures."""
from copy import deepcopy
import pytest
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_human_feedback import question_board
from goodteam_os.assistant_requests import execute_message
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_running_question_uses_original_board_and_answer_without_self_wakeup(tmp_path, monkeypatch):
    from goodteam_os.project_observation_judgment import judge_running_observation, _validate
    from goodteam_os.project_running_observation import observe_running_batch, judgment_human_current
    root = tmp_path / 'project'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    seed = ledger.create_task(title='Implement API docs', description='Document the approved API.',
        role='implementation', write_domains=['docs'], acceptance_criteria=['Document API behavior.'])
    request = dict(confirmed=True, billing_acknowledged=True, idempotency_key='running-human', task_ids=[seed['id']], max_batches=3,
        expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version'], discovery=dict(
            objective='Document the existing API within the approved scope.', allowed_write_domains=['docs'],
            max_new_tasks=2, max_planning_calls=4, allow_running_observation=True, allow_running_human_question=True))
    from goodteam_os.project_outcome_discovery import normalize_discovery
    legacy = {k: v for k, v in request['discovery'].items() if k != 'allow_running_human_question'}
    assert 'allow_running_human_question' not in normalize_discovery(legacy)
    with pytest.raises(ValueError, match='require explicit running observation'):
        normalize_discovery({**request['discovery'], 'allow_running_observation': False})
    grant = continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
        project_config=config, request=request)
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    batch = {'run_id': 'batch', 'status': 'running', 'confirmed': True, 'expected_continuation_id': grant['id'],
             'project_root': str(root.resolve()), 'dispatch_run_id': 'attempt'}
    store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'],
        'pending_batch': {'run_id': 'batch', 'task_ids': [seed['id']]}}, 'project_task_batches': {'batch': batch}})
    row = {'job_id': 'job', 'run_id': 'attempt', 'status': 'running', 'runtime_observation': {'available': True, 'evidence': {'process_alive': True}}}
    monkeypatch.setattr('goodteam_os.task_assistant.worker_invocation_context', lambda *a, **k: {'available': True, 'current': [deepcopy(row)]})
    calls = []
    advice = {'assessment': 'needs_attention', 'rationale': 'The owner must decide the intended audience without stopping independent implementation.',
        'observed_job_ids': ['job'], 'human_question': {'question': 'Should the API documentation serve internal or external customers?', 'why_human': 'Only the owner can decide the intended audience.'}}
    def runner(**kwargs):
        calls.append(deepcopy(kwargs))
        return deepcopy(advice) if len(calls) == 1 else {k: v for k, v in advice.items() if k != 'human_question'}
    def observe():
        return observe_running_batch(store=store, root=root, project_key='DEMO', value=store.read()['project_continuation'])
    def judge():
        return judge_running_observation(store=store, root=root, value=store.read()['project_continuation'], runner=runner, authority_check=lambda: True)
    original = observe()['running_observation']
    # A previously granted advice-only observation cannot acquire board authority.
    with pytest.raises(ValueError, match='does not authorize'):
        _validate(advice, original['packet'])
    first = judge()
    board = question_board(store.read())['items']
    assert len(board) == 1 and board[0]['can_answer']
    assert first['manager_calls'] == 1 and first['pending_batch']['run_id'] == 'batch'
    assert first['running_judgments'][original['digest']]['human_question_id'] == board[0]['id']
    assert judgment_human_current(first, first['running_judgments'][original['digest']])
    assert observe()['running_observation'] == original
    assert judge()['manager_calls'] == 1 and len(calls) == 1
    service = TaskAssistantService(tmp_path, project_config=config)
    # Runtime/batch recovery projection is outside this storage/answer regression.
    monkeypatch.setattr(service, 'session', lambda *a, **k: store.read())
    execute_message(service, project_key='DEMO', root=root, text=f"/answer {board[0]['id']} External customers; keep the original write scope.", message_id='running-answer')
    assert not judgment_human_current(store.read()['project_continuation'], first['running_judgments'][original['digest']])
    updated = observe()
    assert updated['running_observation']['digest'] != original['digest']
    assert updated['running_observation']['packet']['human_context']['answers'][0]['text'].startswith('External customers')
    assert judge()['manager_calls'] == 2 and len(calls) == 2
    assert store.read()['project_task_batches']['batch'] == batch
    assert len(ledger.list_tasks()) == 1
    # A late result after Stop is retained as stale advice, without another question.
    row['runtime_observation']['evidence']['process_alive'] = False
    observe()
    def late(**kwargs):
        store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'], 'status': 'stopped'}})
        return deepcopy(advice)
    stopped = judge_running_observation(store=store, root=root, value=store.read()['project_continuation'], runner=late, authority_check=lambda: True)
    assert stopped['status'] == 'stopped' and len(question_board(store.read())['items']) == 1


def test_own_question_context_preserves_truncation_and_rejects_new_decisions():
    from goodteam_os.project_human_feedback import retain_observation_question
    from goodteam_os.project_running_observation import human_observation_context, judgment_human_current
    value = {'id': 'grant', 'authorization_digest': 'original', 'human_questions': {}}
    def record(identifier, packet):
        return {'observation_digest': identifier, 'packet': packet, 'advice': {'human_question': {
            'question': f'Question {identifier} needs an owner decision now?', 'why_human': 'Only the owner can decide this requirement.'},
            'rationale': 'A concrete scope decision is needed.'}}
    for i in range(10):
        earlier = record(str(i), {'binding': {'batch': 'older'}, 'human_context': {}})
        retain_observation_question(value, project_key='DEMO', observation_id=str(i), record=earlier)
    current = record('current', {'binding': {'batch': 'current'}, 'human_context': human_observation_context(value)})
    current['human_question_id'] = retain_observation_question(value, project_key='DEMO', observation_id='current', record=current)
    assert judgment_human_current(value, current)
    other = record('other', {'binding': {'batch': 'current'}, 'human_context': human_observation_context(value)})
    other_id = retain_observation_question(value, project_key='DEMO', observation_id='other', record=other)
    assert not judgment_human_current(value, current)
    del value['human_questions'][other_id]
    value['human_questions'][current['human_question_id']]['resolution'] = {'status': 'dismissed'}
    assert not judgment_human_current(value, current)
