import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from goodteam_os import web
from goodteam_os.delivery_outcomes import build_delivery_summary
from goodteam_os.state import StateStore, create_initial_state


@pytest.fixture
def api(tmp_path, monkeypatch):
    servers = queue.Queue()
    server_type = web.ThreadingHTTPServer

    class CapturingServer(server_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.put(self)

    monkeypatch.setattr(web, "ThreadingHTTPServer", CapturingServer)
    thread = threading.Thread(target=web.serve, kwargs={"workspace": tmp_path, "port": 0}, daemon=True)
    thread.start()
    server = servers.get(timeout=5)

    def get(path):
        try:
            response = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}" + path, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    yield get
    server.shutdown()
    thread.join(timeout=5)


def test_run_bound_preview_route_rejects_stale_and_foreign_targets(tmp_path, api):
    state = create_initial_state("run-one", "DEMO", "Improve diagnostics")
    path = tmp_path / "artifacts/runs/run-one/qa/report.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Verification\nNot yet accepted.")
    state.artifacts["qa_report"] = str(path)
    StateStore(tmp_path).write_project_current(state)
    href = build_delivery_summary(tmp_path, state=state)["artifacts"][0]["href"]
    status, result = api(href)
    assert 200 == status
    assert path.read_text() == result["content"]
    assert 404 == api(href.replace("project=DEMO", "project=OTHER"))[0]
    assert 404 == api(href.replace("key=qa_report", "key=private"))[0]
    path.write_text("New revision")
    assert 409 == api(href)[0]


def test_full_review_content_carries_same_bytes_version(tmp_path):
    from hashlib import sha256

    state = create_initial_state("run-one", "DEMO", "Review full content")
    path = tmp_path / "artifacts/runs/run-one/prd/prd.md"
    path.parent.mkdir(parents=True)
    raw = ("# PRD\n" + "acceptance\n" * 1500).encode()
    path.write_bytes(raw)
    state.artifacts["prd"] = str(path)
    StateStore(tmp_path).write_project_current(state)
    result = web.read_artifact(workspace=tmp_path, project_key="DEMO", artifact_key="prd")
    assert raw.decode() == result["content"]
    assert "sha256:" + sha256(raw).hexdigest() == result["artifact_version"]
    review = next(item for item in web.build_approval_reviews(state, workspace=tmp_path) if item["approval"] == "prd")
    assert True is review["content_truncated"]
