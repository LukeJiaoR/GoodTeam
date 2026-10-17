"""Actual Manager context assembly over captured input; no live model claim."""
import json
import subprocess
import sys

import pytest

from goodteam_os import web
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.task_start import capture_task_start
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from test_assistant_drafts import setup_service, send
from test_task_skill_entrypoints import ctx as ctx, Ready, bind, method


def discuss(service, root, count=41):
    for index in range(count):
        service.send_message(project_key='DEMO', root=root, message_id=f'discussion-{index}',
                             text=f'Please acknowledge progress discussion {index}; do not change the task.')


def test_confirmed_original_goal_is_not_lost_by_the_40_event_window(tmp_path):
    service, root, executor, calls = setup_service(tmp_path)
    exact = 'Add search. Preserve the EXISTING_PUBLIC_API without new dependencies.'
    proposal = send(service, root, exact)['proposed_task']
    session = TaskAssistantSessionStore(tmp_path, 'DEMO')
    session.confirm_task(proposal['id'], expected_revision=proposal['revision'])
    session.bind_workflow(proposal['id'], 'run-live')
    state = create_initial_state('run-live', 'DEMO', exact)
    state.current_node = 'implementation'
    state.next_node = 'code_review'
    StateStore(tmp_path).write_project_current(state)
    before = StateStore(tmp_path).read_project_current('DEMO')
    discuss(service, root)
    assert exact in calls[-1]['context']
    assert StateStore(tmp_path).read_project_current('DEMO') == before
    assert executor.started == []


def test_latest_recorded_change_survives_40_events_but_chat_is_not_applied(tmp_path):
    service, root, executor, calls = setup_service(tmp_path)
    state = create_initial_state('run-live', 'DEMO', 'Add search')
    state.current_node = 'implementation'
    state.next_node = 'code_review'
    state.change_requests = {'prd': 'CURRENT_CONFIRMED_BOUNDARY: preserve the SQL schema.'}
    StateStore(tmp_path).write_project_current(state)
    send(service, root, 'Earlier idea: OLD_DISCUSSION_BOUNDARY allows a schema rewrite.', 'old-discussion')
    send(service, root, 'Correction for discussion: NEW_DISCUSSION_BOUNDARY asks for a migration.', 'new-discussion')
    discuss(service, root)
    actual = calls[-1]['context']
    assert 'CURRENT_CONFIRMED_BOUNDARY' in actual
    packet = json.loads(actual.rsplit('\n', 1)[1])
    # Older discussion is now intentionally retained as bounded historical
    # excerpts, never promoted to the authoritative task/input or feedback.
    assert 'OLD_DISCUSSION_BOUNDARY' in json.dumps(packet['conversation'])
    assert 'NEW_DISCUSSION_BOUNDARY' in json.dumps(packet['conversation'])
    assert packet['conversation']['summary']['authority'] == 'historical_context_only'
    assert 'OLD_DISCUSSION_BOUNDARY' not in json.dumps(packet['scope']['run'])
    assert 'NEW_DISCUSSION_BOUNDARY' not in json.dumps(packet['scope']['run'])
    assert StateStore(tmp_path).read_project_current('DEMO').change_requests == state.change_requests
    assert executor.started == []


def captured_task(ctx):
    bind(ctx, method(ctx, "PRIVATE_METHOD_BODY"))
    standard = 'CAPTURED_ACCEPTANCE: complete the task offline; never add a production deployment.'
    context = capture_task_start(
        workspace=ctx.workspace, project_key='DEMO', worker_root=ctx.repo,
        task=TaskSkillIdentity('workflow_run', 'DEMO', 'run-1'), command_key='context-probe-start',
        confirmed_input={'exact_request': 'Add search', 'source_type': 'existing_project',
                         'delivery_mode': 'custom', 'delivery_standards': [standard],
                         'private_context': 'PRIVATE_CAPTURE_CONTEXT'},
    )
    state = create_initial_state('run-1', 'DEMO', 'Add search')
    state.delivery_mode = 'custom'
    state.delivery_standards = [standard]
    state.task_skill_context = context
    state.current_node = 'implementation'
    state.next_node = 'code_review'
    StateStore(ctx.workspace).write_project_current(state)
    return standard, context


def manager_service(ctx):
    calls = []
    def capture_turn(**kwargs):
        calls.append(kwargs)
        return {'intent': 'ambiguous', 'response': 'Recorded discussion only.'}
    service = TaskAssistantService(ctx.workspace, project_config=ctx.config,
                                   readiness_executor=Ready(), manager_turn=capture_turn)
    return service, calls


def task_record(call):
    return json.loads(call['context'].rsplit('\n', 1)[1])['scope']['run']


def ask(ctx, service, *, message_id='question', context_run_id=''):
    return service.send_message(project_key='DEMO', root=ctx.repo, message_id=message_id,
                                text='What are the recorded requirements?', context_run_id=context_run_id)


