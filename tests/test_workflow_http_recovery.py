"""Real HTTP/process acceptance: durable decisions outlive failure and races."""

from concurrent.futures import ThreadPoolExecutor
from http.client import RemoteDisconnected
import json
from urllib.error import URLError

import pytest

from workflow_http_helpers import RecoveryServer, approval_body, decisions, prepare_security_gate, retry_body


@pytest.fixture
def recovery(tmp_path):
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        yield server, prepare_security_gate(server)
    finally:
        server.stop()


def raw_state(server):
    return json.loads((server.root / "workspace/state/projects/RECOVERY/current.json").read_text())


def security_decisions(server):
    return [item for item in decisions(server) if item["gate"] == "security"]


def assert_unchanged_execution(server, expected):
    current = server.state()
    assert expected["state"] == current["state"]
    assert expected["worker_jobs"] == current["worker_jobs"]
    return current


@pytest.mark.parametrize("phase,recorded", [("before_publish", False), ("before_execute", True)])
def test_killed_approval_recovers_after_real_process_restart(recovery, phase, recorded):
    server, binding = recovery
    before = server.state()
    server.arm(phase)
    with ThreadPoolExecutor(max_workers=1) as pool:
        inflight = pool.submit(server.request, "/api/approve", binding)
        server.entered()
        durable = raw_state(server)
        assert "approved" == durable["approvals"]["security"]
        assert (1 if recorded else 0) == len(security_decisions(server))
        operation_id = durable["pending_transition"]["id"]
        server.stop(crash=True)
        with pytest.raises((RemoteDisconnected, ConnectionResetError, URLError)):
            inflight.result(timeout=5)
    server.start()
    interrupted = server.state()
    assert "blocked" == interrupted["state"]["next_node"]
    assert "interrupted" == interrupted["state"]["pending_transition"]["status"]
    assert operation_id == interrupted["state"]["pending_transition"]["id"]
    assert before["worker_jobs"] == interrupted["worker_jobs"]
    assert_unchanged_execution(server, interrupted)
    resumed = server.ok("/api/retry", retry_body(server.state()))
    assert "await_release_approval" == resumed["next_node"]
    assert [] == resumed["blockers"]
    assert [operation_id] == [item["workflow_operation_id"] for item in security_decisions(server)]
    completed = server.state()
    assert completed["worker_jobs"]
    assert all(job["adapter"] == "fake" and job["status"] == "completed" for job in completed["worker_jobs"])
    assert True is server.ok("/api/approve", binding)["replayed"]
    assert_unchanged_execution(server, completed)
    final = server.ok("/api/approve", approval_body(completed, "release"))
    assert "complete" == final["current_node"]
    assert 5 == len(decisions(server))


def test_cross_process_requests_share_authority_and_duplicate_approval_replays(recovery):
    server, binding = recovery
    peer = RecoveryServer(server.root)
    before = server.state()
    try:
        peer.start()
        server.arm("before_execute")
        with ThreadPoolExecutor(max_workers=1) as pool:
            inflight = pool.submit(server.request, "/api/approve", binding)
            try:
                server.entered()
                live = peer.state()
                assert "executing" == live["state"]["pending_transition"]["status"]
                assert before["worker_jobs"] == live["worker_jobs"]
                for endpoint, body in [("/api/approve", binding), ("/api/retry", retry_body(server.state()))]:
                    status, error = peer.request(endpoint, body)
                    assert 400 == status
                    assert "busy" in error["message"]
                assert 1 == len(security_decisions(peer))
            finally:
                server.release()
            status, result = inflight.result(timeout=15)
        assert 200 == status
        assert "await_release_approval" == result["next_node"]
        completed = peer.state()
        assert True is peer.ok("/api/approve", binding)["replayed"]
        assert_unchanged_execution(peer, completed)
        assert 1 == len(security_decisions(peer))
    finally:
        peer.stop()


def test_worker_timeout_requires_explicit_retry_and_retains_decision(recovery):
    server, binding = recovery
    (server.control / "worker-timeout").touch()
    failed = server.ok("/api/approve", binding)
    assert "blocked" == failed["next_node"]
    assert "approved" == failed["approvals"]["security"]
    blocked = server.state()
    failed_jobs = [job for job in blocked["worker_jobs"] if job["status"] == "failed"]
    assert 1 == len(failed_jobs)
    assert "timed out after 1 seconds" in failed_jobs[0]["summary"]
    original = security_decisions(server)
    assert_unchanged_execution(server, blocked)
    resumed = server.ok("/api/retry", retry_body(server.state()))
    assert "await_release_approval" == resumed["next_node"]
    assert original == security_decisions(server)
    recovered = server.state()
    assert any(job["status"] == "completed" and job["role"] == "implementation" for job in recovered["worker_jobs"])
    assert any(job["id"] == failed_jobs[0]["id"] and job["status"] == "failed" for job in recovered["worker_jobs"])
    assert all(job["adapter"] == "fake" for job in recovered["worker_jobs"])
    status, _error = server.request("/api/retry", retry_body(server.state()))
    assert 409 == status and _error["code"] == "retry_not_available"
    assert_unchanged_execution(server, recovered)
