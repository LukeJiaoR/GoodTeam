import json
from io import BytesIO

from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import serve


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def capture_post_handler(tmp_path, monkeypatch):
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

    def simulate_post(body_dict):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes))}
                self.path = "/api/approve"
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

    return simulate_post


def write_pending_prd_state(workspace, run_id="run-1"):
    state = create_initial_state(run_id, "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(workspace).write_project_current(state)


def read_action_log(workspace):
    log_path = workspace / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_api_approve_route_records_debate_ref_only_when_provided(tmp_path, monkeypatch):
    without_ref = tmp_path / "without-ref"
    without_ref.mkdir()
    write_pending_prd_state(without_ref)
    simulate_without_ref = capture_post_handler(without_ref, monkeypatch)

    response = simulate_without_ref(
        {
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "changes_requested",
            "note": "Revise the PRD scope.",
        }
    )

    assert 200 in response.responses
    without_ref_actions = read_action_log(without_ref)
    assert without_ref_actions[0]["action"] == "approval_decided"
    assert "debate_ref" not in without_ref_actions[0]

    with_ref = tmp_path / "with-ref"
    with_ref.mkdir()
    write_pending_prd_state(with_ref, run_id="run-2")
    simulate_with_ref = capture_post_handler(with_ref, monkeypatch)

    response = simulate_with_ref(
        {
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "changes_requested",
            "debate_ref": "messages/run-2.jsonl#msg:abc123",
            "note": "Address the debate findings.",
        }
    )

    assert 200 in response.responses
    with_ref_actions = read_action_log(with_ref)
    assert with_ref_actions[0]["action"] == "approval_decided"
    assert with_ref_actions[0]["debate_ref"] == "messages/run-2.jsonl#msg:abc123"
