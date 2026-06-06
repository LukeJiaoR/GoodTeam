import hashlib
import json
import threading
from io import BytesIO
from pathlib import Path


import goodteam_os.web as web
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import serve


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def capture_get_handler(tmp_path, monkeypatch):
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

    return simulate_get


def capture_post_handler(tmp_path, monkeypatch, *, host: str = "127.0.0.1", port: int = 8788):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host=host, port=port)
    assert captured_handler_class is not None

    def simulate_post(path, body_dict, headers=None, wfile=None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes)), **(headers or {})}
                self.path = path
                self.wfile = wfile if wfile is not None else BytesIO()
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

    return simulate_post


def write_project_config(workspace: Path, *, key: str = "REAL", repos: dict[str, Path] | None = None) -> Path:
    projects = workspace / "projects"
    projects.mkdir(exist_ok=True)
    config = projects / f"{key.lower()}.local.yaml"
    lines = ["name: Real Product", f"key: {key}"]
    if repos:
        lines.append("repos:")
        lines.extend(f"  {name}: {path}" for name, path in repos.items())
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def event_handle(event: dict[str, object]) -> str:
    identity_fields = ("timestamp", "role_key", "horizon", "action", "source", "text")
    payload = json.dumps({key: event.get(key) for key in identity_fields}, sort_keys=True, ensure_ascii=False)
    return "mem:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def test_api_workflow_graph_route_returns_static_graph_and_project_run(tmp_path, monkeypatch):
    state = create_initial_state("run-graph", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    StateStore(tmp_path).write_project_current(state)
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/workflow/graph?project=contentops")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert {"from": "prd", "to": "architect", "type": "approved"} in payload["edges"]
    assert payload["run"]["run_id"] == "run-graph"
    assert payload["run"]["current_node"] == "rfc_review_gate"
    assert payload["run"]["next_node"] == "planner"


def test_api_devloop_route_returns_contract_shape(tmp_path, monkeypatch):
    received_roots = []

    def fake_build_dev_loop(root):
        received_roots.append(root)
        return {
            "worktrees": [],
            "pull_requests": [
                {
                    "number": 57,
                    "title": "Add devloop",
                    "head_branch": "feat/devloop",
                    "ci": "pending",
                    "url": "https://github.com/org/repo/pull/57",
                }
            ],
            "pre_reviews": [{"pr": 57, "present": True, "report_path": "/tmp/pre-review-57-report.md"}],
            "warnings": ["no project selected"],
            "generated_at": "2026-06-04T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "goodteam_os.web.build_dev_loop",
        fake_build_dev_loop,
    )
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/devloop")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert set(payload) == {"worktrees", "pull_requests", "pre_reviews", "warnings", "generated_at"}
    assert received_roots == [None]
    assert payload["worktrees"] == []
    assert payload["pull_requests"][0]["head_branch"] == "feat/devloop"
    assert payload["pull_requests"][0]["ci"] == "pending"
    assert payload["pre_reviews"][0]["pr"] == 57
    assert payload["generated_at"]


def test_api_fs_list_route_returns_directory_listing(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").mkdir()
    (tmp_path / "file.txt").write_text("nope", encoding="utf-8")
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get(f"/api/fs/list?path={tmp_path}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "path": str(tmp_path.resolve()),
        "parent": str(tmp_path.resolve().parent),
        "entries": [{"name": "repo", "path": str((tmp_path / "repo").resolve()), "is_git_repo": True}],
    }


def test_api_fs_list_route_status_codes(tmp_path, monkeypatch):
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    bad_request = simulate_get("/api/fs/list?path=relative")
    missing = simulate_get(f"/api/fs/list?path={tmp_path / 'missing'}")
    file_path = tmp_path / "file.txt"
    file_path.write_text("nope", encoding="utf-8")
    not_directory = simulate_get(f"/api/fs/list?path={file_path}")

    assert 400 in bad_request.responses
    assert 404 in missing.responses
    assert 404 in not_directory.responses
    assert json.loads(bad_request.wfile.getvalue().decode("utf-8"))["error"]


def test_api_fs_list_route_maps_permission_error_to_403(tmp_path, monkeypatch):
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    def fake_list_directory(path_value):
        raise web.FsListPermissionError(f"blocked: {path_value}")

    monkeypatch.setattr(web, "list_directory_for_picker", fake_list_directory)

    handler = simulate_get(f"/api/fs/list?path={tmp_path}")

    assert 403 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["error"] == f"blocked: {tmp_path}"


def test_api_fs_mkdir_route_creates_child_directory(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path), "name": "docs"})

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"path": str((tmp_path / "docs").resolve())}
    assert (tmp_path / "docs").is_dir()


