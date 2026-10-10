"""Real local HTTP transport, durable quota and cancellation contract evidence.

The upstream is a loopback model-free server. No native Codex, external provider,
host login, paid model, containment or effective managed-route proof is used.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest

from goodteam_os.skills.codex_bridge import (
    BridgeScope, HostProviderAuthority, HostRouteBinding, TaskProviderBridge,
)
from goodteam_os.skills.models import SkillError, content_digest
from goodteam_os.skills.task_locks import TaskSkillIdentity

TEST_BEARER = "MODEL_FREE_HOST_ONLY_BEARER"


class FixtureAuthority(HostProviderAuthority):
    verification_kind = "contract_fixture"

    def __init__(self, url):
        self.binding = HostRouteBinding("loopback-fixture", content_digest(b"effective-fixture"), url)
        self.revoked = False
        self.binds = self.checks = self.auth_reads = 0

    def bind(self, scope):
        self.binds += 1
        return self.binding

    def check(self, binding, scope):
        self.checks += 1
        if self.revoked or binding != self.binding:
            raise ValueError("Private upstream diagnostic " + TEST_BEARER)

    def authorization(self, binding, scope):
        self.auth_reads += 1
        return TEST_BEARER


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except ConnectionResetError:
                pass  # Expected peer closure after a model-free cancellation.

        def do_POST(self):
            server = self.server
            body = self.rfile.read(int(self.headers["Content-Length"]))
            server.requests.append((self.path, self.headers.get("Authorization"), body))
            server.accepted.set()
            if server.hold:
                server.release.wait(5)
            # Deliberately continue simulated remote work after client cancel.
            server.remote_completed.set()
            result = server.response
            try:
                self.send_response(server.status)
                self.send_header("Content-Type", server.content_type)
                self.send_header("Content-Length", str(len(result)))
                self.send_header("Set-Cookie", "untrusted-upstream-cookie=" + TEST_BEARER)
                self.send_header("Location", server.redirect)
                self.end_headers()
                self.wfile.write(result)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                server.client_disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests = []
    server.accepted = threading.Event()
    server.release = threading.Event()
    server.remote_completed = threading.Event()
    server.client_disconnected = threading.Event()
    server.hold = False
    server.response = b'{"id":"fixture-response","output":[]}'
    server.status = 200
    server.content_type = "application/json"
    server.redirect = "http://127.0.0.1:1/never-contact-this"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


def scope(tmp_path, **changes):
    root = tmp_path.resolve()
    return BridgeScope(TaskSkillIdentity("direct_task", "DEMO", "bridge-review"), str(root / "worker"),
                       "task-lock:fixture", content_digest(b"locked-input"), "review-" + uuid4().hex,
                       "explicit-model", "one-authorized-trial", int(time.time()) + 3600, **changes)


def bridge(tmp_path, upstream, *, selected=None, authority=None):
    authority = authority or FixtureAuthority(f"http://127.0.0.1:{upstream.server_port}/v1/responses")
    return TaskProviderBridge(selected or scope(tmp_path), state_root=tmp_path.resolve() / "operator", authority=authority)


def request(**changes):
    return json.dumps({"model": "explicit-model", "input": "Inspect this captured source.",
                       "stream": False, **changes}).encode()


def forward(target, body=None):
    return target.forward("POST", "/v1/responses", body or request())


def test_production_default_has_no_route_and_does_not_create_state(tmp_path):
    with pytest.raises(SkillError, match="No effective host provider authority") as error:
        TaskProviderBridge(scope(tmp_path), state_root=tmp_path / "operator")
    assert error.value.code == "bridge_authority_unavailable"
    assert not (tmp_path / "operator").exists()


def test_actual_loopback_request_has_host_auth_and_restricted_payload(tmp_path, upstream):
    target = bridge(tmp_path, upstream)
    result = forward(target, request(store=True, max_output_tokens=90000, tool_choice="auto", parallel_tool_calls=True))
    path, auth, raw = upstream.requests[0]
    assert path == "/v1/responses" and auth == "Bearer " + TEST_BEARER
    sent = json.loads(raw)
    assert sent["model"] == "explicit-model"
    assert sent["tools"] == [] and sent["tool_choice"] == "none"
    assert sent["store"] is False and sent["background"] is False and sent["parallel_tool_calls"] is False
    assert sent["max_output_tokens"] == 2048
    assert sent["reasoning"] == {"effort": "low"} and sent["service_tier"] == "default"
    assert result.content_type == "application/json" and result.body == upstream.response
    assert not hasattr(result, "headers") and TEST_BEARER not in repr(result)
    evidence = target.boundaries()
    assert evidence["upstream_requests_allocated"] == 1
    assert evidence["usd_limit"] is None and evidence["provider_price_verified"] is False
    assert evidence["remote_retirement_supported"] is False and evidence["production_admission_composed"] is False
    stored = b"".join(p.read_bytes() for p in (tmp_path / "operator").rglob("*") if p.is_file())
    assert TEST_BEARER.encode() not in stored and b"127.0.0.1" not in stored


@pytest.mark.parametrize("changes", [
    {"model": "other-model"}, {"tools": [{"type": "web_search"}]},
    {"previous_response_id": "borrowed-context"}, {"background": True}, {"stream": "true"},
    {"upstream_url": "https://attacker.invalid"}, {"authorization": "borrowed-account"},
    {"input": [{"type": "function_call", "call_id": "wrong-context"}]},
    {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "file:///secret"}]}]},
    {"tool_choice": {"type": "function", "name": "exec"}}, {"input": []},
    {"service_tier": "priority"},
])
def test_untrusted_request_cannot_select_route_model_tools_or_old_context(tmp_path, upstream, changes):
    target = bridge(tmp_path, upstream)
    with pytest.raises(SkillError) as error:
        forward(target, request(**changes))
    assert error.value.code == "bridge_request_invalid"
    assert target.authority.auth_reads == 0 and not upstream.requests
    assert target.boundaries()["upstream_requests_allocated"] == 0


@pytest.mark.parametrize("body", [
    b'{"model":"other","model":"explicit-model","input":"x"}',
    b'{"model":"explicit-model","input":"x","metadata":{"cost":NaN}}',
    b"[1]", b"\xff", b'{"model":"explicit-model"}',
])
def test_invalid_json_never_consumes_a_request(tmp_path, upstream, body):
    target = bridge(tmp_path, upstream)
    with pytest.raises(SkillError) as error:
        forward(target, body)
    assert error.value.code == "bridge_request_invalid"
    assert not upstream.requests and target.boundaries()["upstream_requests_allocated"] == 0


@pytest.mark.parametrize("method,path", [("GET", "/v1/responses"), ("POST", "/models"),
                                         ("POST", "/v1/responses?route=other")])
def test_only_one_fixed_endpoint_is_exposed(tmp_path, upstream, method, path):
    target = bridge(tmp_path, upstream)
    with pytest.raises(SkillError):
        target.forward(method, path, request())
    assert not upstream.requests and target.boundaries()["upstream_requests_allocated"] == 0


@pytest.mark.parametrize("status", [301, 307, 401, 429, 500])
def test_upstream_failure_redirects_and_rate_limits_are_not_retried(tmp_path, upstream, status):
    target = bridge(tmp_path, upstream)
    upstream.status, upstream.response = status, ("Private diagnostic " + TEST_BEARER).encode()
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == "bridge_upstream_failed" and TEST_BEARER not in str(error.value)
    with pytest.raises(SkillError) as exhausted:
        forward(target)
    assert exhausted.value.code == "bridge_budget_exhausted" and len(upstream.requests) == 1


def test_budget_persists_across_new_contexts_and_controller_restart(tmp_path, upstream):
    selected = scope(tmp_path)
    target = bridge(tmp_path, upstream, selected=selected)
    forward(target)
    with pytest.raises(SkillError) as reused:
        bridge(tmp_path, upstream, selected=selected)
    assert reused.value.code == "bridge_context_used"
    retry = bridge(tmp_path, upstream, selected=replace(selected, context_id="review-" + uuid4().hex))
    with pytest.raises(SkillError) as exhausted:
        forward(retry)
    assert exhausted.value.code == "bridge_budget_exhausted" and len(upstream.requests) == 1


@pytest.mark.parametrize("change", [{"model": "another"}, {"snapshot_digest": content_digest(b"new-input")},
                                     {"max_requests": 2}, {"task_lock_ref": "different-task-lock"}])
def test_authorization_identity_cannot_reset_or_change_the_bound_budget(tmp_path, upstream, change):
    selected = scope(tmp_path)
    bridge(tmp_path, upstream, selected=selected)
    with pytest.raises(SkillError) as error:
        bridge(tmp_path, upstream, selected=replace(selected, context_id="review-" + uuid4().hex, **change))
    assert error.value.code == "bridge_authorization_conflict" and not upstream.requests


def test_concurrent_contexts_share_one_real_upstream_slot(tmp_path, upstream):
    selected = scope(tmp_path)
    targets = [bridge(tmp_path, upstream, selected=replace(selected, context_id="review-" + uuid4().hex)) for _ in range(8)]
    def run(target):
        try:
            forward(target)
            return "ok"
        except SkillError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run, targets))
    assert results.count("ok") == 1 and results.count("bridge_budget_exhausted") == 7
    assert len(upstream.requests) == 1


def test_independent_processes_share_the_durable_request_budget(tmp_path, upstream):
    selected = scope(tmp_path)
    script = """
