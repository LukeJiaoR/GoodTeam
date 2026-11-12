"""Real grant/ledger/source/discovery; Git verification and Manager are fixtures."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from goodteam_os import dispatch_workspace
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_iteration_planner as planner
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.project_iteration import project_iteration_backlog
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore, project_task_intent


def scenario(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    ledger = LocalTaskStore(root, 'DEMO')
    tasks = [ledger.create_task(title=f'Document endpoint {i}', description=f'Document approved endpoint {i}.',
        role='implementation', write_domains=[f'docs/{i}'], acceptance_criteria=['Endpoint is documented.']) for i in range(2)]
    grant = continuation.configure_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
        project_config=config, request=dict(confirmed=True, billing_acknowledged=True, idempotency_key='drain',
            task_ids=[task['id'] for task in tasks], max_batches=3,
            expected_backlog_version=project_iteration_backlog(root, 'DEMO')['version'], discovery=dict(
                objective='Improve the approved endpoint documentation.', allowed_write_domains=['docs'],
                max_new_tasks=2, max_planning_calls=5, allow_backlog_judgment=True)))
    store = TaskAssistantSessionStore(tmp_path, 'DEMO')
    batches = {}
    for i, task in enumerate(tasks):
        run = f'batch-{i}'
        receipt = dict(producer='human_confirmed_candidate_delivery', verification='landed_git_ancestor_and_content',
            run_id=run, reviewed=True, candidate_commit=f'candidate-{i}', landed_commit=f'landed-{i}', dispatch_run_id=f'dispatch-{i}')
        receipt['receipt_id'] = discovery._digest(receipt)
        batches[run] = dict(run_id=run, created_at=f'2026-11-12T0{i}:00:00Z', status='review', project_root=str(root),
            expected_continuation_id=grant['id'], task_ids=[task['id']], delivery_receipt=receipt,
            task_bindings=[dict(task_id=task['id'], task_intent=project_task_intent(task))])
        ledger.update_task(task['id'], status='review', run_id=run)
    store._mutate(lambda s: {**s, 'project_task_batches': deepcopy(batches)})
    monkeypatch.setattr('goodteam_os.project_iteration_recovery.project_batch_recovery_evidence',
        lambda **k: {'unknown_task_ids': []})
    monkeypatch.setattr(discovery, '_head', lambda _: 'current-head')
    monkeypatch.setattr(dispatch_workspace, '_git', lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(continuation, '_verify_landed_batch', lambda **k: (
        dict(run_id=f"dispatch-{k['run_id'][-1]}", integration={'fixture': 'not live Git verification'}), None))
    monkeypatch.setattr(discovery, '_remember', lambda *a: None)
    monkeypatch.setattr(planner, '_project_evidence', lambda *a, **k: {'fixture': 'no live inspection'})
    def deliver(i):
        batch = batches[f'batch-{i}']
        ledger.update_task(tasks[i]['id'], status='done', delivery_verified=True, delivery_receipt=batch['delivery_receipt'])
        continuation._record_delivery(store, batch['run_id'], batch['delivery_receipt'], batch['task_ids'])
    calls = []
    def manager(**kwargs):
        calls.append(deepcopy(kwargs['context']['verified_outcome']))
        return {'selection': {'new_task': None, 'rationale': 'No additional task is justified by this verified outcome.'}}
    def tick():
        return continuation.tick_project_continuation(workspace=tmp_path, project_key='DEMO', root=root,
            project_config=config, manager_runner=manager,
            batch_runner=lambda **_: pytest.fail('No additional Worker may run.'))
    return root, store, batches, deliver, calls, tick


@pytest.mark.parametrize('late', [False, True])
def test_each_authorized_delivery_reaches_original_manager_once(tmp_path, monkeypatch, late):
    root, store, batches, deliver, calls, tick = scenario(tmp_path, monkeypatch)
    deliver(1)
    if not late:
        deliver(0)
    first = tick()
    assert len(calls) == 1 and calls[0]['batch_run_id'] == 'batch-1', first.get('reason')
    assert first['status'] == 'active'
    if late:
        deliver(0)
    second = tick()
    assert [row['batch_run_id'] for row in calls] == ['batch-1', 'batch-0']
    assert second['status'] == 'completed' and second['manager_calls'] == 2
    assert all(b['delivery_receipt']['receipt_id'] in second['discovery_consumed'] for b in batches.values())
    assert tick()['manager_calls'] == 2 and len(calls) == 2
    kwargs = dict(workspace=tmp_path, project_key='DEMO', root=root)
    assert discovery.verified_outcome(**kwargs)['batch_run_id'] == 'batch-1'
    assert discovery.verified_outcome(**kwargs, value=second, run_id='batch-0')['batch_run_id'] == 'batch-0'
