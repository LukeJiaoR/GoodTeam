from http_transport_helpers import prepare_body_reader

import json
from io import BytesIO
from pathlib import Path

import pytest

from goodteam_os.web import report_bug, serve, start_workflow


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def _write_fake_project(workspace: Path, *, key: str = "BUGPROJ") -> Path:
    """Minimal project config (wired to the `fake` worker) discoverable by key.

    `report_bug` resolves config via `_project_config_for_key`, which scans
    `<workspace>/projects/*.yaml`, so the config must live there.
    """
    projects_dir = workspace / "projects"
    docs = workspace / key.lower() / "docs"
    docs.mkdir(parents=True)
    (docs / "codex.md").write_text("# Memory\n- A small service with a bug to fix.\n", encoding="utf-8")
    projects_dir.mkdir(parents=True, exist_ok=True)
    config = projects_dir / f"{key.lower()}.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Bug Project",
                f"key: {key}",
                "repos:",
                f"  docs: {docs}",
                "worker_backend:",
                "  mode: fake",
            ]
        ),
        encoding="utf-8",
    )
    return config


# --- report_bug() unit behavior --------------------------------------------


def test_report_bug_starts_fix_run_and_stops_at_fix_plan_gate(tmp_path):
    _write_fake_project(tmp_path)

    payload = report_bug(
        workspace=tmp_path,
        project_key="BUGPROJ",
        description="Pagination returns one too few rows on the last page.",
        title="Off-by-one in pagination",
        severity="p1",
    )

    assert payload["load_profile"] == "fix"
    assert payload["current_node"] == "fix_plan_review_gate"
    assert payload["next_node"] == "await_fix_plan_approval"
    assert "fix_plan" in payload["artifacts"]
    assert Path(payload["artifacts"]["fix_plan"]).is_file()

    # Defect metadata is echoed back for the FE to confirm.
    assert payload["severity"] == "p1"
    assert payload["title"] == "Off-by-one in pagination"

    # The run idea carries the description and the severity tag.
    assert "Pagination returns one too few rows" in payload["idea"]
    assert "[BUG]" in payload["idea"]
    assert "[p1]" in payload["idea"]
    assert "Off-by-one in pagination" in payload["idea"]


def test_report_bug_works_with_only_a_title(tmp_path):
    _write_fake_project(tmp_path)
    payload = report_bug(
        workspace=tmp_path,
        project_key="BUGPROJ",
        description="",
        title="App crashes on startup",
    )
    assert payload["next_node"] == "await_fix_plan_approval"
    assert payload["severity"] == "p2"  # default
    assert "App crashes on startup" in payload["idea"]


def test_report_bug_empty_description_and_title_raises(tmp_path):
    _write_fake_project(tmp_path)
    with pytest.raises(ValueError, match="Bug description is required."):
        report_bug(
            workspace=tmp_path,
            project_key="BUGPROJ",
            description="   ",
            title="",
        )


def test_report_bug_config_light_project_passes_through(tmp_path):
    """No discoverable config -> resolves to None and still starts a fix run."""
    payload = report_bug(
        workspace=tmp_path,
        project_key="GHOSTPROJ",
        description="Something is broken somewhere.",
    )
    assert payload["load_profile"] == "fix"
    assert payload["next_node"] == "await_fix_plan_approval"


def test_report_bug_active_run_guard_still_applies(tmp_path):
    _write_fake_project(tmp_path)

    # Start a greenfield run so the project has an active run.
    start_workflow(
        workspace=tmp_path,
        idea="Build a brand new feature",
        project_key="BUGPROJ",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="new_project",
        project_config=tmp_path / "projects" / "bugproj.local.yaml",
        load_profile="greenfield",
    )

    with pytest.raises(ValueError, match="active run already exists"):
        report_bug(
            workspace=tmp_path,
            project_key="BUGPROJ",
            description="A new bug while a run is active.",
        )


# --- /report-bug route ------------------------------------------------------


def _capture_post_handler(tmp_path, monkeypatch):
    captured = {}

    def mock_server(addr, handler_class):
        captured["handler_class"] = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)
    handler_class = captured["handler_class"]

    def simulate_post(path, body_dict, headers=None):
        base_headers = headers or {}

        class SimulatedHandler(handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                merged = dict(base_headers)
                merged["Content-Length"] = str(len(body_bytes))
                self.headers = merged
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
        handler.do_POST()
        return handler

    return simulate_post


def test_report_bug_route_returns_run_for_valid_request(tmp_path, monkeypatch):
    _write_fake_project(tmp_path)
    simulate_post = _capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/report-bug",
        {
            "project_key": "BUGPROJ",
            "description": "Login button does nothing on mobile.",
            "title": "Mobile login broken",
            "severity": "p1",
        },
    )
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["load_profile"] == "fix"
    assert payload["next_node"] == "await_fix_plan_approval"
    assert payload["severity"] == "p1"
    assert payload["title"] == "Mobile login broken"


def test_report_bug_route_400_on_missing_description(tmp_path, monkeypatch):
    _write_fake_project(tmp_path)
    simulate_post = _capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post("/api/report-bug", {"project_key": "BUGPROJ"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]


def test_report_bug_route_403_on_cross_origin(tmp_path, monkeypatch):
    _write_fake_project(tmp_path)
    simulate_post = _capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/report-bug",
        {"project_key": "BUGPROJ", "description": "x"},
        headers={"Origin": "https://evil.example", "Host": "127.0.0.1:8788"},
    )
    assert 403 in handler.responses
