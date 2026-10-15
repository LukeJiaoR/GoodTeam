"""The cross-project queue projects current work without writing or acting."""

import json

import pytest

from goodteam_os import web
from goodteam_os.project_attention import build_project_attention
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.team_workspace import build_team_workspace


def workflow(*, run_id="run-1", kind="approval", active=True, actors=(), intent=None):
    return {
        "mission": {"source": "workflow", "project_key": "DEMO", "run_id": run_id,
                    "title": "Deliver the actual task", "read_only": not active},
        "attention_request": {"id": "approval:prd" if kind == "approval" else "workflow-blocked",
                              "kind": kind, "target_id": "prd" if kind == "approval" else run_id,
                              "reason": "approval_required" if kind == "approval" else "Worker failed",
                              "intent_commit": intent},
        "execution_actors": list(actors),
    }


def test_items_are_sorted_counted_once_and_keep_durable_identity():
    preparation = {"execution": {"id": "preparation-2"}, "steps": [
        {"id": "commands", "status": "needs_input"},
        {"id": "worker", "status": "failed", "error": {"message": "Worker unavailable"}},
        {"id": "worker", "status": "failed", "error": {"message": "Worker unavailable"}},
        {"id": "repository", "status": "complete"},
    ]}
    snapshot = build_project_attention(project_key="DEMO", readiness=preparation, workflow=workflow(),
                                       session={"proposed_task": {"id": "draft-2", "needs_clarification": True,
                                                                 "exact_request": "Clarify the scope"}})
    assert snapshot["counts"] == {"blocked": 1, "approval": 1, "needs_input": 2, "total": 4}
    assert [item["kind"] for item in snapshot["items"]] == ["blocked", "approval", "needs_input", "needs_input"]
    assert snapshot["items"][0]["id"] == "readiness:preparation-2:worker"
    approval = snapshot["items"][1]
    assert approval["id"] == "workflow:run-1:approval:prd"
    assert approval["target_id"] == "prd"
    assert approval["run_id"] == "run-1"
    assert snapshot["items"][0]["reason"] == "Worker unavailable"
    assert "actions" not in approval


@pytest.mark.parametrize("mutation", [
    {"read_only": True}, {"project_key": "OTHER"}, {"source": "operator_queue"}, {"run_id": ""},
])
def test_foreign_historical_or_unsupported_mission_is_not_current_attention(mutation):
    projection = workflow()
    projection["mission"].update(mutation)
    assert build_project_attention(project_key="DEMO", readiness={}, workflow=projection)["items"] == []


def test_running_attempt_suppresses_old_failure_but_not_approval():
    actor = {"role": "implementation", "state": "working"}
    for kind, expected in [("blocker", 0), ("approval", 1)]:
        assert build_project_attention(project_key="DEMO", readiness={},
                                       workflow=workflow(kind=kind, actors=[actor]))["counts"]["total"] == expected
    assert build_project_attention(project_key="DEMO", readiness={}, workflow=workflow(
        kind="blocker", actors=[{"role": "orchestrator", "state": "working"}]))["counts"]["blocked"] == 1


@pytest.mark.parametrize("status", ["discussing", "ready_to_confirm", "stale", "ambiguous", "committing"])
def test_unresolved_intent_is_input_not_approval(status):
    snapshot = build_project_attention(project_key="DEMO", readiness={},
                                       workflow=workflow(intent={"status": status}))
    assert snapshot["counts"]["needs_input"] == 1
    assert snapshot["counts"]["approval"] == 0


@pytest.mark.parametrize("proposal", [
    {"id": "draft", "needs_clarification": False},
    {"id": "draft", "needs_clarification": True, "status": "cancelled"},
    {"id": "draft", "needs_clarification": True, "run_id": "already-started"},
    {"needs_clarification": True},
])
def test_only_live_explicit_clarification_drafts_are_attention(proposal):
    assert build_project_attention(project_key="DEMO", readiness={},
                                   session={"proposed_task": proposal})["items"] == []


def _project(tmp_path, key, *, gate=None, blocker=None, terminal=False):
    root = tmp_path / "repos" / key
    root.mkdir(parents=True)
    configs = tmp_path / "projects"
    configs.mkdir(exist_ok=True)
    (configs / f"{key}.local.yaml").write_text(
        f"name: {key} project\nkey: {key}\nrepos:\n  root: {root}\nworker_backend:\n  mode: fake\n")
    state = create_initial_state(f"run-{key}", key, f"The task for {key}")
    if gate:
        state.current_node = f"{gate}_review_gate"
        state.next_node = f"await_{gate}_approval"
    if blocker:
        state.blockers = [blocker]
    if terminal:
        state.next_node = ""
    StateStore(tmp_path).write_project_current(state)
    return root, state


