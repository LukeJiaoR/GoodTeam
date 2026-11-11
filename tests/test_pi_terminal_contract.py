"""Official RPC contract, exercised with free local subprocess fixtures."""
from pathlib import Path
from types import SimpleNamespace

from goodteam_os.session_runtimes import pi_backend_doctor
from goodteam_os.workers import WorkerJobStore


def test_pi_doctor_requires_supported_cancellation_protocol(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr('goodteam_os.session_runtimes.which', lambda _: '/fixture/pi')
    version = ['pi 0.80.4']
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=version[0], stderr=''))
    assert pi_backend_doctor(tmp_path)['available']
    assert not pi_backend_doctor(tmp_path)['ready']
    assert pi_backend_doctor(tmp_path)['capabilities'] == []
    for label in ('pi 1.1.0', '1.5.2', 'pi 1.1.0+fixture'):
        version[0] = label
        assert pi_backend_doctor(tmp_path)['ready']
    for label in ('unverified wrapper', 'pi 1.1.0-rc.1'):
        version[0] = label
        assert not pi_backend_doctor(tmp_path)['protocol_compatible']


def test_runtime_side_cancel_is_never_a_completed_worker(tmp_path, monkeypatch):
    fake = (Path(__file__).parent / 'fixtures' / 'fake_pi_rpc.py').resolve()
    monkeypatch.setenv('GOODTEAM_PI_PATH', str(fake))
    monkeypatch.setenv('FAKE_PI_RUNTIME_ABORTED', '1')
    jobs = WorkerJobStore(tmp_path)
    job = jobs.create_job(run_id='cancelled-attempt', role='research', adapter='pi',
                         task='Return local fixture result', context='', project_key='DEMO')
    result = jobs.run_job(job['id'])
    assert result['status'] == 'failed'
    assert result['failure_class'] == 'pi_aborted'
    assert result['session_status'] == 'aborted'
    metadata = jobs._read_json(jobs.root / job['id'] / 'pi_session.json')
    assert metadata['active_operation']['state'] == 'aborted'
    assert not metadata.get('settled_operation_id')


def test_stop_clears_pending_messages_before_abort_without_retry(tmp_path, monkeypatch):
    from goodteam_os.session_runtimes import PiSessionRuntime, PiRuntimeConfig
    from goodteam_os.pi_rpc import PiRpcError
    import pytest
    root = tmp_path / 'job'
    root.mkdir()
    runtime = PiSessionRuntime(project_root=tmp_path, job_root=root, job={'id': 'job'}, runtime_cwd=tmp_path,
        config=PiRuntimeConfig('unused', '', '', '', tmp_path / 'sessions'), stderr_path=root / 'stderr.txt')
    calls = []
    fail_clear = [False]
    class RPC:
        def request(self, command, **kwargs):
            calls.append(command['type'])
            if command['type'] == 'clear_queue' and fail_clear[0]:
                raise PiRpcError('unknown clear result')
            return {'success': True}
    runtime.rpc = RPC()
    runtime.metadata = {'status': 'running'}
    runtime.abort()
    assert calls == ['clear_queue', 'abort'] and runtime.aborted
    runtime.abort()
    assert len(calls) == 2
    runtime.aborted = False
    runtime.metadata = {'status': 'running'}
    fail_clear[0] = True
    with pytest.raises(PiRpcError, match='not confirmed'):
        runtime.abort()
    assert calls == ['clear_queue', 'abort', 'clear_queue', 'abort']
    assert runtime.aborted
    with pytest.raises(PiRpcError, match='not confirmed'):
        runtime.abort()
    assert len(calls) == 4

    # An aborted event can beat an RPC timeout. It must not erase queue uncertainty.
    runtime.aborted = False
    runtime.metadata = {"status": "running"}
    class TimeoutRPC:
        def request(self, command, **kwargs):
            calls.append(command['type'])
            if command['type'] == 'abort':
                runtime._on_record({'type': 'agent_settled', 'aborted': True})
            raise PiRpcError('lost response')
    runtime.rpc = TimeoutRPC()
    with pytest.raises(PiRpcError, match='lost response'):
        runtime.abort()
    assert runtime.aborted and runtime.metadata['queue_clear_unconfirmed']
    before = len(calls)
    with pytest.raises(PiRpcError, match='not confirmed'):
        runtime.abort()
    assert len(calls) == before
