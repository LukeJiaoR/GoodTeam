"""Model-free real relay child/pipe/HTTP evidence; no Docker admission claim."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from goodteam_os.skills import codex_relay
from goodteam_os.skills.codex_bridge import BridgeRelayPump
from goodteam_os.skills.codex_relay import FrameReader, decode_body, encode_body, write_frame
from goodteam_os.skills.models import SkillError
from test_codex_bridge import TEST_BEARER, bridge, request, upstream as upstream


@contextmanager
def owned_relay(target):
    executable = codex_relay.__file__
    process = subprocess.Popen([sys.executable, "-I", "-u", executable, "--bind", "127.0.0.1", "--port", "0"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env={"PATH": "/usr/bin:/bin"}, start_new_session=True)
    reader = FrameReader(process.stdout.fileno())
    ready, expires = None, time.monotonic() + 3
    while ready is None and time.monotonic() < expires:
        ready = reader.read()
    assert ready and ready["type"] == "ready" and ready["bind"] == "127.0.0.1"
    pump = BridgeRelayPump(target, input_fd=process.stdout.fileno(), output_fd=process.stdin.fileno())
    executor = ThreadPoolExecutor(max_workers=1)
    job = executor.submit(pump.run)
    try:
        yield ready["port"], process, job
    finally:
        # Only this owned child is stopped. No ambient processes/Docker/network.
        process.terminate()
        try:
            process.wait(3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(3)
        try:
            job.result(4)
        finally:
            executor.shutdown(wait=True)
            process.stdin.close()
            process.stdout.close()
        assert process.poll() is not None


def call(port, *, body=None, method="POST", path="/v1/responses", headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body if body is not None else request(), headers=headers or {})
        result = connection.getresponse()
        return result.status, result.getheaders(), result.read()
    finally:
        connection.close()


def test_actual_http_child_pipe_host_http_path_keeps_auth_out_of_relay(tmp_path, upstream, monkeypatch):
    target = bridge(tmp_path, upstream)
    host_frames = []
    original = codex_relay.write_frame
    def observe(fd, frame, **kwargs):
        host_frames.append(frame)
        return original(fd, frame, **kwargs)
    monkeypatch.setattr(codex_relay, "write_frame", observe)
    with owned_relay(target) as (port, process, job):
        status, headers, body = call(port)
        assert status == 200 and body == upstream.response
        assert len(upstream.requests) == 1 and upstream.requests[0][1] == "Bearer " + TEST_BEARER
        assert not any(name.lower() in {"set-cookie", "authorization", "location"} for name, _ in headers)
        assert TEST_BEARER not in json.dumps(host_frames)
        assert TEST_BEARER not in " ".join(process.args)
        assert not job.done()
        second, _, body = call(port)
        assert second == 403 and b"bridge_budget_exhausted" in body
    assert target.boundaries()["upstream_requests_allocated"] == 1


@pytest.mark.parametrize("method,path,headers", [
    ("GET", "/models", {}), ("POST", "/another-task", {}),
    ("POST", "/v1/responses", {"Authorization": "Borrowed worker credential"}),
    ("POST", "/v1/responses", {"Cookie": "Borrowed account"}),
])
def test_relay_rejects_other_endpoints_or_worker_auth_without_touching_host(tmp_path, upstream, method, path, headers):
    target = bridge(tmp_path, upstream)
    with owned_relay(target) as (port, _, _):
        status, _, _ = call(port, method=method, path=path, headers=headers)
        assert status in {400, 404}
        assert target.authority.auth_reads == 0 and not upstream.requests


def test_client_disconnect_crosses_pipe_and_retires_owned_host_transport(tmp_path, upstream):
    upstream.hold = True
    target = bridge(tmp_path, upstream)
    with owned_relay(target) as (port, _, _):
        worker = socket.create_connection(("127.0.0.1", port), timeout=2)
        body = request()
        worker.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nContent-Length: "
                       + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert upstream.accepted.wait(2)
        worker.shutdown(socket.SHUT_RDWR)
        worker.close()
        expires = time.monotonic() + 2
        while not target._retired and time.monotonic() < expires:
            time.sleep(0.01)
        assert target._retired
        stop = target.cancel()
        assert stop.local_transport_stopped and stop.remote_retirement_confirmed is False
        assert not upstream.remote_completed.is_set()
        upstream.release.set()
        assert upstream.remote_completed.wait(2)
        assert len(upstream.requests) == 1


def test_owned_relay_process_exit_closes_host_transport(tmp_path, upstream):
    upstream.hold = True
    target = bridge(tmp_path, upstream)
    with owned_relay(target) as (port, process, job):
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(call, port)
            assert upstream.accepted.wait(2)
            process.terminate()
            process.wait(3)
            job.result(3)
            with pytest.raises((OSError, http.client.HTTPException)):
                pending.result(3)
    assert target.cancel().local_transport_stopped
    assert target.boundaries()["upstream_requests_allocated"] == 1


def test_invalid_parent_pipe_frame_cannot_forward_or_leave_transport_running(tmp_path, upstream):
    target = bridge(tmp_path, upstream)
    input_fd, input_writer = os.pipe()
    output_reader, output_fd = os.pipe()
    try:
        write_frame(input_writer, {"type": "request", "request_id": "not-parent-owned", "body": ""})
        pump = BridgeRelayPump(target, input_fd=input_fd, output_fd=output_fd)
        with pytest.raises(SkillError) as error:
            pump.run()
        assert error.value.code == "bridge_relay_invalid"
        assert target._retired and not upstream.requests
    finally:
        for fd in (input_fd, input_writer, output_reader, output_fd):
            os.close(fd)


@pytest.mark.parametrize("raw", [b'{"type":"request","type":"cancel"}\n', b'[]\n', b'{"x":NaN}\n'])
def test_pipe_json_is_finite_unambiguous_and_bounded(raw):
    reader_fd, writer_fd = os.pipe()
    try:
        os.write(writer_fd, raw)
        with pytest.raises(ValueError):
            FrameReader(reader_fd).read()
    finally:
        os.close(reader_fd)
        os.close(writer_fd)


def test_body_codec_rejects_noncanonical_or_large_content():
    assert decode_body(encode_body(b"source bytes\x00")) == b"source bytes\x00"
    with pytest.raises(ValueError):
        decode_body("not base64")
    with pytest.raises(ValueError):
        encode_body(b"x" * (codex_relay.MAX_BODY + 1))
