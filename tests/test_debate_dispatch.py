from http_transport_helpers import prepare_body_reader

import json
from io import BytesIO

from goodteam_os.debate import _parse_debate_objections
from goodteam_os.messages import read_messages
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
        handler.wfile.seek(0)
        return handler, json.loads(handler.wfile.read().decode("utf-8") or "{}")

    return simulate_post


def test_parse_debate_objections_strips_bullets_ignores_markdown_and_caps():
    text = """
# Debate output

```text
reject: fenced output is ignored
```
- reject: Missing rollback path.
* ask: Which artifact owns the gate?

## Extra notes
reject: Security risk is not mitigated.
ask: Fourth line should be capped.
"""

    parsed = _parse_debate_objections(text, allowed=("ask", "reject"))

    assert parsed == [
        "reject: Missing rollback path.",
        "ask: Which artifact owns the gate?",
        "reject: Security risk is not mitigated.",
    ]


def test_api_debate_posts_and_returns_gate_messages(tmp_path, monkeypatch):
    artifact = tmp_path / "artifacts" / "rfc.md"
    artifact.parent.mkdir()
    artifact.write_text("# RFC\n", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.artifacts["rfc"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    dispatch_calls = []

    def fake_default_debate_dispatch(workspace, project_key, run_id):
        assert workspace == tmp_path
        assert project_key == "CONTENTOPS"
        assert run_id == "run-1"

        def fake_dispatch(**kwargs):
            dispatch_calls.append(kwargs)
            if kwargs["kind"] == "challenge":
                assert kwargs["artifact_ref"] == str(artifact)
                return [
                    "reject: Rollback path is missing.",
                    "ask: Which service owns migration failure?",
                ]
            assert [message["body"] for message in kwargs["challenges"]] == [
                "Rollback path is missing.",
                "Which service owns migration failure?",
            ]
            return [
                "propose: Add rollback section.",
                "assert: Platform owns migration failure.",
            ]

        return fake_dispatch

    monkeypatch.setattr("goodteam_os.web.default_debate_dispatch", fake_default_debate_dispatch)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response, body = simulate_post(
        "/api/debate",
        {
            "project_key": "CONTENTOPS",
            "run": "run-1",
            "gate": "rfc",
        },
    )

    assert 200 in response.responses
    assert [call["kind"] for call in dispatch_calls] == ["challenge", "rebut"]
    messages = body["messages"]
    assert [message["kind"] for message in messages] == ["reject", "ask", "propose", "assert"]
    assert [message["gate"] for message in messages] == ["rfc"] * 4
    assert [message["refs"] for message in messages[:2]] == [[str(artifact)], [str(artifact)]]
    assert [message["to"] for message in messages[:2]] == [["architect"], ["architect"]]
    assert [message["to"] for message in messages[2:]] == [["qa"], ["qa"]]
    assert read_messages(tmp_path, "CONTENTOPS", "run-1", gate="rfc") == messages
