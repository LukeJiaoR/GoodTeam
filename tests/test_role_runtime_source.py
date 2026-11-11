"""Explicit configuration reuse reaches original Worker jobs, not a new pool."""
from copy import deepcopy

import pytest

from goodteam_os import graph
from goodteam_os.goal_continuation import authority_snapshot
from goodteam_os.manager_execution import _parse_team_commands
from goodteam_os.team_control_plane import (
    TEAM_STATE_TARGET_KEY, TeamControlPlaneStore, TeamDispatchBlocked,
    build_team_capability_view, build_team_execution_snapshot, ensure_team_state, execute_team_commands,
    resolve_worker_from_state,
)
from goodteam_os.workers import WorkerJobStore


def test_role_uses_explicit_runtime_source_without_inheriting_methods(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n')
    team = ensure_team_state(tmp_path, 'DEMO', config)
    def command(*items):
        execute_team_commands(tmp_path, 'DEMO', list(items), actor='user', surface='team_ui', config_path=config)
        return TeamControlPlaneStore(tmp_path, 'DEMO').read()
    team = command({'kind': 'change_worker_runtime', 'worker_id': 'research-a',
                    'model': 'fixture-runtime', 'additional_prompt': 'RESEARCH_PRIVATE_METHOD'})
    # Release target Role members: expressing a Planner task no longer requires
    # a permanently rostered Planner when a source was explicitly selected.
    for worker in team['workers']:
        if worker['role'] == 'planner' and worker['lifecycle'] == 'active':
            team = command({'kind': 'inactivate_worker', 'worker_id': worker['id']})
    with pytest.raises(TeamDispatchBlocked):
        resolve_worker_from_state(team, 'planner')
    before = authority_snapshot(tmp_path, 'DEMO', config, root)
    selection = {'kind': 'set_role_runtime_source', 'role': 'planner', 'worker_id': 'research-a'}
    assert _parse_team_commands([selection]) == [selection]
    team = command(selection)
    assert authority_snapshot(tmp_path, 'DEMO', config, root) != before
    assert build_team_execution_snapshot(team)['roles']['planner']['runtime_source_worker_id'] == 'research-a'
    original = deepcopy(team['skill_bindings'])
    fields = graph._worker_job_fields({TEAM_STATE_TARGET_KEY: team}, 'planner', None,
                                     model_fallback=lambda: 'WRONG_FALLBACK')
    assert fields['model'] == 'fixture-runtime' and fields['desired_instances'] == 1
    assert fields['worker_id'] == 'research-a' and fields['additional_prompt'] == ''
    workers = WorkerJobStore(root)
    job = workers.create_job(run_id='role-source', role='planner', task='Plan the next bounded increment',
        context='Planner private context', execution_policy='read_only', **fields)
    assert job['role'] == 'planner' and job['worker_id'] == 'research-a'
    assert workers.run_job(job['id'])['status'] == 'completed'
    assert team['skill_bindings'] == original
    team = command({'kind': 'set_role_runtime_source', 'role': 'architect', 'worker_id': 'research-a'})
    second_fields = graph._worker_job_fields({TEAM_STATE_TARGET_KEY: team}, 'architect', None,
                                            model_fallback=lambda: 'WRONG_FALLBACK')
    second = workers.create_job(run_id='role-source', role='architect', task='Assess the architecture',
        context='Architect private context', execution_policy='read_only', **second_fields)
    assert second['id'] != job['id'] and second['role'] == 'architect'
    assert second['worker_id'] == job['worker_id'] == 'research-a'
    assert second_fields['desired_instances'] == fields['desired_instances'] == 1
    assert (workers.root / job['id'] / 'context.md').read_text() == 'Planner private context'
    assert (workers.root / second['id'] / 'context.md').read_text() == 'Architect private context'
    assert team['skill_bindings'] == original  # both Roles retain their own method subjects
    assert 'RESEARCH_PRIVATE_METHOD' not in second['additional_prompt']
    def capability(current):
        return next(row for row in build_team_capability_view(current)['roles'] if row['role'] == 'planner')['capability']
    assert capability(team) == 'ready'
    from goodteam_os.models import _parse_simple_yaml
    projected = _parse_simple_yaml(config)
    assert projected['role_model_overrides']['planner'] == 'fake:fixture-runtime'
    assert projected['worker_type_settings']['planner'].get('additional_prompt', '') == ''
    missing_model = deepcopy(team)
    next(w for w in missing_model['workers'] if w['id'] == 'research-a')['runtime']['model'] = ''
    deleted = deepcopy(team)
    next(w for w in deleted['workers'] if w['id'] == 'research-a')['lifecycle'] = 'deleted'
    absent = deepcopy(team)
    absent['workers'] = [w for w in absent['workers'] if w['id'] != 'research-a']
    for unavailable in (missing_model, deleted, absent):
        assert capability(unavailable) == 'degraded'
        with pytest.raises(TeamDispatchBlocked, match='no fallback'):
            resolve_worker_from_state(unavailable, 'planner')
    # Explicit legacy member assignment stays strict instead of silently
    # relabelling a named Researcher as Planner.
    with pytest.raises(TeamDispatchBlocked, match='does not provide'):
        resolve_worker_from_state(team, 'planner', worker_id='research-a')
    team = command({'kind': 'disable_role', 'role': 'planner'})
    with pytest.raises(TeamDispatchBlocked, match='disabled'):
        resolve_worker_from_state(team, 'planner')
    team = command({'kind': 'enable_role', 'role': 'planner'},
                   {'kind': 'inactivate_worker', 'worker_id': 'research-a'})
    with pytest.raises(TeamDispatchBlocked, match='no fallback'):
        resolve_worker_from_state(team, 'planner')
    # Removing a choice is explicit and restores old behavior, not another pool.
    team = command({'kind': 'set_role_runtime_source', 'role': 'planner', 'worker_id': ''})
    assert 'runtime_source_worker_id' not in team['roles']['planner']
