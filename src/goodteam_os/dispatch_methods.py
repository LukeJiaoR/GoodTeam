"""[INPUT]: 已确认轨道、Team 绑定与原派工凭据。
[OUTPUT]: 每条轨道的任务锁与运行模式。
[POS]: 把普通 Codex 和 Pi 轨道接入原有批次派工。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from pathlib import Path

from goodteam_os.skills.models import canonical_json, content_digest, SkillConflict
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.pi_methods import ORDINARY_METHODS, READONLY_CODEX_METHODS, RoleMethodInput


SCHEMA = 'dispatch_track_methods.v1'


def ordinary(track):
    return (track.get('adapter') in {'pi', 'pi-rpc'}
            and track.get('execution_policy', 'standard') == 'standard'
            and track.get('role') != 'code_review' and not track.get('review_target'))


def readonly(track):
    return (track.get('adapter') in {'pi', 'pi-rpc'}
            and track.get('execution_policy') == 'read_only'
            and track.get('role') in {'research', 'planner', 'qa'}
            and not track.get('review_target'))


def readonly_codex(track):
    return (track.get('adapter') == 'codex' and track.get('execution_policy') == 'read_only'
            and track.get('role') == 'qa' and not track.get('review_target'))


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
    retry_methods = any(t.get('retry_method_mode') in ORDINARY_METHODS for t in tracks)
    if not scope or (not retry_methods and not any(readonly(t) or readonly_codex(t) for t in tracks)
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
            if track.get('retry_method_mode') in ORDINARY_METHODS:
                entries.append({'track_id': tid, 'mode': 'retry', 'defaults': False})
                continue
            custom = team['skill_bindings'][subject_for_role(str(track['role']))]['mode'] == 'custom'
            defaults = (ordinary(track) or readonly(track)) and track.get('role') in ROLE_PACKAGES
            if custom and readonly_codex(track):
                mode = 'readonly_codex'
            elif (custom or defaults) and (ordinary(track) or readonly(track)):
                mode = 'readonly' if readonly(track) else 'ordinary'
            else:
                mode = 'strict' if custom else 'legacy'
            entries.append({'track_id': tid, 'mode': mode, 'defaults': defaults})
        return {'schema_version': 'dispatch_track_methods.v3' if retry_methods or any(map(readonly_codex, tracks))
                else 'dispatch_track_methods.v2' if any(map(readonly, tracks)) else SCHEMA,
                'team_revision': team['revision'], 'tracks': entries}


def capture_plan(plan, *, scope, root, logical_id, tracks, confirmed_input, parent_report=None):
    from goodteam_os.skills.task_start import capture_task_start
    from goodteam_os.skills.role_packages import ROLE_METHODS_VERSION
    from goodteam_os.skills.worker_requests import subject_for_role
    from goodteam_os.skills.task_context import task_skill_context
    from goodteam_os.workers import WorkerJobStore

    if (not isinstance(plan, dict) or plan.get('schema_version') not in {SCHEMA, 'dispatch_track_methods.v2', 'dispatch_track_methods.v3'}
            or type(plan.get('team_revision')) is not int or not isinstance(plan.get('tracks'), list)
            or len(plan['tracks']) != len(tracks)):
        raise ValueError('Original per-track method route is unavailable.')
    workspace, key = scope
    result = []
    for track, route in zip(tracks, plan['tracks']):
        tid = str(track.get('track_id') or track['role'])
        if (route.get('track_id') != tid or route.get('mode') not in {'ordinary', 'readonly', 'readonly_codex', 'strict', 'legacy', 'retry'}
                or type(route.get('defaults')) is not bool
                or route['mode'] == 'ordinary' and not ordinary(track)
                or route['mode'] == 'readonly' and (not readonly(track) or plan['schema_version'] == SCHEMA)
                or route['mode'] == 'readonly_codex' and (not readonly_codex(track) or plan['schema_version'] != 'dispatch_track_methods.v3')
                or route['defaults'] and route['mode'] not in {'ordinary', 'readonly'}):
            raise ValueError('Original per-track method route changed.')
        if route['mode'] == 'retry':
            old_id = track.get('original_failed_job_id')
            mode = track.get('retry_method_mode')
            old_row = next((row for row in (parent_report or {}).get('jobs', ())
                            if row['job_id'] == old_id and row.get('project_task_id') == track.get('project_task_id')), None)
            if old_row is None or plan['schema_version'] != 'dispatch_track_methods.v3':
                raise ValueError('Method retry requires its original failed dispatch.')
            original = RoleMethodInput(WorkerJobStore(root), old_id, prepare=False)
            if (mode not in ORDINARY_METHODS or original.request['method_loading_mode'] != mode
                    or original.request.get('provider_connection') != track.get('provider_connection')
                    or original.request['execution_policy'] != track.get('execution_policy', 'standard')
                    or any(original.request.get(field) != track.get(field) for field in ('role', 'adapter'))):
                raise ValueError('Method retry changed its original Runtime or Provider reference.')
            _, snapshot = original._identity()
            result.append((task_skill_context(TaskSkillIdentity(**snapshot['task']), snapshot['task_lock_ref']), mode))
            continue
        mode = {'readonly': 'readonly_pi_methods.v1', 'ordinary': 'ordinary_pi_methods.v1',
                'readonly_codex': READONLY_CODEX_METHODS}.get(route['mode'], '')
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