def test_api_fs_mkdir_route_rejects_invalid_names(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    for name in ["", "..", "bad/name", "bad\\name"]:
        handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path), "name": name})
        assert 400 in handler.responses


def test_api_fs_mkdir_route_missing_parent_returns_404(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path / "missing"), "name": "docs"})

    assert 404 in handler.responses


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

    # 3. Test POST /generate-prd routes through generate_prd and returns payload
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "prd_review_gate"},
    )
    handler = simulate_post("/generate-prd", {"project_key": "CONTENTOPS"})
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["current_node"] == "prd_review_gate"

    # 4. Test POST /generate-prd returns 400 when underlying call errors
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: (_ for _ in ()).throw(ValueError("No active run for project: BAD")),
    )
    handler = simulate_post("/generate-prd", {"project_key": "BAD"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload

    # 5. Test POST /generate-rfc routes through generate_rfc and returns payload
    monkeypatch.setattr(
        "goodteam_os.web.generate_rfc",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "rfc_review_gate"},
    )
    handler = simulate_post("/generate-rfc", {"project_key": "CONTENTOPS"})
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["current_node"] == "rfc_review_gate"

    # 6. Test POST /generate-rfc returns 400 when underlying call errors
    monkeypatch.setattr(
        "goodteam_os.web.generate_rfc",
        lambda workspace, project_key: (_ for _ in ()).throw(ValueError("No active run for project: BAD")),
    )
    handler = simulate_post("/generate-rfc", {"project_key": "BAD"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload

    monkeypatch.setattr(
        "goodteam_os.web.refine_artifact",
        lambda workspace, project_key, artifact_key, content: {
            "artifact": artifact_key,
            "base_path": "/tmp/prd.md",
            "proposed_path": "/tmp/prd.proposed.md",
            "diff_summary": {"added_lines": 1, "removed_lines": 0},
        },
    )
    handler = simulate_post(
        "/api/refine-artifact",
        {"project_key": "CONTENTOPS", "artifact": "prd", "content": "# Edited"},
    )
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["artifact"] == "prd"
    assert payload["proposed_path"] == "/tmp/prd.proposed.md"

    state = create_initial_state("run-1", "CONTENTOPS", "Build a content workflow")
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text(
        "# PRD\n\n"
        "## Risks And Open Questions\n\n"
        "- Which market launches first?\n"
        "- What retention period is required?\n",
        encoding="utf-8",
    )
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state, "CONTENTOPS")

    simulate_get = capture_get_handler(tmp_path, monkeypatch)
    handler = simulate_get("/api/open-questions?project=CONTENTOPS&artifact=prd")
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "artifact": "prd",
        "questions": [
            "Which market launches first?",
            "What retention period is required?",
        ],
    }

    seen = {}

    def fake_refine_artifact(workspace, project_key, artifact_key, content, guidance=""):
        seen.update(
            {
                "project_key": project_key,
                "artifact_key": artifact_key,
                "content": content,
                "guidance": guidance,
            }
        )
        return {
            "artifact": artifact_key,
            "base_path": "/tmp/prd.md",
            "proposed_path": "/tmp/prd.proposed.md",
            "diff_summary": {"added_lines": 1, "removed_lines": 1},
        }

    monkeypatch.setattr("goodteam_os.web.refine_artifact", fake_refine_artifact)
    handler = simulate_post(
        "/api/answer-open-questions",
        {
            "project_key": "CONTENTOPS",
            "artifact": "prd",
            "answers": [
                {"question": "Which market launches first?", "answer": "Launch in the US first."},
                {"question": "What retention period is required?", "answer": "   "},
            ],
        },
    )
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["proposed_path"] == "/tmp/prd.proposed.md"
    assert "Q: Which market launches first?\nA: Launch in the US first.\n\n" in seen["guidance"]
    assert "What retention period is required?" not in seen["guidance"]
    assert "Launch in the US first." in seen["guidance"]
    assert seen["content"] == prd.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "goodteam_os.web.accept_refinement",
        lambda workspace, project_key, artifact_key: {"artifact": artifact_key, "accepted": True},
    )
    handler = simulate_post(
        "/api/accept-refinement",
        {"project_key": "CONTENTOPS", "artifact": "prd"},
    )
    assert 200 in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8"))["accepted"] is True

    monkeypatch.setattr(
        "goodteam_os.web.discard_refinement",
        lambda workspace, project_key, artifact_key: {"artifact": artifact_key, "discarded": True},
    )
    handler = simulate_post(
        "/api/discard-refinement",
        {"project_key": "CONTENTOPS", "artifact": "prd"},
    )
    assert 200 in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8"))["discarded"] is True


