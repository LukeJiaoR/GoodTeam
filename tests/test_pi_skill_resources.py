"""Actual locked broker and Pi extension contract; no Pi/model/HTTP acceptance."""
import subprocess
from pathlib import Path
from io import BytesIO
import json
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_role_skill_input import invocation  # noqa: F401
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.skills.models import SkillError


def test_pi_channel_pins_context_rechecks_authority_and_preserves_evidence(invocation, monkeypatch):  # noqa: F811
    create, items, _ = invocation
    provider, ref, job = create('developer', adapter='pi')
    channel = PiResourceChannel(provider, ref, active=lambda: True)
    payload = {'skill_id': items[1]['skill_id'], 'relative': 'detail.md', 'call_id': 'read-1'}
    ref['id'] = 'not-the-bound-context'  # caller mutation cannot retarget the channel
    response = channel.call(payload)
    assert response == {'status': 'broker_read_completed', 'text': 'optional detail bytes',
                        'runtime_read_observed': False, 'method_following_verified': False}
    assert channel.call(payload) == response
    assert channel.call({**payload, 'relative': 'SKILL.md'}) == {'status': 'resource_unavailable'}
    assert channel.call({**payload, 'context_ref': ref}) == {'status': 'invalid_request'}
    assert channel.call({**payload, 'relative': '../SKILL.md'}) == {'status': 'resource_unavailable'}
    # Exercise the real HTTP handler with an explicit socket-free server fixture.
    class Server:
        def __init__(self, address, handler):
            assert address == ('127.0.0.1', 0)
            self.handler, self.server_port = handler, 12345
        def serve_forever(self):
            pass
        def shutdown(self):
            pass
        def server_close(self):
            pass
    monkeypatch.setattr('goodteam_os.skills.pi_resources.ThreadingHTTPServer', Server)
    environment = channel.start()
    with pytest.raises(SkillError):
        channel.start()
    handler = object.__new__(channel.server.handler)
    body = json.dumps(payload).encode()
    handler.path, handler.headers = '/resource', {'Content-Length': str(len(body))}
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    statuses = []
    handler.send_error = handler.send_response = statuses.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_POST()
    assert statuses == [403] and not handler.wfile.getvalue()
    handler.headers['Authorization'] = 'Bearer ' + environment['GOODTEAM_SKILL_RESOURCE_TOKEN']
    handler.do_POST()
    assert statuses == [403, 200] and json.loads(handler.wfile.getvalue()) == response
    blocked = PiResourceChannel(provider, channel.context_ref, active=lambda: True)
    original_private = provider.private_input
    def stop_during_authorization(ref):
        blocked.close()
        return original_private(ref)
    with monkeypatch.context() as local:
        local.setattr(provider, 'private_input', stop_during_authorization)
        with pytest.raises(SkillError):
            blocked.start()
    assert blocked.server is None
    parallel = PiResourceChannel(provider, channel.context_ref, active=lambda: True)
    barrier = Barrier(2)
    def concurrent_authorization(ref):
        value = original_private(ref)
        barrier.wait(timeout=5)
        return value
    def start_once():
        try:
            parallel.start()
            return 'started'
        except SkillError:
            return 'rejected'
    with monkeypatch.context() as local:
        local.setattr(provider, 'private_input', concurrent_authorization)
        with ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(lambda _: start_once(), range(2))) == ['rejected', 'started']
    parallel.close()
    original_read = provider.read_resource
    def stop_during_read(*args, **kwargs):
        data = original_read(*args, **kwargs)
        channel.close()
        return data
    provider.read_resource = stop_during_read
    assert channel.call({**payload, 'call_id': 'read-2'}) == {'status': 'stopped'}
    assert channel.call(payload) == {'status': 'stopped'}
    # The transport is deliberately not activated by ordinary dispatch.
    result = provider.workers.run_job(job['id'])
    assert result['skill_runtime_status'] == 'not_connected'
    assert result['invocation_evidence']['external_invocation'] is False


def test_pi_extension_forwards_only_runtime_call_and_resource_identity():
    extension = Path(__file__).parents[1] / 'src/goodteam_os/pi_skill_resources.mjs'
    script = '''
import assert from 'node:assert/strict';
const {default: register} = await import(process.argv[1]);
let tool; register({registerTool(t) { tool = t; }});
const signal = new AbortController().signal;
globalThis.fetch = async (_url, options) => {
  assert.deepEqual(JSON.parse(options.body), {skill_id:'locked',relative:'detail.md',call_id:'runtime-call'});
  assert.equal(options.signal, signal); assert.equal(options.redirect, 'error');
  return {ok:true, json:async()=>({status:'stopped'})};
};
const result = await tool.execute('runtime-call', {skill_id:'locked',relative:'detail.md',call_id:'forged',context_ref:'foreign'}, signal);
assert.equal(result.details.status, 'stopped');
assert.equal(tool.parameters.additionalProperties, false);
'''
    subprocess.run(['node', '--input-type=module', '-e', script, extension.as_uri()], check=True)
