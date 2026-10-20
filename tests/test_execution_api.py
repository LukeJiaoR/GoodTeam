from __future__ import annotations

from http_transport_helpers import prepare_body_reader
import pytest
import json
from io import BytesIO

import goodteam_os.web as web
from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI


TIMES = iter(
    [
        "2026-07-05T00:00:00Z",
        "2026-07-05T00:01:00Z",
        "2026-07-05T00:02:00Z",
        "2026-07-05T00:03:00Z",
        "2026-07-05T00:04:00Z",
    ]
)


def fixed_clock() -> str:
    return next(TIMES, "2026-07-05T00:04:00Z")


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 230): _snapshot()}
        self.fetch_issue_count = 0
        self.created_branches: list[dict[str, object]] = []
        self.committed_files: list[dict[str, object]] = []
        self.created_prs: list[dict[str, object]] = []
        self.fetched_checks: list[dict[str, object]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        self.fetch_issue_count += 1
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_or_update_branch(
        self,
        *,
        repo: str,
        branch_name: str,
        base_ref: str = "main",
    ) -> dict[str, object]:
        payload = {"repo": repo, "branch_name": branch_name, "base_ref": base_ref}
        self.created_branches.append(payload)
        return payload

    def commit_files(
        self,
        *,
        repo: str,
        branch_name: str,
        message: str,
        files: dict[str, str],
    ) -> dict[str, object]:
        payload = {"repo": repo, "branch_name": branch_name, "message": message, "files": files}
        self.committed_files.append(payload)
        return payload

    def create_pr(
        self,
        *,
        repo: str,
        issue_number: int | None,
        branch_name: str,
        execution_id: str,
        title: str,
        body: str,
    ) -> GitHubBinding:
        self.created_prs.append(
            {
                "repo": repo,
                "issue_number": issue_number,
                "branch_name": branch_name,
                "execution_id": execution_id,
                "title": title,
                "body": body,
            }
        )
        return GitHubBinding(
            repo=repo,
            issue_number=issue_number or 0,
            pr_number=230,
            branch_name=branch_name,
            execution_id=execution_id,
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        self.fetched_checks.append({"repo": repo, "ref": ref})
        return {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ]
        }


def _snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=230,
        title="P0-1F: Execution CLI/API Surface and E2E Test Contract",
        body="Expose the execution loop through stable CLI and local API surfaces.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/230",
        labels=["p0", "execution"],
    )


def test_execution_api_runs_full_surface_with_json_friendly_payloads(tmp_path):
    client = FakeGitHubClient()
    api = ExecutionAPI(tmp_path, github_client=client, clock=fixed_clock)

    intake = api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=230, adapter="fake")
    run = api.run_execution(intake["execution_id"])
    pr = api.create_or_reuse_pr(intake["execution_id"])
    ci = api.observe_ci(intake["execution_id"])
    replay = api.replay(intake["execution_id"])
    ledger = api.ledger(intake["execution_id"])

    assert intake["status"] == "ok"
    assert run["run"]["status"] == "succeeded"
    assert run["worker_job"]["id"] == intake["run"]["worker_job_id"]
    assert pr["binding"]["pr_number"] == 230
    assert ci["ci"]["state"] == "success"
    assert ci["ci"]["total"] == 2
    assert replay["task"]["issue_number"] == 230
    assert ledger == replay
    assert [event["payload"]["transition"] for event in replay["events"]] == [
        "intake_created",
        "worker_job_created",
        "execution_started",
        "sandbox_created",
        "execution_completed",
        "sandbox_cleanup_requested",
        "sandbox_destroyed",
        "pr_opened",
        "ci_observed",
    ]


def test_execution_api_replays_from_fresh_instance_without_memory(tmp_path):
    client = FakeGitHubClient()
    first = ExecutionAPI(tmp_path, github_client=client, clock=fixed_clock)
    execution_id = first.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=230, adapter="fake")[
        "execution_id"
    ]
    first.run_execution(execution_id)

    replay = ExecutionAPI(tmp_path, github_client=client).replay(execution_id)

    assert replay["status"] == "ok"
    assert replay["execution_id"] == execution_id
    assert replay["run"]["status"] == "succeeded"
    assert replay["worker_job"]["run_id"] == execution_id


