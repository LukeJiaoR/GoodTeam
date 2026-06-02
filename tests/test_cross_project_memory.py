import json
from io import BytesIO
from pathlib import Path

from goodteam_os.cross_project_memory import retrieve
from goodteam_os.memory import RoleMemoryStore
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


def write_project_config(workspace: Path, project_key: str, runtime_root: Path) -> Path:
    backend_repo = runtime_root / "backend"
    frontend_repo = runtime_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    projects_dir = workspace / "projects"
    projects_dir.mkdir(exist_ok=True)
    config_path = projects_dir / f"{project_key.lower()}.local.yaml"
    config_path.write_text(
        (
            f"name: Project {project_key}\n"
            f"key: {project_key}\n"
            f"repos:\n"
            f"  backend: {backend_repo}\n"
            f"  frontend: {frontend_repo}\n"
        ),
        encoding="utf-8",
    )
    return config_path


def seed_memory(runtime_root: Path, text: str) -> str:
    store = RoleMemoryStore(runtime_root)
    event = store.append_memory_event(
        "implementation",
        horizon="mid-term",
        text=text,
        source=f"job-{runtime_root.name}/result.md",
    )
    return store._event_id(event)


def test_cross_project_retrieve_fans_out_and_qualifies_ids(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    write_project_config(workspace, "A", root_a)
    write_project_config(workspace, "B", root_b)
    id_a = seed_memory(root_a, "Retrieval moat fact from project A uses alpha routing.")
    id_b = seed_memory(root_b, "Retrieval moat fact from project B uses beta routing.")
    events_a = RoleMemoryStore(root_a).event_path("implementation").read_text(encoding="utf-8")
    events_b = RoleMemoryStore(root_b).event_path("implementation").read_text(encoding="utf-8")

    results = retrieve(
        workspace,
        "implementation",
        "retrieval moat routing",
        scope=["A", "MISSING", "B"],
        k=5,
    )

    assert {result["project_key"] for result in results} == {"A", "B"}
    assert {result["mem_id"] for result in results} == {id_a, id_b}
    assert {result["id"] for result in results} == {f"A:{id_a}", f"B:{id_b}"}
    assert [result["score"] for result in results] == sorted(
        [result["score"] for result in results],
        reverse=True,
    )
    assert RoleMemoryStore(root_a).event_path("implementation").read_text(encoding="utf-8") == events_a
    assert RoleMemoryStore(root_b).event_path("implementation").read_text(encoding="utf-8") == events_b


def test_cross_project_retrieve_requires_scope(tmp_path):
    try:
        retrieve(tmp_path, "implementation", "retrieval", scope=[])
    except ValueError as exc:
        assert "scope is required" in str(exc)
    else:
        raise AssertionError("retrieve should require an explicit scope")


def test_api_memory_cross_returns_results_for_comma_scope(tmp_path, monkeypatch):
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    write_project_config(tmp_path, "A", root_a)
    write_project_config(tmp_path, "B", root_b)
    seed_memory(root_a, "Cross API retrieval fact from project A.")
    seed_memory(root_b, "Cross API retrieval fact from project B.")

    handler = capture_get_handler(tmp_path, monkeypatch)(
        "/api/memory/cross?role=implementation&q=cross%20api%20retrieval&scope=A,B&k=5"
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert {result["project_key"] for result in payload["results"]} == {"A", "B"}
    assert all(result["id"].startswith(f"{result['project_key']}:mem:") for result in payload["results"])


def test_api_memory_cross_400s_on_missing_required_params(tmp_path, monkeypatch):
    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/cross?role=implementation&q=memory")
    assert 400 in handler.responses

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/cross?q=memory&scope=A")
    assert 400 in handler.responses

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/cross?role=implementation&scope=A")
    assert 400 in handler.responses
