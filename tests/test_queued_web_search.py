"""Explicit queue search authority reaches the original Worker, no paid transport."""

import pytest

from goodteam_os.assistant_queue import queue_manager_iteration_choice, require_queued_project_task
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.project_iteration_planner import plan_next_iteration
from goodteam_os.project_iteration_execution import _claim_batch
from goodteam_os.dispatch import _create_track_job
from goodteam_os.tasks import LocalTaskStore, project_task_intent
from goodteam_os.workers import WorkerJobStore
from goodteam_os.web_search import JobWebSearch
from test_web_search import POLICY, PAYLOAD


def test_explicit_search_task_queue_binding_dispatch_receipt_and_replay(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nsource_type: existing_project\nrepos:\n  root: {root}\nworker_backend:\n  mode: pi\n')
    ledger = LocalTaskStore(root, 'DEMO')
    task = ledger.create_task(title='Document public API facts', description='Use public vendor sources only.',
        role='implementation', write_domains=['docs'], acceptance_criteria=['Cite the actual sources.'], web_search=POLICY)
    assert project_task_intent(task)['web_search'] == POLICY
    legacy = {key: value for key, value in task.items() if key != 'web_search'}
    assert 'web_search' not in project_task_intent(legacy)
    from goodteam_os.project_iteration_continuation import _intent
    assert _intent(task) != _intent({**task, 'web_search': {**POLICY, 'max_requests': 1}})
    snapshot = project_iteration_backlog(root, 'DEMO')
    plan_next_iteration(workspace=tmp_path, project_key='DEMO', root=root, project_config=config,
        expected_backlog_version=snapshot['version'], confirmed=True, manager_runner=lambda **_: {
            'selection': {'selected_task_ids': [task['id']], 'compared_task_ids': [task['id']],
                'rationale': 'Document the public API using the explicit existing task allowance.'}})
    queued = queue_manager_iteration_choice(workspace=tmp_path, project_key='DEMO', root=root,
        expected_backlog_version=snapshot['version'])
    proposal = queued['task_assistant_session']['queued_task_proposals'][0]
    assert 'Explicit Web Search allowance' in proposal['exact_request']
    assert 'provider-test' in proposal['exact_request']
    with pytest.raises(ValueError, match='search contract'):
        require_queued_project_task(root, 'DEMO', proposal, workflow=True)
    request = dict(workspace=tmp_path, project_key='DEMO', root=root, project_config=config,
        proposal_ids=queued['queued_proposal_ids'], expected_revisions={proposal['id']: 1}, confirmed=True)
    # A policy edit invalidates the original queue evidence before any claim.
    original = ledger._read()
    ledger.update_task(task['id'], web_search={**POLICY, 'max_requests': 1})
    with pytest.raises(ValueError, match='evidence changed'):
        _claim_batch(**request)
    ledger._write(original)
    batch = _claim_batch(**request)
    assert _claim_batch(**request) == batch
    track = batch['tracks'][0]
    assert track['web_search'] == POLICY and track['execution_policy'] == 'standard'
    assert batch['task_bindings'][0]['task_intent']['web_search'] == POLICY
    workers = WorkerJobStore(root)
    kwargs = dict(run_id=batch['dispatch_run_id'], track=track, constraints=[], project_key='DEMO', command_key='confirmed-search-job')
    job = _create_track_job(workers, **kwargs)
    assert _create_track_job(workers, **kwargs)['id'] == job['id']
    assert job['web_search'] == POLICY and job['project_task_id'] == task['id']
    with pytest.raises(ValueError, match='search authorization changed'):
        _create_track_job(workers, **{**kwargs, 'track': {**track, 'web_search': {**POLICY, 'max_requests': 1}}})
    for change in ({'adapter': 'codex'}, {'execution_policy': 'read_only'}):
        with pytest.raises(ValueError, match='ordinary standard Pi'):
            _create_track_job(workers, **{**kwargs, 'track': {**track, **change}})
    with pytest.raises(ValueError, match='ordinary standard Pi'):
        _create_track_job(workers, **kwargs, method_context={'protected': True})
    with pytest.raises(ValueError, match='ordinary standard Worker'):
        ledger.update_task(task['id'], execution_policy='read_only')
    with pytest.raises(ValueError, match='ordinary standard Worker'):
        ledger.create_task(title='No', description='No', role='research', execution_policy='read_only', web_search=POLICY)
    # Search output is bound to the original task/run/job; no real endpoint call.
    calls = []
    class Providers:
        def resolve(self, ref):
            assert ref == POLICY['provider']
            return {'protocol': 'responses'}
    async def request(*args):
        calls.append(args)
        return PAYLOAD
    host = JobWebSearch(workers.root / job['id'], job, active=lambda: True, provider_store=Providers(), requester=request)
    result = host.call('public vendor API query')
    assert result['job']['project_task_id'] == task['id'] and result['job']['run_id'] == batch['dispatch_run_id']
    assert result['job']['id'] == job['id'] and result['status'] == 'searched'
    assert host.call('public vendor API query') == result and len(calls) == 1
    assert 'https://example.com/evidence' in host.evidence_text()
    host.stop()
    assert host.call('new query')['status'] == 'not_authorized_or_stopped'
    # Existing result publication brings the source into Manager conversation
    # Context. The model cannot promote it to delivery or mutate the allowance.
    from goodteam_os.project_iteration_execution import _publish_batch_result
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.context_continuity import conversation_context
    (workers.root / job['id'] / 'result.md').write_text('Public API notes.' + host.evidence_text())
    report = {'jobs': [{'job_id': job['id'], 'role': job['role'], 'status': 'completed',
        'project_task_id': task['id'], 'dispatch_run_id': batch['dispatch_run_id']}]}
    _publish_batch_result(tmp_path, 'DEMO', root, batch, report, 'review')
    _publish_batch_result(tmp_path, 'DEMO', root, batch, report, 'review')
    session = TaskAssistantSessionStore(tmp_path, 'DEMO').read()
    events = [event for event in session['timeline'] if event['message_id'].startswith('batch-result:')]
    assert len(events) == 1 and 'https://example.com/evidence' in events[0]['content']
    assert events[0]['details']['source_evidence']['worker_results'][0]['project_task_id'] == task['id']
    assert 'https://example.com/evidence' in str(conversation_context(session, query='public API evidence'))
    assert ledger.get_task(task['id'])['status'] == 'todo'  # no delivery acceptance
    # Real method capture for this legacy project remains ordinary. A protected
    # context is rejected by dispatch above, never discarded to get search.
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.task_locks import TaskSkillIdentity
    assert capture_task_start(workspace=tmp_path, project_key='DEMO', worker_root=root,
        task=TaskSkillIdentity('direct_task', 'DEMO', batch['run_id']), command_key='queued-search-capture',
        subjects=('role:implementation',), confirmed_input={'tracks': [track]}) == {}
