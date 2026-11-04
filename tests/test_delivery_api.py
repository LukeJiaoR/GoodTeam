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


def test_approval_brief_preserves_nested_chinese_scope_and_exclusions_beyond_preview(tmp_path):
    from goodteam_os.team_workspace import build_team_workspace

    state = create_initial_state("stockflow", "DEMO", "Build StockFlow")
    state.current_node, state.next_node = "prd_review_gate", "await_prd_approval"
    path = tmp_path / "prd.md"
    path.write_text("# Risk Management Scope PRD\n## 1. 产品意图\n" + "背景说明。" * 1800
                    + "\n\n## 4. V1 功能范围\n### 4.1 订单\n- 订单确认原子占用库存。\n"
                    + "## 12. 非目标\n### 12.1 本期排除\n- 不实现多仓库。\n")
    state.artifacts["prd"] = str(path)
    reviews = web.build_approval_reviews(state, workspace=tmp_path)
    projection = build_team_workspace(state=state, workflow_active=True, project_root=None, approval_reviews=reviews)
    brief = projection["attention_request"]["approval_brief"]
    assert reviews[0]["content_truncated"] is True
    assert brief["scope"]["included"][0]["items"] == ["订单确认原子占用库存。"]
    assert brief["scope"]["excluded"][0]["items"] == ["不实现多仓库。"]
    assert brief["approval_target"]["artifact_version"] == reviews[0]["artifact_version"]
    assert not any(risk["kind"] == "artifact" for risk in brief["risks"])
    assert "artifact_sections" not in reviews[0]
    path.write_text("# Another version\n## Scope\n- Changed scope.\n")
    stale = build_team_workspace(state=state, workflow_active=True, project_root=None, approval_reviews=reviews)
    assert stale["attention_request"]["approval_brief"]["scope"]["included"] == []


def test_rfc_brief_distinguishes_children_of_combined_scope_and_non_goals(tmp_path):
    from goodteam_os.team_workspace import build_team_workspace

    state = create_initial_state("stockflow", "DEMO", "Build StockFlow")
    state.current_node, state.next_node = "rfc_review_gate", "await_rfc_approval"
    path = tmp_path / "rfc.md"
    path.write_text("# StockFlow RFC\n## 2. 范围与非目标\n### 2.1 本 RFC 覆盖\n- 商品、库存、订单。\n"
                    "### 2.2 明确不覆盖\n- 多仓库和物流。\n"
                    "### 2.3 Out of scope / 范围外\n- 在线支付。\n")
    state.artifacts["rfc"] = str(path)
    projection = build_team_workspace(state=state, workflow_active=True, project_root=None,
                                      approval_reviews=web.build_approval_reviews(state, workspace=tmp_path))
    scope = projection["attention_request"]["approval_brief"]["scope"]
    assert scope["included"][0]["items"] == ["商品、库存、订单。"]
    assert scope["excluded"][0]["items"] == ["多仓库和物流。"]
    assert scope["excluded"][1]["items"] == ["在线支付。"]
    assert len(scope["included"]) == 1