def _files(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file() and path.name != ".project-import.lock"}


def test_real_project_list_reuses_work_attention_without_writes(tmp_path):
    _project(tmp_path, "APPROVAL", gate="prd")
    _project(tmp_path, "BLOCKED", blocker="Evidence is missing")
    _project(tmp_path, "FINISHED", gate="prd", blocker="Old blocker", terminal=True)
    root, active = _project(tmp_path, "NORMAL")
    # Initial states contain five pending gates. None is actionable before its turn.
    assert all(value == ApprovalDecision.PENDING for value in active.approvals.values())
    before = _files(tmp_path)
    payload = web.build_project_list(tmp_path)
    assert _files(tmp_path) == before
    projects = {item["project"]["project_key"]: item for item in payload["projects"]}
    assert projects["APPROVAL"]["attention"]["counts"] == {
        "blocked": 0, "approval": 1, "needs_input": 0, "total": 1}
    assert projects["BLOCKED"]["attention"]["items"][0]["reason"] == "Evidence is missing"
    assert projects["FINISHED"]["attention"]["items"] == []
    assert projects["NORMAL"]["attention"]["items"] == []
    assert all(item["attention_count"] == 0 for item in projects.values())
    assert not (root / ".goodteam").exists()


def test_real_preparation_and_draft_project_counts_are_read_only(tmp_path):
    root, _ = _project(tmp_path, "DEMO")
    from goodteam_os.project_readiness import ProjectReadinessStore
    readiness_store = ProjectReadinessStore(tmp_path)
    readiness = readiness_store.initialize("DEMO", root)
    readiness["steps"] = [{"id": "commands", "status": "needs_input"}, {"id": "worker", "status": "failed"}]
    readiness["execution"] = {"id": "preparation-1", "status": "needs_input", "current_step_id": "commands"}
    readiness_store._write("DEMO", readiness)
    session_store = TaskAssistantSessionStore(tmp_path, "DEMO")
    session_store.append_message("message-1", "user", "Clarify required feature")
    session = session_store.propose_task(["message-1"], "Clarify required feature")
    session["proposed_task"]["needs_clarification"] = True
    session_store.path.write_text(json.dumps(session))
    before = _files(tmp_path)
    project = web.build_project_list(tmp_path)["projects"][0]
    assert _files(tmp_path) == before
    assert project["attention_count"] == 2
    assert project["attention"]["counts"] == {"blocked": 1, "approval": 0, "needs_input": 2, "total": 3}
    assert {item["source"] for item in project["attention"]["items"]} == {"readiness", "assistant"}


def test_unreadable_optional_attention_is_unknown_and_other_project_stays_available(tmp_path):
    _project(tmp_path, "BROKEN", gate="prd")
    _project(tmp_path, "OK", gate="rfc")
    session = TaskAssistantSessionStore(tmp_path, "BROKEN")
    session.path.write_text("not json")
    before = _files(tmp_path)
    projects = {item["project"]["project_key"]: item for item in web.build_project_list(tmp_path)["projects"]}
    assert _files(tmp_path) == before
    assert projects["BROKEN"]["attention"] is None
    assert projects["BROKEN"]["active_run"]["run_id"] == "run-BROKEN"
    assert projects["OK"]["attention"]["counts"]["approval"] == 1


def test_source_resolution_replaces_identity_and_does_not_resurrect_old_gate(tmp_path):
    _, state = _project(tmp_path, "DEMO", gate="prd")
    first = web.build_project_list(tmp_path)["projects"][0]["attention"]["items"][0]
    state.approvals["prd"] = ApprovalDecision.APPROVED
    state.next_node = "architect"
    StateStore(tmp_path).write_project_current(state)
    assert web.build_project_list(tmp_path)["projects"][0]["attention"]["items"] == []
    state.run_id = "new-run"
    state.approvals["prd"] = ApprovalDecision.PENDING
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)
    second = web.build_project_list(tmp_path)["projects"][0]["attention"]["items"][0]
    assert first["id"] != second["id"]
    assert second["run_id"] == "new-run"


def test_workflow_projection_is_same_as_work_surface_for_current_approval(tmp_path):
    root, state = _project(tmp_path, "DEMO", gate="prd", blocker="Does not outrank current approval")
    source = build_team_workspace(state=state, workflow_active=True, project_root=root,
                                  approval_reviews=web.build_approval_reviews(state, workspace=tmp_path))
    item = web.build_project_list(tmp_path)["projects"][0]["attention"]["items"][0]
    assert item["id"] == f"workflow:{state.run_id}:{source['attention_request']['id']}"
    assert item["target_id"] == source["attention_request"]["target_id"]
    assert item["kind"] == source["attention_request"]["kind"]


