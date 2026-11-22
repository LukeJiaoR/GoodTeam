"""[INPUT]: 临时执行账本与 GitHub 夹具。
[OUTPUT]: 验证 CLI、HTTP 和记忆归档使用的执行证据。
[POS]: 通过执行证据入口验证账本回放与只读查询。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from goodteam_os import cli, web
from goodteam_os.execution.evidence import ExecutionEvidenceService
from goodteam_os.execution.ledger import ExecutionLedger
from tests.review_workbench_helpers import (
    full_lifecycle as _full_lifecycle,
    memory_event_files as _memory_event_files,
)


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def test_cli_review_is_read_only_for_ledger_and_memory(tmp_path, capsys):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    ledger = ExecutionLedger(tmp_path)
    before_events = ledger.read_events(execution_id)
    before_memory = _memory_event_files(tmp_path)

    exit_code = cli.main(
        [
            "execution",
            "review",
            "--execution-id",
            execution_id,
            "--project-root",
            str(tmp_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["execution_id"] == execution_id

    provenance = ExecutionEvidenceService(tmp_path).build_provenance(execution_id)
    for field in ("ci_summary", "review_summary", "merge_summary", "final_status"):
        assert provenance[field] == payload["evidence"][field]
    assert provenance["final_status"] == "completed"
    assert provenance["pr"] == payload["evidence"]["pr"]["ref"] == "LukeJiaoR/GoodTeam#2160"
    assert payload["evidence"]["github_broker_status"]["pr"]["number"] == 2160
    assert provenance["ci_summary"]["state"] == "success"
    assert provenance["review_summary"] == {"reviews": 1, "comments": 1, "states": ["APPROVED"]}
    assert provenance["merge_summary"]["merged"] is True
    assert ledger.read_events(execution_id) == before_events
    assert _memory_event_files(tmp_path) == before_memory


def test_http_review_returns_json_but_not_the_standalone_page(tmp_path, monkeypatch):
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

    status, _, _ = request("GET", f"/review-workbench/{execution_id}?project_root={root}")
    assert status == 404


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