def test_api_post_same_origin_guard_rejects_cross_origin_and_allows_trusted_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "prd_review_gate"},
    )
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    cross_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://evil.test"},
    )
    assert 403 in cross_origin.responses
    assert json.loads(cross_origin.wfile.getvalue().decode("utf-8"))["error"]

    same_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    assert 200 in same_origin.responses
    assert json.loads(same_origin.wfile.getvalue().decode("utf-8"))["project_key"] == "CONTENTOPS"

    absent_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788"},
    )
    assert 200 in absent_origin.responses


def test_api_approve_accepts_optional_note_without_response_shape_change(tmp_path, monkeypatch):
    captured = {}

    def fake_decide_approval(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run-1", "decision": kwargs["decision"], "next_node": "await_prd_approval"}

    monkeypatch.setattr("goodteam_os.web.decide_approval", fake_decide_approval)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/approve",
        {
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "changes_requested",
            "note": "Tighten acceptance criteria.",
        },
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 200 in handler.responses
    assert captured["note"] == "Tighten acceptance criteria."
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == {
        "run_id": "run-1",
        "decision": "changes_requested",
        "next_node": "await_prd_approval",
    }


def test_api_dispatch_track_starts_background_dispatch_without_blocking(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    response_flushed = threading.Event()
    dispatch_entered = threading.Event()
    dispatch_can_finish = threading.Event()
    completed_status_written = threading.Event()
    calls = []
    thread_errors = []

    class ResponseStream(BytesIO):
        def flush(self):
            super().flush()
            response_flushed.set()

    def fake_ensure_worktree(workspace, *, name, branch):
        assert workspace == tmp_path / "workspace"
        assert name == "my-track"
        assert branch == "feat/my-track"
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_dispatch_tracks(**kwargs):
        if not response_flushed.is_set():
            thread_errors.append("dispatch started before the 202 response was flushed")
        calls.append(kwargs)
        dispatch_entered.set()
        dispatch_can_finish.wait(timeout=5)
        return {"all_ok": True, "run_id": "dispatch-worker"}

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    def fake_safety_check(project_root, *, projects_dir):
        assert project_root == worktree
        assert projects_dir == tmp_path / "workspace" / "projects"
        return {"safe": True, "reason": ""}

    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "completed":
            completed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
            "constraints": "Keep changes scoped.",
            "verify": "pytest tests/test_api.py -q",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
        wfile=ResponseStream(),
    )
    assert 202 in response.responses
    assert response_flushed.is_set()

    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["run_id"].startswith("dispatch-")
    assert payload == {
        "run_id": payload["run_id"],
        "name": "my-track",
        "branch": "feat/my-track",
        "worktree": str(worktree),
        "status": "started",
    }
    assert dispatch_entered.wait(timeout=1)
    assert not thread_errors
    assert calls[0]["project_root"] == worktree
    assert calls[0]["tracks"][0]["role"] == "backend"
    assert calls[0]["tracks"][0]["adapter"] == "codex"
    assert calls[0]["tracks"][0]["task"] == "Implement backend dispatch."
    assert calls[0]["verify_commands"] == {"backend": "pytest tests/test_api.py -q"}
    assert calls[0]["constraints_file"].read_text(encoding="utf-8") == "Keep changes scoped."
    assert calls[0]["projects_dir"] == workspace / "projects"
    dispatch_can_finish.set()
    assert completed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{payload['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == payload["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "completed"
    assert status["error"] == ""


def test_api_dispatch_track_background_failure_writes_status(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    response_flushed = threading.Event()
    dispatch_entered = threading.Event()
    failed_status_written = threading.Event()
    thread_errors = []

    class ResponseStream(BytesIO):
        def flush(self):
            super().flush()
            response_flushed.set()

    def fake_ensure_worktree(workspace, *, name, branch):
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_safety_check(project_root, *, projects_dir):
        return {"safe": True, "reason": ""}

    def fake_dispatch_tracks(**kwargs):
        if not response_flushed.is_set():
            thread_errors.append("dispatch started before the 202 response was flushed")
        dispatch_entered.set()
        raise RuntimeError("dispatch failed after acceptance")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "failed":
            failed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
        wfile=ResponseStream(),
    )

    assert 202 in response.responses
    assert response_flushed.is_set()
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert dispatch_entered.wait(timeout=1)
    assert not thread_errors
    assert failed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{payload['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == payload["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "failed"
    assert status["error"] == "dispatch failed after acceptance"


def test_api_dispatch_track_releases_background_when_accepted_response_write_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    dispatch_entered = threading.Event()
    completed_status_written = threading.Event()
    captured_response_written = None
    captured_result = None

    class BrokenResponseStream(BytesIO):
        def write(self, data):
            raise OSError("client disconnected")

    def fake_ensure_worktree(workspace, *, name, branch):
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_safety_check(project_root, *, projects_dir):
        return {"safe": True, "reason": ""}

    def fake_dispatch_tracks(**kwargs):
        dispatch_entered.set()
        return {"all_ok": True, "run_id": "dispatch-worker"}

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_start_dispatch_track = web.start_dispatch_track

    def start_dispatch_track_and_capture(*args, response_written=None, **kwargs):
        nonlocal captured_response_written, captured_result
        captured_response_written = response_written
        captured_result = original_start_dispatch_track(*args, response_written=response_written, **kwargs)
        return captured_result

    monkeypatch.setattr("goodteam_os.web.start_dispatch_track", start_dispatch_track_and_capture)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "completed":
            completed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    try:
        simulate_post(
            "/api/dispatch-track",
            {
                "name": "my-track",
                "role": "backend",
                "adapter": "codex",
                "task": "Implement backend dispatch.",
            },
            headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
            wfile=BrokenResponseStream(),
        )
    except OSError as exc:
        assert str(exc) == "client disconnected"
    else:
        raise AssertionError("broken response stream should raise")

    assert captured_response_written is not None
    assert captured_response_written.is_set()
    assert captured_result is not None
    assert dispatch_entered.wait(timeout=1)
    assert completed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{captured_result['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == captured_result["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "completed"
    assert status["error"] == ""


def test_api_dispatch_track_rejects_cross_origin_before_dispatch(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dispatch setup should not run for cross-origin requests")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fail_if_called)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.test"},
    )

    assert 403 in response.responses
    assert json.loads(response.wfile.getvalue().decode("utf-8"))["error"]


def test_api_dispatch_track_legacy_path_rejects_cross_origin_before_dispatch(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dispatch setup should not run for cross-origin requests")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fail_if_called)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.test"},
    )

    assert 403 in response.responses
    assert json.loads(response.wfile.getvalue().decode("utf-8"))["error"]


def test_api_dispatch_track_rejects_bad_input(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "Bad Name", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    assert "name" in json.loads(response.wfile.getvalue().decode("utf-8"))["error"]


def test_api_dispatch_track_rejects_unsafe_target(tmp_path, monkeypatch):
    worktree = tmp_path / "goodteam-dev-my-track"

    def fake_ensure_worktree(workspace, *, name, branch):
        raise AssertionError("worktree must not be created before safety passes")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr(
        "goodteam_os.web.ensure_dispatch_project_root_is_safe",
        lambda project_root, *, projects_dir: {"safe": False, "reason": "project_root is inside configured project repo"},
    )
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    assert "configured project repo" in json.loads(response.wfile.getvalue().decode("utf-8"))["error"]
    assert not worktree.exists()


def test_api_dispatch_track_rejects_unsafe_target_without_creating_worktree(tmp_path, monkeypatch):
    configured_repo = tmp_path / "served-repo"
    workspace = configured_repo / "workspace"
    workspace.mkdir(parents=True)
    write_project_config(workspace, repos={"app": configured_repo})
    worktree = configured_repo / "goodteam-dev-my-track"

    def fail_if_git_runs(*args, **kwargs):
        raise AssertionError("git worktree add must not run for unsafe targets")

    monkeypatch.setattr("goodteam_os.web.subprocess.run", fail_if_git_runs)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    assert "configured project repo" in json.loads(response.wfile.getvalue().decode("utf-8"))["error"]
    assert not worktree.exists()


def test_link_dispatch_worktree_venv_uses_absolute_target_for_relative_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "goodteam-dev-my-track"
    (workspace / ".venv" / "bin").mkdir(parents=True)
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)

    web._link_dispatch_worktree_venv(Path("workspace"), worktree)

    bin_link = worktree / ".venv" / "bin"
    assert bin_link.is_symlink()
    assert bin_link.readlink().is_absolute()
    assert bin_link.resolve() == workspace.resolve() / ".venv" / "bin"


def test_api_dispatch_track_safety_uses_served_workspace_projects_dir(tmp_path, monkeypatch):
    configured_repo = tmp_path / "served-repo"
    workspace = configured_repo / "workspace"
    worktree = configured_repo / "goodteam-dev-my-track"
    workspace.mkdir(parents=True)
    write_project_config(workspace, repos={"app": configured_repo})

    def fake_ensure_worktree(workspace, *, name, branch):
        assert workspace == configured_repo / "workspace"
        assert name == "my-track"
        assert branch == "feat/my-track"
        worktree.mkdir(parents=True)
        return worktree

    def fail_if_dispatch_starts(**kwargs):
        raise AssertionError("dispatch should not start for unsafe targets")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fail_if_dispatch_starts)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    assert "configured project repo" in json.loads(response.wfile.getvalue().decode("utf-8"))["error"]


def test_api_state_exposes_worker_backend_options_and_roles(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "real.local.yaml").write_text(
        "name: Real Product\nkey: REAL\nworker_backend:\n  mode: codex\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    class SimulatedHandler(captured_handler_class):
        def __init__(self):
            self.request = FakeRequest()
            self.client_address = ("127.0.0.1", 12345)
            self.server = None
            self.headers = {}
            self.path = "/api/state?project=REAL"
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

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    context = payload["start_context"]
    assert context["worker_backend_options"] == ["codex", "claude", "gemini", "agy", "fake", "noop", "human"]
    assert context["worker_roles"] == [
        "orchestrator",
        "research",
        "product_requirement",
        "architect",
        "review",
        "planner",
        "ui",
        "implementation",
        "frontend",
        "backend",
        "qa",
        "code_review",
        "release",
        "retro_memory",
    ]


def test_api_state_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-runtime"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-runtime",
                "role": "implementation",
                "adapter": "codex",
                "status": "completed",
                "updated_at": "2026-05-31T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/state?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["type"] == "project"
    assert payload["worker_jobs"] == [
        {
            "id": "job-runtime",
            "role": "implementation",
            "adapter": "codex",
            "status": "completed",
            "updated_at": "2026-05-31T00:00:00+00:00",
            "heartbeat_at": "",
            "pid": "",
            "summary": "",
        }
    ]


def test_api_project_config_updates_worker_backends(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    projects = tmp_path / "projects"
    projects.mkdir()
    config = projects / "real.local.yaml"
    config.write_text(
        """name: Real Product
key: REAL
repos:
  app: /tmp/app
worker_backend:
  mode: fake
workers:
  implementation: fake
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    def simulate_post(body_dict):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes))}
                self.path = "/api/project-config"
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

    handler = simulate_post(
        {
            "project": "REAL",
            "worker_backend_mode": "codex",
            "workers": {
                "product_requirement": "anthropic",
                "architect": "claude",
                "planner": "gemini",
                "ui": "antigravity",
                "implementation": "agy",
                "qa": "codex",
            },
            "role_models": {
                "architect": "claude-sonnet-4-6",
                "implementation": "gemini-2.5-pro",
                "qa": "default",
            },
        }
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["role_worker_backends"] == {
        "product_requirement": "anthropic",
        "architect": "claude",
        "planner": "gemini",
        "ui": "antigravity",
        "implementation": "agy",
        "qa": "codex",
    }
    assert payload["role_model_overrides"] == {
        "architect": "claude-sonnet-4-6",
        "implementation": "gemini-2.5-pro",
    }
    assert set(payload["worker_backends_preflight"]) == {
        "codex",
        "anthropic",
        "claude",
        "gemini",
        "agy",
        "antigravity",
    }
    assert "mode: codex" in config.read_text(encoding="utf-8")
    assert "  product_requirement: anthropic" in config.read_text(encoding="utf-8")
    assert "  architect: claude" in config.read_text(encoding="utf-8")
    assert "  planner: gemini" in config.read_text(encoding="utf-8")
    assert "  ui: antigravity" in config.read_text(encoding="utf-8")
    assert "  implementation: agy" in config.read_text(encoding="utf-8")
    assert "  qa: codex" in config.read_text(encoding="utf-8")
    content = config.read_text(encoding="utf-8")
    role_model_block = content.split("role_model_overrides:\n", 1)[1]
    assert "  architect: claude-sonnet-4-6" in role_model_block
    assert "  implementation: gemini-2.5-pro" in role_model_block
    assert "  qa:" not in role_model_block

    invalid = simulate_post(
        {
            "project": "REAL",
            "worker_backend_mode": "not-real",
            "workers": {},
        }
    )

    assert 400 in invalid.responses
    invalid_payload = json.loads(invalid.wfile.getvalue().decode("utf-8"))
    assert "error" in invalid_payload


def test_api_project_config_still_creates_local_project_config(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    class SimulatedHandler(captured_handler_class):
        def __init__(self):
            self.request = FakeRequest()
            self.client_address = ("127.0.0.1", 12345)
            self.server = None
            body_bytes = json.dumps(
                {
                    "project_name": "Real Product",
                    "project_key": "REAL",
                    "docs_path": str(docs),
                    "worker_backend_mode": "fake",
                }
            ).encode("utf-8")
            self.headers = {"Content-Length": str(len(body_bytes))}
            self.path = "/api/project-config"
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

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert Path(payload["project_config"]).is_file()


def test_api_memory_returns_empty_roles_without_memory(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"project_key": "REAL", "roles": []}
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "*"


def test_api_agents_returns_discovered_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "goodteam_os.web.discover_installed_agents",
        lambda: [
            {
                "backend": "claude",
                "aliases": ["anthropic", "claude"],
                "available": True,
                "ready": True,
                "command": "claude",
                "command_path": "/fake/bin/claude",
                "hint": "ready",
                "models": ["claude-sonnet-4-6", "claude-opus-4-5"],
                "model_default": "claude-sonnet-4-6",
            }
        ],
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/agents")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "agents": [
            {
                "backend": "claude",
                "aliases": ["anthropic", "claude"],
                "available": True,
                "ready": True,
                "command": "claude",
                "command_path": "/fake/bin/claude",
                "hint": "ready",
                "models": ["claude-sonnet-4-6", "claude-opus-4-5"],
                "model_default": "claude-sonnet-4-6",
            }
        ]
    }
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "*"


def test_api_messages_returns_messages_for_run(tmp_path, monkeypatch):
    message = append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="implementation",
        to=["qa"],
        kind="ask",
        body="Please review the message ledger.",
        gate="rfc",
    )
    append_message(
        tmp_path,
        "REAL",
        "run-2",
        from_role="implementation",
        to=["qa"],
        kind="ask",
        body="A different run should not be returned.",
        gate="rfc",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/messages?project=REAL&run=run-1")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"messages": [message]}
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "*"


def test_api_memory_returns_role_summaries_event_count_and_latest_retrieval(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.ensure_role_memory("architect", "Architect", "Real Product")
    memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Architecture decisions must keep the dashboard API read-only.",
        source="job-old/result.md",
    )
    memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory panel should lazy-load per-role retrieval packets.",
        source="job-new/result.md",
    )
    jobs_root = project_root / ".goodteam" / "workers" / "jobs"
    old_job = jobs_root / "job-old"
    new_job = jobs_root / "job-new"
    old_job.mkdir(parents=True)
    new_job.mkdir(parents=True)
    (old_job / "job.json").write_text(
        json.dumps({"id": "job-old", "role": "architect", "adapter": "fake", "status": "completed", "updated_at": "2026-05-30T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (old_job / "role_pickup_packet.json").write_text(
        json.dumps({"relevant_events": [{"id": "mem:old", "score": 0.1, "horizon": "long-term", "text": "old", "source": "old", "timestamp": "old"}]}),
        encoding="utf-8",
    )
    (new_job / "job.json").write_text(
        json.dumps({"id": "job-new", "role": "architect", "adapter": "fake", "status": "completed", "updated_at": "2026-05-31T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (new_job / "role_pickup_packet.json").write_text(
        json.dumps(
            {
                "relevant_events": [
                    {
                        "id": "mem:panel",
                        "score": 1.2,
                        "horizon": "mid-term",
                        "text": "Memory panel should lazy-load per-role retrieval packets.",
                        "source": "job-new/result.md",
                        "timestamp": "2026-05-31T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert len(payload["roles"]) == 1
    role = payload["roles"][0]
    assert role["role"] == "architect"
    assert "dashboard API read-only" in role["summaries"]["long-term"]
    assert "lazy-load per-role retrieval packets" in role["summaries"]["mid-term"]
    assert role["event_count"] == 2
    assert "No role-specific notes recorded yet" in role["short_term"]
    assert role["latest_retrieval"]["job_id"] == "job-new"
    assert role["latest_retrieval"]["relevant_events"] == [
        {
            "id": "mem:panel",
            "score": 1.2,
            "horizon": "mid-term",
            "text": "Memory panel should lazy-load per-role retrieval packets.",
            "source": "job-new/result.md",
            "timestamp": "2026-05-31T00:00:00+00:00",
        }
    ]


def test_api_memory_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)

    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    (projects / "real.local.yaml").write_text(
        f"name: Real Product\nkey: REAL\nrepos:\n  backend: {backend_repo}\n  frontend: {frontend_repo}\n",
        encoding="utf-8",
    )

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.ensure_role_memory("architect", "Architect", "Real Product")
    memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Runtime-root memory should load even when context packet path is empty.",
        source="job-runtime/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert len(payload["roles"]) == 1
    role = payload["roles"][0]
    assert role["role"] == "architect"
    assert "Runtime-root memory should load" in role["summaries"]["long-term"]
    assert role["event_count"] == 1


def test_api_memory_event_returns_full_event(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory event drill-down should expose the full source event.",
        source="job-x/result.md",
    )
    event_id = event_handle(event)

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "id": event_id,
        "role": "architect",
        "horizon": "mid-term",
        "timestamp": event["timestamp"],
        "source": "job-x/result.md",
        "text": "Memory event drill-down should expose the full source event.",
        "action": "add",
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
        "cited_by_jobs": [],
    }


def test_api_memory_events_returns_browsable_role_events(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    old_event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory browse API exposes the older card behavior.",
        source="job-old/result.md",
    )
    old_id = event_handle(old_event)
    new_event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory browse API exposes the current card behavior.",
        source="job-new/result.md",
        supersedes=old_id,
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=architect&limit=10")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert payload["role"] == "architect"
    assert [event["id"] for event in payload["events"]] == [event_handle(new_event), old_id]
    assert payload["events"][0]["body"] == "Memory browse API exposes the current card behavior."
    assert payload["events"][0]["is_superseded"] is False
    assert payload["events"][1]["is_superseded"] is True


def test_api_memory_events_uses_query_and_limit(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Memory browse search mentions database only.",
        source="job-db/result.md",
    )
    best = memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Memory browse search mentions database and sqlite.",
        source="job-sqlite/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)(
        "/api/memory/events?project=REAL&role=implementation&q=database%20sqlite&limit=1"
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert [event["id"] for event in payload["events"]] == [event_handle(best)]


def test_api_memory_events_returns_empty_for_unknown_role(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=missing")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"project_key": "REAL", "role": "missing", "events": []}


def test_api_memory_events_caps_limit(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    for index in range(205):
        memory_store.append_memory_event(
            "qa",
            horizon="short-term",
            text=f"Memory browse API cap event {index}.",
            source=f"job-{index}/result.md",
        )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=qa&limit=500")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert len(payload["events"]) == 200


def test_api_memory_event_returns_supersede_details(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    old_event = memory_store.append_memory_event(
        "implementation",
        horizon="mid-term",
        text="API contract uses the v1 memory detail payload.",
        source="job-old/result.md",
    )
    old_id = event_handle(old_event)
    new_event = memory_store.append_memory_event(
        "implementation",
        horizon="mid-term",
        text="API contract uses the v2 memory detail payload.",
        source="job-new/result.md",
        supersedes=old_id,
    )
    new_id = event_handle(new_event)

    old_handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={old_id}")
    new_handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={new_id}")

    assert 200 in old_handler.responses
    old_payload = json.loads(old_handler.wfile.getvalue().decode("utf-8"))
    assert old_payload["supersedes"] == ""
    assert old_payload["is_superseded"] is True
    assert new_id in old_payload["superseded_by"]

    assert 200 in new_handler.responses
    new_payload = json.loads(new_handler.wfile.getvalue().decode("utf-8"))
    assert new_payload["supersedes"] == old_id
    assert new_payload["superseded_by"] == []
    assert new_payload["is_superseded"] is False


def test_api_memory_event_returns_citing_jobs(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Cited detail payload includes jobs that used this memory.",
        source="job-memory/result.md",
    )
    event_id = event_handle(event)
    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-citing"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-citing",
                "role": "implementation",
                "adapter": "fake",
                "status": "queued",
                "updated_at": "2026-06-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "summary": "Used cited memory.",
                "updated_at": "2026-06-02T00:00:00+00:00",
                "memory_citations": [event_id],
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["cited_by_jobs"] == [
        {
            "id": "job-citing",
            "role": "implementation",
            "adapter": "fake",
            "status": "completed",
            "updated_at": "2026-06-02T00:00:00+00:00",
            "summary": "Used cited memory.",
        }
    ]


def test_api_memory_event_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Runtime-root drill-down should load even when context packet path is empty.",
        source="job-runtime/result.md",
    )
    event_id = event_handle(event)

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "id": event_id,
        "role": "architect",
        "horizon": "long-term",
        "timestamp": event["timestamp"],
        "source": "job-runtime/result.md",
        "text": "Runtime-root drill-down should load even when context packet path is empty.",
        "action": "add",
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
        "cited_by_jobs": [],
    }


def test_api_memory_event_returns_404_for_unknown_id(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory event drill-down should expose the full source event.",
        source="job-x/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/event?project=REAL&id=mem:missing")

    assert 404 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload


def test_api_memory_event_returns_400_for_missing_id(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/event?project=REAL")

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload


def test_api_memory_returns_400_for_bad_project(tmp_path, monkeypatch):
    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=MISSING")

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload
