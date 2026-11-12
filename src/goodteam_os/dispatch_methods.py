"""Per-track method capture for new mixed batches, using original Skill stores."""
from pathlib import Path

from goodteam_os.skills.models import canonical_json, content_digest, SkillConflict
from goodteam_os.skills.task_locks import TaskSkillIdentity


SCHEMA = 'dispatch_track_methods.v1'


def ordinary(track):
    return (track.get('adapter') in {'pi', 'pi-rpc'}
            and track.get('execution_policy', 'standard') == 'standard'
            and track.get('role') != 'code_review' and not track.get('review_target'))


def readonly(track):
    return (track.get('adapter') in {'pi', 'pi-rpc'}
            and track.get('execution_policy') == 'read_only'
            and track.get('role') in {'research', 'planner'}
            and not track.get('review_target'))


def identity(logical_id, track_id):
    return 'dispatch-track:' + content_digest(canonical_json([logical_id, track_id]).encode())[7:]


def new_plan(scope, root, logical_id, tracks):
    from goodteam_os.service import service_state_dir
    from goodteam_os.skills.task_service import ProjectTaskSkillService
    from goodteam_os.skills.role_packages import ROLE_PACKAGES
    from goodteam_os.skills.worker_requests import subject_for_role
    from goodteam_os.team_control_plane import TeamControlPlaneStore
    from goodteam_os.workers import WorkerJobStore

    homogeneous_defaults = all(ordinary(t) and t.get('role') in ROLE_PACKAGES for t in tracks)
    if not scope or (not any(map(readonly, tracks))
                     and (not any(map(ordinary, tracks)) or homogeneous_defaults)):
        return None
    workspace, key = scope
    ids = [str(t.get('track_id') or t['role']) for t in tracks]
    if len(set(ids)) != len(ids):
        raise ValueError('Dispatch requires distinct track identities.')
    jobs = WorkerJobStore(root)
    if any(jobs.find_job_by_idempotency_key(f'direct-job:{key}:{logical_id}:{i}') for i in range(len(tracks))):
        return None
    if (Path(service_state_dir()) / 'skills/task-locks').exists():
        service = ProjectTaskSkillService(workspace, key, worker_roots=(root,))
        if service.locks.find_task(task=TaskSkillIdentity('direct_task', key, logical_id),
                                   command_key=f'direct-start:{key}:{logical_id}'):
            return None  # Historical shared captures must retain their old route.
        for tid in ids:
            child = identity(logical_id, tid)
            if service.locks.find_task(task=TaskSkillIdentity('direct_task', key, child),
                                       command_key=f'direct-start:{key}:{child}'):
                raise SkillConflict('operation_conflict', 'Mixed dispatch original route receipt is missing.')
    store = TeamControlPlaneStore(workspace, key)
    with store._locked():
        team = store.read()
        if team is None:
            return None
        entries = []
        for tid, track in zip(ids, tracks):
            custom = team['skill_bindings'][subject_for_role(str(track['role']))]['mode'] == 'custom'
            defaults = (ordinary(track) or readonly(track)) and track.get('role') in ROLE_PACKAGES
            mode = ('readonly' if readonly(track) else 'ordinary') if (ordinary(track) or readonly(track)) and (custom or defaults) else 'strict' if custom else 'legacy'
            entries.append({'track_id': tid, 'mode': mode, 'defaults': defaults})
        return {'schema_version': 'dispatch_track_methods.v2' if any(map(readonly, tracks)) else SCHEMA,
                'team_revision': team['revision'], 'tracks': entries}


def capture_plan(plan, *, scope, root, logical_id, tracks, confirmed_input):
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.role_packages import ROLE_METHODS_VERSION
    from goodteam_os.skills.worker_requests import subject_for_role

    if (not isinstance(plan, dict) or plan.get('schema_version') not in {SCHEMA, 'dispatch_track_methods.v2'}
            or type(plan.get('team_revision')) is not int or not isinstance(plan.get('tracks'), list)
            or len(plan['tracks']) != len(tracks)):
        raise ValueError('Original per-track method route is unavailable.')
    workspace, key = scope
    result = []
    for track, route in zip(tracks, plan['tracks']):
        tid = str(track.get('track_id') or track['role'])
        if (route.get('track_id') != tid or route.get('mode') not in {'ordinary', 'readonly', 'strict', 'legacy'}
                or type(route.get('defaults')) is not bool
                or route['mode'] == 'ordinary' and not ordinary(track)
                or route['mode'] == 'readonly' and (not readonly(track) or plan['schema_version'] != 'dispatch_track_methods.v2')
                or route['defaults'] and route['mode'] not in {'ordinary', 'readonly'}):
            raise ValueError('Original per-track method route changed.')
        mode = 'readonly_pi_methods.v1' if route['mode'] == 'readonly' else 'ordinary_pi_methods.v1' if route['mode'] == 'ordinary' else ''
        context = {}
        if route['mode'] != 'legacy':
            child = identity(logical_id, tid)
            context = capture_task_start(workspace=workspace, project_key=key, worker_root=root,
                task=TaskSkillIdentity('direct_task', key, child), command_key=f'direct-start:{key}:{child}',
                subjects=(subject_for_role(str(track['role'])),), expected_team_revision=plan['team_revision'],
                default_role_methods=route['defaults'],
                confirmed_input={**confirmed_input, 'track_route': route, 'batch_task_id': logical_id,
                    **({'method_loading_mode': mode} if mode else {}),
                    **({'default_role_methods': ROLE_METHODS_VERSION} if route['defaults'] else {})})
            if not context:
                raise ValueError('Captured method track cannot become legacy.')
        result.append((context, mode))
    return result
