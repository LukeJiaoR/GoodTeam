import hashlib
import json
from io import BytesIO
from pathlib import Path


from goodteam_os.memory import RoleMemoryStore
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
    assert context["worker_backend_options"] == ["fake", "codex", "anthropic", "noop", "human"]
    assert context["worker_roles"] == ["ui", "implementation", "qa"]


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
            "workers": {"implementation": "anthropic", "qa": "codex"},
        }
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["role_worker_backends"] == {"implementation": "anthropic", "qa": "codex"}
    assert set(payload["worker_backends_preflight"]) == {"anthropic", "codex"}
    assert "mode: codex" in config.read_text(encoding="utf-8")
    assert "  implementation: anthropic" in config.read_text(encoding="utf-8")

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
    }


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
