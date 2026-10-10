"""Deterministic loopback/pipe handoff regressions; the parent is model-free."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import os
import socketserver
import threading
import time

import pytest

from goodteam_os.skills import codex_relay

FIRST_BODY = b'{"first":"complete"}'
BUDGET_BODY = b'{"error":"bridge_budget_exhausted"}'


def call(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("POST", "/v1/responses", b'{"input":"fixture"}')
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@contextmanager
def relay_fixture(*, parent_release=None):
    input_fd, input_writer = os.pipe()
    output_reader, output_fd = os.pipe()
    stopped, accepted = threading.Event(), threading.Event()
    frames, errors = [], []
    reader = codex_relay.FrameReader(output_reader)

    def run_server():
        try:
            codex_relay.serve(bind="127.0.0.1", port=0, input_fd=input_fd,
                              output_fd=output_fd, deadline=3)
        except Exception as error:
            errors.append(error)

    def parent():
        requests = 0
        try:
            while not stopped.is_set():
                frame = reader.read()
                if frame is None:
                    continue
                frames.append(frame)
                if frame["type"] == "cancel":
                    continue
                assert frame["type"] == "request"
                requests += 1
                accepted.set()
                if requests == 1 and parent_release is not None:
                    assert parent_release.wait(3), "fixture parent was not released"
                codex_relay.write_frame(input_writer, {
                    "type": "response", "request_id": frame["request_id"],
                    "status": 200 if requests == 1 else 403,
                    "content_type": "application/json",
                    "body": codex_relay.encode_body(FIRST_BODY if requests == 1 else BUDGET_BODY),
                })
        except Exception as error:
            errors.append(error)

    server_thread = threading.Thread(target=run_server)
    parent_thread = None
    server_thread.start()
    try:
        ready, expires = None, time.monotonic() + 3
        while ready is None and time.monotonic() < expires:
            ready = reader.read()
        assert ready and ready["type"] == "ready"
        parent_thread = threading.Thread(target=parent)
        parent_thread.start()
        yield ready["port"], frames, accepted
    finally:
        if parent_release is not None:
            parent_release.set()
        stopped.set()
        if parent_thread is not None:
            parent_thread.join(4)
        os.close(input_writer)  # EOF stops only this fixture's relay.
        server_thread.join(4)
        for fd in (input_fd, output_reader, output_fd):
            os.close(fd)
        assert not server_thread.is_alive()
        assert parent_thread is None or not parent_thread.is_alive()
        assert errors == []


@pytest.mark.parametrize("delivery", ["held", "broken"])
def test_completed_response_releases_slot_before_client_can_issue_next_request(monkeypatch, delivery):
    published, release = threading.Event(), threading.Event()
    original = socketserver._SocketWriter.write

    def delayed_delivery(writer, data):
        result = original(writer, data)
        if data == FIRST_BODY:
            published.set()
            if delivery == "broken":
                raise BrokenPipeError("fixture delivery failed after the body was visible")
            if not release.wait(3):
                raise TimeoutError("fixture delivery was not released")
        return result

    monkeypatch.setattr(socketserver._SocketWriter, "write", delayed_delivery)
    with relay_fixture() as (port, frames, _):
        try:
            assert call(port) == (200, FIRST_BODY)
            assert published.wait(1)
            # The first handler cannot finish yet in the held case. A client
            # that already consumed its body must reach the parent, not 409.
            assert call(port) == (403, BUDGET_BODY)
            assert [frame["type"] for frame in frames] == ["request", "request"]
        finally:
            release.set()


def test_genuinely_overlapping_request_is_still_rejected_without_parent_forward():
    release = threading.Event()
    with relay_fixture(parent_release=release) as (port, frames, accepted):
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(call, port)
            try:
                assert accepted.wait(2)
                assert call(port) == (409, b"")
                assert len(frames) == 1
            finally:
                release.set()
            assert first.result(3) == (200, FIRST_BODY)
