"""Captured read-only Role + authorized Search through original offline Runtime.

Workspace mapping and local listeners are explicit doubles, as in the read-only
Runtime fixture. Only fake RPC runs; the Search provider/requester never networks.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_manager_pi_method_producer import setup
from test_manager_pi_runtime import fake_manager_pi
from test_web_search import POLICY, PAYLOAD
from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionRuntime
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.skills.worker_requests import _services
from goodteam_os.web_search import JobWebSearch


@pytest.mark.parametrize('role', ['research', 'planner'])
@pytest.mark.parametrize('authorized', [True, False])
def test_readonly_methods_search_retains_original_authority(tmp_path, monkeypatch, role, authorized):
    monkeypatch.setenv('GOODTEAM_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('GOODTEAM_HOME', str(tmp_path / 'home'))
    root, _, _, workers = setup(tmp_path)
    import goodteam_os.dispatch_workspace as spaces

    class BeforeGit(Exception):
        pass

    seen = []

    def capture(_root, _run, job_ids, **_kwargs):
        seen.extend(job_ids)
        raise BeforeGit()

    monkeypatch.setattr(spaces, 'prepare_dispatch_workspace', capture)
    track = dict(role=role, adapter='pi', model='fixture', execution_policy='read_only',
                 task='Read the supplied repository and report evidence.', context='PRIVATE ORIGINAL ROLE CONTEXT',
                 track_id='task-one', project_task_id='task-one')
    if authorized:
        track['web_search'] = deepcopy(POLICY)
    from goodteam_os.tasks import LocalTaskStore
    ledger = LocalTaskStore(root, 'DEMO')
    task = ledger.create_task(title='Research the bounded question', description=track['task'], role=role,
        execution_policy='read_only', web_search=track.get('web_search'))
    assert task.get('web_search') == track.get('web_search')
    track.update(project_task_id=task['id'], track_id=task['id'])
    if authorized:
        with pytest.raises(ValueError, match='explicitly authorized'):
            ledger.update_task(task['id'], role='implementation')
        assert ledger.get_task(task['id'])['role'] == role
    with pytest.raises(BeforeGit):
        dispatch_tracks(project_root=root, require_isolation=True, logical_task_id='readonly-search', tracks=[track])
    job = workers._read_json(workers.root / seen[0] / 'job.json')
    assert job['method_loading_mode'] == 'readonly_pi_methods.v1'
    target = tmp_path / 'fixture-workspace'
    target.mkdir()
    (target / '.git').mkdir()

    def mapping(source, run_id, ids, **_kwargs):
        assert source == root and run_id == job['run_id'] and ids is None
        return SimpleNamespace(workspace=lambda job_id: target if job_id == job['id'] else None)

    monkeypatch.setattr(spaces, 'prepare_dispatch_workspace', mapping)
    method = PiMethodInput(workers, job['id'])
    method.bind_workspace(dict(available=True, mode='dispatch_isolated', workspace_path=str(target),
                               execution_id=job['run_id'], sandbox_id=job['id']))
    original_prompt = method.prompt()
    calls = []

    class Providers:
        def resolve(self, ref):
            assert ref == POLICY['provider']
            return {'protocol': 'responses'}

    async def requester(connection, selected, query):
        assert connection == {'protocol': 'responses'} and selected == POLICY
        calls.append(query)
        return PAYLOAD

    original_init = JobWebSearch.__init__

    def init(host, job_root, original, *, active):
        original_init(host, job_root, original, active=active, provider_store=Providers(), requester=requester)

    monkeypatch.setattr(JobWebSearch, '__init__', init)
    monkeypatch.setattr(JobWebSearch, 'start', lambda host: {
        'GOODTEAM_SEARCH_URL': 'fixture://search', 'GOODTEAM_SEARCH_TOKEN': host.token})

    def witness(channel):
        channel._check_context()
        policy = channel.startup_policy
        assert channel.call(dict(operation='tool_policy_witness', version=policy.version, nonce=policy.nonce,
                                 active=list(policy.tools), available=list(policy.tools)))['status'] == 'policy_witness_received'
        return {'GOODTEAM_SKILL_RESOURCE_URL': 'fixture://resource', 'GOODTEAM_SKILL_RESOURCE_TOKEN': channel.token}

    monkeypatch.setattr(PiResourceChannel, 'start', witness)
    runtime = PiSessionRuntime(project_root=root, job_root=workers.root / job['id'], job=job,
        runtime_cwd=target, config=PiRuntimeConfig(str(fake_manager_pi(tmp_path)), '', '', '', tmp_path / 'sessions', 2),
        stderr_path=tmp_path / 'stderr', method_input=method)
    try:
        runtime.start_or_resume()
        env = runtime.rpc.env
        policy = json.loads(env['GOODTEAM_PI_TOOL_POLICY'])
        assert env['GOODTEAM_SKILL_RESOURCE_TOKEN'] == runtime.method_resources.token
        assert runtime.metadata['readonly_tool_contract']['tools'] == policy['tools']
        assert 'goodteam_skill_resource' in policy['tools']
        assert not {'bash', 'write', 'edit'} & set(policy['tools'])
        if not authorized:
            assert runtime.web_search is None and 'GOODTEAM_SEARCH_TOKEN' not in env
            assert 'goodteam_web_search' not in policy['tools'] and calls == []
            return
        assert job['web_search'] == POLICY
        assert env['GOODTEAM_SEARCH_TOKEN'] == runtime.web_search.token
        assert 'goodteam_web_search' in policy['tools']
        assert runtime.rpc.command.count('--extension') == 3
        found = runtime.web_search.call('public evidence query')
        assert found['status'] == 'searched' and found['job']['id'] == job['id']
        assert runtime.web_search.call('public evidence query') == found and calls == ['public evidence query']
        evidence = json.loads((workers.root / job['id'] / 'web_search.json').read_text())
        assert len(evidence['requests']) == 1
        assert 'public evidence query' in json.dumps(evidence)
        assert method.prompt() == original_prompt and 'public evidence query' not in method.prompt()
        assert 'PRIVATE ORIGINAL ROLE CONTEXT' not in json.dumps(calls)
        item = method._identity()[1]['methods']['items'][0]
        _services(workers)[3].revoke(item['skill_id'], item['version_id'], actor='fixture', reason='test',
                                    command_key='revoke', expected_revision=0)
        assert runtime.web_search.call('after revoke')['status'] == 'not_authorized_or_stopped'
        assert runtime.web_search.call('public evidence query') == found
        runtime.abort()
        assert runtime.web_search.closed and runtime.method_resources.closed.is_set()
        assert runtime.web_search.call('after Stop')['status'] == 'not_authorized_or_stopped'
        assert calls == ['public evidence query']
        assert json.loads((workers.root / job['id'] / 'web_search.json').read_text()) == evidence
    finally:
        runtime.close()
