"""Production Manager capture -> method broker -> real fake-RPC subprocess.

The listener/witness and model are offline doubles, not an installed Pi SDK.
"""
from pathlib import Path
import pytest
from test_manager_pi_method_producer import setup, create
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.pi_manager_tools import PiManagerTools, PiManagerPolicyError


@pytest.fixture(autouse=True)
def reject_nonfixture_transports(tmp_path, monkeypatch):
    from goodteam_os.workers import WorkerJobStore
    from goodteam_os.pi_rpc import PiRpcProcess
    original_run, original_start = WorkerJobStore._run_job, PiRpcProcess.start
    def guarded_run(store, job_id, **kwargs):
        request = store._read_json(store.root / job_id / 'job.json')
        assert request['adapter'] == 'pi' and request['method_loading_mode'] == 'manager_pi_methods.v1'
        return original_run(store, job_id, **kwargs)
    def guarded_start(process):
        binary = Path(process.command[0]).resolve()
        assert binary.is_relative_to(tmp_path.resolve()) and binary.name == 'fake-manager-pi'
        return original_start(process)
    monkeypatch.setattr(WorkerJobStore, '_run_job', guarded_run)
    monkeypatch.setattr(PiRpcProcess, 'start', guarded_start)


def fake_manager_pi(tmp_path):
    source = (Path(__file__).parent / 'fixtures/fake_pi_rpc.py').read_text()
    source = source.replace('session_file.touch()', '''if option('--session'):
    session_file = Path(option('--session'))
session_file.touch()''')
    source = source.replace('entries: list[dict[str, object]] = []', '''entries: list[dict[str, object]] = []
if option('--session'):
    saved = [json.loads(line) for line in session_file.read_text().splitlines() if line]
    session_id = saved[0]['id']
    entries = saved[1:]''')
    source = source.replace('"".join(json.dumps(entry, ensure_ascii=False)',
        '''json.dumps({'type': 'session', 'id': session_id, 'cwd': str(Path.cwd()), 'version': 3}) + "\\n" + "".join(json.dumps(entry, ensure_ascii=False)''')
    path = tmp_path / 'fake-manager-pi'
    path.write_text(source)
    path.chmod(0o755)
    return path


def witness(channel):
    channel._check_context()
    policy = channel.startup_policy
    assert isinstance(policy, PiManagerTools)
    assert channel.call({'operation': 'tool_policy_witness', 'version': policy.version,
        'nonce': policy.nonce, 'active': list(policy.tools), 'available': list(policy.tools)})['status'] == 'policy_witness_received'
    return {}


def test_manager_real_producer_method_job_uses_restricted_runtime_and_restore(tmp_path, monkeypatch):
    root, config, _, workers = setup(tmp_path)
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake_manager_pi(tmp_path)))
    channels, commands = [], []
    def start(channel):
        channels.append(channel)
        return witness(channel)
    monkeypatch.setattr(PiResourceChannel, 'start', start)
    from goodteam_os.pi_rpc import PiRpcProcess
    original_start = PiRpcProcess.start
    def start_rpc(process):
        commands.append(list(process.command))
        assert process.request_guard is not None
        with pytest.raises(PiManagerPolicyError):
            process.request({'type': 'bash', 'command': 'not allowed'})
        return original_start(process)
    monkeypatch.setattr(PiRpcProcess, 'start', start_rpc)
    job, _ = create(root, config, workers)
    result = workers.run_job(job['id'])
    assert result['status'] == 'completed', result
    assert job['execution_policy'] == 'read_only' and result['strict_isolation_verified'] is False
    assert commands[0][commands[0].index('--tools') + 1] == 'goodteam_skill_resource'
    assert '--no-mcp' in commands[0] and '--no-extensions' in commands[0]
    metadata = workers._read_json(workers.root / job['id'] / 'pi_session.json')
    assert metadata['manager_tool_contract']['version'] == 'pi-manager-tools.v1'
    first_operation = metadata['active_operation']['id']
    original_request = (workers.root / job['id'] / 'job.json').read_bytes()
    replay = workers.run_job(job['id'])
    assert replay['status'] == 'completed', replay
    assert '--session' in commands[1]
    assert workers._read_json(workers.root / job['id'] / 'pi_session.json')['active_operation']['id'] == first_operation
    assert workers._read_json(workers.root / job['id'] / 'pi_session.json')['settled_operation_id'] == first_operation
    assert channels[0].startup_policy.nonce != channels[1].startup_policy.nonce
    assert (workers.root / job['id'] / 'job.json').read_bytes() == original_request
    assert all(channel.closed.is_set() for channel in channels)


