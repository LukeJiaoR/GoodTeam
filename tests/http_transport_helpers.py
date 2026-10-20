"""HTTP transport objects for handler-unit fixtures using in-memory bodies."""

from email.message import Message


class MemoryConnection:
    def __init__(self):
        self.timeout = 15.0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


def prepare_body_reader(handler):
    # Keep handler-unit assertions intact while matching BaseHTTPRequestHandler's
    # header/connection interfaces. Socket behavior is covered by real HTTP tests.
    headers = Message()
    for key, value in handler.headers.items():
        headers[key] = value
    handler.headers = headers
    handler.connection = MemoryConnection()
    handler.close_connection = True
