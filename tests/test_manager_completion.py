"""Manager public entry reuses only its original verified completion, offline."""
import json
import pytest
from test_manager_pi_method_producer import setup
from test_manager_pi_runtime import fake_manager_pi, witness, reject_nonfixture_transports  # noqa: F401
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.pi_rpc import PiRpcProcess
from goodteam_os.manager_execution import ManagerExecutionError, run_manager_chat_turn, run_manager_interpretation


@pytest.mark.parametrize('entry', ['chat', 'intent'])
@pytest.mark.parametrize('bad', ['', 'terminal', 'session', 'missing_result'])
def test_public_manager_completed_turn_reuses_original_without_runtime(tmp_path, monkeypatch, entry, bad):
    root, config, team, workers = setup(tmp_path)
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: pi\n')
    state = team.read()
    state['manager']['runtime'].update(provider='pi', model='fixture')
    team._write_state(state)
    output = ({'intent': 'project_question', 'response': 'The supplied task is still running.'}
              if entry == 'chat' else {'summary': 'Keep the supplied task scope.', 'items': ['Only the original task.']})
    fake = fake_manager_pi(tmp_path)
    old = '"Fake Pi completed.\\n\\n## Learned\\n- Persistent sessions stay local."'
    assert old in fake.read_text()
    fake.write_text(fake.read_text().replace(old, repr(json.dumps(output))))
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    monkeypatch.setattr(PiResourceChannel, 'start', witness)
    launches = []
    start = PiRpcProcess.start
    def track(rpc):
        launches.append(rpc)
        return start(rpc)
    monkeypatch.setattr(PiRpcProcess, 'start', track)
    def call():
        if entry == 'chat':
            return run_manager_chat_turn(project_config=config, project_key='DEMO', message_id='one',
                text='What is the current supplied status?', context='Only original facts.')
        return run_manager_interpretation(project_config=config, project_key='DEMO', run_id='run-one',
            intent_id='one', change_request='Keep the original task.', context='Only original facts.')
    first = call()
    job_root = workers.root / first['manager_execution']['job_id']
    original = (job_root / 'job.json').read_bytes()
    receipt = (job_root / 'status.json').read_bytes()
    if bad == 'terminal':
        meta = json.loads(receipt)
        meta['phase'] = 'other'
        (job_root / 'status.json').write_text(json.dumps(meta))
    elif bad == 'session':
        meta = json.loads((job_root / 'pi_session.json').read_text())
        meta['active_operation']['prompt_sha256'] = 'wrong'
        (job_root / 'pi_session.json').write_text(json.dumps(meta))
    elif bad == 'missing_result':
        (job_root / 'result.md').unlink()
    # Any Runtime or preflight attempt would lose this test, even if it would
    # restore a settled session without issuing a second model prompt.
    monkeypatch.setattr(PiRpcProcess, 'start', lambda _: pytest.fail('No second Runtime for completed Manager'))
    if bad:
        with pytest.raises(ManagerExecutionError):
            call()
    else:
        assert call() == first
        assert (job_root / 'status.json').read_bytes() == receipt
    assert len(launches) == 1
    assert (job_root / 'job.json').read_bytes() == original
