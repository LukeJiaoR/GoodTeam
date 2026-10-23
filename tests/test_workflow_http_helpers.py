"""Fixture diagnostics must preserve failures rather than hide them as waits."""

import pytest

import workflow_http_helpers as helpers


class ReadinessServer:
    def __init__(self, *states):
        self.states = iter(states)
        self.calls = []

    def ok(self, path):
        self.calls.append(path)
        return next(self.states)


def home(status, *, error=None):
    return {"readiness": {
        "execution": {"status": status, "current_step_id": "repository_understanding"},
        "steps": [{"id": "repository_understanding", "error": error}],
    }}


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_readiness_wait_reports_terminal_step_error_without_waiting(monkeypatch, status):
    server = ReadinessServer(home(status, error={
        "code": "repository_snapshot_failed", "message": "Trusted Git executable was not available.",
    }))
    monkeypatch.setattr(helpers.time, "sleep", lambda _: pytest.fail("Terminal failure was polled again"))

    with pytest.raises(AssertionError, match=(
        rf"Recovery fixture readiness {status} at repository_understanding: "
        r"repository_snapshot_failed: Trusted Git executable was not available\."
    )):
        helpers.wait_for_readiness_input(server)

    assert server.calls == ["/api/projects/RECOVERY/home"]


def test_readiness_wait_preserves_pending_poll_and_returns_observed_input(monkeypatch):
    ready = home("needs_input")
    server = ReadinessServer(home("running"), ready)
    sleeps = []
    monkeypatch.setattr(helpers.time, "sleep", sleeps.append)

    assert helpers.wait_for_readiness_input(server) is ready
    assert server.calls == ["/api/projects/RECOVERY/home"] * 2
    assert sleeps == [0.02]


def test_readiness_wait_does_not_promote_stalled_execution(monkeypatch):
    server = ReadinessServer(home("running"))
    ticks = iter([0, 0, helpers.WAIT_SECONDS + 1])
    monkeypatch.setattr(helpers.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(helpers.time, "sleep", lambda _: None)

    with pytest.raises(AssertionError, match="Recovery fixture condition did not become ready"):
        helpers.wait_for_readiness_input(server)


def test_readiness_wait_reports_missing_step_error_without_masking_terminal_state():
    server = ReadinessServer({"readiness": {"execution": {"status": "failed"}, "steps": []}})

    with pytest.raises(AssertionError, match="Recovery fixture readiness failed at unknown: unknown"):
        helpers.wait_for_readiness_input(server)
