"""One original ordinary method invocation, two existing tools, no network spend."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from test_role_skill_input import invocation  # noqa: F401
from test_web_search import POLICY, PAYLOAD
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.worker_requests import _services
from goodteam_os.session_runtimes import PiRuntimeConfig, PiSessionRuntime


def test_methods_and_search_share_original_invocation_without_sharing_authority(invocation, tmp_path, monkeypatch):  # noqa: F811
    from goodteam_os.web_search import JobWebSearch
    from goodteam_os.skills.pi_resources import PiResourceChannel
    create, items, _ = invocation
    policy = deepcopy(POLICY)
    provider, _, job = create('developer', 'pi', method_loading_mode='ordinary_pi_methods.v1', web_search=policy)
    policy['max_requests'] = 20
    assert job['web_search'] == POLICY
    workers = provider.workers
    method = PiMethodInput(workers, job['id'])
    assert method.request['web_search'] == POLICY
    with pytest.raises(SkillError):
        create('developer', 'pi', method_loading_mode='ordinary_pi_methods.v1', web_search=policy)
    with pytest.raises(ValueError):
        create('researcher', 'pi', web_search=POLICY)  # Old strict path remains closed.
    with pytest.raises(ValueError):
        create('researcher', 'pi', method_loading_mode='ordinary_pi_methods.v1',
               execution_policy='read_only', web_search=POLICY)
    other_provider, _, other_job = create('researcher', 'pi', method_loading_mode='ordinary_pi_methods.v1')
    other = PiMethodInput(other_provider.workers, other_job['id'])
    assert 'web_search' not in other.request
    assert not other.search_authorized(workers.root/job['id'], job)
    calls = []
    async def fixture_request(connection, selected, query):
        calls.append((selected, query))
        return PAYLOAD
    class Providers:
        def resolve(self, ref):
            assert ref == POLICY['provider']
            return {'protocol':'responses'}
    original_init = JobWebSearch.__init__
    def init(host, job_root, original, *, active):
        original_init(host, job_root, original, active=active, provider_store=Providers(), requester=fixture_request)
    monkeypatch.setattr(JobWebSearch, '__init__', init)
    monkeypatch.setattr(JobWebSearch, 'start', lambda host: {'GOODTEAM_SEARCH_URL':'fixture://search', 'GOODTEAM_SEARCH_TOKEN':host.token})
    monkeypatch.setattr(PiResourceChannel, 'start', lambda channel: (channel._check_context() or
        {'GOODTEAM_SKILL_RESOURCE_URL':'fixture://resource', 'GOODTEAM_SKILL_RESOURCE_TOKEN':channel.token}))
    runtime = PiSessionRuntime(project_root=workers.project_root, job_root=workers.root/job['id'], job=job,
        runtime_cwd=workers.project_root, config=PiRuntimeConfig(
            str((Path(__file__).parent/'fixtures/fake_pi_rpc.py').resolve()), '', '', '', tmp_path/'sessions', 2),
        stderr_path=tmp_path/'stderr', method_input=method)
    try:
        runtime.start_or_resume()
        assert runtime.rpc.command.count('--extension') == 2
        assert runtime.rpc.env['GOODTEAM_SEARCH_TOKEN'] == runtime.web_search.token
        assert runtime.rpc.env['GOODTEAM_SKILL_RESOURCE_TOKEN'] == runtime.method_resources.token
        resource = runtime.method_resources.call({'skill_id':items[1]['skill_id'], 'relative':'detail.md','call_id':'resource-1'})
        assert resource['text'] == 'optional detail bytes'
        found = runtime.web_search.call('public query')
        assert found['status'] == 'searched' and found['job']['id'] == job['id'] and len(calls) == 1
        # Neither mutable projection changes nor method content may expand the
        # immutable request allowance at the actual query-consumption boundary.
        original_policy = job['web_search']
        job['web_search'] = policy
        assert runtime.web_search.call('changed policy')['status'] == 'not_authorized_or_stopped'
        job['web_search'] = original_policy
        item = items[0]
        _services(workers)[3].revoke(item['skill_id'],item['version_id'],actor='fixture',reason='test',command_key='revoke',expected_revision=0)
        assert runtime.web_search.call('after revoke')['status'] == 'not_authorized_or_stopped'
        assert runtime.web_search.call('public query') == found and len(calls) == 1
        runtime.abort()
        assert runtime.web_search.closed and runtime.method_resources.closed.is_set()
        assert runtime.method_resources.call({'skill_id':items[1]['skill_id'],'relative':'detail.md','call_id':'late'}) == {'status':'stopped'}
    finally:
        runtime.close()
    assert runtime.method_resources.closed.is_set() and runtime.web_search.closed
    assert len(calls) == 1 and calls[0][1] == 'public query'
    assert 'private context' not in json.dumps(calls)


def test_original_dispatch_creation_pins_search_policy_and_cannot_upgrade_old_methods(invocation):  # noqa: F811
    from goodteam_os.dispatch import _create_track_job
    create, _, _ = invocation
    provider, _, existing = create('developer', 'pi')
    args = dict(store=provider.workers, run_id='run',
        track={'role':'developer','adapter':'pi','model':'fixture','task':'public research','web_search':deepcopy(POLICY)},
        constraints=[], method_context=existing['task_skill_context'], project_key='project', command_key='dispatched-method')
    with pytest.raises(ValueError, match='explicitly authorized'):
        _create_track_job(**args)
    job = _create_track_job(**args, method_loading_mode='ordinary_pi_methods.v1')
    assert job['web_search'] == POLICY and job['method_workspace_mode'] == 'dispatch_isolated'
    assert _create_track_job(**args, method_loading_mode='ordinary_pi_methods.v1')['id'] == job['id']
    args['track']['web_search']['max_requests'] = 20
    with pytest.raises(SkillError):
        _create_track_job(**args, method_loading_mode='ordinary_pi_methods.v1')
    assert PiMethodInput(provider.workers,job['id']).request['web_search'] == POLICY
