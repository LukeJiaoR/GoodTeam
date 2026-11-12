"""Original job -> locked methods -> existing Pi RPC, using a free fake process."""
import json
from pathlib import Path
import pytest
from test_role_skill_input import invocation  # noqa: F401
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.worker_requests import reject_unverified_runtime_entry


def test_explicit_ordinary_role_methods_reach_original_pi_and_keep_identity(invocation, monkeypatch):  # noqa: F811
    create, items, _ = invocation
    fake = (Path(__file__).parent / 'fixtures/fake_pi_rpc.py').resolve()
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    channels = []
    # Socket creation is unavailable here. Keep the real broker, replacing only
    # listener startup; fake RPC does not load extensions or call a paid model.
    from goodteam_os.skills.pi_resources import PiResourceChannel
    def start(channel):
        channel._check_context()
        channels.append(channel)
        assert channel.call({"skill_id": items[1]["skill_id"], "relative": "detail.md", "call_id": "active-read"})["text"] == "optional detail bytes"
        return {}
    monkeypatch.setattr(PiResourceChannel, 'start', start)
    provider, _, job = create('developer', 'pi', method_loading_mode='ordinary_pi_methods.v1')
    workers = provider.workers
    result = workers.run_job(job['id'])
    assert result['status'] == 'completed', result
    method = PiMethodInput(workers, job['id'])
    payload = json.loads(method.prompt())
    assert payload['role'] == 'developer' and payload['context'] == 'developer private context'
    assert 'shared complete body' in payload['required_methods'][0]['text']
    assert 'optional complete body' not in method.prompt()
    assert channels[0].closed.is_set()
    assert channels[0].call({'skill_id': items[1]['skill_id'], 'relative': 'detail.md', 'call_id': 'late'}) == {'status': 'stopped'}
    metadata = workers._read_json(workers.root / job['id'] / 'pi_session.json')
    from hashlib import sha256
    assert metadata['active_operation']['prompt_sha256'] == sha256(method.prompt().encode()).hexdigest()
    assert metadata['job_id'] == job['id']
    assert workers._session_job(job['id'])[1]['id'] == job['id']
    # Same job restart does not send an initial prompt twice.
    again = workers.run_job(job['id'])
    assert again['status'] == 'completed'
    assert workers._read_json(workers.root / job['id'] / 'pi_session.json')['active_operation']['id'] == metadata['active_operation']['id']
    other_provider, _, other_job = create('researcher', 'pi', method_loading_mode='ordinary_pi_methods.v1')
    other = PiMethodInput(other_provider.workers, other_job['id'])
    assert 'developer private context' not in other.prompt()
    assert other.ref != method.ref
    with pytest.raises(SkillError):
        reject_unverified_runtime_entry(workers.root / other_job['id'], other_job, method_input=method)
    # No dictionary flag can enter the Runtime without original registration.
    with pytest.raises(SkillError):
        reject_unverified_runtime_entry(workers.root / job['id'], job)
    with pytest.raises(SkillError):
        create('unsupported', 'pi', execution_policy='read_only', method_loading_mode='ordinary_pi_methods.v1')