import json,sys
from pathlib import Path
from goodteam_os.skills.codex_bridge import BridgeScope, TaskProviderBridge
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.models import SkillError
from test_codex_bridge import FixtureAuthority, request
value=json.loads(sys.argv[1]);value['task']=TaskSkillIdentity(**value['task'])
target=TaskProviderBridge(BridgeScope(**value),state_root=Path(sys.argv[2]),authority=FixtureAuthority(sys.argv[3]))
try:
 target.forward('POST','/v1/responses',request());print('ok')
except SkillError as error:
 print(error.code)
"""
    processes = []
    endpoint = f"http://127.0.0.1:{upstream.server_port}/v1/responses"
    try:
        for _ in range(4):
            value = asdict(replace(selected, context_id="review-" + uuid4().hex))
            processes.append(subprocess.Popen([sys.executable, "-c", script, json.dumps(value),
                             str(tmp_path.resolve() / "operator"), endpoint], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "src:tests",
                             "PYTHONDONTWRITEBYTECODE": "1", "GOODTEAM_DISABLE_TG_NETWORK": "1"}))
        results = []
        for child in processes:
            out, _ = child.communicate(timeout=10)
            assert child.returncode == 0
            results.append(out.decode().strip())
        assert results.count("ok") == 1 and results.count("bridge_budget_exhausted") == 3
        assert len(upstream.requests) == 1
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.wait(3)


def test_quota_is_durable_before_external_connect(tmp_path, upstream, monkeypatch):
    import http.client
    target = bridge(tmp_path, upstream)
    def fail_connect(self):
        assert target.boundaries()["upstream_requests_allocated"] == 1
        raise OSError("Private connection diagnostic " + TEST_BEARER)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", fail_connect)
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == "bridge_upstream_failed" and TEST_BEARER not in str(error.value)
    assert not upstream.requests and target.boundaries()["upstream_requests_allocated"] == 1


def test_real_cancel_stops_local_transport_but_remote_mock_keeps_working(tmp_path, upstream):
    upstream.hold = True
    target = bridge(tmp_path, upstream)
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(forward, target)
        assert upstream.accepted.wait(2)
        started = time.monotonic()
        stop = target.cancel()
        assert time.monotonic() - started < 1.5
        assert stop.context_retired and stop.local_transport_stopped
        assert stop.remote_retirement_confirmed is False
        with pytest.raises(SkillError) as stopped:
            job.result(2)
        assert stopped.value.code == "bridge_cancelled"
        assert not upstream.remote_completed.is_set()
        upstream.release.set()
        assert upstream.remote_completed.wait(2)
    with pytest.raises(SkillError) as retired:
        forward(target)
    assert retired.value.code == "bridge_context_retired"
    assert len(upstream.requests) == 1 and target.boundaries()["upstream_requests_allocated"] == 1


def test_cancel_before_request_never_forwards_and_context_cannot_resume(tmp_path, upstream):
    selected = scope(tmp_path)
    target = bridge(tmp_path, upstream, selected=selected)
    assert target.cancel().local_transport_stopped
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == "bridge_context_retired"
    with pytest.raises(SkillError) as error:
        bridge(tmp_path, upstream, selected=selected)
    assert error.value.code == "bridge_context_used"
    assert not upstream.requests and target.boundaries()["upstream_requests_allocated"] == 0


def test_deadline_stops_real_transport_with_spent_quota_and_unknown_remote_retirement(tmp_path, upstream):
    upstream.hold = True
    target = bridge(tmp_path, upstream, selected=scope(tmp_path, deadline_seconds=1))
    started = time.monotonic()
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == "bridge_deadline" and time.monotonic() - started < 2
    assert target.cancel().remote_retirement_confirmed is False
    assert target.boundaries()["upstream_requests_allocated"] == 1
    assert len(upstream.requests) == 1


def test_host_route_drift_revokes_before_next_request_without_secret_error(tmp_path, upstream):
    target = bridge(tmp_path, upstream)
    target.authority.revoked = True
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == "bridge_authorization_revoked" and TEST_BEARER not in str(error.value)
    assert target.authority.auth_reads == 0 and not upstream.requests
    retirement = tmp_path / "operator" / target.scope.authorization_id / (target.scope.context_id + ".retired")
    assert retirement.read_bytes() == b"retired\n"
    target.authority.revoked = False
    with pytest.raises(SkillError) as retired:
        forward(target)
    assert retired.value.code == "bridge_context_retired"
    assert not upstream.requests and target.boundaries()["upstream_requests_allocated"] == 0


def test_synchronous_inflight_revocation_retires_before_watcher_can_observe_it(tmp_path, upstream):
    class ObservedAuthority(FixtureAuthority):
        def __init__(self, url):
            super().__init__(url)
            self.forward_ident = None
            self.watcher_checked = threading.Event()
            self.release_watcher = threading.Event()
            self.watcher_returned = threading.Event()
            self.watcher_timed_out = threading.Event()

        def check(self, binding, selected):
            super().check(binding, selected)
            if self.forward_ident is not None and threading.get_ident() != self.forward_ident:
                # Hold a successful watcher check while the real HTTP response
                # makes forward() observe revocation synchronously. No fake
                # transport, sleeps or call-count-dependent authorization.
                self.watcher_checked.set()
                try:
                    if not self.release_watcher.wait(5):
                        self.watcher_timed_out.set()
                        raise AssertionError("The test did not release the observed watcher check")
                finally:
                    self.watcher_returned.set()

    authority = ObservedAuthority(f"http://127.0.0.1:{upstream.server_port}/v1/responses")
    target = bridge(tmp_path, upstream, selected=scope(tmp_path, max_requests=2), authority=authority)
    upstream.hold = True

    def owned_forward():
        authority.forward_ident = threading.get_ident()
        return forward(target)

    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(owned_forward)
        try:
            assert upstream.accepted.wait(2)
            assert authority.watcher_checked.wait(2)
            authority.revoked = True
            upstream.release.set()
            with pytest.raises(SkillError) as error:
                job.result(2)
            assert error.value.code == "bridge_authorization_revoked"
            assert TEST_BEARER not in str(error.value)
            assert not authority.watcher_timed_out.is_set()
            assert not authority.watcher_returned.is_set()
        finally:
            authority.release_watcher.set()
            upstream.release.set()

    retirement = tmp_path / "operator" / target.scope.authorization_id / (target.scope.context_id + ".retired")
    assert retirement.read_bytes() == b"retired\n"
    authority.revoked = False
    with pytest.raises(SkillError) as retired:
        forward(target)
    assert retired.value.code == "bridge_context_retired"
    assert len(upstream.requests) == 1
    assert target.boundaries()["upstream_requests_allocated"] == 1


def test_inflight_host_revocation_stops_real_transport_without_remote_retirement_claim(tmp_path, upstream):
    upstream.hold = True
    target = bridge(tmp_path, upstream)
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(forward, target)
        assert upstream.accepted.wait(2)
        target.authority.revoked = True
        with pytest.raises(SkillError) as error:
            job.result(2)
        assert error.value.code == "bridge_authorization_revoked"
        assert TEST_BEARER not in str(error.value)
    stop = target.cancel()
    assert stop.local_transport_stopped and stop.remote_retirement_confirmed is False
    assert target.boundaries()["upstream_requests_allocated"] == 1


@pytest.mark.parametrize("response,type_,code", [
    (TEST_BEARER.encode(), "application/json", "bridge_response_invalid"),
    (b"x" * 129, "application/json", "bridge_response_limit"),
    (b"<html>private error</html>", "text/html", "bridge_response_invalid"),
])
def test_response_limits_and_secret_reflection_do_not_refund_or_leak(tmp_path, upstream, response, type_, code):
    target = bridge(tmp_path, upstream, selected=scope(tmp_path, max_response_bytes=128))
    upstream.response, upstream.content_type = response, type_
    with pytest.raises(SkillError) as error:
        forward(target)
    assert error.value.code == code and TEST_BEARER not in str(error.value)
    assert target.boundaries()["upstream_requests_allocated"] == 1


def test_sse_is_bounded_without_forwarding_upstream_headers(tmp_path, upstream):
    upstream.content_type = "text/event-stream; charset=utf-8"
    upstream.response = b'data: {"type":"response.completed"}\n\n'
    result = forward(bridge(tmp_path, upstream), request(stream=True))
    assert result.body == upstream.response and result.content_type == "text/event-stream"


def test_state_inside_worker_is_rejected_before_any_call(tmp_path, upstream):
    selected = scope(tmp_path)
    authority = FixtureAuthority(f"http://127.0.0.1:{upstream.server_port}/v1/responses")
    with pytest.raises(SkillError) as error:
        TaskProviderBridge(selected, state_root=tmp_path / "worker" / "ledger", authority=authority)
    assert error.value.code == "bridge_ledger_unsafe" and not upstream.requests


@pytest.mark.parametrize("endpoint", ["http://outside.invalid/v1/responses", "https://external.invalid/v1/responses",
    "http://127.0.0.1:1234/v1/responses?token=secret", "http://user:secret@127.0.0.1/v1/responses"])
def test_fixture_cannot_borrow_remote_authority_or_unsafe_routing(tmp_path, endpoint):
    with pytest.raises(SkillError) as error:
        TaskProviderBridge(scope(tmp_path), state_root=tmp_path / "operator", authority=FixtureAuthority(endpoint))
    assert error.value.code == "bridge_route_unverified" and not (tmp_path / "operator").exists()
