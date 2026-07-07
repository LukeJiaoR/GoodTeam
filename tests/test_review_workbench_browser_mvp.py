from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from goodteam_os import cli, web
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRun
from tests.review_workbench_helpers import (
    api as _api,
    failed_event as _failed_event,
    full_lifecycle as _full_lifecycle,
    memory_event_files as _memory_event_files,
)


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def test_completed_execution_exports_browser_visible_html_and_json(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    api.finalize(execution_id)
    api.record_review_decision(execution_id, "approve", "LukeJiaoR", "ok")

    result = api.export_review_workbench_html(execution_id)

    html = Path(result["html_path"]).read_text(encoding="utf-8")
    payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "exported"
    assert result["schema_version"] == "review_workbench.v1"
    assert payload["schema_version"] == "review_workbench.v1"
    assert payload["execution_id"] == execution_id
    assert payload["runtime_policy"]["schema_version"] == "runtime_policy.v1"
    assert payload["runtime_policy"]["worker_backend"]["class"] == "fake"
    assert f"Review Workbench - {execution_id}" in html
    assert "data-schema-version=\"review_workbench.v1\"" in html
    assert "Status</dt><dd>completed</dd>" in html
    assert "LukeJiaoR/GoodTeam#216" in html
    assert "LukeJiaoR/GoodTeam#2160" in html
    assert "commit-sha-216" in html
    assert "2 files changed (+130/-2)" in html
    assert "tests/test_review_workbench_diff_artifact.py" in html
    assert "Memory provenance</dt><dd>complete</dd>" in html
    assert "Decision</dt><dd>approve by LukeJiaoR</dd>" in html
    assert "Task contract</h2>" in html
    assert "Schema</dt><dd>task_contract.v1</dd>" in html
    assert "Runtime policy</h2>" in html
    assert "Schema</dt><dd>runtime_policy.v1</dd>" in html
    assert "Worker backend</dt><dd>fake (fake)</dd>" in html
    assert "External invocation</dt><dd>not requested</dd>" in html
    assert "Expected artifacts" in html
    assert "Sandbox</dt><dd>available</dd>" in html
    assert "sandbox_created" in html
    assert "sandbox_destroyed" in html
    assert "diff_artifact_persisted" in html
    assert "review_decision_recorded" in html


def test_export_preserves_empty_states_without_mock_data(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    result = api.export_review_workbench_html(execution_id)

    html = Path(result["html_path"]).read_text(encoding="utf-8")
    payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert payload["diff"]["available"] is False
    assert payload["diff"]["reason"] == "diff_artifact_missing"
    assert payload["diff"]["changed_files"] == []
    assert "No persisted PR diff artifact is available." in html
    assert "Changed files</h2>" in html
    assert "No changed files available from persisted diff artifact." in html
    assert "No sandbox commands recorded." in html


def test_pending_and_failed_executions_render_without_crash(tmp_path):
    pending_api = _api(tmp_path)
    intake = pending_api.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=216,
        adapter="fake",
    )
    pending_id = intake["execution_id"]
    pending_api.run_execution(pending_id)

    pending = pending_api.export_review_workbench_html(pending_id)
    pending_html = Path(pending["html_path"]).read_text(encoding="utf-8")
    assert "Status</dt><dd>pending</dd>" in pending_html
    assert "pr_missing" in pending_html
    assert "No review decision recorded." in pending_html

    failed_id = "exec_failed_browser_workbench"
    ledger = ExecutionLedger(tmp_path)
    ledger.create_run(
        ExecutionRun(
            execution_id=failed_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_browser_workbench",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )
    ledger.append_event(_failed_event(failed_id))

    failed = ExecutionAPI(tmp_path).export_review_workbench_html(failed_id)
    failed_html = Path(failed["html_path"]).read_text(encoding="utf-8")
    assert "Status</dt><dd>failed</dd>" in failed_html
    assert "execution_failed:worker_failed" in failed_html


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
