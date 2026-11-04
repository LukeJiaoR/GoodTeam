"""Representative regressions from the real StockFlow greenfield trial."""

from pathlib import Path

import pytest

from goodteam_os import graph, web
from goodteam_os.project_import import import_project
from goodteam_os.projects import write_project_config
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.task_assistant import TaskAssistantService
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.workflow_retry import build_retry_target
from goodteam_os.workflow_revisions import retry_legacy_revision


def test_empty_project_can_prepare_git_and_resume_the_same_confirmed_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    workspace, root = tmp_path / "workspace", tmp_path / "stockflow"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    config = web._project_config_for_key(workspace, key)
    monkeypatch.setattr(web.ProjectReadinessExecutor, "_dispatch",
                        lambda _self, key, root, eid, attempt, runner, _background: runner(key, root, eid, attempt))
    monkeypatch.setattr(web.ProjectReadinessExecutor, "_default_preflight",
                        lambda *_: {"status": "ready", "mode": "codex", "local_only": True})
    web.ProjectReadinessExecutor(workspace).start(key, root, {"goal": "StockFlow", "brief": "Inventory"},
                                                idempotency_key="prepare")
    assert list(root.iterdir()) == []  # Preparation remains read-only.
    service = TaskAssistantService(workspace, project_config=config)
    monkeypatch.setattr(web, "TaskAssistantService", lambda *_args, **_kwargs: service)
    # This is the repository prerequisite regardless of model selection.
    monkeypatch.setattr(web, "_workflow_requires_repository", lambda *_args, **_kwargs: True)
    store = TaskAssistantSessionStore(workspace, key)
    store.append_message("request", "user", "Build StockFlow")
    proposal = store.propose_task(["request"], "Build StockFlow")["proposed_task"]
    calls = []
    monkeypatch.setattr(web, "start_workflow", lambda **request: calls.append(request) or {"run_id": request["run_id"]})
    path = f"/api/projects/{key}/task-assistant/actions"
    request = {"action": "confirm_task", "proposal_id": proposal["id"], "expected_revision": proposal["revision"]}

    with pytest.raises(web.ProjectApiError) as error:
        web.handle_project_api_post(workspace, path, request)
    assert error.value.code == "git_baseline_required"
    blocked = store.read()["proposed_task"]
    assert blocked["confirmed"] is True and not blocked.get("start_claim_id") and not calls
    assert StateStore(workspace).read_project_current(key) is None

    (root / ".DS_Store").write_bytes(b"ignored Finder metadata")
    recovered = web.initialize_project_git_for_request(workspace, {"project_key": key, "scaffold_new_project": True})
    assert recovered["state"] == "valid_head" and recovered["remote_created"] is False
    assert (root / "README.md").is_file() and (root / ".gitignore").is_file()
    status, result = web.handle_project_api_post(workspace, path, request)
    assert status == 200 and result["workflow_started"] is True
    assert result["run_id"] == f"task-{proposal['id']}" and len(calls) == 1
    assert store.read()["proposed_task"]["start_status"] == "completed"
    (root / "business.txt").write_text("Do not scaffold an existing project")
    with pytest.raises(ValueError, match="empty imported new project"):
        web.initialize_project_git_for_request(workspace, {"project_key": key, "scaffold_new_project": True})


def test_failed_real_prd_generation_blocks_and_retries_without_a_template_or_fake_change_request(tmp_path, monkeypatch):
    root = tmp_path / "target"
    root.mkdir()
    config = write_project_config(tmp_path / "projects", "STOCKFLOW", "StockFlow", repo_path=str(root), worker_mode="fake")
    state = create_initial_state("stockflow-run", "STOCKFLOW", "Build inventory and orders")
    state.source_ref = str(config)
    monkeypatch.setattr(graph, "should_generate_artifact_with_worker", lambda *_: True)
    monkeypatch.setattr(web, "should_generate_artifact_with_worker", lambda *_: True)
    calls = []

    def generate(**request):
        calls.append(request)
        if len(calls) % 2 == 1:
            request["failure_status"].update(summary="Codex CLI exited with code 1.")
            return None
        return "# StockFlow PRD\n\n## Acceptance Criteria\n- Orders reserve inventory atomically.\n"

    monkeypatch.setattr(graph, "generate_artifact_via_worker", generate)
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda *_args, **_kwargs: None)
    blocked = graph.run_until_next_gate(state, tmp_path, config)
    assert blocked.current_node == "product_requirement" and blocked.next_node == "revise_prd"
    assert blocked.blockers and "prd" not in blocked.artifacts
    assert not list((tmp_path / "artifacts").rglob("prd.md"))
    assert blocked.change_requests == {} and build_retry_target(blocked)["available"] is True
    StateStore(tmp_path).write_project_current(blocked)
    append = web.append_action

    def record_ready(*args, **kwargs):
        if kwargs.get("action") in {"prd_generated", "prd_regenerated", "prd_revised"}:
            durable = StateStore(tmp_path).read_project_current("STOCKFLOW")
            assert durable.next_node == "await_prd_approval"
            assert durable.artifacts["prd"] == kwargs["artifact_path"]
        return append(*args, **kwargs)

    monkeypatch.setattr(web, "append_action", record_ready)
    recovered = retry_legacy_revision(blocked, tmp_path)
    assert recovered["next_node"] == "await_prd_approval" and not recovered["blockers"]
    assert recovered["approvals"]["prd"] == "pending" and len(calls) == 2
    assert Path(recovered["artifacts"]["prd"]).read_text().startswith("# StockFlow PRD")

    # Reject a real existing PRD, fail regeneration, then retry the same run.
    # Historical references remain readable without becoming ready events.
    old_path = recovered["artifacts"]["prd"]
    rejected = web.decide_approval(workspace=tmp_path, project_key="STOCKFLOW", approval="prd",
                                   decision="rejected", note="Regenerate this PRD")
    assert rejected["next_node"] == "revise_prd" and rejected["artifacts"]["prd"] == old_path
    assert not any(item["action"] == "prd_regenerated" for item in web.build_action_items(tmp_path, "STOCKFLOW"))
    blocked = StateStore(tmp_path).read_project_current("STOCKFLOW")
    assert blocked.change_requests == {} and build_retry_target(blocked)["available"] is True
    recovered = retry_legacy_revision(blocked, tmp_path)
    assert recovered["next_node"] == "await_prd_approval" and not recovered["blockers"]
    assert recovered["artifacts"]["prd"] != old_path and Path(old_path).is_file() and len(calls) == 4
    events = [item for item in web.build_action_items(tmp_path, "STOCKFLOW")
              if item["action"] in {"prd_generated", "prd_regenerated"}]
    assert {item["action"] for item in events} == {"prd_generated", "prd_regenerated"}
    assert next(item for item in events if item["action"] == "prd_regenerated")["artifact_path"] == recovered["artifacts"]["prd"]
    changed = web.decide_approval(workspace=tmp_path, project_key="STOCKFLOW", approval="prd",
                                  decision="changes_requested", note="Clarify stock reservation")
    assert changed["next_node"] == "revise_prd"
    retry_legacy_revision(StateStore(tmp_path).read_project_current("STOCKFLOW"), tmp_path)
    revised = [item for item in web.build_action_items(tmp_path, "STOCKFLOW") if item["action"] == "prd_revised"]
    assert len(revised) == 1 and revised[0]["note"] == "Clarify stock reservation"
