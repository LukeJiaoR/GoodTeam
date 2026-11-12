"""Views over the original task/batch ledgers, not another execution queue."""
from goodteam_os.project_iteration import rank_project_backlog
from goodteam_os.project_iteration_execution import _require_execution_slot


def pending_batches(session, value, tasks):
    """Reconstruct outstanding batches after a restart or cursor change."""
    from goodteam_os.project_batch_feedback import valid_receipt
    from goodteam_os.project_investigation_feedback import _valid_receipt
    result = []
    observed = {r['batch_run_id'] for r in (value.get('batch_observations') or {}).values() if valid_receipt(value, r)}
    for batch in session.get('project_task_batches', {}).values():
        if batch.get('expected_continuation_id') != value.get('id') or batch.get('status') in {'delivered', 'abandoned'}:
            continue
        ids = batch.get('task_ids') or []
        if not ids or batch['run_id'] in observed:
            continue
        if all(tasks.get(i, {}).get('status') == 'done' and tasks[i].get('delivery_verified') is True for i in ids):
            continue
        if all(_valid_receipt(value, (value.get('investigation_observations') or {}).get(i, {}))
               and value['investigation_observations'][i].get('batch_run_id') == batch['run_id'] for i in ids):
            continue
        result.append(batch)
    return sorted(result, key=lambda b: (b.get('status') not in {'review', 'blocked'}, b.get('created_at', ''), b['run_id']))


def select_pending(session, value, tasks):
    original = value.get('pending_batch')
    # An unacknowledged reservation owns its identity until reconciled.
    if value.get("planning_backlog_version") or value.get("discovery_pending"):
        return original
    if original and (not original.get('run_id') or original['run_id'] not in session.get('project_task_batches', {})):
        return original
    batches = pending_batches(session, value, tasks)
    if not batches:
        return original
    # Rotate the observation cursor across original outstanding identities.
    # One long-running or unaccepted batch must not hide a sibling's result.
    position = next((i for i, batch in enumerate(batches)
                     if batch['run_id'] == (original or {}).get('run_id')), -1)
    chosen = batches[(position + 1) % len(batches)]
    if not all(key in chosen for key in ('run_id', 'task_ids', 'proposal_ids', 'expected_revisions')):
        return original
    return {key: chosen[key] for key in ('run_id', 'task_ids', 'proposal_ids', 'expected_revisions')}


def ready_parallel_tasks(workspace, project_key, root, session, value, tasks):
    if (value.get('workflow_recovery') or value['batches_started'] >= value.get('max_batches', value['batches_started'])
            or value['manager_calls'] >= value['max_manager_calls']):
        return []
    from goodteam_os.dispatch_capacity import available_dispatch_slots
    if available_dispatch_slots(root) < 1:
        return []
    occupied = {i for b in pending_batches(session, value, tasks) for i in b.get('task_ids', [])}
    ready = []
    for task in rank_project_backlog(list(tasks.values()), project_key, limit=None)['candidates']:
        identifier = task['task_id']
        if not task['eligible_to_plan'] or identifier not in value['task_ids'] or identifier in occupied:
            continue
        try:
            _require_execution_slot(workspace, project_key, 'next-unclaimed-batch', continuation_id=value['id'], task_ids=[identifier])
        except ValueError:
            continue
        ready.append(identifier)
    return ready
