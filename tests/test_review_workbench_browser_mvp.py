from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from goodteam_os import cli, web
from goodteam_os.execution.ledger import ExecutionLedger
from tests.review_workbench_helpers import (
    full_lifecycle as _full_lifecycle,
    memory_event_files as _memory_event_files,
)


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def test_export_is_read_only_for_ledger_and_memory(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    ledger = ExecutionLedger(tmp_path)
    before_events = ledger.read_events(execution_id)
    before_memory = _memory_event_files(tmp_path)

    first = api.export_review_workbench_html(execution_id)
    second = api.export_review_workbench_html(execution_id)

    assert Path(first["html_path"]).read_text(encoding="utf-8") == Path(second["html_path"]).read_text(
        encoding="utf-8"
    )
    assert ledger.read_events(execution_id) == before_events
    assert _memory_event_files(tmp_path) == before_memory


def test_cli_exports_browser_workbench_artifacts(tmp_path, capsys):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)

    exit_code = cli.main(
        [
            "execution",
            "export-review-workbench",
            "--execution-id",
            execution_id,
            "--project-root",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "exported"
    assert Path(payload["html_path"]).is_file()
    assert Path(payload["json_path"]).is_file()


def test_http_routes_expose_read_only_workbench_json_and_page(tmp_path, monkeypatch):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)
    root = quote(str(tmp_path))

    status, headers, body = request("GET", f"/api/executions/{execution_id}/review-workbench?project_root={root}")
    payload = json.loads(body)
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["schema_version"] == "review_workbench.v1"
    assert payload["diff"]["summary"] == "2 files changed (+130/-2)"

    status, headers, html = request("GET", f"/review-workbench/{execution_id}?project_root={root}")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "Review Workbench" in html
    assert execution_id in html
    assert "2 files changed (+130/-2)" in html


def _capture_dashboard_requests(workspace: Path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            server_port = 8788

            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr(web, "ThreadingHTTPServer", mock_server)
    web.serve(workspace, host="127.0.0.1", port=8788)
    assert captured_handler_class is not None

    def simulate(method: str, path: str):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = type("Server", (), {"server_port": 8788})()
                self.headers = {"Host": "127.0.0.1:8788"}
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
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        return (
            handler.responses[-1],
            handler.response_headers,
            handler.wfile.getvalue().decode("utf-8"),
        )

    return simulate
