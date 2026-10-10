"""Real HTTP/write-boundary regression with the existing isolated fake fixture."""
from workflow_http_helpers import RecoveryServer, decisions, prepare_security_gate


def test_direct_http_request_cannot_bypass_reason_or_manufacture_a_positive_review(tmp_path):
    server = RecoveryServer(tmp_path)
    try:
        server.start()
        binding = prepare_security_gate(server)
        before = server.state()
        prior_decisions = decisions(server)
        for note in ['', ' \n\t']:
            status, payload = server.request('/api/approve', {
                **binding, 'note': note,
                'client_context': {'recommendation': 'approve', 'reason_required': False},
            })
            assert status == 400
            assert payload['message'] == 'Record why you are continuing despite the current review limitations.'
            after = server.state()
            assert after['state'] == before['state']
            assert after['worker_jobs'] == before['worker_jobs']
            assert decisions(server) == prior_decisions
        note = 'Fake-only HTTP acceptance: continue with simulated evidence to verify the approval ledger.'
        server.ok('/api/approve', {**binding, 'note': note})
        added = [item for item in decisions(server) if item['gate'] == 'security']
        assert len(added) == 1 and added[0]['note'] == note
    finally:
        server.stop()