def test_captured_delivery_standard_reaches_manager_after_41_turns(ctx):
    standard, _ = captured_task(ctx)
    service, calls = manager_service(ctx)
    service.send_message(project_key='DEMO', root=ctx.repo, message_id='early-standard',
                         text=f'The standard already recorded for this task is: {standard}')
    assert standard in calls[-1]['context']
    before = StateStore(ctx.workspace).read_project_current('DEMO')
    discuss(service, ctx.repo)
    service.send_message(project_key='DEMO', root=ctx.repo, message_id='ask-original-standards',
                         text='What delivery standards must the current task meet? Quote the recorded criteria.')
    status, original = web.handle_project_api_get(ctx.workspace, '/api/projects/DEMO/runs/run-1/input-context')
    assert status == 200 and original['capture_status'] == 'locked'
    assert original['input']['delivery_standards'] == [standard]
    assert StateStore(ctx.workspace).read_project_current('DEMO') == before
    assert ctx.workers.list_jobs() == []
    # Baseline lost this standard once it left the last-40-event window.
    assert standard in calls[-1]['context'], json.dumps({
        'locked_input_available': True, 'history_entries': len(TaskAssistantSessionStore(ctx.workspace, 'DEMO').read()['timeline']),
        'model_context_has_original_goal': 'Add search' in calls[-1]['context'],
        'model_context_has_delivery_standard': standard in calls[-1]['context'],
    })

    record = task_record(calls[-1])['task_input']
    assert record['input'] == original['input']
    assert record['task_lock_ref'] == original['task_lock_ref']
    assert record['capture_status'] == 'locked'


def test_original_contract_and_saved_change_feedback_remain_distinct(ctx):
    standard, _ = captured_task(ctx)
    states = StateStore(ctx.workspace)
    state = states.read_project_current('DEMO')
    state.delivery_standards = ['MUTABLE_RECORD_MUST_NOT_REPLACE_CAPTURE']
    state.change_requests = {'prd': 'CURRENT_RECORDED_FEEDBACK: replace search UI delivery with an offline CSV export.'}
    path = states.write_project_current(state)
    before = path.read_bytes()
    service, calls = manager_service(ctx)
    service.send_message(project_key='DEMO', root=ctx.repo, message_id='discussion',
                         text='For discussion only: UNCONFIRMED_DEPLOYMENT_IDEA.')
    run = task_record(calls[-1])
    assert run['task_input']['input']['delivery_standards'] == [standard]
    assert run['change_requests'] == state.change_requests
    assert 'MUTABLE_RECORD_MUST_NOT_REPLACE_CAPTURE' not in calls[-1]['context']
    assert 'UNCONFIRMED_DEPLOYMENT_IDEA' not in json.dumps(run['task_input'])
    assert 'keep it distinct from the original contract' in calls[-1]['context']
    assert path.read_bytes() == before
    assert ctx.workers.list_jobs() == []


def test_fresh_process_restores_the_same_input_identity_without_reconstructing_chat(ctx):
    standard, _ = captured_task(ctx)
    code = '''
import json, sys
from pathlib import Path
from goodteam_os.task_assistant import TaskAssistantService
class Ready:
    def status(self, *args):
        return {'lifecycle': 'ready', 'project_mode': 'new_project', 'steps': []}
    def start(self, *args, **kwargs):
        raise AssertionError('No preparation allowed')
captured = []
def turn(**values):
    captured.append(json.loads(values['context'].rsplit('\\n', 1)[1])['scope']['run']['task_input'])
    return {'intent': 'ambiguous', 'response': 'Read-only context restored.'}
service = TaskAssistantService(Path(sys.argv[1]), project_config=Path(sys.argv[3]), readiness_executor=Ready(), manager_turn=turn)
service.send_message(project_key='DEMO', root=Path(sys.argv[2]), message_id='fresh-process', text='What was required?')
print(json.dumps(captured[0]))
'''
    process = subprocess.run([sys.executable, '-c', code, str(ctx.workspace), str(ctx.repo), str(ctx.config)],
                             capture_output=True, text=True, check=True, timeout=20)
    restored = json.loads(process.stdout)
    _, original = web.handle_project_api_get(ctx.workspace, '/api/projects/DEMO/runs/run-1/input-context')
    assert restored['capture_status'] == 'locked'
    assert restored['input']['delivery_standards'] == [standard]
    assert restored['task_lock_ref'] == original['task_lock_ref']
    assert ctx.workers.list_jobs() == []


