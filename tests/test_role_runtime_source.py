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


def test_role_uses_explicit_runtime_source_without_inheriting_methods(tmp_path, monkeypatch):
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
    # The ordinary discussion entry must use that same callable capability,
    # rather than requiring a fixed Planner member again at queue publication.
    from goodteam_os.task_assistant import TaskAssistantService
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.tasks import LocalTaskStore
    from goodteam_os.manager_assignment import queue_assignment

    action = {'kind': 'queue_task', 'role': 'planner', 'execution_policy': 'read_only',
              'write_domains': [], 'acceptance_criteria': ['Return a bounded next-step plan.'],
              'delegation': {'purpose': 'Choose the next increment.', 'context': 'Only the supplied project facts.',
                             'scope': 'Plan only; do not implement.'}}
    calls = []
    def manager(**kwargs):
        calls.append(kwargs)
        return {'intent': 'work_request', 'response': 'Queueing the requested planning task.',
                'task_intake': {'ready': True, 'missing_decisions': [], 'source_message_ids': ['plan-one']},
                'task_action': action}
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=manager)
    monkeypatch.setattr(service, '_readiness_state', lambda *_: {'project_mode': 'existing_project', 'lifecycle': 'ready'})
    monkeypatch.setattr(service, 'session', lambda *_: TaskAssistantSessionStore(tmp_path, 'DEMO').read())
    request = dict(project_key='DEMO', root=root, message_id='plan-one', text='Plan the next bounded increment.')
    queued_result = service.send_message(**request)
    queued = queued_result['session']['queued_task_proposals']
    assert len(queued) == 1 and not queued[0]['confirmed']
    task = LocalTaskStore(root, 'DEMO').get_task(queued[0]['project_task_id'])
    assert task['role'] == 'planner' and task['execution_policy'] == 'read_only'
    assert task['delegation']['scope'] == action['delegation']['scope']
    assert 'worker_id' not in task and not queued_result['workflow_started']
    service.send_message(**request)
    assert len(calls) == 1
    # Continuing Manager decisions must see the same configured capability as
    # ordinary chat. This is a packet/selection fixture, not accepted delivery.
    from goodteam_os.project_outcome_discovery import _packet, validate_discovered_task
    grant = dict(id='fixture-grant', authorization_digest='fixture-authority',
                 discovery=dict(objective='Plan the next bounded increment.', allow_planning=True,
                                allowed_write_domains=[], max_new_tasks=2),
                 max_manager_calls=3, manager_calls=0, max_batches=3, batches_started=0,
                 new_tasks_created=0, task_ids=[])
    def discovery_packet():
        return _packet(tmp_path, 'DEMO', root, grant, None, project_config=config,
                       backlog_source={'receipt_id': 'fixture-backlog'})
    proposal = dict(new_task=dict(title='Plan the next increment',
        description='Plan the next bounded increment against supplied repository facts.',
        acceptance_criteria=['Return bounded next steps with unresolved questions.'],
        write_domains=[], dependencies=[], source_refs=['fixture-backlog'], task_type='planning'),
        rationale='The existing authorized scope needs a bounded plan before implementation.')
    packet = discovery_packet()
    assert packet['planning_capability']['dispatchable'] is True
    assert validate_discovered_task(packet, proposal)['new_task']['task_type'] == 'planning'
    assert 'worker_capabilities' not in packet
    assert not packet['discovery'].get('allow_investigation')
    with pytest.raises(ValueError):
        validate_discovered_task(packet, {**proposal, 'new_task': {**proposal['new_task'], 'task_type': 'investigation'}})
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
    # Artifact routing must inspect the explicitly selected backend, not a
    # permanently rostered member (which is still fake for Architect here).
    # This exercises only routing; no Codex/Pi process or model is started.
    routing = deepcopy(team)
    source = next(w for w in routing['workers'] if w['id'] == 'research-a')
    source['runtime']['provider'] = 'codex'
    with monkeypatch.context() as patch:
        patch.setattr('goodteam_os.team_control_plane.worker_backend_doctor',
                      lambda provider: {'ready': True})
        patch.setattr(graph, 'load_project_target', lambda path: {TEAM_STATE_TARGET_KEY: routing})
        assert graph.resolve_role_adapter({TEAM_STATE_TARGET_KEY: routing}, 'architect') == 'codex'
        assert graph.should_generate_artifact_with_worker(config, 'architect') is True
        source['runtime']['provider'] = 'fake'
        for member in routing['workers']:
            if member['role'] == 'architect':
                member['runtime']['provider'] = 'codex'
        assert graph.should_generate_artifact_with_worker(config, 'architect') is False
        source['lifecycle'] = 'inactive'
        with pytest.raises(TeamDispatchBlocked, match='no fallback'):
            graph.should_generate_artifact_with_worker(config, 'architect')
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
    with pytest.raises(ValueError, match='capability is unavailable'):
        queue_assignment(service, store=TaskAssistantSessionStore(tmp_path, 'DEMO'), root=root,
                         message_id='disabled', sources=[{'message_id': 'disabled', 'content': 'Another plan'}], action=action)
    with pytest.raises(TeamDispatchBlocked, match='disabled'):
        resolve_worker_from_state(team, 'planner')
    assert discovery_packet()['planning_capability']['dispatchable'] is False
    team = command({'kind': 'enable_role', 'role': 'planner'},
                   {'kind': 'inactivate_worker', 'worker_id': 'research-a'})
    with pytest.raises(TeamDispatchBlocked, match='no fallback'):
        resolve_worker_from_state(team, 'planner')
    with pytest.raises(ValueError, match='capability is unavailable'):
        queue_assignment(service, store=TaskAssistantSessionStore(tmp_path, 'DEMO'), root=root,
                         message_id='inactive', sources=[{'message_id': 'inactive', 'content': 'Another plan'}], action=action)
    assert discovery_packet()['planning_capability']['dispatchable'] is False
    assert len(LocalTaskStore(root, 'DEMO').list_tasks()) == 1
    # Removing a choice is explicit and restores old behavior, not another pool.
    team = command({'kind': 'set_role_runtime_source', 'role': 'planner', 'worker_id': ''})
    assert 'runtime_source_worker_id' not in team['roles']['planner']