def test_execution_api_requires_github_client_for_github_operations(tmp_path):
    api = ExecutionAPI(tmp_path)

    with pytest.raises(ValueError, match="GitHubClient is required"):
        api.intake_github_issue(repo="LukeJiaoR/GoodTeam", issue_number=230, adapter="fake")

    with pytest.raises(ValueError, match="GitHubClient is required"):
        api.create_or_reuse_pr("exec_missing")

    with pytest.raises(ValueError, match="GitHubClient is required"):
        api.observe_ci("exec_missing")


def test_execution_api_missing_execution_raises_clear_error(tmp_path):
    api = ExecutionAPI(tmp_path, github_client=FakeGitHubClient())

    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        api.run_execution("exec_missing")

    with pytest.raises(ValueError, match="Execution run not found: exec_missing"):
        api.replay("exec_missing")


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def capture_execution_handlers(workspace, monkeypatch, client: FakeGitHubClient):
    captured_handler_class = None

    def factory(project_root, *, github_client=None, clock=None):
        return ExecutionAPI(project_root, github_client=client, clock=clock)

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr(web, "ExecutionAPI", factory)
    monkeypatch.setattr(web, "ThreadingHTTPServer", mock_server)
    web.serve(workspace, host="127.0.0.1", port=8788)
    assert captured_handler_class is not None

    def simulate(method: str, path: str, body_dict: dict[str, object] | None = None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict or {}).encode("utf-8")
                self.headers = {
                    "Content-Length": str(len(body_bytes)),
                    "Host": "127.0.0.1:8788",
                    "Origin": "http://127.0.0.1:8788",
                }
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO(body_bytes)
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
        return handler.responses[-1], json.loads(handler.wfile.getvalue().decode("utf-8"))

    return simulate


def test_execution_http_api_endpoints_share_execution_api_surface(tmp_path, monkeypatch):
    client = FakeGitHubClient()
    request = capture_execution_handlers(tmp_path, monkeypatch, client)

    status, intake = request(
        "POST",
        "/api/executions/github/issues/intake",
        {"project_root": str(tmp_path), "repo": "LukeJiaoR/GoodTeam", "issue_number": 230, "adapter": "fake"},
    )
    execution_id = intake["execution_id"]
    assert status == 200
    assert intake["status"] == "ok"

    for path, key in [
        (f"/api/executions/{execution_id}/run", "worker_job"),
        (f"/api/executions/{execution_id}/pr", "binding"),
        (f"/api/executions/{execution_id}/ci", "ci"),
    ]:
        status, payload = request("POST", path, {"project_root": str(tmp_path)})
        assert status == 200
        assert payload["status"] == "ok"
        assert payload["execution_id"] == execution_id
        assert key in payload

    status, replay = request("GET", f"/api/executions/{execution_id}?project_root={tmp_path}")
    assert status == 200
    assert replay["task"]["issue_number"] == 230
    status, ledger = request("GET", f"/api/executions/{execution_id}/ledger?project_root={tmp_path}")
    assert status == 200
    assert ledger["events"] == replay["events"]


def test_execution_http_api_returns_stable_error_json(tmp_path, monkeypatch):
    request = capture_execution_handlers(tmp_path, monkeypatch, FakeGitHubClient())

    status, bad_input = request("POST", "/api/executions/github/issues/intake", {"project_root": str(tmp_path)})
    assert status == 400
    assert bad_input["status"] == "error"
    assert "repo is required" in bad_input["error"]

    status, missing = request("GET", f"/api/executions/exec_missing?project_root={tmp_path}")
    assert status == 404
    assert missing == {"status": "error", "error": "Execution run not found: exec_missing"}
