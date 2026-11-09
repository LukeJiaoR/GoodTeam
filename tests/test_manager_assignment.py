"""One chat assignment traverses the real queue and isolated subprocess pool.

The provider is a protocol fixture: this proves routing/results, not model quality.
"""
import json
import os
import sys

import pytest

from goodteam_os.manager_execution import _parse_chat_turn
from goodteam_os.project_iteration_execution import run_queued_iteration_batch
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore
from test_dispatch_isolation import repository


def test_manager_assignment_returns_isolated_research_output_once(tmp_path, monkeypatch):
    source = repository(tmp_path / 'source')
    config = tmp_path / 'projects/demo.local.yaml'
    config.parent.mkdir()
    config.write_text(f'key: DEMO\nsource_type: existing_project\nrepos:\n  root: {source}\nworker_backend:\n  mode: codex\n')
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    binary = binary_dir / 'codex'
    binary.write_text(f'#!{sys.executable}\n' + '''import sys
from pathlib import Path
if '--version' in sys.argv:
    print('assignment protocol fixture')
    raise SystemExit()
prompt = sys.stdin.read()
assert '这个项目是干嘛的' in prompt
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('Observed greeting in README. Remaining: independent review.\\n')
''')
    binary.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary_dir) + os.pathsep + os.environ['PATH'])
    calls = []
    def manager(**kwargs):
        calls.append(kwargs)
        return _parse_chat_turn(json.dumps({
            'intent': 'work_request', 'response': 'Assigning this research.',
            'task_intake': {'ready': True, 'missing_decisions': [], 'source_message_ids': ['research']},
            'task_action': {'kind': 'queue_task', 'role': 'research', 'execution_policy': 'read_only',
                            'write_domains': [], 'acceptance_criteria': ['Identify the greeting and remaining uncertainty.']},
        }))
    service = TaskAssistantService(tmp_path, project_config=config, manager_turn=manager)
    monkeypatch.setattr(service, 'team_view', lambda _: {'roles': [
        {'role': 'research', 'state': 'enabled', 'workers': [{'dispatchable': True}]}]})
    # Keep this legacy isolated configuration free of readiness preparation.
    monkeypatch.setattr(service, '_readiness_state', lambda *_: {'project_mode': 'existing_project', 'lifecycle': 'ready'})
    monkeypatch.setattr(service, 'session', lambda *_: TaskAssistantSessionStore(tmp_path, 'DEMO').read())
    request = dict(project_key='DEMO', root=source, text='这个项目是干嘛的', message_id='research')
    result = service.send_message(**request)
    queued = result['session']['queued_task_proposals']
    assert not result['session']['proposed_task']
    assert len(queued) == len(LocalTaskStore(source, 'DEMO').list_tasks()) == 1
    task = LocalTaskStore(source, 'DEMO').get_task(queued[0]['project_task_id'])
    assert task['role'] == 'research' and task['execution_policy'] == 'read_only'
    assert task['description'] == queued[0]['exact_request'] == request['text']
    assert not queued[0]['confirmed']
    service.send_message(**request)
    assert len(calls) == 1
    args = dict(workspace=tmp_path, project_key='DEMO', root=source, project_config=config,
                proposal_ids=[queued[0]['id']], expected_revisions={queued[0]['id']: 1})
    with pytest.raises(ValueError, match='Confirm'):
        run_queued_iteration_batch(**args)
    result = run_queued_iteration_batch(**args, confirmed=True)
    assert result['status'] == 'review', result
    job = result['report']['jobs'][0]
    assert job['role'] == 'research' and job['write_scope_verified']
    timeline = TaskAssistantSessionStore(tmp_path, 'DEMO').read()['timeline']
    replies = [item for item in timeline if item['message_id'].startswith('batch-result:')]
    assert len(replies) == 1
    assert 'Observed greeting in README' in replies[0]['content']
    assert 'independent review' in replies[0]['content']
    run_queued_iteration_batch(**args, confirmed=True)
    assert TaskAssistantSessionStore(tmp_path, 'DEMO').read()['timeline'] == timeline
    assert LocalTaskStore(source, 'DEMO').get_task(task['id'])['status'] == 'review'
