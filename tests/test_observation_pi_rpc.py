"""Original observation entry through real offline RPC subprocess, never a model."""
import json
import pytest
from test_observation_pi_identity import observation
from test_manager_pi_runtime import fake_manager_pi, witness, reject_nonfixture_transports  # noqa: F401
from goodteam_os.pi_rpc import PiRpcProcess
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.project_observation_runtime import judge_with_production_runtime


@pytest.mark.parametrize('mode', ['normal', 'stop', 'lost_reply'])
def test_observation_original_authority_controls_first_rpc_write(tmp_path, monkeypatch, mode):
    stop_before_prompt = mode == "stop"
    root, config, store, packet = observation(tmp_path, monkeypatch)
    fake = fake_manager_pi(tmp_path)
    output = {'assessment': 'wait', 'rationale': 'The original Worker is still executing its authorized task.',
        'observed_job_ids': ['worker']}
    old = '"Fake Pi completed.\\n\\n## Learned\\n- Persistent sessions stay local."'
    source = fake.read_text()
    assert old in source
    fake.write_text(source.replace(old, repr(json.dumps(output))))
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    monkeypatch.setattr(PiResourceChannel, 'start', witness)
    launches, accepted, records = [], [], []
    original_start = PiRpcProcess.start
    def start(rpc):
        assert rpc.prompt_authorizer is not None
        authority = rpc.prompt_authorizer
        on_record = rpc.on_record
        def record(item):
            records.append(item)
            on_record(item)
        rpc.on_record = record
        def authorize(send):
            assert not store.read()['project_continuation']['running_judgments']['one'].get('transport_started')
            result = authority(send)
            assert store.read()['project_continuation']['running_judgments']['one']['transport_started']
            accepted.append(True)
            return result
        rpc.prompt_authorizer = authorize
        original_start(rpc)
        launches.append(rpc)
        if stop_before_prompt:
            store._mutate(lambda s: {**s, 'project_continuation': {**s['project_continuation'], 'status': 'stopped'}})
    monkeypatch.setattr(PiRpcProcess, 'start', start)
    def judge():
        return judge_with_production_runtime(store=store, root=root, project_config=config, project_key='DEMO',
            value=store.read()['project_continuation'], authority_check=lambda: True)
    if mode == 'lost_reply':
        from goodteam_os import project_observation_runtime
        original_run = project_observation_runtime._run
        def lose_reply(**kwargs):
            original_run(**kwargs)
            raise TimeoutError('Fixture response lost after actual offline RPC completion')
        monkeypatch.setattr(project_observation_runtime, '_run', lose_reply)
    value = judge()
    if mode == 'lost_reply':
        assert value['running_judgments']['one']['status'] == 'unconfirmed'
        value = judge()  # Original completed session, never another prompt.
    assert len(launches) == 1 and not launches[0].alive
    assert value['manager_calls'] == 1
    if stop_before_prompt:
        assert value['status'] == 'stopped' and not accepted
        assert not any(item.get('type') == 'agent_start' for item in records)
    else:
        assert value['running_judgments']['one']['status'] == 'recorded', value
        assert value['running_judgments']['one']['advice'] == output
        assert accepted == [True]
        assert judge()['manager_calls'] == 1 and len(launches) == 1