@pytest.mark.parametrize("source", ["session", "workflow"])
def test_foreign_source_fails_unknown_without_exposing_foreign_attention(tmp_path, source):
    _, state = _project(tmp_path, "DEMO", gate="prd")
    if source == "session":
        store = TaskAssistantSessionStore(tmp_path, "DEMO")
        session = store.read()
        session["project_key"] = "OTHER"
        store.path.write_text(json.dumps(session))
    else:
        path = StateStore(tmp_path).project_state_dir("DEMO") / "current.json"
        data = json.loads(path.read_text())
        data["project_key"] = "OTHER"
        path.write_text(json.dumps(data))
    assert web.build_project_list(tmp_path)["projects"][0]["attention"] is None


@pytest.mark.parametrize("source,value", [("execution", []), ("execution", "invalid"), ("proposed_task", "invalid")])
def test_malformed_optional_source_is_not_an_empty_valid_queue(source, value):
    with pytest.raises(ValueError):
        build_project_attention(project_key="DEMO", readiness={source: value} if source == "execution" else {},
                                session={source: value} if source == "proposed_task" else {})


def test_revision_hint_matches_work_without_persisting_display_blocker(tmp_path):
    _, state = _project(tmp_path, "DEMO")
    state.next_node = "revise_prd"
    state.approvals["prd"] = ApprovalDecision.CHANGES_REQUESTED
    StateStore(tmp_path).write_project_current(state)
    item = web.build_project_list(tmp_path)["projects"][0]["attention"]["items"][0]
    assert item["kind"] == "blocked"
    assert item["reason"] == "Changes requested; retry to apply the recorded revision."
    assert StateStore(tmp_path).read_project_current("DEMO").blockers == []


@pytest.mark.parametrize("status", ["recording", "executing"])
def test_pending_transition_is_unknown_without_recovering_or_replaying(tmp_path, monkeypatch, status):
    _, state = _project(tmp_path, "DEMO", gate="prd")
    state.pending_transition = {"status": status}
    StateStore(tmp_path).write_project_current(state)
    def forbidden(*args, **kwargs):
        pytest.fail("Cross-project scan must not recover workflow transitions")
    monkeypatch.setattr(web, "recover_transition", forbidden)
    before = _files(tmp_path)
    assert web.build_project_list(tmp_path)["projects"][0]["attention"] is None
    assert _files(tmp_path) == before


def test_http_multi_project_refresh_and_exact_run_detail(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    _, state = _project(tmp_path, "DEMO", gate="prd")
    _project(tmp_path, "OTHER", blocker="Only OTHER is blocked")
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            assert response.status == 200
            return json.load(response)
    try:
        before = _files(tmp_path)
        first = get("/api/projects")
        demo = next(item for item in first["projects"] if item["project"]["project_key"] == "DEMO")
        target = demo["attention"]["items"][0]
        detail = get(f"/api/projects/DEMO/runs/{target['run_id']}")
        assert detail["run"]["project_key"] == "DEMO"
        assert detail["run"]["run_id"] == "run-DEMO"
        assert next(item for item in detail["approval_reviews"] if item["is_actionable"])["approval"] == target["target_id"]
        assert _files(tmp_path) == before
        with pytest.raises(urllib.error.HTTPError) as error:
            get(f"/api/projects/OTHER/runs/{target['run_id']}")
        assert error.value.code == 404
        state.approvals["prd"] = ApprovalDecision.APPROVED
        state.next_node = "architect"
        StateStore(tmp_path).write_project_current(state)
        current = get("/api/projects")
        assert next(item for item in current["projects"] if item["project"]["project_key"] == "DEMO")["attention"]["items"] == []
        assert next(item for item in current["projects"] if item["project"]["project_key"] == "OTHER")["attention"]["counts"]["blocked"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("transition", [None, [], "invalid"])
def test_invalid_transition_keeps_launcher_but_cannot_claim_clear(tmp_path, transition):
    _, state = _project(tmp_path, "DEMO", gate="prd")
    state.pending_transition = transition
    StateStore(tmp_path).write_project_current(state)
    project = web.build_project_list(tmp_path)["projects"][0]
    assert project["attention"] is None
    assert project["active_run"]["run_id"] == "run-DEMO"


@pytest.mark.parametrize("steps", [None, {}, "invalid", [None], [{}], [{"status": "needs_input"}]])
def test_invalid_preparation_cannot_be_a_zero_attention_queue(steps):
    with pytest.raises(ValueError):
        build_project_attention(project_key="DEMO", readiness={"steps": steps})
