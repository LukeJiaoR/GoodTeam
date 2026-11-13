"""Explicit Human additions to an existing grant, never a Manager action."""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.goal_continuation import authority_snapshot, goal_guard
from goodteam_os.project_iteration import _internal, rank_project_backlog
from goodteam_os.project_iteration_continuation import _digest, _intent, _scope_tasks
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def add_project_continuation_tasks(*, workspace, project_key, root, project_config, request):
    """Retain one confirmed additive scope receipt under the original locks.

    This does not take the execution lock held throughout active Worker work.
    Original seed identity, discovery authority, budgets and in-flight work stay
    intact. This operation neither dispatches nor accepts any task result.
    """
    if request.get('confirmed') is not True or request.get('billing_acknowledged') is not True:
        raise ValueError('Explicit confirmation of the added tasks and configured model charges is required.')
    ids, key = request.get('task_ids'), request.get('idempotency_key')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 128
            or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)
            or not isinstance(key, str) or not 1 <= len(key) <= 256):
        raise ValueError('Select distinct existing tasks and provide a stable confirmation identity.')
    identity = {name: request.get(name) for name in ('continuation_id', 'task_ids', 'expected_backlog_version')}
    command = _digest(key)
    workspace, root, project_config = Path(workspace), Path(root), Path(project_config)
    store = TaskAssistantSessionStore(workspace, project_key)
    with goal_guard(workspace, project_key):
        def apply(session):
            value = session.get('project_continuation') or {}
            if value.get('id') != identity['continuation_id']:
                raise ValueError('The displayed continuation was replaced; review its current scope.')
            previous = value.get('scope_additions', {}).get(command)
            if previous:
                if previous['confirmation'] != identity:
                    raise ValueError('This confirmation identity already has different task scope.')
                return session  # Replay after Stop is read-only, never renewed authority.
            if value.get('status') != 'active':
                raise ValueError('Only the current active continuation can receive confirmed later tasks.')
            if value.get('workflow_recovery'):
                raise ValueError('Workflow recovery retains its original fixed task scope.')
            if value.get('planning_backlog_version') or value.get('discovery_pending'):
                raise ValueError('Reconcile the original reserved Manager decision before changing its task scope.')
            if (value['max_batches'] is not None and value['batches_started'] >= value['max_batches']
                    or value['max_manager_calls'] is not None and value['manager_calls'] >= value['max_manager_calls']):
                raise ValueError('The existing iteration or Manager allowance is exhausted; no budget is added.')
            if authority_snapshot(workspace, project_key, project_config, root) != value['authority']:
                raise ValueError('The original effective authority changed.')
            if value.get('discovery'):
                from goodteam_os.project_outcome_discovery import _current
                _current(session, value)
            known, _ = _scope_tasks(value, LocalTaskStore(root, project_key).list_tasks())
            if rank_project_backlog(list(known.values()), project_key, limit=None)['version'] != identity['expected_backlog_version']:
                raise ValueError('The displayed task scope changed; review it before confirming.')
            if (len(value['task_ids']) + len(ids) > 128 or set(ids) & set(value['task_ids'])
                    or any(i not in known or _internal(known[i]) or known[i]['status'] != 'todo'
                           or known[i].get('run_id') for i in ids)):
                raise ValueError('Add only available unstarted tasks outside the existing scope (128 total maximum).')
            intents = {i: _intent(known[i]) for i in ids}
            receipt = {'confirmation': deepcopy(identity), 'task_intents': intents,
                       'created_at': datetime.now(UTC).isoformat(), 'actor': 'user'}
            receipt['receipt_id'] = _digest(receipt)
            # Preserve original start-command replay for fixed-scope grants too.
            value.setdefault('seed_task_ids', list(value['task_ids']))
            value.setdefault('seed_task_intents', dict(value['task_intents']))
            value.setdefault('scope_additions', {})[command] = receipt
            value['task_ids'].extend(ids)
            value['task_intents'].update(intents)
            session.setdefault('timeline', []).append({
                'message_id': 'project-scope-addition:' + command, 'actor': 'user',
                'event': 'project_continuation_scope_added', 'created_at': receipt['created_at'],
                'details': {'continuation_id': value['id'], 'receipt': deepcopy(receipt),
                            'budget_increased': False, 'execution_started': False}})
            return session
        store._mutate(apply)
        return store.read()['project_continuation']


def confirmed_added_task(value, task):
    """Attribution to a Human addition, not new read-only feedback permission."""
    for receipt in (value.get('scope_additions') or {}).values():
        confirmation = receipt.get('confirmation') or {}
        if (confirmation.get('continuation_id') == value.get('id')
                and task.get('id') in confirmation.get('task_ids', [])
                and receipt.get('actor') == 'user'
                and receipt.get('receipt_id') == _digest({k: v for k, v in receipt.items() if k != 'receipt_id'})
                and receipt.get('task_intents', {}).get(task['id']) == _intent(task)
                and value.get('task_intents', {}).get(task['id']) == _intent(task)):
            return True
    return False
