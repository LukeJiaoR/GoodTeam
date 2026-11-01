"""Real local HTTP transport, durable quota and cancellation contract evidence.

The upstream is a loopback model-free server. No native Codex, external provider,
host login, paid model, containment or effective managed-route proof is used.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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

