"""Finite resource boundaries for GoodTeam's local dashboard HTTP transport.

These are transport limits, not deadlines for workflow or provider execution.
See docs/http-transport-limits.md for sizing and compatibility details.
"""

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
REQUEST_IDLE_TIMEOUT_SECONDS = 15.0
REQUEST_BODY_TIMEOUT_SECONDS = 60.0
MAX_ACTIVE_CONNECTIONS = 32
_READ_CHUNK_BYTES = 64 * 1024


class RequestBodyError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400, *, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        self.recovery = ["retry_request"]


def read_request_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Validate framing before reading; never read until EOF or beyond the cap."""
    try:
        if handler.headers.get_all("Transfer-Encoding", []):
            raise RequestBodyError(
                "unsupported_transfer_encoding", "Transfer-Encoding is not supported; send Content-Length."
            )
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise RequestBodyError("invalid_content_length", "Send a single Content-Length header.")
        # Preserve the existing empty-body contract for requests without a length.
        value = lengths[0].strip() if lengths else "0"
        if not re.fullmatch(r"[0-9]+", value):
            raise RequestBodyError("invalid_content_length", "Content-Length must be a non-negative decimal integer.")
        # Compare decimal strings before int(): even an extremely long numeric
        # header must give a stable 413 without an oversized integer conversion.
        value = value.lstrip("0") or "0"
        maximum = str(MAX_REQUEST_BODY_BYTES)
        if len(value) > len(maximum) or (len(value) == len(maximum) and value > maximum):
            raise RequestBodyError(
                "request_body_too_large", "Request body exceeds the dashboard transport limit.", 413,
                details={"max_bytes": MAX_REQUEST_BODY_BYTES},
            )
        length = int(value)
        if not length:
            return b""
        deadline = time.monotonic() + REQUEST_BODY_TIMEOUT_SECONDS
        previous_timeout = handler.connection.gettimeout()
        body = bytearray()
        try:
            while len(body) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                handler.connection.settimeout(min(REQUEST_IDLE_TIMEOUT_SECONDS, remaining))
                # read1 performs at most one underlying read, so a trickling body
                # cannot reset an overall deadline inside BufferedReader.read().
                chunk = handler.rfile.read1(min(_READ_CHUNK_BYTES, length - len(body)))
                if not chunk:
                    raise RequestBodyError("incomplete_request_body", "Request body ended before Content-Length.")
                body.extend(chunk)
                if time.monotonic() >= deadline:
                    raise TimeoutError
        except TimeoutError as exc:
            raise RequestBodyError("request_body_timeout", "Request body was not received in time.", 408) from exc
        finally:
            handler.connection.settimeout(previous_timeout)
        return bytes(body)
    except (RequestBodyError, OSError):
        # Unread bytes must never be interpreted as another request.
        handler.close_connection = True
        raise


class BoundedHTTPRequestHandler(BaseHTTPRequestHandler):
    # StreamRequestHandler.setup applies this before request-line/header reads,
    # including when the short handler is embedded in a standard HTTP server.
    timeout = REQUEST_IDLE_TIMEOUT_SECONDS


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Admit only a finite number of handler threads without blocking accept."""

    def __init__(self, *args, max_connections=MAX_ACTIVE_CONNECTIONS, **kwargs):
        if isinstance(max_connections, bool) or not isinstance(max_connections, int) or max_connections < 1:
            raise ValueError("max_connections must be a positive integer")
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(REQUEST_IDLE_TIMEOUT_SECONDS)
        return request, address

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            # Do not spawn another thread or block the accept/heartbeat loop on
            # an overloaded client's response. No application request was run.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()
