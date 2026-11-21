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


def test_work_and_run_views_keep_pending_review_when_repository_is_unavailable(tmp_path, api):
    config = tmp_path / "projects" / "demo.local.yaml"
    config.parent.mkdir()
    config.write_text(
        f"key: DEMO\nname: Demo\nrepos:\n  app: {tmp_path / 'missing-repository'}\n"
        "reviewer_profiles:\n  implementation:\n    - correctness-security\n"
    )
    state = create_initial_state("run-one", "DEMO", "Review existing work")
    state.source_ref = str(config)
    state.current_node, state.next_node = "staging_review_gate", "await_staging_approval"
    report = tmp_path / "staging.md"
    report.write_text("# Staging report\nAwaiting independent review.\n")
    state.artifacts["staging_report"] = str(report)
    StateStore(tmp_path).write_project_current(state)

    for path in ("/api/state?project=DEMO", f"/api/projects/DEMO/runs/{state.run_id}"):
        status, result = api(path)

        assert status == 200
        assert result["state"]["run_id"] == state.run_id
        review = next(item for item in result["approval_reviews"] if item["approval"] == "staging")
        assert review["is_pending"] is True
        assert review["legacy_reviewer_jobs"] == []
        assert review["reviewer_provenance"]["badge"]["kind"] == "missing"
        assert review["is_actionable"] is False


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
