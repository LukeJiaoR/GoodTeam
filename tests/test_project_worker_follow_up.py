"""Manager decision -> durable original Pi queue; no external model requests."""
from copy import deepcopy
import json
import os

import pytest

from goodteam_os.project_iteration_continuation import _intent
from goodteam_os.project_observation_judgment import judge_running_observation
from goodteam_os.project_outcome_discovery import authorization_digest, normalize_discovery
from goodteam_os.project_running_observation import observe_running_batch
from goodteam_os.project_worker_follow_up import consume_worker_follow_up
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore


@pytest.mark.parametrize("cancel_before_enqueue", [False, True])
def test_authorized_manager_follow_up_uses_original_job_queue_once_and_preserves_truth(tmp_path, monkeypatch, cancel_before_enqueue):
    task = LocalTaskStore(tmp_path, 'DEMO').create_task(title='Inspect current documentation',
        description='Report existing documentation only', role='research', acceptance_criteria=['Report evidence'])
    discovery = normalize_discovery({'objective': 'Inspect documented behavior without expanding the task scope.',
        'allowed_write_domains': ['docs'], 'max_new_tasks': 1, 'max_planning_calls': 4,
        'allow_running_observation': True, 'worker_follow_up_limit': 2})
    value = {'id': 'grant', 'status': 'active', 'authority': 'fixed', 'manager_calls': 0, 'max_manager_calls': 4,
        'max_batches': 2, 'task_ids': [task['id']], 'task_intents': {task['id']: _intent(task)},
        'seed_task_ids': [task['id']], 'seed_task_intents': {task['id']: _intent(task)},
        'approved_backlog_version': 'initial', 'discovery': discovery,
        'pending_batch': {'run_id': 'batch', 'task_ids': [task['id']]}}
    value['authorization_digest'] = authorization_digest(value)
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    batch = {'run_id': 'batch', 'confirmed': True, 'project_root': str(tmp_path.resolve()), 'dispatch_run_id': 'attempt',
        'status': 'running', 'expected_continuation_id': 'grant'}
    store._mutate(lambda s: {**s, 'project_continuation': deepcopy(value), 'project_task_batches': {'batch': batch}})
    jobs = WorkerJobStore(tmp_path)
    job = jobs.create_job(run_id='attempt', role='research', adapter='pi', task='Inspect documentation', context='', project_key='DEMO')
    root = jobs.root / job['id']
    jobs._write_status(root, job, 'running', 'Fixture process', extra={'pid': os.getpid(), 'heartbeat_at': jobs._now()})
    jobs._write_json(root / 'pi_session.json', {'status': 'running'})
    def observe_and_judge():
        observed = observe_running_batch(store=store, root=tmp_path, project_key='DEMO', value=value)
        def manager(**kwargs):
            assert kwargs['contract']['actions_authorized']
            return {'assessment': 'needs_attention', 'rationale': 'Check whether the original documentation evidence is available.',
                'observed_job_ids': [job['id']], 'follow_up': {'job_id': job['id'], 'message': 'What evidence is available for your original documentation task?'}}
        return judge_running_observation(store=store, root=tmp_path, value=observed, runner=manager, authority_check=lambda: True)
    judged = observe_and_judge()
    key = judged['running_observation']['digest']
    checks = 0
    def authority_check():
        nonlocal checks
        checks += 1
        if cancel_before_enqueue and checks == 1:
            LocalTaskStore(tmp_path, 'DEMO').update_task(task['id'], status='canceled')
        return True
    def consume():
        return consume_worker_follow_up(store=store, root=tmp_path, project_key='DEMO', value=store.read()['project_continuation'], authority_check=authority_check)
    original_send = WorkerJobStore.session_control
    sends = []
    def lost_enqueue_response(self, *args, **kwargs):
        sends.append(args[0])
        original_send(self, *args, **kwargs)
        raise OSError("Enqueue completed but its response was lost")
    monkeypatch.setattr(WorkerJobStore, 'session_control', lost_enqueue_response)
    first = consume()
    receipt = first['running_judgments'][key]['worker_control']
    if cancel_before_enqueue:
        assert not (root / 'pi_control_queue.jsonl').exists() and sends == []
        assert 'canceled' in receipt['error']
        return
    unchanged = observe_and_judge()
    assert unchanged['running_observation']['digest'] == key
    assert unchanged['manager_calls'] == 1

    assert receipt['status'] == 'unconfirmed' and not receipt['worker_reply_attributed'] and not receipt['execution_completed']
    queue_path = root / 'pi_control_queue.jsonl'
    before = queue_path.read_bytes()
    request = json.loads(before)
    assert request['actor'] == 'manager' and request['source'] == 'project_continuation'
    assert request['job_id'] == job['id'] and request['execution_id'] == 'attempt'
    jobs._append_pi_control_ack(root, receipt['control_id'], 'processing')
    processing = consume()
    assert processing['running_judgments'][key]['worker_control']['status'] == 'processing'
    jobs._append_pi_control_ack(root, receipt['control_id'], 'rpc_accepted')
    second = consume()
    assert queue_path.read_bytes() == before
    assert second['running_judgments'][key]['worker_control']['rpc_accepted']
    assert not second['running_judgments'][key]['worker_control']['worker_reply_attributed']
    # A new observation cannot silently send a second question to the same job.
    jobs._write_json(root / 'pi_session.json', {'status': 'running', 'pendingMessageCount': 1})
    next_value = observe_and_judge()
    next_key = next_value['running_observation']['digest']
    assert next_key != key
    consume()
    assert 'worker_control' not in store.read()['project_continuation']['running_judgments'][next_key]
    assert queue_path.read_bytes() == before and sends == [job['id']]
    # Mutating scope never mints more authority.
    store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'], 'discovery': {**discovery, 'worker_follow_up_limit': 3}}})
    with pytest.raises(ValueError, match='scope changed'):
        consume()
    assert queue_path.read_bytes() == before


def test_old_observation_permission_does_not_grant_controls():
    base = {'objective': 'Observe existing tasks without controlling their execution.', 'allowed_write_domains': ['docs'],
            'max_new_tasks': 1, 'max_planning_calls': 2, 'allow_running_observation': True}
    assert 'worker_follow_up_limit' not in normalize_discovery(base)
    for limit in (0, 11, True):
        with pytest.raises(ValueError):
            normalize_discovery({**base, 'worker_follow_up_limit': limit})
    with pytest.raises(ValueError):
        normalize_discovery({**base, 'allow_running_observation': False, 'worker_follow_up_limit': 1})
    from goodteam_os.project_observation_judgment import _validate
    packet = {'workers': [{'job_id': 'job', 'follow_up_supported': True}]}
    result = {'assessment': 'wait', 'rationale': 'Read-only grants cannot send questions.', 'observed_job_ids': ['job'],
              'follow_up': {'job_id': 'job', 'message': 'Please report the current task evidence.'}}
    with pytest.raises(ValueError):
        _validate(result, packet)

    result['follow_up']['job_id'] = 'foreign-job'
    with pytest.raises(ValueError):
        _validate(result, packet, follow_up_allowed=True)
