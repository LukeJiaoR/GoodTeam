"""Isolated framing, deadline, and connection-admission regressions."""

import io
import json
import queue
import socket
import threading
from email.message import Message
from http.client import HTTPResponse
from types import SimpleNamespace

import pytest

from goodteam_os import http_transport as transport
from goodteam_os import web


class Body(io.BytesIO):
    def __init__(self, value=b""):
        super().__init__(value)
        self.reads = []

    def read1(self, size):
        self.reads.append(size)
        assert 0 < size <= 65536
        return super().read1(size)


def reader(headers, body=b""):
    message = Message()
    for key, value in headers:
        message[key] = value
    connection = SimpleNamespace(gettimeout=lambda: 15, settimeout=lambda value: None)
    return SimpleNamespace(headers=message, rfile=Body(body), connection=connection, close_connection=False)


@pytest.mark.parametrize("value", ["-1", "+2", "1.5", "", "1, 1", "one", "١", "1\n 2"])
def test_invalid_lengths_fail_without_read(value):
    handler = reader([("Content-Length", value)], b"{}")
    with pytest.raises(transport.RequestBodyError) as error:
        transport.read_request_body(handler)
    assert error.value.status == 400
    assert error.value.code == "invalid_content_length"
    assert handler.close_connection and handler.rfile.reads == []


@pytest.mark.parametrize("headers,code,status", [
    ([("Content-Length", "2"), ("Content-Length", "2")], "invalid_content_length", 400),
    ([("Content-Length", "2"), ("Content-Length", "3")], "invalid_content_length", 400),
    ([("Transfer-Encoding", "chunked")], "unsupported_transfer_encoding", 400),
    ([("Transfer-Encoding", "identity"), ("Content-Length", "2")], "unsupported_transfer_encoding", 400),
    ([("Content-Length", str(transport.MAX_REQUEST_BODY_BYTES + 1))], "request_body_too_large", 413),
    ([("Content-Length", "9" * 5000)], "request_body_too_large", 413),
])
def test_rejected_framing_never_reads(headers, code, status):
    handler = reader(headers)
    with pytest.raises(transport.RequestBodyError) as error:
        transport.read_request_body(handler)
    assert (error.value.code, error.value.status) == (code, status)
    assert handler.rfile.reads == []
    assert handler.close_connection


@pytest.mark.parametrize("headers", [[], [("Content-Length", "0")], [("Content-Length", "00000")]])
def test_empty_body_compatibility(headers):
    handler = reader(headers)
    assert transport.read_request_body(handler) == b""
    assert handler.rfile.reads == []


def test_exact_cap_and_json_escaped_document_are_accepted():
    body = b"x" * transport.MAX_REQUEST_BODY_BYTES
    handler = reader([("Content-Length", str(len(body)))], body + b"not part of body")
    assert transport.read_request_body(handler) == body
    assert handler.rfile.read() == b"not part of body"
    document = json.dumps({"content": "\u0001" * (1024 * 1024)}).encode()
    assert len(document) < transport.MAX_REQUEST_BODY_BYTES
    handler = reader([("Content-Length", "000" + str(len(document)))], document)
    assert transport.read_request_body(handler) == document


