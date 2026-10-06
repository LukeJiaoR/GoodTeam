"""The actual bridge must quiesce before its parent publishes a response."""
from goodteam_os.skills.codex_bridge import BridgeRelayPump
from test_codex_bridge import bridge, upstream as upstream
from test_codex_relay import call, owned_relay


def test_parent_publishes_only_after_forwarder_exits(tmp_path, upstream, monkeypatch):
    target = bridge(tmp_path, upstream)
    quiescent = []
    original = BridgeRelayPump._send

    def observe(pump, frame):
        if frame["type"] == "response":
            quiescent.append(pump._active is None)
        return original(pump, frame)

    monkeypatch.setattr(BridgeRelayPump, "_send", observe)
    with owned_relay(target) as (port, _, _):
        assert call(port)[0] == 200
        status, _, body = call(port)
        assert status == 403 and b"bridge_budget_exhausted" in body
    assert quiescent == [True, True]
    assert len(upstream.requests) == 1
    assert target.boundaries()["upstream_requests_allocated"] == 1
