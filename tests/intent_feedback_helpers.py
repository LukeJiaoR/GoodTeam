import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from goodteam_os import web


@pytest.fixture(name="feedback_post")
def feedback_post_fixture(tmp_path, monkeypatch):
    servers = queue.Queue()
    server_type = web.ThreadingHTTPServer

    class CapturingServer(server_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.put(self)

    monkeypatch.setattr(web, "ThreadingHTTPServer", CapturingServer)
    thread = threading.Thread(
        target=web.serve,
        kwargs={"workspace": tmp_path, "host": "127.0.0.1", "port": 0},
        daemon=True,
    )
    thread.start()
    server = servers.get(timeout=5)
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path, payload):
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Origin": base},
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    try:
        yield post
    finally:
        server.shutdown()
        thread.join(timeout=5)