def test_total_body_deadline_cannot_be_extended_by_trickling(monkeypatch):
    now = [0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(transport, "REQUEST_BODY_TIMEOUT_SECONDS", 2)
    handler = reader([("Content-Length", "3")])
    timeouts = []
    handler.connection.settimeout = timeouts.append

    def trickle(_size):
        now[0] += 1
        return b"x"

    handler.rfile.read1 = trickle
    with pytest.raises(transport.RequestBodyError) as error:
        transport.read_request_body(handler)
    assert (error.value.code, error.value.status) == ("request_body_timeout", 408)
    assert timeouts == [2, 1, 15]
    assert handler.close_connection


@pytest.fixture(params=["short", "production"])
def dashboard(request, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "context_estimate_for_request", lambda workspace, body: calls.append(body) or {"ok": True})
    if request.param == "short":
        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(tmp_path))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
    else:
        created = queue.Queue()

        class Server(web.ThreadingHTTPServer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.put(self)

        monkeypatch.setattr(web, "ThreadingHTTPServer", Server)
        thread = threading.Thread(target=web.serve, kwargs={"workspace": tmp_path, "port": 0}, daemon=True)
        thread.start()
        server = created.get(timeout=5)
    try:
        yield server, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def exchange(server, headers, body=b"", *, half_close=False, path="/api/context-estimate", origin=None):
    with socket.create_connection(server.server_address, timeout=3) as client:
        host = f"127.0.0.1:{server.server_port}"
        lines = [f"POST {path} HTTP/1.1", f"Host: {host}", f"Origin: {origin or 'http://' + host}"]
        lines.extend(f"{key}: {value}" for key, value in headers)
        client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        if half_close:
            client.shutdown(socket.SHUT_WR)
        response = HTTPResponse(client)
        response.begin()
        payload = json.loads(response.read())
        assert client.recv(1) == b""
        return response.status, payload


@pytest.mark.parametrize("headers,body,half_close,expected", [
    ([("Content-Length", "-1")], b"", False, (400, "invalid_content_length")),
    ([("Content-Length", "abc")], b"", False, (400, "invalid_content_length")),
    ([("Content-Length", str(transport.MAX_REQUEST_BODY_BYTES + 1))], b"", False, (413, "request_body_too_large")),
    ([("Content-Length", "2"), ("Content-Length", "2")], b"", False, (400, "invalid_content_length")),
    ([("Transfer-Encoding", "chunked"), ("Content-Length", "2")], b"", False, (400, "unsupported_transfer_encoding")),
    ([("Content-Length", "10")], b"{}", True, (400, "incomplete_request_body")),
])
def test_both_handlers_reject_bad_transport_before_application(dashboard, headers, body, half_close, expected):
    server, calls = dashboard
    status, payload = exchange(server, headers, body, half_close=half_close)
    assert (status, payload["code"]) == expected
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["request_id"] and calls == []


def test_body_timeout_is_structured_and_next_request_succeeds(dashboard, monkeypatch):
    monkeypatch.setattr(transport, "REQUEST_IDLE_TIMEOUT_SECONDS", 0.05)
    server, calls = dashboard
    status, payload = exchange(server, [("Content-Length", "2")], b"{")
    assert (status, payload["code"]) == (408, "request_body_timeout")
    assert payload["request_id"] and calls == []
    assert exchange(server, [("Content-Length", "2")], b"{}")[0] == 200
    assert calls == [{}]


def test_existing_origin_rejection_precedes_body_validation(dashboard):
    server, calls = dashboard
    status, payload = exchange(server, [("Content-Length", "-1")], origin="https://other.example")
    assert (status, payload["code"]) == (403, "same_origin_required")
    assert calls == []


@pytest.mark.parametrize("headers,body", [([], b""), ([("Content-Length", "0")], b""), ([("Content-Length", "2")], b"{}")])
def test_legitimate_and_empty_requests_remain_compatible(dashboard, headers, body):
    server, calls = dashboard
    assert exchange(server, headers, body) == (200, {"ok": True})
    assert calls == [{}]


def test_cap_does_not_spawn_or_block_and_slots_recover(monkeypatch):
    entered = queue.Queue()
    released = queue.Queue()

    class Handler(transport.BoundedHTTPRequestHandler):
        timeout = 0.1

        def handle(self):
            entered.put(self.connection.gettimeout())
            try:
                self.rfile.read(1)
            except TimeoutError:
                pass
            finally:
                released.put(True)

    server = transport.BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, max_connections=1)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    first = socket.create_connection(server.server_address, timeout=3)
    try:
        assert entered.get(timeout=3) == 0.1
        with socket.create_connection(server.server_address, timeout=3) as extra:
            assert extra.recv(1) == b""
        assert entered.empty()
        released.get(timeout=3)
        # Wait on the actual semaphore release rather than assuming thread timing.
        assert server._connection_slots.acquire(timeout=3)
        server._connection_slots.release()
        with socket.create_connection(server.server_address, timeout=3) as healthy:
            assert entered.get(timeout=3) == 0.1
            healthy.sendall(b"x")
            released.get(timeout=3)
    finally:
        first.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_thread_start_failure_releases_slot(monkeypatch):
    server = transport.BoundedThreadingHTTPServer(("127.0.0.1", 0), transport.BoundedHTTPRequestHandler, max_connections=1)
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("thread start failed")))
    try:
        with pytest.raises(RuntimeError, match="thread start failed"):
            server.process_request(None, None)
        assert server._connection_slots.acquire(blocking=False)
        server._connection_slots.release()
    finally:
        server.server_close()


def test_incomplete_headers_time_out_without_dispatch(dashboard, monkeypatch):
    monkeypatch.setattr(transport.BoundedHTTPRequestHandler, "timeout", 0.05)
    server, calls = dashboard
    with socket.create_connection(server.server_address, timeout=3) as client:
        client.sendall(b"POST /api/context-estimate HTTP/1.1\r\nHost:")
        assert client.recv(1) == b""
    assert calls == []
    assert exchange(server, [("Content-Length", "2")], b"{}")[0] == 200


def test_read_timeout_does_not_limit_application_processing(dashboard, monkeypatch):
    monkeypatch.setattr(transport.BoundedHTTPRequestHandler, "timeout", 0.01)
    monkeypatch.setattr(transport, "REQUEST_BODY_TIMEOUT_SECONDS", 0.01)
    server, calls = dashboard

    def processing(workspace, body):
        # Longer than either read timeout, with no input still pending.
        threading.Event().wait(0.05)
        calls.append(body)
        return {"ok": True}

    monkeypatch.setattr(web, "context_estimate_for_request", processing)
    assert exchange(server, [("Content-Length", "2")], b"{}")[0] == 200
    assert calls == [{}]
