from __future__ import annotations

from http_transport_helpers import prepare_body_reader
import json
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import pytest

from goodteam_os import web
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


def test_browser_decision_post_approve_writes_review_decision_event(tmp_path, monkeypatch):
    api, execution_id = _full_lifecycle(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": "approve",
            "reviewer": "LukeJiaoR",
            "reason": "Evidence checks out.",
        },
    )

    payload = json.loads(body)
    event = _decision_events(tmp_path, execution_id)[0]
    assert status == 200
    assert payload["status"] == "recorded"
    assert payload["reviewer_decision"]["decision"] == "approve"
    assert payload["workbench_status"] == "completed"
    assert event.payload["transition"] == "review_decision_recorded"
    assert event.payload["decision"] == "approve"
    assert event.payload["reviewer"] == "LukeJiaoR"
    assert event.payload["reason"] == "Evidence checks out."


@pytest.mark.parametrize("decision", ["request_changes", "reject"])
def test_browser_decision_post_records_non_approve_decisions(tmp_path, monkeypatch, decision):
    _, execution_id = _full_lifecycle(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": decision,
            "reviewer": "reviewer-a",
            "reason": f"{decision} reason",
        },
    )

    payload = json.loads(body)
    event = _decision_events(tmp_path, execution_id)[0]
    assert status == 200
    assert payload["status"] == "recorded"
    assert payload["reviewer_decision"]["decision"] == decision
    assert event.payload["decision"] == decision


def test_browser_decision_post_repeated_same_decision_is_deduped(tmp_path, monkeypatch):
    _, execution_id = _full_lifecycle(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)
    body = {
        "project_root": str(tmp_path),
        "decision": "approve",
        "reviewer": "LukeJiaoR",
        "reason": "ok",
    }

    first_status, _, first_body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        body,
    )
    second_status, _, second_body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        body,
    )

    first = json.loads(first_body)
    second = json.loads(second_body)
    events = _decision_events(tmp_path, execution_id)
    assert first_status == 200
    assert second_status == 200
    assert first["status"] == "recorded"
    assert second["status"] == "deduped"
    assert second["event"]["event_id"] == first["event"]["event_id"]
    assert len(events) == 1


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            {"decision": "maybe", "reviewer": "LukeJiaoR", "reason": "nope"},
            "Review decision must be one of",
        ),
        (
            {"decision": "approve", "reviewer": "   ", "reason": "no reviewer"},
            "reviewer is required",
        ),
    ],
)
def test_browser_decision_post_rejects_invalid_input_without_ledger_write(
    tmp_path,
    monkeypatch,
    payload,
    expected_error,
):
    _, execution_id = _full_lifecycle(tmp_path)
    before = ExecutionLedger(tmp_path).read_events(execution_id)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {"project_root": str(tmp_path), **payload},
    )

    assert status == 400
    assert expected_error in json.loads(body)["error"]
    assert ExecutionLedger(tmp_path).read_events(execution_id) == before


def test_browser_decision_post_missing_execution_does_not_create_ledger_file(
    tmp_path,
    monkeypatch,
):
    request = _capture_dashboard_requests(tmp_path, monkeypatch)
    missing_id = "missing_browser_decision"

    status, _, body = request(
        "POST",
        f"/api/executions/{missing_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": "reject",
            "reviewer": "LukeJiaoR",
            "reason": "cannot review missing execution",
        },
    )

    missing_events = tmp_path / ".goodteam" / "executions" / missing_id / "events.jsonl"
    assert status in {400, 404}
    assert "not found" in json.loads(body)["error"].lower()
    assert not missing_events.exists()


@pytest.mark.parametrize("decision", ["request_changes", "reject"])
def test_browser_decision_post_allows_non_approve_for_pending_execution(
    tmp_path,
    monkeypatch,
    decision,
):
    api = _api(tmp_path)
    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=216, adapter="fake")
    execution_id = intake["execution_id"]
    api.run_execution(execution_id)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": decision,
            "reviewer": "LukeJiaoR",
            "reason": "not ready",
        },
    )

    payload = json.loads(body)
    assert status == 200
    assert payload["status"] == "recorded"
    assert payload["workbench_status"] == "pending"
    assert _decision_events(tmp_path, execution_id)[0].payload["decision"] == decision


def test_browser_decision_post_allows_reject_for_failed_execution(tmp_path, monkeypatch):
    execution_id = _failed_execution(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": "reject",
            "reviewer": "LukeJiaoR",
            "reason": "worker failed",
        },
    )

    payload = json.loads(body)
    assert status == 200
    assert payload["workbench_status"] == "failed"
    assert _decision_events(tmp_path, execution_id)[0].payload["decision"] == "reject"


