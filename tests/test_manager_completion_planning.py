"""Public planning replay uses persisted completion, not a second Pi process.

The SDK witness and model response are explicit offline fixtures.
"""
import json

import pytest

from test_manager_pi_runtime import (
    fake_manager_pi, witness, reject_nonfixture_transports,  # noqa: F401
)
from test_manager_pi_method_producer import setup
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.pi_rpc import PiRpcProcess
from goodteam_os.project_iteration_planner import run_manager_iteration_choice
from goodteam_os.manager_execution import ManagerExecutionError


@pytest.mark.parametrize('tamper', [False, True])
def test_public_planning_completed_replay_never_restarts_pi(tmp_path, monkeypatch, tamper):
    root, config, team, workers = setup(tmp_path)
    config.write_text(f'key: DEMO\nrepos:\n  root: {root}\nworker_backend:\n  mode: pi\n')
    state = team.read()
    state['manager']['runtime'].update(provider='pi', model='fixture')
    team._write_state(state)
    selection = dict(selected_task_ids=['DEMO-1'], compared_task_ids=['DEMO-1'],
        rationale='The supplied facts justify this already authorized independent task.')
    fake = fake_manager_pi(tmp_path)
    old = '"Fake Pi completed.\\n\\n## Learned\\n- Persistent sessions stay local."'
    source = fake.read_text()
    assert old in source
    fake.write_text(source.replace(old, repr(json.dumps(selection))))
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    monkeypatch.setattr(PiResourceChannel, 'start', witness)
    starts = []
    original_start = PiRpcProcess.start

    def start(process):
        starts.append(process.command)
        return original_start(process)

    monkeypatch.setattr(PiRpcProcess, 'start', start)
    kwargs = dict(project_config=config, project_key='DEMO',
        backlog={'version': 'fixture', 'candidates': []},
        context={'schema_version': 'fixture_supplied_context.v1', 'project_key': 'DEMO',
                 'facts': ['DEMO-1 is the already authorized next task.']},
        planning_attempt_id='original-authorized-reservation')
    first = run_manager_iteration_choice(**kwargs)
    job_id = first['manager_execution']['job_id']
    job_root = workers.root / job_id
    assert first['selection'] == selection and len(starts) == 1
    assert workers._read_json(job_root / 'job.json')['status'] == 'queued'
    evidence = {name: (job_root / name).read_bytes()
                for name in ('job.json', 'status.json', 'pi_session.json', 'result.md')}
    if tamper:
        metadata = json.loads(evidence['pi_session.json'])
        metadata['active_operation']['prompt_sha256'] = 'wrong-input'
        (job_root / 'pi_session.json').write_text(json.dumps(metadata))
        with pytest.raises(ManagerExecutionError):
            run_manager_iteration_choice(**kwargs)
    else:
        for _ in range(2):
            replay = run_manager_iteration_choice(**kwargs)
            assert replay['selection'] == selection
            assert replay['manager_execution']['job_id'] == job_id
        assert all((job_root / name).read_bytes() == saved for name, saved in evidence.items())
    assert len(starts) == 1
