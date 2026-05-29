import json
from pathlib import Path
from io import BytesIO
from dataclasses import asdict

import pytest

from goodteam_os.web import serve
from goodteam_os.state import AgileDevState, ApprovalDecision, StateStore


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def test_api_handler_do_get_and_do_options(tmp_path, monkeypatch):
    # 1. Capture the DashboardHandler class
    captured_handler_class = None
    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class
        class FakeServer:
            def serve_forever(self):
                pass
        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)
    
    assert captured_handler_class is not None

    # Helper to simulate a GET request
    def simulate_get(path):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                self.headers = {}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO()
                self.responses = []
                self.response_headers = {}

            def send_response(self, code, message=None):
                self.responses.append(code)

            def send_header(self, keyword, value):
                self.response_headers[keyword] = value

            def end_headers(self):
                pass

            def send_error(self, code, message=None, explain=None):
                self.responses.append(code)

        handler = SimulatedHandler()
        handler.do_GET()
        return handler

    # Helper to simulate an OPTIONS request
    def simulate_options(path):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                self.headers = {}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO()
                self.responses = []
                self.response_headers = {}

            def send_response(self, code, message=None):
                self.responses.append(code)

            def send_header(self, keyword, value):
                self.response_headers[keyword] = value

            def end_headers(self):
                pass

        handler = SimulatedHandler()
        handler.do_OPTIONS()
        return handler

    # 2. Test GET /api/state on empty workspace
    handler = simulate_get("/api/state")
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["type"] == "overview"
    assert payload["project_items"] == []
    assert payload["legacy_state"] is None
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "*"

    # 3. Test OPTIONS preflight request
    handler = simulate_options("/api/state")
    assert 204 in handler.responses
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "*"
    assert "GET" in handler.response_headers.get("Access-Control-Allow-Methods", "")

    # 4. Test GET static files fallback (should fall back to render HTML template and return 200)
    handler = simulate_get("/")
    assert 200 in handler.responses
    assert handler.response_headers.get("Content-Type") == "text/html; charset=utf-8"
    assert b"<!doctype html>" in handler.wfile.getvalue()

    # 5. Test static file serving when file exists in frontend/dist
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    index_html = dist_dir / "index.html"
    index_html.write_text("Hello React", encoding="utf-8")

    # Serving from frontend/dist/index.html should succeed and return static text
    handler = simulate_get("/")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"Hello React"
    assert handler.response_headers.get("Content-Type") == "text/html"

    # Static CSS serving
    style_css = dist_dir / "assets" / "style.css"
    style_css.parent.mkdir(parents=True, exist_ok=True)
    style_css.write_text("body { color: red; }", encoding="utf-8")

    handler = simulate_get("/assets/style.css")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"body { color: red; }"
    assert handler.response_headers.get("Content-Type") == "text/css"


def test_api_handler_do_post_routing(tmp_path, monkeypatch):
    captured_handler_class = None
    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class
        class FakeServer:
            def serve_forever(self):
                pass
        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    # Helper to simulate a POST request
    def simulate_post(path, body_dict):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes))}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO(body_bytes)
                self.responses = []
                self.response_headers = {}

            def send_response(self, code, message=None):
                self.responses.append(code)

            def send_header(self, keyword, value):
                self.response_headers[keyword] = value

            def end_headers(self):
                pass

            def send_error(self, code, message=None, explain=None):
                self.responses.append(code)

        handler = SimulatedHandler()
        handler.do_POST()
        return handler

    # 1. Test POST /api/start validation (should return 400 because idea is missing)
    handler = simulate_post("/api/start", {"project_key": "CONTENTOPS"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload

    # 2. Test POST /start legacy path validation (should return 400 because idea is missing)
    handler = simulate_post("/start", {"project_key": "CONTENTOPS"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload
