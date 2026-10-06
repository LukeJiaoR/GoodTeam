"""Secret-free HTTP <-> parent pipe relay; standalone container source.

No auth lookup, external HTTP client, host config, model CLI or Docker API. Only
POST /v1/responses is forwarded as a bounded frame to the owning parent. The
parent resolves the effective host route. No production startup installs this
relay; its private-container/network admission is separate.
"""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import select
import socket
import sys
import threading
import time
from uuid import uuid4

MAX_BODY = 8 * 1024 * 1024
MAX_FRAME = 12 * 1024 * 1024


def encode_body(body):
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise ValueError("Relay body limit")
    return base64.b64encode(body).decode("ascii")


def decode_body(body):
    if not isinstance(body, str) or len(body) > MAX_BODY * 4 // 3 + 4:
        raise ValueError("Relay body limit")
    result = base64.b64decode(body, validate=True)
    if len(result) > MAX_BODY:
        raise ValueError("Relay body limit")
    return result


class FrameReader:
    def __init__(self, fd):
        self.fd, self.buffer = fd, bytearray()

    def read(self, timeout=0.05):
        if b"\n" not in self.buffer:
            if not select.select([self.fd], [], [], timeout)[0]:
                return None
            part = os.read(self.fd, 65536)
            if not part:
                if self.buffer:
                    raise ValueError("Truncated relay frame")
                raise EOFError
            self.buffer.extend(part)
        if len(self.buffer) > MAX_FRAME:
            raise ValueError("Relay frame limit")
        if b"\n" not in self.buffer:
            return None
        raw, _, tail = self.buffer.partition(b"\n")
        self.buffer = bytearray(tail)
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate relay field")
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError("Relay frame type")
        return value


def write_frame(fd, frame, *, timeout=2):
    data = memoryview(json.dumps(frame, allow_nan=False, separators=(",", ":")).encode() + b"\n")
    if len(data) > MAX_FRAME:
        raise ValueError("Relay frame limit")
    deadline = time.monotonic() + timeout
    while data:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
            raise TimeoutError("Relay parent transport stalled")
        # POSIX's minimum PIPE_BUF is 512. The owned pipe has one writer.
        written = os.write(fd, data[:512])
        data = data[written:]


class _Relay:
    def __init__(self, input_fd, output_fd, deadline):
        self.reader, self.output_fd = FrameReader(input_fd), output_fd
        self.deadline = deadline
        self.mutex = threading.RLock()
        self.writer = threading.Lock()
        self.active = None
        self.closed = threading.Event()

    def send(self, frame):
        with self.writer:
            write_frame(self.output_fd, frame)

    def response_loop(self):
        try:
            while not self.closed.is_set():
                frame = self.reader.read()
                if frame is None:
                    continue
                if (set(frame) != {"type", "request_id", "status", "content_type", "body"}
                        or frame["type"] != "response" or type(frame["status"]) is not int
                        or not 100 <= frame["status"] <= 599
                        or frame["content_type"] not in {"application/json", "text/event-stream"}):
                    raise ValueError("Invalid parent response")
                body = decode_body(frame["body"])
                with self.mutex:
                    if self.active is not None and self.active[0] == frame["request_id"]:
                        self.active[1].put_nowait((frame["status"], frame["content_type"], body))
        except Exception:
            self.closed.set()


def serve(*, bind, port, input_fd, output_fd, deadline=20):
    if bind not in {"127.0.0.1", "0.0.0.0"} or type(port) is not int or not 0 <= port <= 65535 or not 1 <= deadline <= 120:
        raise ValueError("Fixed relay listener required")
    relay = _Relay(input_fd, output_fd, deadline)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(relay.deadline)

        def reject(self, code):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def do_GET(self):
            self.reject(404)

        def do_POST(self):
            lengths = self.headers.get_all("Content-Length", [])
            if (self.path != "/v1/responses" or len(lengths) != 1 or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= MAX_BODY or self.headers.get("Transfer-Encoding")
                    or self.headers.get("Authorization") or self.headers.get("Cookie")):
                self.reject(400)
                return
            request_id, replies = uuid4().hex, queue.Queue(maxsize=1)
            with relay.mutex:
                if relay.active is not None or relay.closed.is_set():
                    self.reject(409)
                    return
                relay.active = request_id, replies
            sent = False
            try:
                expires = time.monotonic() + relay.deadline
                chunks, received, length = [], 0, int(lengths[0])
                while received < length:
                    remaining = expires - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Relay request deadline")
                    self.connection.settimeout(remaining)
                    chunk = self.rfile.read1(min(65536, length - received))
                    if not chunk:
                        self.reject(400)
                        return
                    chunks.append(chunk)
                    received += len(chunk)
                body = b"".join(chunks)
                relay.send({"type": "request", "request_id": request_id, "method": "POST",
                            "path": self.path, "body": encode_body(body)})
                sent = True
                expires = time.monotonic() + relay.deadline
                while not relay.closed.is_set() and time.monotonic() < expires:
                    try:
                        code, content_type, result = replies.get(timeout=0.05)
                        # The complete parent response ends the owned request.
                        # Release it before any HTTP bytes become visible: the
                        # client can submit again before this handler returns.
                        with relay.mutex:
                            if relay.active is not None and relay.active[0] == request_id:
                                relay.active = None
                        sent = False
                        self.send_response(code)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(result)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(result)
                        self.wfile.flush()
                        return
                    except queue.Empty:
                        pass
                    if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                        return  # Only the owning client disconnect retires it.
                self.reject(504)
            except (OSError, ValueError, TimeoutError):
                self.close_connection = True
            finally:
                self.close_connection = True
                if sent:
                    try:
                        relay.send({"type": "cancel", "request_id": request_id})
                    except (OSError, ValueError, TimeoutError):
                        relay.closed.set()
                with relay.mutex:
                    if relay.active is not None and relay.active[0] == request_id:
                        relay.active = None

    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    reader_thread = threading.Thread(target=relay.response_loop, daemon=True)
    reader_thread.start()
    try:
        relay.send({"type": "ready", "bind": bind, "port": server.server_port})
        relay.closed.wait()
    finally:
        relay.closed.set()
        server.shutdown()
        server.server_close()
        server_thread.join(1)
        reader_thread.join(1)


def source_bytes():
    return Path(__file__).read_bytes()


def main():
    parser = argparse.ArgumentParser(description="Owned secret-free provider relay; no production auto-start.")
    parser.add_argument("--bind", choices=["127.0.0.1", "0.0.0.0"], required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    try:
        serve(bind=args.bind, port=args.port, input_fd=sys.stdin.fileno(), output_fd=sys.stdout.fileno())
    except Exception:
        # Never expose a request, config, credential or provider diagnostic.
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