@pytest.mark.parametrize("execution_factory", ["pending", "failed"])
def test_browser_decision_post_rejects_approve_for_non_completed_execution(
    tmp_path,
    monkeypatch,
    execution_factory,
):
    if execution_factory == "pending":
        api = _api(tmp_path)
        intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=216, adapter="fake")
        execution_id = intake["execution_id"]
        api.run_execution(execution_id)
    else:
        execution_id = _failed_execution(tmp_path)
    before = ExecutionLedger(tmp_path).read_events(execution_id)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": "approve",
            "reviewer": "LukeJiaoR",
            "reason": "too early",
        },
    )

    assert status == 400
    assert "approve decision requires completed execution" in json.loads(body)["error"]
    assert ExecutionLedger(tmp_path).read_events(execution_id) == before


def test_workbench_get_routes_remain_read_only_for_ledger_and_memory(tmp_path, monkeypatch):
    _, execution_id = _full_lifecycle(tmp_path)
    ledger = ExecutionLedger(tmp_path)
    before_events = ledger.read_events(execution_id)
    before_memory = _memory_event_files(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)
    root = quote(str(tmp_path))

    json_status, _, _ = request("GET", f"/api/executions/{execution_id}/review-workbench?project_root={root}")
    html_status, _, _ = request("GET", f"/review-workbench/{execution_id}?project_root={root}")

    assert json_status == 200
    assert html_status == 200
    assert ledger.read_events(execution_id) == before_events
    assert _memory_event_files(tmp_path) == before_memory


def test_static_export_disables_decision_actions(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)

    result = api.export_review_workbench_html(execution_id)

    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "<form id=\"review-decision-form\"" in html
    assert "Decision actions require local server route." in html
    assert "disabled" in html
    assert "fetch(" not in html


def test_browser_decision_post_does_not_write_memory_or_call_live_paths(
    tmp_path,
    monkeypatch,
):
    _, execution_id = _full_lifecycle(tmp_path)
    before_memory = _memory_event_files(tmp_path)
    live_calls: list[str] = []

    class GuardedExecutionAPI(ExecutionAPI):
        def __init__(self, project_root):
            super().__init__(project_root)

        def run_execution(self, execution_id):
            live_calls.append("run_execution")
            raise AssertionError("worker execution must not be triggered")

        def create_or_reuse_pr(self, execution_id):
            live_calls.append("create_or_reuse_pr")
            raise AssertionError("GitHub PR creation must not be triggered")

        def observe_ci(self, execution_id):
            live_calls.append("observe_ci")
            raise AssertionError("GitHub CI observation must not be triggered")

        def sync_reviews(self, execution_id):
            live_calls.append("sync_reviews")
            raise AssertionError("GitHub review sync must not be triggered")

    monkeypatch.setattr(web, "ExecutionAPI", GuardedExecutionAPI)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": "approve",
            "reviewer": "LukeJiaoR",
            "reason": "ok",
        },
    )

    assert status == 200
    assert json.loads(body)["status"] == "recorded"
    assert _memory_event_files(tmp_path) == before_memory
    assert live_calls == []


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

    def simulate(method: str, path: str, payload: dict[str, object] | None = None):
        raw_body = json.dumps(payload or {}).encode("utf-8")

        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = type("Server", (), {"server_port": 8788})()
                self.headers = {
                    "Content-Length": str(len(raw_body)),
                    "Content-Type": "application/json",
                    "Host": "127.0.0.1:8788",
                    "Origin": "http://127.0.0.1:8788",
                }
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO(raw_body)
                prepare_body_reader(self)
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


def _decision_events(tmp_path: Path, execution_id: str):
    return [
        event
        for event in ExecutionLedger(tmp_path).read_events(execution_id)
        if event.payload.get("transition") == "review_decision_recorded"
    ]


def _failed_execution(tmp_path: Path) -> str:
    execution_id = "exec_failed_browser_decision"
    ledger = ExecutionLedger(tmp_path)
    ledger.create_run(
        ExecutionRun(
            execution_id=execution_id,
            task_id="task_failed",
            worker_job_id="worker_failed",
            adapter="fake",
            status="failed",
            branch_name="goodteam/exec_failed_browser_decision",
            pr_number=None,
            created_at="2026-07-06T02:30:00Z",
            updated_at="2026-07-06T02:31:00Z",
        )
    )
    ledger.append_event(_failed_event(execution_id))
    return execution_id


@pytest.mark.parametrize("decision", ["request_changes", "reject"])
def test_browser_decision_post_rejects_missing_reason_for_changes_or_reject(
    tmp_path,
    monkeypatch,
    decision,
):
    _, execution_id = _full_lifecycle(tmp_path)
    request = _capture_dashboard_requests(tmp_path, monkeypatch)

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": decision,
            "reviewer": "reviewer-a",
            "reason": "",
        },
    )

    assert status == 400
    assert "reason is required" in json.loads(body)["error"].lower()

    status, _, body = request(
        "POST",
        f"/api/executions/{execution_id}/review-workbench/decision",
        {
            "project_root": str(tmp_path),
            "decision": decision,
            "reviewer": "reviewer-a",
            "reason": "   ",
        },
    )

    assert status == 400
    assert "reason is required" in json.loads(body)["error"].lower()