def test_historical_manager_reference_keeps_original_run_inputs(ctx):
    standard, _ = captured_task(ctx)
    states = StateStore(ctx.workspace)
    states.archive_project_current('DEMO', reason='completed')
    new = create_initial_state('run-2', 'DEMO', 'NEW_CURRENT_GOAL')
    new.delivery_standards = ['NEW_CURRENT_STANDARD']
    states.write_project_current(new)
    before = states.read_project_current('DEMO')
    service, calls = manager_service(ctx)
    ask(ctx, service, context_run_id='run-1')
    record = task_record(calls[-1])['task_input']
    assert record['run_id'] == 'run-1' and record['project_key'] == 'DEMO'
    assert record['input']['delivery_standards'] == [standard]
    assert 'NEW_CURRENT_GOAL' not in calls[-1]['context']
    assert 'NEW_CURRENT_STANDARD' not in calls[-1]['context']
    assert states.read_project_current('DEMO') == before


@pytest.mark.parametrize('damage', ['missing_index', 'corrupt_index', 'missing_reference', 'wrong_root'])
def test_unavailable_capture_is_not_replaced_or_repaired_for_manager(ctx, damage):
    captured_task(ctx)
    root = ctx.service.locks.root / ctx.service.packages.scope.namespace
    index = root / 'index.json'
    if damage == 'missing_index':
        index.unlink()
    elif damage == 'corrupt_index':
        index.write_text('{broken')
    elif damage == 'missing_reference':
        states = StateStore(ctx.workspace)
        state = states.read_project_current('DEMO')
        state.task_skill_context = {}
        states.write_project_current(state)
    before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    service, calls = manager_service(ctx)
    selected_root = ctx.repo if damage != 'wrong_root' else ctx.root / 'different-repo'
    selected_root.mkdir(exist_ok=True)
    service.send_message(project_key='DEMO', root=selected_root, message_id='unavailable', text='What was required?')
    record = task_record(calls[-1])['task_input']
    assert record['capture_status'] == 'unavailable'
    assert record['input'] is None and record['task_lock_ref'] is None
    assert 'CAPTURED_ACCEPTANCE' not in calls[-1]['context']
    assert {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    assert ctx.workers.list_jobs() == []


def test_legacy_input_is_explicitly_unverified_and_does_not_create_a_capture(ctx):
    state = create_initial_state('legacy-run', 'DEMO', 'Legacy request')
    state.delivery_mode = 'custom'
    state.delivery_standards = ['LEGACY_RECORD_ONLY']
    states = StateStore(ctx.workspace)
    states.write_project_current(state)
    root = ctx.service.locks.root
    before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    service, calls = manager_service(ctx)
    ask(ctx, service)
    record = task_record(calls[-1])['task_input']
    assert record['capture_status'] == 'legacy_unverified'
    assert record['input_origin'] == 'workflow_state'
    assert record['input']['delivery_standards'] == ['LEGACY_RECORD_ONLY']
    assert record['task_lock_ref'] is None
    assert {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before


def test_foreign_project_state_is_rejected_before_manager_input(ctx):
    captured_task(ctx)
    path = StateStore(ctx.workspace).project_state_dir('DEMO') / 'current.json'
    raw = json.loads(path.read_text())
    raw.update(project_key='OTHER', idea='FOREIGN_TASK')
    # Drop the original lock so the state parser can expose the foreign legacy record.
    raw['task_skill_context'] = {}
    path.write_text(json.dumps(raw))
    service, calls = manager_service(ctx)
    from goodteam_os.manager_execution import ManagerExecutionError
    with pytest.raises(ManagerExecutionError):
        ask(ctx, service)
    assert calls == []


def test_manager_contract_excludes_private_capture_and_current_method_bodies(ctx):
    standard, _ = captured_task(ctx)
    bind(ctx, method(ctx, "NEW_TEAM_METHOD_BODY", key="new"), key="new-binding")
    service, calls = manager_service(ctx)
    ask(ctx, service)
    record = task_record(calls[-1])['task_input']
    assert record['input']['delivery_standards'] == [standard]
    serialized = json.dumps(record)
    assert 'PRIVATE_CAPTURE_CONTEXT' not in serialized
    assert 'PRIVATE_METHOD_BODY' not in serialized and 'NEW_TEAM_METHOD_BODY' not in serialized
    assert str(ctx.workspace) not in serialized and str(ctx.repo) not in serialized
    assert 'subjects' not in record


@pytest.mark.parametrize('identity', [{'project_key': 'OTHER', 'run_id': 'run-1'},
                                     {'project_key': 'DEMO', 'run_id': 'other-run'}])
def test_shared_projection_refuses_different_project_or_run(ctx, identity):
    from goodteam_os.task_input_context import task_input_context_for_state

    captured_task(ctx)
    state = StateStore(ctx.workspace).read_project_current('DEMO')
    record = task_input_context_for_state(ctx.workspace, ctx.repo, state, **identity)
    assert record['capture_status'] == 'unavailable'
    assert record['input'] is None and record['task_lock_ref'] is None
    assert record['error_code'] == 'snapshot_scope_mismatch'
