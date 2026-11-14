"""Shared project Worker capacity; original jobs remain the execution ledger.

The short resource claim records only occupancy. It does not dispatch, retry,
accept results, infer termination from process locks or change job state.
"""
from pathlib import Path
import time

from goodteam_os.dispatch_workspace import dispatch_artifact_root, dispatch_job_terminal
from goodteam_os.memory_storage import atomic_write, read_bytes, role_lock
from goodteam_os.parallel_capacity import DEFAULT_MAX_CONCURRENT_WORKERS
from goodteam_os.skills.models import canonical_json
import json


class DispatchCapacityUnknown(ValueError):
    """The same invocation already owns a slot with no verified termination."""


def _location(project_root):
    return dispatch_artifact_root(Path(project_root), 'shared-worker-capacity')


def _identity(job):
    required = ('id', 'run_id', 'role', 'adapter')
    if any(not isinstance(job.get(key), str) or not job[key] for key in required):
        raise ValueError('Shared capacity requires the original invocation identity.')
    return {'job_id': job['id'], 'dispatch_run_id': job['run_id'],
            'role': job['role'], 'adapter': job['adapter']}


def _read(location):
    raw = read_bytes(location / 'occupancy.json')
    if raw is None:
        return {}
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('schema_version') != 'dispatch_capacity.v1'
            or not isinstance(value.get('slots'), dict)
            or any(key not in {str(i) for i in range(DEFAULT_MAX_CONCURRENT_WORKERS)}
                   or not isinstance(row, dict) or set(row) != {'job_id', 'dispatch_run_id', 'role', 'adapter'}
                   for key, row in value['slots'].items())):
        raise ValueError('The retained Worker resource occupancy is invalid.')
    return value['slots']


def _not_started(project_root, row):
    """A rejected/queued invocation owns no running process or capacity.

    This is occupancy evidence only, never successful delivery or permission to
    replay a task. Started attempts still require the original terminal proof.
    """
    root = Path(project_root) / '.goodteam' / 'workers' / 'jobs' / row['job_id']
    raw = read_bytes(root / 'job.json')
    if raw is None:
        return False
    job = json.loads(raw)
    if not isinstance(job, dict) or _identity(job) != row:
        return False
    raw_status = read_bytes(root / 'status.json')
    status = json.loads(raw_status) if raw_status is not None else {}
    if not isinstance(status, dict):
        return False
    if status and (status.get('job_id') != row['job_id']
                   or status.get('run_id') != row['dispatch_run_id']):
        return False
    if (status and status.get('status') != 'queued'
            and (status.get('invocation_evidence') or {}).get('external_invocation') is not False):
        return False
    records = (job, status)
    return not any(record.get('started_at') or record.get('pid') is not None
                   or record.get('status') == 'running' or record.get('process_alive') is True
                   or (record.get('invocation_evidence') or {}).get('external_invocation') is True
                   for record in records)


def _terminal(project_root, row):
    try:
        dispatch_job_terminal(Path(project_root), row)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _retained(project_root, slots):
    return {key: row for key, row in slots.items() if not _terminal(project_root, row)}


def _unmanaged_running(project_root, slots):
    """Count ordinary dispatch and Workflow jobs covered by this capacity pool."""
    managed = {row['job_id'] for row in slots.values()}
    count = 0
    root = Path(project_root) / '.goodteam' / 'workers' / 'jobs'
    if not root.exists():
        return 0
    for directory in root.iterdir():
        raw = read_bytes(directory / 'job.json')
        if raw is None:
            continue
        job = json.loads(raw)
        if not isinstance(job, dict) or job.get('id') in managed:
            continue
        # Manager and protected Reviewer transports have different completion
        # receipts and are not admitted by the ordinary Worker capacity pool.
        # Do not reinterpret their retained history as an unretired Worker.
        context = job.get('sandbox_context') or {}
        ordinary_workflow = (job.get('workflow_workspace') is not None
                             or isinstance(context, dict) and context.get('mode') == 'workflow_isolated')
        ordinary_dispatch = str(job.get('run_id', '')).startswith('dispatch-')
        if job.get('role') == 'code_review' or not (ordinary_dispatch or ordinary_workflow):
            continue
        raw_status = read_bytes(directory / 'status.json')
        status = json.loads(raw_status) if raw_status is not None else {}
        # Historical timeout/session failures can be labeled failed while no
        # observed exit exists. A started/attempted invocation is still capacity
        # until the same original terminal verifier proves retirement.
        attempted = any(record.get('started_at') or record.get('pid') is not None
                        or (record.get('invocation_evidence') or {}).get('external_invocation') is True
                        for record in (job, status))
        if (attempted or job.get('status') == 'running' or status.get('status') == 'running'
                or status.get('process_alive') is True):
            if not _terminal(project_root, _identity(job)):
                count += 1
    return count


def _save(location, slots):
    atomic_write(location / 'occupancy.json', canonical_json(
        {'schema_version': 'dispatch_capacity.v1', 'slots': slots}).encode('utf-8'))


def available_dispatch_slots(project_root):
    """Advisory only, no claim/cleanup; unknown invocations continue to occupy."""
    location = _location(project_root)
    with role_lock(location):
        slots = _retained(project_root, _read(location))
        return max(0, DEFAULT_MAX_CONCURRENT_WORKERS - len(slots) - _unmanaged_running(project_root, slots))


def run_with_project_capacity(project_root, job, operation, *, check_start=None, poll_seconds=.05):
    """Wait for one shared slot, then invoke the original operation once.

    check_start must be a read-only current authorization check. It is called
    while waiting and immediately before invoking; it may reject a late Stop.
    No caller may treat available_dispatch_slots as an execution reservation.
    """
    identity = _identity(job)
    location = _location(project_root)
    while True:
        if check_start is not None:
            check_start()
        with role_lock(location):
            slots = _retained(project_root, _read(location))
            if any(row['job_id'] == identity['job_id'] for row in slots.values()):
                raise DispatchCapacityUnknown('This original Worker invocation already occupies capacity; reconcile it before retrying.')
            available = (next((str(i) for i in range(DEFAULT_MAX_CONCURRENT_WORKERS) if str(i) not in slots), None)
                         if len(slots) + _unmanaged_running(project_root, slots) < DEFAULT_MAX_CONCURRENT_WORKERS else None)
            if available is not None:
                slots[available] = identity
                _save(location, slots)
                break
        time.sleep(poll_seconds)
    invoked = False
    try:
        if check_start is not None:
            check_start()
        invoked = True
        return operation()
    finally:
        # A host exception/crash is not proof the Worker and descendants ended.
        # The original terminal/retirement evidence is the recovery authority.
        try:
            unstarted = _not_started(project_root, identity)
        except (OSError, ValueError, KeyError, TypeError):
            unstarted = False
        if not invoked or unstarted or _terminal(project_root, identity):
            with role_lock(location):
                slots = _read(location)
                if slots.get(available) == identity:
                    del slots[available]
                    _save(location, slots)