@pytest.mark.parametrize('bad', ['missing', 'extra_tool'])
def test_unwitnessed_manager_never_sends_model_prompt(tmp_path, monkeypatch, bad):
    root, config, _, workers = setup(tmp_path)
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake_manager_pi(tmp_path)))
    def start(channel):
        if bad == 'extra_tool':
            policy = channel.startup_policy
            channel.call({'operation': 'tool_policy_witness', 'version': policy.version,
                'nonce': policy.nonce, 'active': list(policy.tools), 'available': [*policy.tools, 'bash']})
        return {}
    monkeypatch.setattr(PiResourceChannel, 'start', start)
    monkeypatch.setattr('goodteam_os.session_runtimes.PI_STARTUP_TIMEOUT_FLOOR_SECONDS', 0.02)
    monkeypatch.setenv('GOODTEAM_PI_REQUEST_TIMEOUT', '0.1')
    job, _ = create(root, config, workers)
    result = workers.run_job(job['id'])
    assert result['status'] == 'failed'
    meta = workers._read_json(workers.root / job['id'] / 'pi_session.json')
    assert not meta.get('initial_prompt_sent') and not meta.get('active_operation')


def test_manager_extension_blocks_project_tools_and_requires_exact_witness(tmp_path):
    import os
    import subprocess
    policy = PiManagerTools()
    script = r'''
import extension from './src/goodteam_os/pi_manager_tools.mjs';
import assert from 'node:assert/strict';
const policy=JSON.parse(process.env.GOODTEAM_PI_TOOL_POLICY);
let hooks={},active=[],accept=true;
const sdk={on:(name,fn)=>hooks[name]=fn,setActiveTools:tools=>active=tools,
  getActiveTools:()=>active,getAllTools:()=>policy.tools.map(name=>({name}))};
globalThis.fetch=async (_url,req)=>{
  assert.equal(JSON.parse(req.body).nonce,policy.nonce);
  return {ok:true,json:async()=>({status:accept?'policy_witness_received':'policy_rejected'})};
};
extension(sdk);
assert.equal((await hooks.tool_call({toolName:'goodteam_skill_resource'})).block,true);
await hooks.session_start();
assert.equal(await hooks.tool_call({toolName:'goodteam_skill_resource'}),undefined);
for (const toolName of ['read','grep','find','ls','bash','write','edit','goodteam_web_search','mcp__x']) {
  assert.equal((await hooks.tool_call({toolName})).block,true);
}
accept=false;
await assert.rejects(hooks.session_start(),/rejected/);
assert.equal((await hooks.tool_call({toolName:'goodteam_skill_resource'})).block,true);
accept=true;sdk.getAllTools=()=>[...policy.tools.map(name=>({name})),{name:'bash'}];
await assert.rejects(hooks.session_start(),/mismatch/);
'''
    subprocess.run(['node', '--input-type=module', '-e', script], check=True,
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, **policy.environment()}, capture_output=True)
    for command in ('bash', 'export_html', 'switch_session', 'new_session', 'compact', 'set_model'):
        with pytest.raises(PiManagerPolicyError):
            policy.guard({'type': command})
    with pytest.raises(PiManagerPolicyError):
        policy.guard({'type': 'prompt', 'message': 'not yet witnessed'})
    policy.accept({'operation': 'tool_policy_witness', 'version': policy.version, 'nonce': policy.nonce,
        'active': list(policy.tools), 'available': list(policy.tools)})
    policy.guard({'type': 'prompt', 'message': 'supplied Context'})
    policy.invalidate()
    with pytest.raises(PiManagerPolicyError):
        policy.guard({'type': 'follow_up', 'message': 'after Stop'})
    policy.guard({'type': 'abort'})  # Stop remains usable after the witness is revoked.


def test_original_manager_decision_entry_returns_structured_action_from_restricted_pi(tmp_path, monkeypatch):
    import json
    from goodteam_os.project_iteration_planner import run_manager_iteration_choice
    root, config, team, workers = setup(tmp_path)
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: pi\n')
    state = team.read()
    state['manager']['runtime'].update(provider='pi', model='fixture')
    team._write_state(state)
    selection = {'selected_task_ids': ['DEMO-1'], 'compared_task_ids': ['DEMO-1'],
        'rationale': 'The supplied facts justify this already authorized independent task.'}
    fake = fake_manager_pi(tmp_path)
    old = '"Fake Pi completed.\\n\\n## Learned\\n- Persistent sessions stay local."'
    source = fake.read_text()
    assert old in source
    fake.write_text(source.replace(old, repr(json.dumps(selection))))
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    monkeypatch.setattr(PiResourceChannel, 'start', witness)
    context = {'schema_version': 'fixture_supplied_context.v1', 'project_key': 'DEMO',
        'facts': ['DEMO-1 is the already authorized next task.']}
    from goodteam_os.workers import WorkerJobStore
    original_run = WorkerJobStore._run_job
    def bounded_run(store, job_id, **kwargs):
        request = store._read_json(store.root / job_id / 'job.json')
        assert request['adapter'] == 'pi' and request['method_loading_mode'] == 'manager_pi_methods.v1'
        return original_run(store, job_id, **kwargs)
    monkeypatch.setattr(WorkerJobStore, '_run_job', bounded_run)
    outcome = run_manager_iteration_choice(project_config=config, project_key='DEMO',
        backlog={'version': 'fixture', 'candidates': []}, context=context)
    assert outcome['selection'] == selection
    job = workers._read_json(workers.root / outcome['manager_execution']['job_id'] / 'job.json')
    assert job['method_loading_mode'] == 'manager_pi_methods.v1'
    assert outcome['manager_execution']['adapter'] == 'pi'
