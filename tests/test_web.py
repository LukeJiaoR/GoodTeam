import errno
import json
import inspect
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os import project_import
import goodteam_os.web as web
from goodteam_os.messages import append_message
from goodteam_os.operator_queue import DurableOperatorQueue
from goodteam_os.staging import delivery_contract_config
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from goodteam_os.web import (
    build_approval_evidence_snapshot,
    build_approval_recommendation,
    build_action_items,
    build_dev_loop,
    build_provenance_refs,
    build_workflow_graph,
    build_project_overview_items,
    build_role_observations,
    build_approval_reviews,
    build_start_console_context,
    build_workflow_steps,
    decide_approval,
    discover_project_configs,
    discover_project_directories,
    _request_origin_is_same_origin,
    create_local_project_config,
    delete_project,
    read_artifact,
    read_artifact_preview,
    read_open_questions,
    render_markdown_preview,
    maybe_send_favicon_response,
    retry_blocked_workflow,
    terminate_project_run,
    resolve_dashboard_state,
    save_artifact,
    is_active_run,
    list_directory_for_picker,
    open_path_in_file_manager,
    start_workflow,
)


def _write_context_project(workspace: Path, project_root: Path, key: str = "CTX") -> Path:
    projects = workspace / "projects"
    projects.mkdir()
    config = projects / f"{key.lower()}.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Context Project",
                f"key: {key}",
                "repos:",
                f"  root: {project_root}",
                "worker_backend:",
                "  mode: fake",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _write_reviewer_dispatch_project(
    workspace: Path,
    project_root: Path,
    *,
    dispatch_mode: str = "parallel",
    acknowledged: bool = False,
) -> Path:
    projects = workspace / "projects"
    projects.mkdir(exist_ok=True)
    config = projects / "demo.local.yaml"
    lines = [
        "name: Demo",
        "key: DEMO",
        "repos:",
        f"  app: {project_root}",
        "worker_backend:",
        "  mode: fake",
        "reviewer_profiles:",
        "  implementation:",
        "    - correctness-security",
        "    - architecture-maintainability",
        "reviewer_dispatch:",
        f"  mode: {dispatch_mode}",
    ]
    if acknowledged:
        lines.extend(
            [
                "  rollout:",
                "    enabled: true",
                '    acknowledge: "parallel-reviewer-dispatch-is-advisory"',
            ]
        )
    lines.extend(["  phases:", "    implementation: parallel"])
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def _post_web_json(workspace: Path, route: str, payload: dict[str, object], *, origin: str | None = None):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(workspace))
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}{route}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": origin or f"http://127.0.0.1:{server.server_port}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post_web_raw(
    workspace: Path,
    route: str,
    payload: bytes,
    *,
    content_type: str = "application/json",
):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(workspace))
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{route}",
            data=payload,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Origin": f"http://127.0.0.1:{server.server_port}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get_web_json(workspace: Path, route: str):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(workspace))
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}{route}",
                timeout=5,
            ) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_project_import_returns_created_home_contract(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root), "display_name": "Target"},
    )
    assert status == 201
    assert payload["created"] is True
    assert payload["project"]["project_key"] == "TARGET"
    assert payload["readiness"]["lifecycle"] == "preparing"
    assert payload["capabilities"]["start_workflow"] is False
    assert payload["home_url"] == "/projects/TARGET/home"


def test_api_project_import_returns_structured_missing_root_error(tmp_path: Path) -> None:
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(tmp_path / "missing")},
    )
    assert status == 404
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "project_root_not_found"
    assert payload["recovery"] == ["choose_another_directory"]
    assert payload["request_id"]


def test_api_project_import_rejects_relative_root_with_stable_recovery(
    tmp_path: Path,
) -> None:
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": "relative/project"},
    )

    assert status == 400
    assert payload["code"] == "project_root_not_absolute"
    assert payload["details"] == {"root": "relative/project"}
    assert payload["recovery"] == ["choose_another_directory"]
    assert payload["request_id"]


def test_api_project_import_duplicate_returns_ok(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    assert _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})[0] == 201

    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root)},
    )

    assert status == 200
    assert payload["created"] is False


def test_api_project_import_rejects_cross_origin_mutation(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()

    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root)},
        origin="https://attacker.example",
    )

    assert status == 403
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "same_origin_required"
    assert payload["request_id"]
    assert not (tmp_path / "projects").exists()


def test_api_project_mode_confirmation_updates_home_projection(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"x")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/AMBIGUOUS/mode",
        {"mode": "existing_project"},
    )
    assert status == 200
    assert payload["readiness"]["project_mode"] == "existing_project"
    assert payload["next_action"]["id"] == "repository_understanding"


def test_project_home_restores_from_disk_after_handler_restart(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    (root / "package.json").write_text('{"name":"existing"}', encoding="utf-8")

    first_status, imported = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root)},
    )
    assert first_status == 201

    second_status, restored = _get_web_json(
        tmp_path,
        f"/api/projects/{imported['project']['project_key']}/home",
    )
    assert second_status == 200
    assert restored["readiness"] == imported["readiness"]
    assert restored["project"]["root"] == str(root.resolve())


def test_project_home_projects_confirmed_commands_and_constraints(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    _write_context_project(tmp_path, root, key="FACTS")
    readiness = {
        "project_mode": "existing_project",
        "steps": [],
        "inputs": {
            "command_verification": {
                "values": {"confirmed_command_ids": ["test-command"]},
            },
            "constraints_confirmation": {
                "values": {"confirmed": True, "note": "Keep changes scoped"},
            },
        },
        "artifacts": {
            "repository": {
                "content": {
                    "commands": [
                        {"id": "test-command", "command": "npm test"},
                        {"id": "lint-command", "command": "npm run lint"},
                    ],
                    "constraints": ["No network writes"],
                },
            },
        },
    }

    home = web.build_project_home(tmp_path, "FACTS", readiness_override=readiness)

    assert home["facts"]["confirmed_commands"] == ["npm test"]
    assert home["facts"]["constraints"] == ["No network writes", "Keep changes scoped"]
    assert home["facts"]["constraints_confirmed"] is True
    assert home["facts"]["worker_status"] == "ready"
    assert home["facts"]["worker_backend"] == "fake"


def test_start_workflow_resolves_default_model_catalog_outside_repo_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = start_workflow(
        workspace=tmp_path / "workspace",
        idea="Verify cwd-independent startup",
        project_key="CWD",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        dry_run=True,
    )

    assert result["model_profile"] == "balanced"


def test_start_workflow_rejects_incomplete_persisted_readiness(tmp_path: Path) -> None:
    root = tmp_path / "target"
    root.mkdir()
    (root / "package.json").write_text('{"name":"target"}', encoding="utf-8")
    imported = project_import.import_project(tmp_path, str(root))

    with pytest.raises(ValueError, match="readiness must be complete"):
        start_workflow(
            workspace=tmp_path,
            idea="Do not bypass readiness",
            project_key=imported["project"]["project_key"],
            model_config=Path("models.yaml"),
            model_profile="balanced",
            source_type="existing_project",
            project_config=Path(imported["project"]["config_path"]),
        )


def test_project_list_projects_attention_and_active_run_fields(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})

    status, payload = _get_web_json(tmp_path, "/api/projects")

    assert status == 200
    assert len(payload["projects"]) == 1
    assert payload["projects"][0]["readiness"]["lifecycle"] == "attention_required"
    assert payload["projects"][0]["attention_count"] == 1
    assert payload["projects"][0]["active_run"] is None


def test_project_list_keeps_legacy_readiness_read_only(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})
    readiness_path = tmp_path / "state" / "projects" / "LEGACY" / "readiness.json"
    readiness_path.unlink()

    status, payload = _get_web_json(tmp_path, "/api/projects")

    assert status == 200
    assert payload["projects"][0]["readiness"]["lifecycle"] == "legacy"
    assert payload["projects"][0]["attention_count"] == 0
    assert readiness_path.exists() is False


def test_unknown_project_home_uses_stable_error_shape(tmp_path: Path) -> None:
    status, payload = _get_web_json(tmp_path, "/api/projects/MISSING/home")

    assert status == 404
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "project_not_found"
    assert payload["details"] == {"project_key": "MISSING"}
    assert payload["recovery"] == ["return_to_projects"]
    assert payload["request_id"]


def test_invalid_project_mode_uses_stable_error_shape(tmp_path: Path) -> None:
    root = tmp_path / "ambiguous"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(tmp_path, "/api/projects/import", {"root": str(root)})

    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/AMBIGUOUS/mode",
        {"mode": "guess"},
    )
    assert status == 400
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "invalid_project_mode"
    assert payload["recovery"] == ["choose_project_mode"]
    assert payload["request_id"]


def test_project_import_hides_unexpected_exception_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_import(*args, **kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(web, "import_project", fail_import)
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(tmp_path)},
    )
    assert status == 500
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "internal_error"
    assert payload["message"] == "GoodTeam could not complete this request."
    assert payload["details"] == {}
    assert payload["recovery"] == ["retry", "report_request_id"]
    assert "sensitive" not in json.dumps(payload)
    assert payload["request_id"]


def test_product_spa_path_contract() -> None:
    assert web.is_product_spa_path("/") is True
    assert web.is_product_spa_path("/legacy") is True
    assert web.is_product_spa_path("/projects") is True
    assert web.is_product_spa_path("/inbox") is True
    assert web.is_product_spa_path("/activity") is True
    assert web.is_product_spa_path("/settings") is True
    assert web.is_product_spa_path("/projects/DEMO/home") is True
    assert web.is_product_spa_path("/projects/DEMO/work") is True
    assert web.is_product_spa_path("/projects/DEMO/work/runs/run-1") is True
    assert web.is_product_spa_path("/projects/DEMO/review") is True
    assert web.is_product_spa_path("/projects/DEMO/review/prd") is True
    assert web.is_product_spa_path("/projects/DEMO/knowledge") is True
    assert web.is_product_spa_path("/projects/DEMO/team") is True
    assert web.is_product_spa_path("/projects/DEMO/settings/general") is True
    assert web.is_product_spa_path("/api/projects") is False
    assert web.is_product_spa_path("/review-workbench/run-1") is False
    assert web.is_product_spa_path("/assets/index.js") is False


def test_project_run_history_and_detail_are_server_authoritative(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="RUNS")
    store = StateStore(tmp_path)

    archived = create_initial_state("run-archived", "RUNS", "Archived goal")
    archived.current_node = "prd_review_gate"
    archived.next_node = "await_prd_approval"
    task_ledger = project_root / ".goodteam" / "tasks" / "tasks.json"
    task_ledger.parent.mkdir(parents=True)
    task_ledger.write_text(
        json.dumps({"next_id": 2, "tasks": [{"id": "RUNS-1", "title": "Archived stage", "role": "product_requirement", "status": "done"}]}),
        encoding="utf-8",
    )
    archived.artifacts["task_ledger"] = str(task_ledger)
    store.write_project_current(archived)
    store.archive_project_current("RUNS", reason="cancelled")

    active = create_initial_state("run-active", "RUNS", "Active goal")
    active.current_node = "takeover_review_gate"
    active.next_node = "await_takeover_approval"
    store.write_project_current(active)
    task_ledger.write_text(
        json.dumps({"next_id": 2, "tasks": [{"id": "RUNS-1", "title": "Current mission stage", "role": "product_requirement", "status": "in_progress"}]}),
        encoding="utf-8",
    )

    status, history = web.handle_project_api_get(tmp_path, "/api/projects/RUNS/runs")
    assert status == 200
    assert [run["run_id"] for run in history["runs"]] == ["run-active", "run-archived"]
    assert [run["status"] for run in history["runs"]] == ["active", "archived"]

    detail_status, detail = web.handle_project_api_get(
        tmp_path,
        "/api/projects/RUNS/runs/run-archived",
    )
    assert detail_status == 200
    assert detail["run"]["reason"] == "cancelled"
    assert detail["state"]["idea"] == "Archived goal"
    assert detail["workflow_steps"]
    assert detail["team_workspace"]["mission"]["id"] == "run-archived"
    assert detail["team_workspace"]["mission"]["read_only"] is True
    assert detail["team_workspace"]["attention_request"] is None
    assert detail["team_workspace"]["capabilities"] == []
    assert [task["objective"] for task in detail["team_workspace"]["outcome_tasks"]] == [
        "Draft and approve PRD",
        "Design RFC & Security Review",
        "Implement approved design",
        "Verify implementation",
        "Approve and complete release",
    ]
    assert all(
        task["updated_at"] == detail["run"]["updated_at"]
        for task in detail["team_workspace"]["outcome_tasks"]
    )
    assert [actor["responsibility"] for actor in detail["team_workspace"]["execution_actors"]] == [
        "Draft and approve PRD",
        "Design RFC & Security Review",
        "Implement approved design",
        "Verify implementation",
        "Approve and complete release",
    ]
    assert all(
        task["objective"] != "Current mission stage"
        for task in detail["team_workspace"]["outcome_tasks"]
    )

    # A fresh request rebuilds the same semantic story from durable stores;
    # there is no process-local or browser conversation dependency.
    reloaded = web.build_project_run_detail(tmp_path, "RUNS", "run-archived")
    assert reloaded["team_workspace"] == detail["team_workspace"]

    with pytest.raises(web.ProjectApiError) as missing:
        web.handle_project_api_get(tmp_path, "/api/projects/RUNS/runs/run-missing")
    assert missing.value.code == "run_not_found"
    assert missing.value.status == 404


def test_project_state_exposes_versioned_team_workspace_projection(tmp_path: Path) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    config = _write_context_project(tmp_path, project_root, key="TEAM")
    state = create_initial_state("run-team", "TEAM", "Make OAuth production ready")
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = ApprovalDecision.PENDING
    ledger = project_root / ".goodteam" / "tasks" / "tasks.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"tasks": [], "next_id": 1}', encoding="utf-8")
    state.artifacts["task_ledger"] = str(ledger)
    web.sync_project_stage_tasks(state, config)
    StateStore(tmp_path).write_project_current(state)
    web.append_action(
        tmp_path,
        run_id=state.run_id,
        actor="human",
        action="workflow_started",
        project_key=state.project_key,
        after_node=state.next_node,
    )

    payload = web.build_project_work_state(tmp_path, "TEAM")

    workspace = payload["team_workspace"]
    assert workspace["schema_version"] == "team_workspace.v1"
    assert workspace["mission"]["objective"] == "Make OAuth production ready"
    assert workspace["attention_request"]["kind"] == "approval"
    assert workspace["attention_request"]["target_type"] == "review"
    assert workspace["attention_request"]["target_id"] == "takeover"
    assert workspace["attention_request"]["task_id"] == ""
    assert workspace["attention_request"]["approval_brief"]["schema_version"] == "manager_approval_brief.v1"
    assert workspace["attention_request"]["approval_brief"]["approval_type"] == "research_planning"
    assert workspace["attention_request"]["approval_brief"]["approval_target"] == {
        "run_id": "run-team",
        "target_type": "review",
        "target_id": "takeover",
        "task_id": "",
        "artifact_key": "takeover",
        "artifact_version": "missing",
    }
    assert workspace["attention_request"]["approval_brief"]["consequence"]["next_stage"] == "product_requirement"
    assert workspace["outcome_tasks"]
    assert payload["agile_board"]["counts"]["backlog"] == 5


def test_project_state_exposes_task_assistant_before_first_workflow(tmp_path: Path) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    (project_root / "package.json").write_text("{}", encoding="utf-8")
    _write_context_project(tmp_path, project_root, key="TEAM")

    payload = web.build_project_work_state(tmp_path, "TEAM")

    assert payload["task_assistant_session"]["project_mode_hint"]["mode"] == "existing_project"
    assert payload["team_workspace"]["mission"]["source"] == "task_assistant"
    assert payload["team_workspace"]["manager_timeline"][0]["event"] == "welcome_existing_project"


def test_task_assistant_message_api_uses_project_bound_service(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    config = _write_context_project(tmp_path, project_root, key="TEAM")
    calls = []

    class FakeService:
        def __init__(self, workspace, *, project_config):
            calls.append(("workspace", workspace, project_config))

        def send_message(self, **values):
            calls.append(("message", values))
            return {"intent": "project_understanding", "task_assistant_session": {}}

    monkeypatch.setattr(web, "TaskAssistantService", FakeService)

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/messages",
        {"message_id": "message-1", "text": "看一下当前项目"},
    )

    assert status == 200
    assert payload["intent"] == "project_understanding"
    assert calls[0] == ("workspace", tmp_path, config.resolve())
    assert calls[-1] == (
        "message",
        {
            "project_key": "TEAM",
            "root": project_root.resolve(),
            "message_id": "message-1",
            "text": "看一下当前项目",
        },
    )


def test_http_router_recognizes_task_assistant_message_path() -> None:
    assert web._is_project_api_post_path("/api/projects/DEMO/task-assistant/messages") is True


def test_task_assistant_confirm_action_starts_exact_proposal_once_ready(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    config = _write_context_project(tmp_path, project_root, key="TEAM")
    store = web.TaskAssistantSessionStore(tmp_path, "TEAM")
    store.append_message("message-1", "user", "修复登录问题")
    proposal = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]
    calls = []

    class ReadyExecutor:
        def __init__(self, workspace):
            pass

        def status(self, key, root):
            return {"lifecycle": "ready"}

    def fake_start_workflow(**values):
        calls.append(values)
        return {"run_id": values["run_id"]}

    monkeypatch.setattr(web, "ProjectReadinessExecutor", ReadyExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": True})
    monkeypatch.setattr(web, "start_workflow", fake_start_workflow)

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {"action": "confirm_task", "proposal_id": proposal["id"]},
    )

    assert status == 200
    assert payload["workflow_started"] is True
    assert payload["task_assistant_session"]["proposed_task"]["run_id"] == f"task-{proposal['id']}"
    assert calls[0]["idea"] == "修复登录问题"
    assert calls[0]["project_config"] == config.resolve()
    assert calls[0]["run_id"] == f"task-{proposal['id']}"

    repeated_status, repeated_payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {"action": "confirm_task", "proposal_id": proposal["id"]},
    )

    assert repeated_status == 200
    assert repeated_payload["run_id"] == f"task-{proposal['id']}"
    assert len(calls) == 1


def test_concurrent_task_confirmations_start_workflow_only_once(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    store = web.TaskAssistantSessionStore(tmp_path, "TEAM")
    store.append_message("message-1", "user", "修复登录问题")
    proposal_id = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]["id"]
    started = threading.Event()
    release = threading.Event()
    calls = []
    first_result = {}

    class ReadyExecutor:
        def __init__(self, workspace):
            pass

        def status(self, key, root):
            return {"lifecycle": "ready"}

    def fake_start_workflow(**values):
        calls.append(values)
        call_number = len(calls)
        if call_number == 1:
            started.set()
            assert release.wait(timeout=5)
        return {"run_id": values["run_id"]}

    def first_confirmation():
        try:
            first_result["value"] = web.handle_project_api_post(
                tmp_path,
                "/api/projects/TEAM/task-assistant/actions",
                {"action": "confirm_task", "proposal_id": proposal_id},
            )
        except BaseException as exc:
            first_result["error"] = exc

    monkeypatch.setattr(web, "ProjectReadinessExecutor", ReadyExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": True})
    monkeypatch.setattr(web, "start_workflow", fake_start_workflow)
    thread = threading.Thread(target=first_confirmation)
    thread.start()
    assert started.wait(timeout=5)

    second_status, second_payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {"action": "confirm_task", "proposal_id": proposal_id},
    )
    release.set()
    thread.join(timeout=5)

    assert "error" not in first_result
    assert len(calls) == 1
    assert second_status == 202
    assert second_payload["status"] == "workflow_starting"
    assert second_payload["task_assistant_session"]["status"] == "workflow_starting"


def test_task_confirmation_reconciles_persisted_start_after_interruption(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    store = web.TaskAssistantSessionStore(tmp_path, "TEAM")
    store.append_message("message-1", "user", "修复登录问题")
    proposal = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]
    store.confirm_task(proposal["id"])
    run_id = f"task-{proposal['id']}"
    store.claim_workflow_start(proposal["id"], "interrupted-owner", run_id)
    state = create_initial_state(run_id, "TEAM", "修复登录问题")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    class ReadyExecutor:
        def __init__(self, workspace):
            pass

        def status(self, key, root):
            return {"lifecycle": "ready"}

    monkeypatch.setattr(web, "ProjectReadinessExecutor", ReadyExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": True})
    monkeypatch.setattr(
        web,
        "start_workflow",
        lambda **values: pytest.fail("reconciliation must not start a second workflow"),
    )

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {"action": "confirm_task", "proposal_id": proposal["id"]},
    )

    assert status == 200
    assert payload["run_id"] == run_id
    assert payload["task_assistant_session"]["proposed_task"]["run_id"] == run_id


def test_task_confirmation_does_not_retry_an_indeterminate_start(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    store = web.TaskAssistantSessionStore(tmp_path, "TEAM")
    store.append_message("message-1", "user", "修复登录问题")
    proposal = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]
    calls = []

    class ReadyExecutor:
        def __init__(self, workspace):
            pass

        def status(self, key, root):
            return {"lifecycle": "ready"}

    def interrupted_start(**values):
        calls.append(values)
        raise RuntimeError("connection lost after dispatch")

    monkeypatch.setattr(web, "ProjectReadinessExecutor", ReadyExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": True})
    monkeypatch.setattr(web, "start_workflow", interrupted_start)

    with pytest.raises(RuntimeError, match="connection lost"):
        web.handle_project_api_post(
            tmp_path,
            "/api/projects/TEAM/task-assistant/actions",
            {"action": "confirm_task", "proposal_id": proposal["id"]},
        )

    with pytest.raises(web.ProjectApiError) as captured:
        web.handle_project_api_post(
            tmp_path,
            "/api/projects/TEAM/task-assistant/actions",
            {"action": "confirm_task", "proposal_id": proposal["id"]},
        )

    assert captured.value.code == "workflow_start_indeterminate"
    assert captured.value.status == 409
    assert len(calls) == 1
    session = store.read()
    assert session["status"] == "workflow_start_indeterminate"
    assert session["proposed_task"]["start_status"] == "indeterminate"


def test_task_assistant_confirm_action_keeps_preparing_without_exposing_readiness_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    store = web.TaskAssistantSessionStore(tmp_path, "TEAM")
    store.append_message("message-1", "user", "修复登录问题")
    proposal = store.propose_task(["message-1"], "修复登录问题")["proposed_task"]

    class PreparingExecutor:
        def __init__(self, workspace):
            pass

        def status(self, key, root):
            return {"lifecycle": "preparing"}

    monkeypatch.setattr(web, "ProjectReadinessExecutor", PreparingExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": False})

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {"action": "confirm_task", "proposal_id": proposal["id"]},
    )

    assert status == 202
    assert payload["workflow_started"] is False
    assert payload["status"] == "preparing_project"
    assert payload["task_assistant_session"]["proposed_task"]["confirmed"] is True


def test_http_router_recognizes_task_assistant_action_path() -> None:
    assert web._is_project_api_post_path("/api/projects/DEMO/task-assistant/actions") is True


def test_task_assistant_preparation_action_submits_chat_card_input(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    calls = []

    class InputExecutor:
        def __init__(self, workspace):
            pass

        def submit_input(self, key, root, step_id, values, *, background):
            calls.append((key, root, step_id, values, background))
            return {"lifecycle": "attention_required"}

        def status(self, key, root):
            return {"lifecycle": "attention_required"}

    monkeypatch.setattr(web, "ProjectReadinessExecutor", InputExecutor)
    monkeypatch.setattr(web, "build_capabilities", lambda state: {"start_workflow": False})

    status, payload = web.handle_project_api_post(
        tmp_path,
        "/api/projects/TEAM/task-assistant/actions",
        {
            "action": "continue_preparation",
            "step_id": "command_verification",
            "values": {"confirmed_command_ids": ["test"]},
        },
    )

    assert status == 202
    assert payload["status"] == "preparing_project"
    assert calls == [("TEAM", project_root.resolve(), "command_verification", {"confirmed_command_ids": ["test"]}, False)]


def test_project_state_restores_persisted_intent_commit_discussion(tmp_path: Path) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    artifact = tmp_path / "prd.md"
    artifact.write_text("# PRD\n\nShip Google sign-in first.\n", encoding="utf-8")
    state = create_initial_state("run-intent", "TEAM", "Add provider-based authentication")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    version = web.artifact_version_for_path(str(artifact))

    opened = web.start_request_changes_intent(
        workspace=tmp_path,
        project_key="TEAM",
        approval="prd",
        expected_run_id="run-intent",
        expected_target_type="review",
        expected_target_id="prd",
        expected_artifact_key="prd",
        expected_artifact_version=version,
    )["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path,
        project_key="TEAM",
        intent_id=opened["id"],
        text="Ship Google only and preserve a generic provider interface.",
    )

    workspace = web.build_project_work_state(tmp_path, "TEAM")["team_workspace"]

    intent = workspace["attention_request"]["intent_commit"]
    assert intent["id"] == opened["id"]
    assert intent["status"] == "ready_to_confirm"
    assert intent["interpretation"]["summary"] == (
        "Ship Google only and preserve a generic provider interface."
    )
    assert [
        item["event"]
        for item in workspace["manager_timeline"]
        if item.get("details", {}).get("intent_id") == opened["id"]
    ] == [
        "change_request_started",
        "change_discussion_started",
        "change_request_shared",
        "change_intent_summarized",
    ]


def test_project_state_attention_targets_synced_outcome_by_task_id(tmp_path: Path) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    config = _write_context_project(tmp_path, project_root, key="TEAM")
    state = create_initial_state("run-team-review", "TEAM", "Review the product requirements")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    ledger = project_root / ".goodteam" / "tasks" / "tasks.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"tasks": [], "next_id": 1}', encoding="utf-8")
    state.artifacts["task_ledger"] = str(ledger)
    web.sync_project_stage_tasks(state, config)
    StateStore(tmp_path).write_project_current(state)

    workspace = web.build_project_work_state(tmp_path, "TEAM")["team_workspace"]

    assert workspace["attention_request"]["target_type"] == "review"
    assert workspace["attention_request"]["target_id"] == "prd"
    assert workspace["attention_request"]["task_id"] == "TEAM-1"
    brief = workspace["attention_request"]["approval_brief"]
    assert brief["approval"] == "prd"
    assert brief["approval_type"] == "research_planning"
    assert brief["approval_target"] == {
        "run_id": "run-team-review",
        "target_type": "review",
        "target_id": "prd",
        "task_id": "TEAM-1",
        "artifact_key": "prd",
        "artifact_version": "missing",
    }
    assert brief["consequence"]["next_stage"] == "architect"
    assert brief["consequence"]["task_id"] == "TEAM-2"
    target = next(item for item in workspace["outcome_tasks"] if item["id"] == "TEAM-1")
    assert target["review_target_type"] == "review"
    assert target["review_target_id"] == "prd"
    assert [
        (actor["task_id"], actor["state"])
        for actor in workspace["execution_actors"]
    ] == [
        ("TEAM-1", "reviewing"),
        ("TEAM-2", "waiting"),
        ("TEAM-3", "waiting"),
        ("TEAM-4", "waiting"),
        ("TEAM-5", "waiting"),
    ]
    architect = next(actor for actor in workspace["execution_actors"] if actor["task_id"] == "TEAM-2")
    assert architect["dependency_task_id"] == "TEAM-1"
    assert architect["dependency_task_title"] == "Draft and approve PRD"
    assert workspace["execution_actors"][0]["status_reason"] == "awaiting_your_approval"


def test_project_state_prefers_active_operator_queue_over_historical_workflow_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="TEAM")
    historical = create_initial_state("run-historical", "TEAM", "Old workflow mission")
    historical.current_node = "end"
    historical.next_node = ""
    StateStore(tmp_path).write_project_current(historical)

    state_dir = tmp_path / "service-state"
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(state_dir))
    queue = DurableOperatorQueue(state_dir)
    item, _ = queue.enqueue(
        idempotency_key="TEAM:issue:375",
        source_url="https://github.com/LukeJiaoR/GoodTeam/issues/375",
        source_revision="revision-375",
        project_key="TEAM",
        repository_identity="LukeJiaoR/GoodTeam",
        metadata={
            "issue_number": 375,
            "issue_title": "Current Operator mission",
            "task_contract": {"summary": "Keep Operator visible through review."},
        },
    )
    queue.claim_next()
    queue.transition(
        item["id"],
        phase="review_ready",
        expected_phase="hydrating",
        updates={"execution_id": "exec_operator375"},
    )

    payload = web.build_project_work_state(tmp_path, "TEAM")

    assert payload["active_run"] is False
    assert payload["state"]["run_id"] == "run-historical"
    assert payload["team_workspace"]["mission"]["id"] == item["id"]
    assert payload["team_workspace"]["mission"]["source"] == "operator_queue"
    assert payload["team_workspace"]["mission"]["phase"] == "review_ready"


def test_project_run_detail_rejects_encoded_path_segments(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="RUNS")

    with pytest.raises(web.ProjectApiError) as error:
        web.handle_project_api_get(tmp_path, "/api/projects/RUNS/runs/run%2Fother")

    assert error.value.code == "invalid_run_id"
    assert error.value.status == 400


def test_workspace_settings_persist_and_apply_to_future_project_imports(tmp_path):
    status, defaults = web.handle_project_api_get(tmp_path, "/api/settings")
    assert status == 200
    assert defaults["defaults"] == {
        "worker_backend": "codex",
        "model_profile": "balanced",
    }

    save_status, saved = web.handle_project_api_post(
        tmp_path,
        "/api/settings",
        {"worker_backend": "fake", "model_profile": "balanced"},
    )
    assert save_status == 200
    assert saved["defaults"]["worker_backend"] == "fake"
    settings_path = tmp_path / ".goodteam" / "product-settings.json"
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "model_profile": "balanced",
        "worker_backend": "fake",
    }

    project_root = tmp_path / "future-project"
    project_root.mkdir()
    import_status, imported = web.handle_project_api_post(
        tmp_path,
        "/api/projects/import",
        {"root": str(project_root)},
    )
    assert import_status == 201
    config = Path(imported["project"]["config_path"])
    target = web.load_project_target(config)
    assert target["worker_backend"]["mode"] == "fake"
    assert target["model_profile"] == "balanced"


def test_workspace_settings_reject_unknown_defaults(tmp_path):
    with pytest.raises(web.ProjectApiError) as worker_error:
        web.handle_project_api_post(
            tmp_path,
            "/api/settings",
            {"worker_backend": "unknown", "model_profile": "balanced"},
        )
    assert worker_error.value.code == "invalid_worker_backend"
    assert worker_error.value.status == 400

    with pytest.raises(web.ProjectApiError) as model_error:
        web.handle_project_api_post(
            tmp_path,
            "/api/settings",
            {"worker_backend": "codex", "model_profile": "unknown"},
        )
    assert model_error.value.code == "invalid_model_profile"
    assert model_error.value.status == 400


def test_focused_and_full_handlers_share_project_api_dispatch() -> None:
    focused_source = inspect.getsource(web.build_dashboard_handler)
    full_source = inspect.getsource(web.serve)

    for helper_name in ("handle_project_api_get", "handle_project_api_post"):
        assert helper_name in focused_source
        assert helper_name in full_source


@pytest.mark.parametrize(
    "encoded_key",
    [
        "%",
        "%ZZ",
        "%E4",
        "DEMO/OTHER",
        "DEMO%2FOTHER",
        "DEMO%2fOTHER",
        "%00",
        "---",
    ],
)
def test_project_home_rejects_invalid_encoded_project_key(
    tmp_path: Path,
    encoded_key: str,
) -> None:
    status, payload = _get_web_json(
        tmp_path,
        f"/api/projects/{encoded_key}/home",
    )

    assert status == 400
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "invalid_project_key"


def test_project_mode_rejects_encoded_multi_segment_key(tmp_path: Path) -> None:
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/DEMO%2fOTHER/mode",
        {"mode": "existing_project"},
    )

    assert status == 400
    assert payload["code"] == "invalid_project_key"


def test_unicode_project_key_home_and_mode_http_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "unicode"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    imported_status, imported = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(root), "project_key": "项目", "display_name": "项目"},
    )
    encoded_key = urllib.parse.quote("项目", safe="")

    home_status, home = _get_web_json(
        tmp_path,
        f"/api/projects/{encoded_key}/home",
    )
    mode_status, confirmed = _post_web_json(
        tmp_path,
        f"/api/projects/{encoded_key}/mode",
        {"mode": "existing_project"},
    )

    assert imported_status == 201
    assert imported["project"]["project_key"] == "项目"
    assert home_status == 200
    assert home["project"]["project_key"] == "项目"
    assert mode_status == 200
    assert confirmed["readiness"]["project_mode"] == "existing_project"


@pytest.mark.parametrize("payload", [b"{", b"[]", b'"value"', b"null"])
def test_project_post_rejects_malformed_or_non_object_json(
    tmp_path: Path,
    payload: bytes,
) -> None:
    status, response = _post_web_raw(
        tmp_path,
        "/api/projects/import",
        payload,
    )

    assert status == 400
    assert set(response) == {"code", "message", "details", "recovery", "request_id"}
    assert response["code"] == "invalid_request"
    assert payload.decode("utf-8", errors="ignore") not in response["message"]


def test_project_import_workspace_isolation_has_explicit_http_recovery(
    tmp_path: Path,
) -> None:
    status, payload = _post_web_json(
        tmp_path,
        "/api/projects/import",
        {"root": str(tmp_path)},
    )

    assert status == 400
    assert payload["code"] == "project_workspace_not_isolated"
    assert "choose_another_directory" in payload["recovery"]


def test_project_mode_static_symlink_fails_closed_without_target_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(workspace, "/api/projects/import", {"root": str(root)})
    key_dir = workspace / "state" / "projects" / "PROJECT"
    external_payload = json.loads((key_dir / "readiness.json").read_text(encoding="utf-8"))
    external_payload["owner"] = "external"
    external_content = json.dumps(external_payload, sort_keys=True) + "\n"
    external_readiness = redirected / "readiness.json"
    external_readiness.write_text(external_content, encoding="utf-8")
    before = {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
    key_dir.rename(workspace / "state" / "projects" / "PROJECT-detached")
    key_dir.symlink_to(redirected, target_is_directory=True)

    status, payload = _post_web_json(
        workspace,
        "/api/projects/PROJECT/mode",
        {"mode": "existing_project"},
    )

    after = {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
    assert status == 400
    assert payload["code"] == "project_workspace_not_isolated"
    assert external_readiness.read_text(encoding="utf-8") == external_content
    assert after == before


def test_project_mode_validation_swap_has_stable_http_isolation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    redirected = root / "redirected"
    redirected.mkdir(parents=True)
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(workspace, "/api/projects/import", {"root": str(root)})
    source_readiness = workspace / "state" / "projects" / "PROJECT" / "readiness.json"
    external_payload = json.loads(source_readiness.read_text(encoding="utf-8"))
    external_payload["owner"] = "external"
    external_content = json.dumps(external_payload, sort_keys=True) + "\n"
    external_readiness = redirected / "readiness.json"
    external_readiness.write_text(external_content, encoding="utf-8")
    before = {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
    original_validate = project_import._validate_project_state_destination
    swapped = False

    def validate_then_swap(path: Path, target_root: Path, project_key: str):
        nonlocal swapped
        original_validate(path, target_root, project_key)
        if not swapped:
            swapped = True
            state_dir = workspace / "state"
            state_dir.rename(workspace / "state-detached")
            state_dir.symlink_to(redirected, target_is_directory=True)

    monkeypatch.setattr(
        project_import,
        "_validate_project_state_destination",
        validate_then_swap,
    )

    status, payload = _post_web_json(
        workspace,
        "/api/projects/PROJECT/mode",
        {"mode": "existing_project"},
    )

    after = {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }
    assert status == 400
    assert payload["code"] == "project_workspace_not_isolated"
    assert "choose_another_directory" in payload["recovery"]
    assert external_readiness.read_text(encoding="utf-8") == external_content
    assert after == before


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_key",
        "wrong_root",
        "missing_key",
        "missing_root",
        "nonstr_key",
        "nonstr_root",
    ],
)
def test_project_mode_readiness_mismatch_has_stable_http_contract(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(workspace, "/api/projects/import", {"root": str(root)})
    readiness_path = workspace / "state" / "projects" / "PROJECT" / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if mutation == "wrong_key":
        readiness["project_key"] = "OTHER"
    elif mutation == "wrong_root":
        readiness["canonical_root"] = str(tmp_path / "sensitive-other-root")
    elif mutation == "missing_key":
        readiness.pop("project_key")
    elif mutation == "missing_root":
        readiness.pop("canonical_root")
    elif mutation == "nonstr_key":
        readiness["project_key"] = ["PROJECT"]
    else:
        readiness["canonical_root"] = {"path": str(root)}
    readiness_path.write_text(json.dumps(readiness, indent=1) + "\n", encoding="utf-8")
    before = readiness_path.read_bytes()

    status, payload = _post_web_json(
        workspace,
        "/api/projects/PROJECT/mode",
        {"mode": "existing_project"},
    )

    assert status == 409
    assert set(payload) == {"code", "message", "details", "recovery", "request_id"}
    assert payload["code"] == "project_readiness_mismatch"
    assert payload["details"] == {"project_key": "PROJECT"}
    assert payload["recovery"] == ["reload_project", "return_to_projects"]
    assert "sensitive-other-root" not in json.dumps(payload)
    assert readiness_path.read_bytes() == before
    assert [path.name for path in readiness_path.parent.iterdir()] == ["readiness.json"]


def test_project_mode_anchored_verify_failure_restores_old_http_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"asset")
    _post_web_json(workspace, "/api/projects/import", {"root": str(root)})
    readiness_path = workspace / "state" / "projects" / "PROJECT" / "readiness.json"
    before = readiness_path.read_bytes()

    def fail_published_verify(_store, _project_key):
        raise OSError(errno.ENOTDIR, "injected directory identity failure")

    monkeypatch.setattr(
        web.ProjectReadinessStore,
        "_verify_anchored_write",
        fail_published_verify,
    )

    status, payload = _post_web_json(
        workspace,
        "/api/projects/PROJECT/mode",
        {"mode": "existing_project"},
    )

    assert status == 400
    assert payload["code"] == "project_workspace_not_isolated"
    assert readiness_path.read_bytes() == before
    assert [path.name for path in readiness_path.parent.iterdir()] == ["readiness.json"]


def test_full_handler_project_routes_use_shared_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "project"
    root.mkdir()
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    created_servers: queue.Queue = queue.Queue()
    server_type = web.ThreadingHTTPServer

    class CapturingServer(server_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_servers.put(self)

    monkeypatch.setattr(web, "ThreadingHTTPServer", CapturingServer)
    thread = threading.Thread(
        target=web.serve,
        kwargs={
            "workspace": workspace,
            "host": "127.0.0.1",
            "port": 0,
            "frontend_dist": frontend_dist,
        },
        daemon=True,
    )
    thread.start()
    server = created_servers.get(timeout=5)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = urllib.request.Request(
            f"{base_url}/api/projects/import",
            data=json.dumps({"root": str(root)}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": base_url},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            imported_status = response.status
            imported = json.loads(response.read().decode("utf-8"))

        malformed = urllib.request.Request(
            f"{base_url}/api/projects/import",
            data=b"{",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": base_url},
        )
        with pytest.raises(urllib.error.HTTPError) as malformed_error:
            urllib.request.urlopen(malformed, timeout=5)
        with malformed_error.value:
            malformed_payload = json.loads(malformed_error.value.read().decode("utf-8"))

        non_object = urllib.request.Request(
            f"{base_url}/api/projects/import",
            data=b"[]",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": base_url},
        )
        with pytest.raises(urllib.error.HTTPError) as non_object_error:
            urllib.request.urlopen(non_object, timeout=5)
        with non_object_error.value:
            non_object_payload = json.loads(non_object_error.value.read().decode("utf-8"))

        cross_origin = urllib.request.Request(
            f"{base_url}/api/projects/PROJECT/mode",
            data=json.dumps({"mode": "existing_project"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://attacker.example"},
        )
        with pytest.raises(urllib.error.HTTPError) as cross_origin_error:
            urllib.request.urlopen(cross_origin, timeout=5)
        with cross_origin_error.value:
            cross_origin_payload = json.loads(cross_origin_error.value.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert imported_status == 201
    assert imported["created"] is True
    assert malformed_error.value.code == 400
    assert set(malformed_payload) == {
        "code",
        "message",
        "details",
        "recovery",
        "request_id",
    }
    assert malformed_payload["code"] == "invalid_request"
    assert non_object_error.value.code == 400
    assert set(non_object_payload) == {
        "code",
        "message",
        "details",
        "recovery",
        "request_id",
    }
    assert non_object_payload["code"] == "invalid_request"
    assert cross_origin_error.value.code == 403
    assert cross_origin_payload["code"] == "same_origin_required"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_build_dev_loop_without_project_returns_empty_note():
    dev_loop = build_dev_loop(None)

    assert dev_loop["worktrees"] == []
    assert dev_loop["pull_requests"] == []
    assert dev_loop["pre_reviews"] == []
    assert dev_loop["is_git_repo"] is False
    assert dev_loop["warnings"] == ["no project selected"]


def test_request_origin_allows_local_vite_proxy_origin():
    assert _request_origin_is_same_origin(
        origin="http://127.0.0.1:5173",
        host="127.0.0.1:8788",
    ) is True
    assert _request_origin_is_same_origin(
        origin="http://localhost:5173",
        host="127.0.0.1:8788",
    ) is True


def test_request_origin_rejects_non_local_cross_origin():
    assert _request_origin_is_same_origin(
        origin="https://evil.example",
        host="127.0.0.1:8788",
    ) is False


def test_build_dev_loop_non_git_directory_reports_not_git_repo(tmp_path):
    parent_repo = tmp_path / "parent"
    project_root = parent_repo / "scaffold"
    project_root.mkdir(parents=True)
    _init_git_repo(parent_repo)

    dev_loop = build_dev_loop(project_root)

    assert dev_loop["worktrees"] == []
    assert dev_loop["pull_requests"] == []
    assert dev_loop["pre_reviews"] == []
    assert dev_loop["is_git_repo"] is False
    assert dev_loop["warnings"] == ["project repo has no git history yet"]


def test_build_dev_loop_git_directory_reports_git_repo(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["is_git_repo"] is True
    assert dev_loop["warnings"] == []


def test_build_dev_loop_parses_git_worktrees(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == web.DEV_LOOP_SUBPROCESS_TIMEOUT_SECONDS
        assert kwargs["check"] is False
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"""worktree {tmp_path}
HEAD abc1234567890
branch refs/heads/main

""",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["is_git_repo"] is True
    assert dev_loop["worktrees"] == [{"path": str(tmp_path), "branch": "main", "head": "abc1234"}]
    assert dev_loop["pull_requests"] == []
    assert dev_loop["warnings"] == []


def test_build_dev_loop_gh_nonzero_degrades_to_warning(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="not authenticated")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["pull_requests"] == []
    assert "gh pr list failed" in dev_loop["warnings"]


def test_build_dev_loop_pre_reviews_reflect_report_presence(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "pre-review-57-report.md").write_text("ok", encoding="utf-8")
    (report_dir / "pre-review-59-report.md").write_text("failed ci", encoding="utf-8")
    rationale_path = tmp_path / ".goodteam" / "provenance" / "pr-57" / "review-rationale.md"
    rationale_path.parent.mkdir(parents=True)
    rationale_path.write_text("Approved because the regression slice covers the change.\n", encoding="utf-8")
    monkeypatch.setattr(web, "PRE_REVIEW_REPORT_DIR", report_dir)
    git_output = """worktree /repo/ready
HEAD abc1234567890
branch refs/heads/feat/ready

worktree /repo/failing
HEAD def1234567890
branch refs/heads/feat/failing

"""
    gh_output = json.dumps(
        [
            {
                "number": 57,
                "title": "Ready",
                "headRefName": "feat/ready",
                "url": "https://github.com/org/repo/pull/57",
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
            },
            {
                "number": 58,
                "title": "Waiting",
                "headRefName": "feat/waiting",
                "url": "https://github.com/org/repo/pull/58",
                "statusCheckRollup": [{"status": "IN_PROGRESS"}],
            },
            {
                "number": 59,
                "title": "Failing",
                "headRefName": "feat/failing",
                "url": "https://github.com/org/repo/pull/59",
                "statusCheckRollup": [{"conclusion": "FAILURE", "status": "COMPLETED"}],
            },
        ]
    )

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout=git_output, stderr="")
        if command[:3] == ["git", "-C", "/repo/ready"] and command[-2:] == ["status", "--short"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["git", "-C", "/repo/failing"] and command[-2:] == ["status", "--short"]:
            return SimpleNamespace(returncode=0, stdout=" M src/goodteam_os/web.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=gh_output, stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["pull_requests"] == [
        {
            "number": 57,
            "title": "Ready",
            "head_branch": "feat/ready",
            "ci": "pass",
            "url": "https://github.com/org/repo/pull/57",
            "land_ready": True,
            "land_command": f"scripts/land-pr.sh 57 --worktree /repo/ready --review-rationale {rationale_path}",
        },
        {
            "number": 58,
            "title": "Waiting",
            "head_branch": "feat/waiting",
            "ci": "pending",
            "url": "https://github.com/org/repo/pull/58",
            "land_ready": False,
            "land_command": "scripts/land-pr.sh 58",
        },
        {
            "number": 59,
            "title": "Failing",
            "head_branch": "feat/failing",
            "ci": "fail",
            "url": "https://github.com/org/repo/pull/59",
            "land_ready": False,
            "land_command": "scripts/land-pr.sh 59 --worktree /repo/failing",
        },
    ]
    assert dev_loop["pre_reviews"] == [
        {"pr": 57, "present": True, "report_path": str(report_dir / "pre-review-57-report.md")},
        {"pr": 58, "present": False, "report_path": str(report_dir / "pre-review-58-report.md")},
        {"pr": 59, "present": True, "report_path": str(report_dir / "pre-review-59-report.md")},
    ]
    assert dev_loop["land_tag_readiness"]["server_execution"] == "disabled_by_default"
    assert dev_loop["land_tag_readiness"]["pull_requests"] == [
        {
            "pr": 57,
            "ready": True,
            "missing": [],
            "command": f"scripts/land-pr.sh 57 --worktree /repo/ready --review-rationale {rationale_path}",
            "ci": "pass",
            "pre_review": True,
            "head_branch": "feat/ready",
            "worktree": "/repo/ready",
            "worktree_status": "clean",
            "review_rationale": True,
            "review_rationale_path": str(rationale_path),
        },
        {
            "pr": 58,
            "ready": False,
            "missing": ["ci_not_green", "missing_pre_review", "missing_worktree", "missing_rationale"],
            "command": "scripts/land-pr.sh 58",
            "ci": "pending",
            "pre_review": False,
            "head_branch": "feat/waiting",
            "worktree": "",
            "worktree_status": "missing",
            "review_rationale": False,
            "review_rationale_path": str(tmp_path / ".goodteam" / "provenance" / "pr-58" / "review-rationale.md"),
        },
        {
            "pr": 59,
            "ready": False,
            "missing": ["ci_not_green", "dirty_worktree", "missing_rationale"],
            "command": "scripts/land-pr.sh 59 --worktree /repo/failing",
            "ci": "fail",
            "pre_review": True,
            "head_branch": "feat/failing",
            "worktree": "/repo/failing",
            "worktree_status": "dirty",
            "review_rationale": False,
            "review_rationale_path": str(tmp_path / ".goodteam" / "provenance" / "pr-59" / "review-rationale.md"),
        },
    ]
    assert dev_loop["server_actions"]["actions"]["land_pr"]["readiness"] == dev_loop["land_tag_readiness"]
    assert dev_loop["server_actions"]["actions"]["land_pr"]["available"] is False
    assert dev_loop["server_actions"]["actions"]["tag_release"]["available"] is False


def test_build_dev_loop_reports_release_graduation_readiness_without_mutating(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "graduate-release.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "release-up.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    release_dir = tmp_path.parent / "goodteam-release"

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is False
        command_text = " ".join(command)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if command_text.endswith("rev-parse HEAD"):
            if str(release_dir) in command:
                return SimpleNamespace(returncode=0, stdout="releasehead987654321\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="devhead123456789\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    original_is_dir = Path.is_dir
    monkeypatch.setenv("GOODTEAM_RELEASE_DIR", str(release_dir))
    monkeypatch.setattr(web.subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "is_dir", lambda self: True if self == release_dir else original_is_dir(self))

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["release_readiness"] == {
        "available": True,
        "release_dir": str(release_dir),
        "release_exists": True,
        "dev_head": "devhead123456789",
        "release_head": "releasehead987654321",
        "up_to_date": False,
        "release_up_command": f"GOODTEAM_RELEASE_DIR={release_dir} scripts/release-up.sh --ref devhead123456789",
        "graduate_command": f"GOODTEAM_RELEASE_DIR={release_dir} scripts/graduate-release.sh --ref devhead123456789",
    }
    assert dev_loop["server_actions"]["actions"]["graduate_release"] == {
        "available": False,
        "mode": "human_command_only",
        "reason": "disabled_by_default",
        "command": f"GOODTEAM_RELEASE_DIR={release_dir} scripts/graduate-release.sh --ref devhead123456789",
    }
    assert dev_loop["server_actions"]["actions"]["land_pr"]["reason"] == "disabled_by_default"
    assert dev_loop["server_actions"]["actions"]["tag_release"]["available"] is False


def test_build_server_actions_marks_destructive_actions_available_only_with_env_gate(tmp_path, monkeypatch):
    disabled = web.build_server_action_capabilities(
        tmp_path,
        release_readiness={"graduate_command": "scripts/graduate-release.sh --ref abc1234"},
        land_tag_readiness={"server_execution": "disabled_by_default", "pull_requests": []},
    )

    assert disabled["execution_policy"] == {
        "status": "disabled",
        "destructive_actions_available": False,
        "env_gate_configured": False,
        "nonce_configured": False,
        "same_origin_required": True,
        "typed_confirmation_required": True,
        "argv_allowlist_required": True,
        "audit_required": True,
        "audit_dir": str(tmp_path / ".goodteam" / "server-actions"),
        "timeout_seconds": web.SERVER_ACTION_TIMEOUT_SECONDS,
        "enabled_actions": [],
        "blocked_actions": {
            "land_pr": ["disabled_by_default", "missing_nonce"],
            "graduate_release": ["disabled_by_default", "missing_nonce"],
            "tag_release": ["tag_release_safety_design_required"],
        },
    }
    assert disabled["actions"]["land_pr"]["available"] is False
    assert disabled["actions"]["land_pr"]["mode"] == "human_command_only"
    assert disabled["actions"]["graduate_release"]["available"] is False

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")

    enabled = web.build_server_action_capabilities(
        tmp_path,
        release_readiness={"graduate_command": "scripts/graduate-release.sh --ref abc1234"},
        land_tag_readiness={"server_execution": "disabled_by_default", "pull_requests": []},
    )

    assert enabled["execution_policy"] == {
        "status": "guarded_execute",
        "destructive_actions_available": True,
        "env_gate_configured": True,
        "nonce_configured": True,
        "same_origin_required": True,
        "typed_confirmation_required": True,
        "argv_allowlist_required": True,
        "audit_required": True,
        "audit_dir": str(tmp_path / ".goodteam" / "server-actions"),
        "timeout_seconds": web.SERVER_ACTION_TIMEOUT_SECONDS,
        "enabled_actions": ["land_pr", "graduate_release"],
        "blocked_actions": {
            "tag_release": ["tag_release_safety_design_required"],
        },
    }
    assert enabled["actions"]["land_pr"]["available"] is True
    assert enabled["actions"]["land_pr"]["mode"] == "guarded_execute"
    assert enabled["actions"]["land_pr"]["reason"] == ""
    assert enabled["actions"]["tag_release"]["available"] is False
    assert enabled["actions"]["tag_release"]["mode"] == "report_only"
    assert enabled["actions"]["tag_release"]["reason"] == "tag_release_safety_design_required"
    assert enabled["actions"]["tag_release"]["readiness"]["server_execution"] == "report_only"
    assert enabled["actions"]["tag_release"]["readiness"]["blocked_by"] == [
        "tag_name_policy",
        "signed_tag_policy",
        "remote_push_policy",
        "rollback_policy",
        "human_release_decision",
    ]
    assert enabled["actions"]["tag_release"]["readiness"]["future_enablement_requires"] == [
        "GOODTEAM_SERVER_ACTIONS=1",
        "GOODTEAM_SERVER_ACTION_NONCE",
        "typed confirmation: TAG RELEASE <tag>",
        "argv allowlist with shell=False",
        "audit record before any remote push",
        "human-reviewed release notes and rollback plan",
    ]
    assert enabled["actions"]["graduate_release"]["available"] is True
    assert enabled["actions"]["graduate_release"]["mode"] == "guarded_execute"


def test_build_dev_loop_hides_release_graduation_commands_for_non_goodteam_repo(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if command[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="devhead123456789\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    dev_loop = build_dev_loop(tmp_path)

    assert dev_loop["release_readiness"] == {
        "available": False,
        "reason": "release scripts unavailable",
    }


def test_build_workflow_graph_static_nodes_and_edges(tmp_path):
    graph = build_workflow_graph(tmp_path)

    assert graph["project_key"] == ""
    assert graph["run"] is None
    assert {
        "name": "architect",
        "gate": "prd",
        "runner": "run_architecture_until_gate",
        "kind": "stage",
    } in graph["nodes"]
    assert {
        "name": "security",
        "gate": "security",
        "runner": "noop_security_gate",
        "kind": "stage",
    } in graph["nodes"]
    assert {"name": "prd", "kind": "gate"} in graph["nodes"]
    assert {"name": "rejected", "kind": "terminal"} in graph["nodes"]
    assert {"name": "revise_prd", "kind": "revise"} in graph["nodes"]
    assert {"name": "product_requirement", "kind": "role"} in graph["nodes"]
    assert {"from": "product_requirement", "to": "architect", "type": "dependency"} in graph["edges"]
    assert {"from": "prd", "to": "architect", "type": "dependency"} in graph["edges"]
    assert {"from": "architect", "to": "planner", "type": "dependency"} in graph["edges"]
    assert {"from": "security", "to": "planner", "type": "dependency"} in graph["edges"]
    assert {"from": "prd", "to": "architect", "type": "approved"} in graph["edges"]
    assert {"from": "prd", "to": "revise_prd", "type": "changes_requested"} in graph["edges"]
    assert {"from": "prd", "to": "rejected", "type": "rejected"} in graph["edges"]
    assert graph["execution_plan"] == {
        "scheduler": "sequential",
        "parallel_level_execution": "deferred",
        "levels": [
            {"index": 0, "identifiers": ["product_requirement", "prd"], "runnable": [], "passive": ["product_requirement", "prd"]},
            {"index": 1, "identifiers": ["architect", "rfc"], "runnable": ["architect"], "passive": ["rfc"]},
            {"index": 2, "identifiers": ["security"], "runnable": ["security"], "passive": []},
            {"index": 3, "identifiers": ["planner", "staging"], "runnable": ["planner"], "passive": ["staging"]},
            {"index": 4, "identifiers": ["release"], "runnable": ["release"], "passive": []},
            {"index": 5, "identifiers": ["retro_memory"], "runnable": ["retro_memory"], "passive": []},
        ],
    }
    assert graph["workflow_execution_plan_v2"]["version"] == 2
    assert graph["workflow_execution_plan_v2"]["valid"] is True
    assert graph["workflow_execution_plan_v2"]["validation_errors"] == []
    assert graph["workflow_execution_plan_v2"]["parallel_execution_plan"] == {
        "version": 1,
        "valid": False,
        "scheduler": "parallel_levels",
        "runner_kind": "read_only_fake_projection",
        "parallel_level_execution": "blocked",
        "parallel_readiness": {
            "status": "blocked_for_real_workers",
            "safe_runner_kinds": ["fake_worker", "read_only_advisor"],
            "blocked_runner_kinds": ["human_gate", "sequential_worker"],
            "requires_worker_isolation": True,
            "requires_live_smoke": True,
            "worker_isolation": {
                "status": "required_before_real_parallel_workers",
                "required_strategy": "worktree_per_worker",
                "allowed_strategies": ["worktree_per_worker"],
                "rejected_strategies": ["shared_worktree", "single_runtime_root"],
                "evidence_required": [
                    "per-worker worktree path outside repository root",
                    "declared write domains mapped to one worker each",
                    "fan-in merge or review step after all workers finish",
                    "live smoke run using fake/read-only workers before real workers",
                ],
            },
            "blockers": [
                "Parallel execution refuses runner 'architect' with kind 'sequential_worker'",
                "Parallel execution refuses runner 'planner' with kind 'sequential_worker'",
                "Parallel execution refuses runner 'release' with kind 'sequential_worker'",
                "Parallel execution refuses runner 'retro_memory' with kind 'sequential_worker'",
                "Parallel execution refuses runner 'security' with kind 'human_gate'",
            ],
        },
        "validation_errors": [
            "Parallel execution refuses runner 'architect' with kind 'sequential_worker'",
            "Parallel execution refuses runner 'planner' with kind 'sequential_worker'",
            "Parallel execution refuses runner 'release' with kind 'sequential_worker'",
            "Parallel execution refuses runner 'retro_memory' with kind 'sequential_worker'",
            "Parallel execution refuses runner 'security' with kind 'human_gate'",
        ],
        "batches": [],
    }
    assert {
        "id": "planner",
        "kind": "stage",
        "dependencies": ["architect", "rfc", "security"],
        "write_domains": ["workflow:planner"],
        "runner": "run_delivery_until_release_gate",
        "runner_kind": "sequential_worker",
        "gate": "rfc",
        "fan_in_required": True,
    } in graph["workflow_execution_plan_v2"]["nodes"]
    node_names = {node["name"] for node in graph["nodes"]}
    edge_endpoints = {edge["from"] for edge in graph["edges"]} | {edge["to"] for edge in graph["edges"]}
    assert edge_endpoints <= node_names
    assert {node["kind"] for node in graph["nodes"]} <= {"stage", "gate", "terminal", "revise", "role"}


def test_dashboard_stepper_order_follows_workflow_table_happy_path():
    assert web._dashboard_stepper_table_order() == [
        "takeover",
        "product_requirement",
        "prd",
        "architect",
        "rfc",
        "security",
        "planner",
        "staging",
        "release",
        "retro_memory",
    ]

    steps = web._dashboard_stepper_steps()

    assert [step["label"] for step in steps] == [
        "Intake",
        "Research",
        "PRD",
        "RFC",
        "Arch Rev",
        "Security",
        "Planning",
        "UI Design",
        "Impl",
        "QA",
        "Code Review",
        "Staging",
        "Release",
        "Retro",
    ]
    assert web._dashboard_stepper_node_sequence(steps) == {
        "takeover_review_gate": 1,
        "await_takeover_approval": 1,
        "research": 2,
        "product_requirement": 3,
        "await_prd_approval": 3,
        "architect": 4,
        "rfc_review_gate": 5,
        "await_rfc_approval": 5,
        "await_security_approval": 6,
        "security_review_gate": 6,
        "planner": 7,
        "ui": 8,
        "implementation": 9,
        "qa": 10,
        "code_review": 11,
        "staging": 12,
        "await_staging_approval": 12,
        "release": 13,
        "await_release_approval": 13,
        "retro_memory": 14,
        "complete": 15,
    }


def test_build_workflow_graph_includes_active_project_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.approvals["prd"] = ApprovalDecision.APPROVED
    StateStore(tmp_path).write_project_current(state)

    graph = build_workflow_graph(tmp_path, project_key="contentops")

    assert graph["project_key"] == "CONTENTOPS"
    assert graph["run"] == {
        "run_id": "run-1",
        "current_node": "prd_review_gate",
        "next_node": "await_prd_approval",
        "current_graph_node": "prd",
        "next_graph_node": "prd",
        "approvals": {
            "prd": "approved",
            "rfc": "pending",
            "staging": "pending",
            "security": "pending",
            "release": "pending",
        },
    }

    state.current_node = "architect"
    state.next_node = "planner"
    StateStore(tmp_path).write_project_current(state)

    graph = build_workflow_graph(tmp_path, project_key="contentops")

    assert graph["run"]["current_graph_node"] == "architect"
    assert graph["run"]["next_graph_node"] == "planner"


def test_build_workflow_graph_emits_forward_compat_error_and_blocked_edges(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web,
        "GATE_TRANSITIONS",
        {
            "qa": SimpleNamespace(
                gate="qa",
                on_approved_next="release",
                on_changes_requested="revise_qa",
                on_rejected="rejected",
            )
        },
    )
    monkeypatch.setattr(
        web,
        "WORKFLOW_NODES",
        {
            "qa_runner": SimpleNamespace(
                name="qa_runner",
                run_guard_gate="qa",
                runner_fn="run_qa_until_gate",
                on_error="qa_error",
                on_blocked="qa_blocked",
            )
        },
    )

    graph = build_workflow_graph(tmp_path)

    assert {"from": "qa_runner", "to": "qa_error", "type": "error"} in graph["edges"]
    assert {"from": "qa_runner", "to": "qa_blocked", "type": "blocked"} in graph["edges"]
    assert graph["execution_plan"] == {
        "scheduler": "unavailable",
        "parallel_level_execution": "deferred",
        "levels": [],
        "error": "Workflow node 'qa_runner' has unknown error target 'qa_error'",
    }
    assert graph["workflow_execution_plan_v2"]["valid"] is False
    assert "Workflow node 'qa_runner' has unknown error target 'qa_error'" in graph["workflow_execution_plan_v2"]["validation_errors"]
    node_names = {node["name"] for node in graph["nodes"]}
    edge_endpoints = {edge["from"] for edge in graph["edges"]} | {edge["to"] for edge in graph["edges"]}
    assert edge_endpoints <= node_names


def test_explicit_reviewer_job_action_runs_matching_queued_jobs_only(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    artifact = tmp_path / "staging.md"
    artifact.write_text("# Staging\n\nReady.\n", encoding="utf-8")
    store = WorkerJobStore(project_root)
    matching = store.create_job(
        run_id="run-1",
        role="code_review",
        adapter="fake",
        task="Review staging",
        context="Context",
        profile_id="correctness-security",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version="sha256:be26be20d7c29c183f44a3637d4fd1f02697345e98bfcab39b9a01532eb7c594",
        dispatch_group_id="reviewer-dispatch-current",
        idempotency_key="matching-key",
    )
    store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="fake",
        task="Implement",
        context="Context",
        approval="staging",
        gate="staging",
        artifact_key="staging_report",
        artifact_path=str(artifact),
    )
    store.create_job(
        run_id="run-2",
        role="code_review",
        adapter="fake",
        task="Wrong run",
        context="Context",
        profile_id="architecture-maintainability",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
    )
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    ran: list[str] = []
    original_run_job = WorkerJobStore.run_job

    def spy_run_job(self: WorkerJobStore, job_id: str):
        ran.append(job_id)
        return original_run_job(self, job_id)

    def fail_create_job(*_args, **_kwargs):
        raise AssertionError("explicit reviewer execution must not dispatch new jobs")

    monkeypatch.setattr(web.WorkerJobStore, "run_job", spy_run_job)
    monkeypatch.setattr(web.WorkerJobStore, "create_job", fail_create_job)
    monkeypatch.setattr(web.WorkerJobStore, "create_or_reuse_job", fail_create_job)

    status, payload = web.run_queued_reviewer_jobs_action(
        tmp_path,
        {
            "project_key": "DEMO",
            "run_id": "run-1",
            "approval": "staging",
            "artifact_key": "staging_report",
        },
    )

    assert status == 200
    assert ran == [matching["id"]]
    assert payload["ran_count"] == 1
    assert payload["selected_count"] == 1
    assert payload["skipped_count"] == 2
    persisted_state = StateStore(tmp_path).read_project_current("DEMO")
    assert persisted_state is not None
    assert persisted_state.approvals["staging"] == ApprovalDecision.PENDING
    reviews = build_approval_reviews(persisted_state, workspace=tmp_path)
    staging = next(review for review in reviews if review["approval"] == "staging")
    projection = staging["multi_reviewer_projection"]
    reviewer = next(item for item in projection["reviewers"] if item["profile_id"] == "correctness-security")
    assert reviewer["status"]["status"] == "completed"


def test_explicit_reviewer_job_action_defaults_to_current_artifact_version(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    artifact = tmp_path / "staging.md"
    artifact.write_text("# Staging\n\nCurrent.\n", encoding="utf-8")
    current_version = "sha256:296b27c6f9f5f5b84a41bc01a79c5a80f59fb4048c6995b35f09519523cd6009"
    store = WorkerJobStore(project_root)
    current_job = store.create_job(
        run_id="run-1",
        role="code_review",
        adapter="fake",
        task="Review current staging",
        context="Context",
        profile_id="correctness-security",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version=current_version,
        dispatch_group_id="reviewer-dispatch-current",
        idempotency_key="current-key",
    )
    store.create_job(
        run_id="run-1",
        role="code_review",
        adapter="fake",
        task="Review old staging",
        context="Context",
        profile_id="architecture-maintainability",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version="sha256:old",
        dispatch_group_id="reviewer-dispatch-old",
        idempotency_key="old-key",
    )
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    ran: list[str] = []
    original_run_job = WorkerJobStore.run_job

    def spy_run_job(self: WorkerJobStore, job_id: str):
        ran.append(job_id)
        return original_run_job(self, job_id)

    monkeypatch.setattr(web.WorkerJobStore, "run_job", spy_run_job)

    status, payload = web.run_queued_reviewer_jobs_action(
        tmp_path,
        {
            "project_key": "DEMO",
            "run_id": "run-1",
            "approval": "staging",
            "artifact_key": "staging_report",
        },
    )

    assert status == 200
    assert ran == [current_job["id"]]
    assert payload["artifact_version"] == current_version
    assert payload["selected_count"] == 1


def test_explicit_reviewer_job_action_refuses_wrong_gate_and_wrong_run(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.approvals["security"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    StateStore(tmp_path).write_project_current(state)

    wrong_gate_status, wrong_gate_payload = web.run_queued_reviewer_jobs_action(
        tmp_path,
        {"project_key": "DEMO", "run_id": "run-1", "approval": "security"},
    )
    wrong_run_status, wrong_run_payload = web.run_queued_reviewer_jobs_action(
        tmp_path,
        {"project_key": "DEMO", "run_id": "run-2", "approval": "staging"},
    )

    assert wrong_gate_status == 409
    assert "not currently actionable" in wrong_gate_payload["error"]
    assert wrong_run_status == 409
    assert "run_id does not match" in wrong_run_payload["error"]


def test_failed_reviewer_retry_action_creates_retry_for_current_gate_only(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    artifact = tmp_path / "staging.md"
    artifact.write_text("# Staging\n\nReady.\n", encoding="utf-8")
    artifact_version = web.artifact_version_for_path(str(artifact))
    store = WorkerJobStore(project_root)
    failed = store.create_job(
        run_id="run-1",
        role="code_review",
        adapter="fake",
        task="Review staging",
        context="Context",
        profile_id="correctness-security",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version=artifact_version,
        dispatch_group_id="reviewer-dispatch-current",
        idempotency_key="failed-key",
    )
    failed_root = project_root / ".goodteam" / "workers" / "jobs" / failed["id"]
    (failed_root / "status.json").write_text(
        '{"status":"failed","summary":"Codex CLI exited with code 1.","updated_at":"2026-07-05T10:00:00+00:00"}',
        encoding="utf-8",
    )
    stale = store.create_job(
        run_id="run-1",
        role="code_review",
        adapter="fake",
        task="Review old staging",
        context="Context",
        profile_id="architecture-maintainability",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version="sha256:old",
        dispatch_group_id="reviewer-dispatch-old",
        idempotency_key="old-failed-key",
    )
    stale_root = project_root / ".goodteam" / "workers" / "jobs" / stale["id"]
    (stale_root / "status.json").write_text(
        '{"status":"failed","summary":"Old artifact failed.","updated_at":"2026-07-05T09:00:00+00:00"}',
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)

    def fail_run_job(*_args, **_kwargs):
        raise AssertionError("retry action must not run reviewer jobs")

    monkeypatch.setattr(web.WorkerJobStore, "run_job", fail_run_job)

    status, payload = web.retry_failed_reviewer_jobs_action(
        tmp_path,
        {
            "project_key": "DEMO",
            "run_id": "run-1",
            "approval": "staging",
            "artifact_key": "staging_report",
            "artifact_version": artifact_version,
        },
    )

    retry_jobs = [
        job
        for job in store.list_jobs()
        if job.get("original_failed_job_id") == failed["id"]
    ]
    assert status == 200
    assert payload["selected_count"] == 1
    assert payload["retried_count"] == 1
    assert payload["approval_unchanged"] is True
    assert payload["advisory_only"] is True
    assert payload["artifact_version"] == artifact_version
    assert len(retry_jobs) == 1
    assert retry_jobs[0]["status"] == "queued"
    assert retry_jobs[0]["original_failed_job_id"] == failed["id"]
    persisted_state = StateStore(tmp_path).read_project_current("DEMO")
    assert persisted_state is not None
    assert persisted_state.approvals["staging"] == ApprovalDecision.PENDING
    assert persisted_state.next_node == "await_staging_approval"


def test_failed_reviewer_retry_action_refuses_wrong_run_gate_and_stale_artifact(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    artifact = tmp_path / "staging.md"
    artifact.write_text("# Staging\n\nReady.\n", encoding="utf-8")
    current_version = web.artifact_version_for_path(str(artifact))
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.approvals["security"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)

    wrong_run_status, wrong_run_payload = web.retry_failed_reviewer_jobs_action(
        tmp_path,
        {"project_key": "DEMO", "run_id": "run-2", "approval": "staging"},
    )
    wrong_gate_status, wrong_gate_payload = web.retry_failed_reviewer_jobs_action(
        tmp_path,
        {"project_key": "DEMO", "run_id": "run-1", "approval": "security"},
    )
    stale_status, stale_payload = web.retry_failed_reviewer_jobs_action(
        tmp_path,
        {
            "project_key": "DEMO",
            "run_id": "run-1",
            "approval": "staging",
            "artifact_key": "staging_report",
            "artifact_version": "sha256:old",
        },
    )

    assert wrong_run_status == 409
    assert "run_id does not match" in wrong_run_payload["error"]
    assert wrong_gate_status == 409
    assert "not currently actionable" in wrong_gate_payload["error"]
    assert stale_status == 409
    assert "artifact_version does not match" in stale_payload["error"]
    assert current_version != "sha256:old"


def test_failed_reviewer_retry_route_is_registered_in_post_dispatcher():
    source = inspect.getsource(web.serve)

    assert '"/reviewer-jobs/retry"' in source
    assert 'retry_failed_reviewer_jobs_action(workspace, request)' in source


def test_maybe_send_favicon_response_returns_204_without_network_server():
    class FakeHandler:
        def __init__(self):
            self.calls = []

        def send_response(self, status):
            self.calls.append(("response", status))

        def send_header(self, name, value):
            self.calls.append(("header", name, value))

        def end_headers(self):
            self.calls.append(("end",))

    handler = FakeHandler()

    handled = maybe_send_favicon_response(handler, "/favicon.ico")

    assert handled is True
    assert handler.calls == [
        ("response", 204),
        ("header", "Content-Length", "0"),
        ("end",),
    ]
    assert maybe_send_favicon_response(handler, "/") is False


def test_resolve_dashboard_state_reads_existing_state(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_current(state)

    resolved = resolve_dashboard_state(tmp_path)

    assert resolved.run_id == "run-1"
    assert resolved.next_node == "await_prd_approval"


def test_resolve_dashboard_state_reads_project_scoped_state(tmp_path):
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for cross-platform post production.",
    )
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    resolved = resolve_dashboard_state(tmp_path, project_key="CONTENTOPS")

    assert resolved.run_id == "run-1"
    assert resolved.project_key == "CONTENTOPS"
    assert resolved.next_node == "await_prd_approval"


def test_resolve_dashboard_state_returns_none_without_current_state(tmp_path):
    assert resolve_dashboard_state(tmp_path) is None


def test_is_active_run_detects_current_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.next_node = "await_prd_approval"

    assert is_active_run(state) is True
    assert is_active_run(None) is False


def test_build_role_observations_maps_artifacts_to_role_statuses():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"
    state.role_models = {
        "orchestrator": {"primary": "codex:gpt-5.5", "mode": "short-context"},
        "product_requirement": {"primary": "codex:gpt-5.5", "draft": "codex:gpt-5.5"},
        "architect": {"primary": "codex:gpt-5.5"},
        "implementation": {"primary": "codex:gpt-5.3-codex", "mode": "worker-dispatch"},
    }

    observations = build_role_observations(state)
    product = next(role for role in observations if role["name"] == "Product / Requirement")
    architect = next(role for role in observations if role["name"] == "Architect")
    orchestrator = next(role for role in observations if role["name"] == "Task Assistant")
    implementation = next(role for role in observations if role["name"] == "Implementation")

    assert orchestrator["model"] == "codex:gpt-5.5"
    assert orchestrator["mode"] == "short-context"
    assert product["status"] == "Waiting for approval"
    assert product["artifact"] == "prd"
    assert product["model"] == "codex:gpt-5.5"
    assert architect["status"] == "No activity"
    assert implementation["model"] == "codex:gpt-5.3-codex"


def test_build_role_observations_marks_next_role_ready():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"

    observations = build_role_observations(state)
    architect = next(role for role in observations if role["name"] == "Architect")

    assert architect["status"] == "Ready"


def test_build_workflow_steps_marks_current_review_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"

    steps = build_workflow_steps(state)
    product = next(step for step in steps if step["key"] == "product_requirement")
    prd_review = next(step for step in steps if step["key"] == "prd_review")
    architecture = next(step for step in steps if step["key"] == "architecture")

    assert product["status"] == "done"
    assert product["artifact_path"] == "/tmp/prd.md"
    assert prd_review["status"] == "review-needed"
    assert prd_review["action_label"] == "Review PRD"
    assert prd_review["approval"] == "prd"
    assert architecture["status"] == "not-started"


def test_build_workflow_steps_for_existing_project_starts_with_takeover_review():
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.source_type = "existing_project"
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.artifacts["takeover"] = "/tmp/takeover-brief.md"
    state.approvals["takeover"] = "pending"

    steps = build_workflow_steps(state)
    takeover = steps[0]
    prd = next(step for step in steps if step["key"] == "product_requirement")

    assert takeover["key"] == "takeover_review"
    assert takeover["status"] == "review-needed"
    assert takeover["action_label"] == "Review TAKEOVER"
    assert takeover["artifact_path"] == "/tmp/takeover-brief.md"
    assert prd["status"] == "not-started"


def test_build_workflow_steps_marks_next_step_ready():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "architect"
    state.approvals["prd"] = "approved"
    state.artifacts["prd"] = "/tmp/prd.md"

    steps = build_workflow_steps(state)
    architecture = next(step for step in steps if step["key"] == "architecture")

    assert architecture["status"] == "ready"
    assert architecture["status_label"] == "Ready"
    assert "no worker execution" in architecture["feedback"]


def test_build_workflow_steps_without_state_is_not_started():
    steps = build_workflow_steps(None)

    assert steps[0]["status"] == "not-started"
    assert all(step["artifact_path"] == "" for step in steps)


def test_build_approval_reviews_reads_prd_artifact(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    reviews = build_approval_reviews(state)
    prd_review = next(review for review in reviews if review["approval"] == "prd")

    assert prd_review["artifact"] == "prd"
    assert prd_review["artifact_path"] == str(prd)
    assert "# PRD" in prd_review["content"]
    assert prd_review["is_pending"] is True
    assert prd_review["is_actionable"] is True
    rfc_review = next(review for review in reviews if review["approval"] == "rfc")
    assert rfc_review["is_actionable"] is False


def test_build_approval_reviews_titles_takeover_for_humans(tmp_path):
    takeover = tmp_path / "takeover-brief.md"
    takeover.write_text("# Takeover Brief\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Take over the existing project")
    state.source_type = "existing_project"
    state.approvals["takeover"] = ApprovalDecision.PENDING
    state.artifacts["takeover"] = str(takeover)
    state.next_node = "await_takeover_approval"

    reviews = build_approval_reviews(state)
    takeover_review = next(review for review in reviews if review["approval"] == "takeover")

    assert takeover_review["title"] == "Takeover Brief"
    assert takeover_review["is_actionable"] is True


def test_build_approval_reviews_includes_multi_role_prd_reviewers(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    reviews = build_approval_reviews(state)
    prd_review = next(review for review in reviews if review["approval"] == "prd")

    assert prd_review["review_roles"] == [
        "Product / Requirement",
        "Architect",
        "Review / QA",
    ]


def test_build_approval_reviews_includes_empty_rfc_committee(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.next_node = "await_rfc_approval"

    reviews = build_approval_reviews(state, workspace=tmp_path)
    rfc_review = next(review for review in reviews if review["approval"] == "rfc")
    prd_review = next(review for review in reviews if review["approval"] == "prd")

    assert rfc_review["committee"] == {
        "advisors": ["qa"],
        "convened": False,
        "messages": [],
    }
    assert "committee" not in prd_review


def test_build_approval_reviews_includes_rfc_committee_messages(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.next_node = "await_rfc_approval"
    challenge = append_message(
        tmp_path,
        "GTO",
        "run-1",
        from_role="qa",
        to=["architect"],
        kind="ask",
        gate="rfc",
        body="Which acceptance test protects this design?",
    )
    rebuttal = append_message(
        tmp_path,
        "GTO",
        "run-1",
        from_role="architect",
        to=["qa"],
        kind="assert",
        gate="rfc",
        body="The integration test covers the approval transition.",
        in_reply_to=str(challenge["id"]),
    )
    append_message(
        tmp_path,
        "GTO",
        "run-1",
        from_role="product",
        to=["architect"],
        kind="ask",
        gate="prd",
        body="This message belongs to a different gate.",
    )

    reviews = build_approval_reviews(state, workspace=tmp_path)
    rfc_review = next(review for review in reviews if review["approval"] == "rfc")

    assert rfc_review["committee"] == {
        "advisors": ["qa"],
        "convened": True,
        "messages": [
            {
                "id": challenge["id"],
                "from_role": "qa",
                "to": ["architect"],
                "kind": "ask",
                "gate": "rfc",
                "body": "Which acceptance test protects this design?",
                "in_reply_to": "",
            },
            {
                "id": rebuttal["id"],
                "from_role": "architect",
                "to": ["qa"],
                "kind": "assert",
                "gate": "rfc",
                "body": "The integration test covers the approval transition.",
                "in_reply_to": challenge["id"],
            }
        ],
    }
    projected_challenge, projected_rebuttal = rfc_review["committee"]["messages"]
    assert projected_challenge["id"]
    assert projected_rebuttal["id"]
    assert projected_rebuttal["in_reply_to"] == projected_challenge["id"]


def test_build_approval_recommendation_requests_changes_for_blockers_and_masks_text(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.blockers = ["Fix /Users/luke/private/app.py before hitting 10.0.0.5"]
    review = {"approval": "prd", "content": "# PRD"}
    evidence = {
        "blocker_count": 1,
        "ci_status": "passed",
        "smoke_status": "passed",
        "qa_status": "passed",
    }

    recommendation = build_approval_recommendation(
        state=state,
        review=review,
        evidence_snapshot=evidence,
        workspace=tmp_path,
    )

    assert recommendation["verdict"] == "request_changes"
    assert recommendation["blocking_count"] >= 1
    assert any(reason["kind"] == "blocker" for reason in recommendation["reasons"])
    reason_text = " ".join(reason["text"] for reason in recommendation["reasons"])
    assert "/Users/luke/private/app.py" not in reason_text
    assert "10.0.0.5" not in reason_text


def test_build_approval_recommendation_cautions_for_unresolved_committee_objection(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    review = {
        "approval": "rfc",
        "content": "# RFC",
        "committee": {
            "messages": [
                {
                    "id": "msg-1",
                    "kind": "ask",
                    "from_role": "qa",
                    "body": "Who owns the rollback evidence?",
                    "in_reply_to": "",
                }
            ]
        },
    }
    evidence = {
        "blocker_count": 0,
        "ci_status": "passed",
        "smoke_status": "passed",
        "qa_status": "passed",
    }

    recommendation = build_approval_recommendation(
        state=state,
        review=review,
        evidence_snapshot=evidence,
        workspace=tmp_path,
    )

    assert recommendation["verdict"] in {"caution", "request_changes", "reject"}
    assert any(reason["kind"] == "open_objection" for reason in recommendation["reasons"])


def test_build_approval_recommendation_approves_clean_review_with_known_evidence(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    review = {"approval": "prd", "content": "# PRD\n\nReady."}
    evidence = {
        "blocker_count": 0,
        "ci_status": "passed",
        "smoke_status": "passed",
        "qa_status": "passed",
    }

    recommendation = build_approval_recommendation(
        state=state,
        review=review,
        evidence_snapshot=evidence,
        workspace=tmp_path,
    )

    assert recommendation == {
        "verdict": "approve",
        "confidence": "high",
        "blocking_count": 0,
        "reasons": [{"kind": "clean", "text": "Artifact content is present and no blocking signals were found."}],
    }


def test_build_approval_recommendation_never_raises_on_missing_data(tmp_path):
    recommendation = build_approval_recommendation(
        state=object(),
        review=object(),
        evidence_snapshot=object(),
        workspace=tmp_path,
    )

    assert recommendation["verdict"] in {"approve", "request_changes", "reject", "caution"}
    assert recommendation["confidence"] in {"high", "medium", "low"}
    assert isinstance(recommendation["blocking_count"], int)
    assert isinstance(recommendation["reasons"], list)


def test_build_approval_reviews_actionable_review_carries_recommendation(tmp_path):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    config_dir = tmp_path / "projects"
    config_dir.mkdir()
    (config_dir / "gto.local.yaml").write_text(
        f"name: GoodTeam\nkey: GTO\nrepos:\n  app: {project_root}\n",
        encoding="utf-8",
    )
    manifest_dir = project_root / ".goodteam" / "provenance" / "pr-42"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pr": 42,
                "branch": "feat/provenance",
                "ci_status": "passed",
                "squash_merge_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReady.", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    reviews = build_approval_reviews(state, workspace=tmp_path)

    prd_review = next(review for review in reviews if review["approval"] == "prd")
    rfc_review = next(review for review in reviews if review["approval"] == "rfc")
    assert isinstance(prd_review["recommendation"], dict)
    assert isinstance(prd_review["evidence_snapshot"]["provenance_refs"], list)
    assert prd_review["evidence_snapshot"]["provenance_refs"][0]["pr"] == "42"
    assert prd_review["evidence_snapshot"]["provenance_refs"][0]["branch"] == "feat/provenance"
    assert "recommendation" not in rfc_review


def test_build_provenance_refs_returns_empty_without_manifests(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")

    assert build_provenance_refs(state=state, workspace=tmp_path, project_key="GTO") == []


def test_build_provenance_refs_parses_manifest_and_masks_paths(tmp_path):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    config_dir = tmp_path / "projects"
    config_dir.mkdir()
    (config_dir / "gto.local.yaml").write_text(
        f"name: GoodTeam\nkey: GTO\nrepos:\n  app: {project_root}\n",
        encoding="utf-8",
    )
    manifest_dir = project_root / ".goodteam" / "provenance" / "pr-42"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "pr": 42,
                "branch": "feat/secret",
                "ci_status": "passed",
                "squash_merge_sha": "abc123",
                "job_ref": "/Users/luke/private/jobs/job-1.json",
                "contract_ref": "/Users/luke/private/contracts/contract.json",
                "pre_review_ref": "/Users/luke/private/reviews/pre-review.md",
                "archive_paths": {
                    "job": "/Users/luke/private/jobs/job-1.json",
                    "contract": "/Users/luke/private/contracts/contract.json",
                    "pre_review": "/Users/luke/private/reviews/pre-review.md",
                },
            }
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")

    refs = build_provenance_refs(state=state, workspace=tmp_path, project_key="GTO")

    assert refs[0]["pr"] == "42"
    assert refs[0]["branch"] == "feat/secret"
    assert refs[0]["ci_status"] == "passed"
    assert refs[0]["merge_sha"] == "abc123"
    for key in ["job_ref", "contract_ref", "pre_review_ref"]:
        assert "local-workspace-dir" in refs[0][key]
        assert "/Users/luke/private" not in refs[0][key]

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")
    assert snapshot["provenance_refs"] == refs


def test_read_artifact_preview_truncates_long_content(tmp_path):
    artifact = tmp_path / "long.md"
    artifact.write_text("a" * 20, encoding="utf-8")

    preview = read_artifact_preview(str(artifact), max_chars=5)

    assert preview == "aaaaa\n\n[Preview truncated]"


def test_render_markdown_preview_outputs_readable_safe_html():
    html = render_markdown_preview(
        "# PRD\n\nReview **bold** text.\n\n- One\n- Two\n\n```html\n<script>alert(1)</script>\n```"
    )

    assert '<h1 id="prd">PRD</h1>' in html
    assert "<p>Review <strong>bold</strong> text.</p>" in html
    assert "<ul>" in html
    assert "<li>One</li>" in html
    assert "<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>" in html
    assert "<script>" not in html


def test_read_artifact_reads_current_run_artifact(tmp_path):
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-brief.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Takeover\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["takeover"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = read_artifact(workspace=tmp_path, artifact_key="takeover")

    assert payload["artifact"] == "takeover"
    assert payload["path"] == str(artifact)
    assert payload["content"] == "# Takeover\n\nReview me"


def test_read_artifact_accepts_workspace_prefixed_relative_artifact_path(tmp_path, monkeypatch):
    workspace = tmp_path / "dev-workspace"
    artifact = workspace / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# PRD\n\n## Open Questions\n\n- Which slice ships first?\n", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["prd"] = str(artifact.relative_to(tmp_path))
    StateStore(workspace).write_current(state)

    monkeypatch.chdir(tmp_path)

    payload = read_open_questions(workspace=Path("dev-workspace"), artifact_key="prd")

    assert payload == {
        "artifact": "prd",
        "questions": ["Which slice ships first?"],
    }


def test_read_artifact_rejects_unregistered_absolute_path(tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n\nDo not expose this.", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    StateStore(tmp_path).write_current(state)

    with pytest.raises(ValueError, match="Artifact is not registered"):
        read_artifact(workspace=tmp_path, artifact_key=str(secret))


def test_open_path_in_file_manager_rejects_sibling_prefix_path(tmp_path):
    workspace = tmp_path / "app"
    sibling = tmp_path / "app-other"
    workspace.mkdir()
    sibling.mkdir()

    with pytest.raises(ValueError, match="outside workspace"):
        open_path_in_file_manager(workspace=workspace, relative_path="../app-other")


def test_save_artifact_updates_current_run_artifact_and_logs_edit(tmp_path):
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# PRD\n\nBefore", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD\n\nAfter")

    assert payload["artifact"] == "prd"
    assert payload["bytes"] == len("# PRD\n\nAfter".encode("utf-8"))
    assert artifact.read_text(encoding="utf-8") == "# PRD\n\nAfter"
    edit_log = tmp_path / "state" / "edit-log.jsonl"
    assert edit_log.is_file()
    assert '"artifact": "prd"' in edit_log.read_text(encoding="utf-8")
    action_log = tmp_path / "state" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    assert actions[-1]["action"] == "artifact_saved"
    assert actions[-1]["artifact"] == "prd"


def test_save_artifact_rejects_unregistered_artifact(tmp_path):
    StateStore(tmp_path).write_current(create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio"))

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "Artifact is not registered" in str(exc)
    else:
        raise AssertionError("Expected unregistered artifact to be rejected")


def test_save_artifact_rejects_task_ledger_as_non_editable_artifact(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    ledger = project_root / ".goodteam" / "tasks" / "tasks.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"tasks": []}', encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["task_ledger"] = str(ledger)
    StateStore(tmp_path).write_current(state)

    with pytest.raises(ValueError, match="Resource is not a markdown artifact: task_ledger"):
        save_artifact(workspace=tmp_path, artifact_key="task_ledger", content="# Ledger")


def test_save_artifact_rejects_path_outside_current_run(tmp_path):
    outside = tmp_path / "notes.md"
    outside.write_text("# Outside", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["prd"] = str(outside)
    StateStore(tmp_path).write_current(state)

    try:
        save_artifact(workspace=tmp_path, artifact_key="prd", content="# PRD")
    except ValueError as exc:
        assert "current run artifacts directory" in str(exc)
    else:
        raise AssertionError("Expected outside path to be rejected")


def test_build_start_console_context_reads_project_and_model_config(tmp_path):
    project_config = tmp_path / "real-project.local.yaml"
    project_config.write_text(
        "name: Real Product\nkey: REAL\nmodel_profile: cheap\ndelivery_mode: multi_service\nrole_model_overrides:\n  architect: codex:gpt-5.5\nworker_type_settings:\n  implementation:\n    reasoning_depth: high\n    additional_prompt: Keep APIs stable.\n    desired_instances: 2\ndeployment_backend:\n  mode: ssh_compose\n  staging_ssh_target: real-staging\n  staging_remote_dir: /srv/real\n  staging_deploy_command: docker compose up -d\n  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health\ndelivery_contract:\n  required: true\n  minimum_evidence: functional\n  fatal_output_patterns:\n    migration_error: migrate: executable file not found\n  staging_acceptance:\n    critical-user-journey:\n      type: local_command\n      command: ./scripts/e2e-critical.sh\n",
        encoding="utf-8",
    )
    context = build_start_console_context(
        workspace=tmp_path / "goodteam",
        model_config=Path("models.yaml"),
        project_config=project_config,
    )

    assert context["project_key"] == "REAL"
    assert context["project_name"] == "Real Product"
    assert "balanced" in context["profiles"]
    assert context["project_config"] == str(project_config)
    assert context["task_backend"] == {"mode": "local", "sync": []}
    assert context["worker_backend"] == {"mode": "codex"}
    assert context["model_profile"] == "cheap"
    assert context["delivery_mode"] == "multi_service"
    assert context["role_model_overrides"]["architect"] == "codex:gpt-5.5"
    assert context["worker_type_settings"]["implementation"] == {
        "reasoning_depth": "high",
        "additional_prompt": "Keep APIs stable.",
        "desired_instances": 2,
    }
    assert context["deployment_backend"]["mode"] == "ssh_compose"
    assert context["deployment_backend"]["staging_ssh_target"] == "real-staging"
    assert context["delivery_contract"]["required"] is True
    assert context["delivery_contract"]["acceptance_count"] == 1


def test_build_start_console_context_exposes_latest_archived_run_for_restart(tmp_path):
    workspace = tmp_path / "goodteam"
    project_config = workspace / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "name: AI Content Studio\nkey: CONTENTOPS\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Restart this exact workflow goal")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(workspace).write_project_current(state)

    terminate_project_run(workspace=workspace, project_key="CONTENTOPS")

    context = build_start_console_context(
        workspace=workspace,
        model_config=Path("models.yaml"),
        project_config=project_config,
    )

    assert context["latest_archived_run"] == {
        "run_id": "run-1",
        "idea": "Restart this exact workflow goal",
        "source_type": "idea",
        "archive_path": str(workspace / "state" / "projects" / "CONTENTOPS" / "archived" / "run-1-cancelled.json"),
    }


def test_build_start_console_context_scopes_directory_candidates_to_project_repos(tmp_path):
    project_root = tmp_path / "contentops"
    docs = project_root / "docs"
    backend = project_root / "backend"
    frontend = project_root / "frontend"
    unrelated = tmp_path / "eastern-birth-chart"
    for path in [docs, backend, frontend, unrelated]:
        path.mkdir(parents=True)
    project_config = tmp_path / "workspace" / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {docs}",
                f"  backend: {backend}",
                f"  frontend: {frontend}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    context = build_start_console_context(
        workspace=tmp_path / "workspace",
        model_config=Path("models.yaml"),
        project_config=project_config,
        project_base=tmp_path,
    )

    candidate_paths = {item["path"] for item in context["candidate_dirs"]}
    assert context["project_base"] == str(project_root)
    assert str(docs) in candidate_paths
    assert str(backend) in candidate_paths
    assert str(frontend) in candidate_paths
    assert str(unrelated) not in candidate_paths


def test_discover_project_configs_lists_only_local_configs(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "real.local.yaml").write_text("name: Real Product\nkey: REAL\n", encoding="utf-8")
    (projects / "ignored.example.yaml").write_text("name: Ignored Config\nkey: IGNORED\n", encoding="utf-8")

    configs = discover_project_configs(tmp_path)

    assert configs == [projects / "real.local.yaml"]


def test_build_project_overview_items_lists_project_configs_with_run_status(tmp_path):
    content_ops_config = tmp_path / "projects" / "contentops.local.yaml"
    gto_config = tmp_path / "projects" / "gto.local.yaml"
    content_ops_config.parent.mkdir()
    content_ops_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\nmodel_profile: cheap\n", encoding="utf-8")
    gto_config.write_text("name: GoodTeam\nkey: GTO\nmodel_profile: max_quality\n", encoding="utf-8")
    active = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio that turns research into posts for TikTok, Reddit, X, and LinkedIn.",
    )
    active.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(active)

    items = build_project_overview_items(tmp_path, model_config=Path("models.yaml"))

    by_key = {item["project_key"]: item for item in items}
    assert by_key["CONTENTOPS"]["status"] == "active"
    assert by_key["CONTENTOPS"]["next_node"] == "await_prd_approval"
    assert by_key["CONTENTOPS"]["model_profile"] == "cheap"
    assert by_key["GTO"]["status"] == "idle"
    assert by_key["GTO"]["model_profile"] == "max_quality"


def test_discover_project_directories_lists_immediate_children(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("nope", encoding="utf-8")

    dirs = discover_project_directories(tmp_path)

    assert dirs == [
        {"name": "api", "path": str(tmp_path / "api")},
        {"name": "docs", "path": str(tmp_path / "docs")},
    ]


def test_discover_project_directories_lists_nested_project_repos(tmp_path):
    project = tmp_path / "product"
    docs = project / "product-docs"
    api = project / "product-api"
    ignored = project / "product-api" / "node_modules"
    for path in [docs, api, ignored]:
        path.mkdir(parents=True)

    dirs = discover_project_directories(tmp_path)
    paths = [item["path"] for item in dirs]

    assert str(project) in paths
    assert str(docs) in paths
    assert str(api) in paths
    assert str(ignored) not in paths


def test_discover_project_directories_defaults_to_home_without_personal_path(tmp_path, monkeypatch):
    (tmp_path / "api").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    dirs = discover_project_directories()

    assert dirs == [{"name": "api", "path": str(tmp_path / "api")}]
    assert "/Users/luke/WorkSpace/codeAi" not in inspect.getsource(discover_project_directories)


def test_list_directory_for_picker_returns_files_and_dirs_filtered_sorted_and_git_flag(tmp_path):
    names = ["zeta", "Alpha", ".config", "beta"]
    for name in names:
        (tmp_path / name).mkdir()
    (tmp_path / "Alpha" / ".git").mkdir()
    for name in web.FS_LIST_IGNORED_NAMES:
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("selectable", encoding="utf-8")

    payload = list_directory_for_picker(str(tmp_path))

    assert payload["path"] == str(tmp_path.resolve())
    assert payload["parent"] == str(tmp_path.resolve().parent)
    assert payload["entries"] == [
        {"name": ".config", "path": str((tmp_path / ".config").resolve()), "is_dir": True, "is_git_repo": False},
        {"name": "Alpha", "path": str((tmp_path / "Alpha").resolve()), "is_dir": True, "is_git_repo": True},
        {"name": "beta", "path": str((tmp_path / "beta").resolve()), "is_dir": True, "is_git_repo": False},
        {"name": "file.txt", "path": str((tmp_path / "file.txt").resolve()), "is_dir": False, "is_git_repo": False},
        {"name": "zeta", "path": str((tmp_path / "zeta").resolve()), "is_dir": True, "is_git_repo": False},
    ]


def test_list_directory_for_picker_defaults_to_home_and_has_null_parent_at_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    home_payload = list_directory_for_picker("")
    root_payload = list_directory_for_picker(str(Path("/").resolve()))

    assert home_payload["path"] == str(tmp_path.resolve())
    assert root_payload["parent"] is None


def test_list_directory_for_picker_rejects_invalid_paths(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("nope", encoding="utf-8")

    for bad_path in ["relative/path", "\x00"]:
        try:
            list_directory_for_picker(bad_path)
        except web.FsListBadRequestError:
            pass
        else:
            raise AssertionError(f"expected bad request for {bad_path!r}")

    for missing_path in [tmp_path / "missing", file_path]:
        try:
            list_directory_for_picker(str(missing_path))
        except web.FsListNotFoundError:
            pass
        else:
            raise AssertionError(f"expected not found for {missing_path}")


def test_list_directory_for_picker_caps_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "FS_LIST_MAX_ENTRIES", 3)
    for name in ["delta", "bravo", "charlie", "alpha"]:
        (tmp_path / name).mkdir()

    payload = list_directory_for_picker(str(tmp_path))

    assert [entry["name"] for entry in payload["entries"]] == ["alpha", "bravo", "charlie"]


def test_create_local_project_config_writes_selected_directories(tmp_path):
    docs = tmp_path / "content-research"
    backend = tmp_path / "content-pipeline-api"
    frontend = tmp_path / "publishing-dashboard"
    for path in [docs, backend, frontend]:
        path.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        docs_path=str(docs),
        backend_path=str(backend),
        frontend_path=str(frontend),
        task_sync=["plane"],
        plane_api_host_url="https://api.plane.so/",
        plane_workspace_slug="leader_kanban",
        plane_project_id="project-123",
        plane_api_key_env="PLANE_API_KEY",
        plane_mcp_server="plane",
        worker_backend_mode="codex",
        workers={"implementation": "opencode", "qa": "codex"},
        model_profile="cheap",
        delivery_mode="multi_service",
        role_model_overrides={"architect": "codex:gpt-5.5"},
        deployment_backend_mode="ssh_compose",
        staging_ssh_target="contentops-staging",
        staging_remote_dir="/srv/contentops",
        staging_git_ref="main",
        staging_public_url="https://staging.contentops.example.com",
        staging_deploy_command="docker compose up -d --build",
        staging_healthcheck_command="curl -fsS http://127.0.0.1:8080/health",
        staging_smoke_command="curl -fsS https://staging.contentops.example.com/health",
        delivery_contract_required=True,
        delivery_contract_minimum_evidence="functional",
        delivery_contract_fatal_patterns="migrate: executable file not found\nrelation .* does not exist",
        delivery_contract_acceptance="migrations|ssh_command|docker compose exec -T backend ./migrate status\ncritical-user-journey|local_command|./scripts/e2e-critical.sh",
    )

    config = Path(payload["project_config"])
    assert config == tmp_path / "projects" / "contentops.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "name: AI Content Studio" in content
    assert f"  docs: {docs}" in content
    assert f"  backend: {backend}" in content
    assert f"  frontend: {frontend}" in content
    assert "task_backend:" in content
    assert "  mode: local" in content
    assert "  sync: plane" in content
    assert "workers:" in content
    assert "  implementation: opencode" in content
    assert "  qa: codex" in content
    assert "  plane:" in content
    assert '    api_host_url: "https://api.plane.so/"' in content
    assert "    workspace_slug: leader_kanban" in content
    assert "    project_id: project-123" in content
    assert "    api_key_env: PLANE_API_KEY" in content
    assert "    mcp_server: plane" in content
    assert "api_key:" not in content
    assert payload["task_backend"]["plane"]["workspace_slug"] == "leader_kanban"
    assert "worker_backend:" in content
    assert "  mode: codex" in content
    assert "model_profile: cheap" in content
    assert "delivery_mode: multi_service" in content
    assert "deployment_backend:" in content
    assert "  mode: ssh_compose" in content
    assert "  staging_ssh_target: contentops-staging" in content
    assert "  staging_remote_dir: /srv/contentops" in content
    assert "  staging_git_ref: main" in content
    assert '  staging_public_url: "https://staging.contentops.example.com"' in content
    assert "  staging_deploy_command: docker compose up -d --build" in content
    assert '  staging_healthcheck_command: "curl -fsS http://127.0.0.1:8080/health"' in content
    assert '  staging_smoke_command: "curl -fsS https://staging.contentops.example.com/health"' in content
    assert "delivery_contract:" in content
    assert "  required: true" in content
    assert "  minimum_evidence: functional" in content
    assert "  fatal_output_patterns:" in content
    assert '    pattern_1: "migrate: executable file not found"' in content
    assert "    pattern_2: relation .* does not exist" in content
    assert "  staging_acceptance:" in content
    assert "    check_1:" in content
    assert "      id: migrations" in content
    assert "      type: ssh_command" in content
    assert "      command: docker compose exec -T backend ./migrate status" in content
    assert "    check_2:" in content
    assert "      id: critical-user-journey" in content
    assert "      type: local_command" in content
    assert "      command: ./scripts/e2e-critical.sh" in content
    assert "role_model_overrides:" in content
    assert '  architect: "codex:gpt-5.5"' in content
    assert payload["worker_backend"] == {"mode": "codex"}
    assert payload["deployment_backend"]["mode"] == "ssh_compose"
    assert payload["delivery_contract"]["required"] is True
    assert payload["delivery_contract"]["acceptance_count"] == 2
    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "multi_service"
    assert payload["role_model_overrides"] == {"architect": "codex:gpt-5.5"}


def test_create_local_project_config_round_trips_quoted_delivery_contract_values(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        deployment_backend_mode="ssh_compose",
        staging_deploy_command='bash -lc "echo key: value # keep"',
        delivery_contract_required=True,
        delivery_contract_minimum_evidence="functional",
        delivery_contract_fatal_patterns=r"ERROR: relation .* does not exist # fatal",
        delivery_contract_acceptance='migrations:status|local_command|bash -lc "echo key: value # keep"',
    )

    config = Path(payload["project_config"])
    target = web.load_project_target(config)
    contract = delivery_contract_config(target)

    assert target["deployment_backend"]["staging_deploy_command"] == 'bash -lc "echo key: value # keep"'
    assert contract["fatal_output_patterns"] == [r"ERROR: relation .* does not exist # fatal"]
    assert contract["staging_acceptance"] == [
        {
            "id": "migrations:status",
            "type": "local_command",
            "command": 'bash -lc "echo key: value # keep"',
        }
    ]


def test_create_local_project_config_preserves_literal_quote_wrapped_values(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        deployment_backend_mode="ssh_compose",
        staging_deploy_command='"deploy:prod # safe"',
        delivery_contract_required=True,
        delivery_contract_fatal_patterns='"ERROR: quoted"',
        delivery_contract_acceptance='quoted-check|local_command|"npm run smoke:quoted # keep"',
    )

    config = Path(payload["project_config"])
    target = web.load_project_target(config)
    contract = delivery_contract_config(target)

    assert target["deployment_backend"]["staging_deploy_command"] == '"deploy:prod # safe"'
    assert contract["fatal_output_patterns"] == ['"ERROR: quoted"']
    assert contract["staging_acceptance"] == [
        {
            "id": "quoted-check",
            "type": "local_command",
            "command": '"npm run smoke:quoted # keep"',
        }
    ]


def test_create_local_project_config_rejects_unsafe_role_model_override_keys(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    with pytest.raises(ValueError, match="Role model override role"):
        create_local_project_config(
            workspace=tmp_path,
            project_name="AI Content Studio",
            project_key="CONTENTOPS",
            project_root=str(project_root),
            role_model_overrides={"architect: injected": "codex:gpt-5.5"},
        )


def test_create_local_project_config_accepts_manual_worker_alias(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        worker_backend_mode="manual",
    )

    content = Path(payload["project_config"]).read_text(encoding="utf-8")
    assert "worker_backend:" in content
    assert "  mode: manual" in content


def test_create_local_project_config_writes_single_project_root(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
    )

    config = Path(payload["project_config"])
    content = config.read_text(encoding="utf-8")
    assert "repos:" in content
    assert f"  root: {project_root}" in content
    assert "  docs:" not in content
    assert "  backend:" not in content
    assert "  frontend:" not in content
    assert payload["repos"] == {"root": str(project_root)}
    assert (project_root / "docs").is_dir()


def test_create_local_project_config_initializes_git_on_fresh_root(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
    )

    commit_count = subprocess.run(
        ["git", "-C", str(project_root), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (project_root / ".git").exists()
    assert (project_root / "README.md").read_text(encoding="utf-8") == "# AI Content Studio\n"
    assert (project_root / ".gitignore").is_file()
    assert commit_count.stdout.strip() == "1"
    assert payload["git"] == {"initialized": True, "remote_created": False, "warnings": []}


def test_create_local_project_config_skips_existing_git_repo(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()
    _init_git_repo(project_root)

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
    )

    assert payload["git"]["initialized"] is False
    assert payload["git"]["remote_created"] is False
    assert "git init skipped: project root already contains .git" in payload["git"]["warnings"]


def test_create_local_project_config_create_remote_without_token_warns(tmp_path, monkeypatch):
    project_root = tmp_path / "contentops"
    project_root.mkdir()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        init_git=False,
        create_remote=True,
    )

    assert Path(payload["project_config"]).is_file()
    assert payload["git"]["initialized"] is False
    assert payload["git"]["remote_created"] is False
    assert "create-remote requested but no GITHUB_TOKEN/GH_TOKEN set" in payload["git"]["warnings"]


def test_create_local_project_config_create_remote_uses_gh_token(tmp_path, monkeypatch):
    project_root = tmp_path / "contentops"
    project_root.mkdir()
    calls = []
    monkeypatch.setenv("GITHUB_TOKEN", "token-from-env")

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        init_git=False,
        create_remote=True,
        remote_private=False,
    )

    gh_calls = [call for call in calls if call["command"][:3] == ["gh", "repo", "create"]]
    assert Path(payload["project_config"]).is_file()
    assert payload["git"] == {"initialized": False, "remote_created": True, "warnings": []}
    assert len(gh_calls) == 1
    assert gh_calls[0]["command"] == [
        "gh",
        "repo",
        "create",
        "contentops",
        "--public",
        "--source",
        str(project_root),
        "--remote",
        "origin",
        "--push",
    ]
    assert gh_calls[0]["env"]["GH_TOKEN"] == "token-from-env"
    assert gh_calls[0]["cwd"] == project_root


def test_create_local_project_config_custom_standards_round_trip(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        project_root=str(project_root),
        delivery_mode="custom",
        delivery_standards="Run local preview.\nDocument rollback.",
    )

    config = Path(payload["project_config"])
    content = config.read_text(encoding="utf-8")
    target = web.load_project_target(config)
    assert "delivery_mode: custom" in content
    assert "delivery_standards:" in content
    assert "  standard_0001: Run local preview." in content
    assert "  standard_0002: Document rollback." in content
    assert target["delivery_standards"] == {
        "standard_0001": "Run local preview.",
        "standard_0002": "Document rollback.",
    }
    assert payload["delivery_standards"] == ["Run local preview.", "Document rollback."]


def test_create_local_project_config_rejects_newlines_in_scalar_values(tmp_path):
    project_root = tmp_path / "contentops"
    project_root.mkdir()

    with pytest.raises(ValueError, match="Project name must be a single line"):
        create_local_project_config(
            workspace=tmp_path,
            project_name="AI Content Studio\nrepos:\n  hijack: /tmp/hijack",
            project_key="CONTENTOPS",
            project_root=str(project_root),
        )


def test_web_normalize_delivery_mode_accepts_new_presets_and_custom():
    for mode in [
        "static_site",
        "cli_tool",
        "library",
        "data_pipeline",
        "mobile_app",
        "feature_increment",
        "custom",
    ]:
        assert web.normalize_delivery_mode(mode) == mode


def test_render_delivery_mode_options_uses_full_delivery_mode_catalog():
    html = web.render_delivery_mode_options("cli_tool")

    for mode in web.DELIVERY_MODES:
        assert f'value="{mode}"' in html
    assert '<option value="cli_tool" selected>' in html
    assert "Multi-service product" in html
    assert "Static site / landing" in html
    assert "Custom" in html



def test_product_frontend_replaces_legacy_dashboard_contracts():
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend" / "src"

    assert not (frontend / "App.jsx").exists()
    assert not (frontend / "components" / "ProjectTabRouter.jsx").exists()
    for retired_page in [
        frontend / "features" / "inbox" / "InboxPage.jsx",
        frontend / "features" / "activity" / "ActivityPage.jsx",
        frontend / "features" / "settings" / "SettingsPage.jsx",
        frontend / "features" / "settings" / "ProjectSettingsPage.jsx",
        frontend / "components" / "ui" / "Breadcrumb.jsx",
    ]:
        assert not retired_page.exists()

    router = (frontend / "app" / "AppRouter.jsx").read_text(encoding="utf-8")
    for route in [
        "/projects",
        "/inbox",
        "/activity",
        "/settings",
        "/projects/:projectKey/home",
        "/projects/:projectKey/work",
        "/projects/:projectKey/work/runs/:runId",
        "/projects/:projectKey/review",
        "/projects/:projectKey/review/:reviewId",
        "/projects/:projectKey/knowledge",
        "/projects/:projectKey/settings/:section",
    ]:
        assert route in router

    run_api = (frontend / "features" / "runs" / "runApi.js").read_text(encoding="utf-8")
    review_api = (frontend / "features" / "reviews" / "reviewApi.js").read_text(encoding="utf-8")
    knowledge_api = (
        frontend / "features" / "knowledge" / "knowledgeApi.js"
    ).read_text(encoding="utf-8")
    settings_api = (
        frontend / "features" / "settings" / "settingsApi.js"
    ).read_text(encoding="utf-8")
    for endpoint in [
        "/api/start",
        "/api/retry",
        "/api/dispatch-track",
        "/api/messages",
        "/api/devloop",
    ]:
        assert endpoint in run_api
    assert "/api/approve" in review_api
    assert "/api/recover-takeover-brief" in review_api
    assert "/api/artifact" in review_api
    assert "/api/artifact" in knowledge_api
    # Team mutations go through the control-plane command surface (#402).
    assert "/team/commands" in settings_api
    assert "/team/events" in settings_api


def test_frontend_i18n_locale_files_exist_and_define_current_product_chrome_keys():
    root = Path(__file__).resolve().parents[1]
    en = root / "frontend" / "src" / "i18n" / "locales" / "en.js"
    zh = root / "frontend" / "src" / "i18n" / "locales" / "zh-CN.js"

    assert en.exists()
    assert zh.exists()

    en_text = en.read_text(encoding="utf-8")
    zh_text = zh.read_text(encoding="utf-8")
    for key in [
        "product.brand",
        "product.navigation.projects",
        "product.navigation.projectLabel",
        "product.workNavigation.team",
        "product.workNavigation.workbench",
        "product.workNavigation.reviews",
        "product.workNavigation.knowledge",
        "product.appearance.label",
        "product.appearance.light",
        "product.appearance.dark",
        "product.language.label",
        "product.language.auto",
        "product.language.en",
        "product.language.zhCN",
    ]:
        assert key in en_text
        assert key in zh_text

    # Retired dashboard chrome, inbox/activity/settings surfaces, and the hidden
    # Vivid/Minimal theme system stay deleted; these keys must not return.
    for key in [
        "nav.groups.work",
        "nav.tabs.overview",
        "chrome.runId",
        "language.english",
        "theme.toggle",
        "approvalBanner.review",
        "product.navigation.inbox",
        "product.navigation.activity",
        "product.navigation.settings",
        "product.navigation.projectSettings",
        "product.navigation.breadcrumb",
        "inbox.title",
        "activity.title",
        "settings.title",
        "toast.settingsSaved",
    ]:
        assert key not in en_text
        assert key not in zh_text



def test_frontend_i18n_resolves_auto_language_and_falls_back_to_english():
    i18n_path = Path(__file__).resolve().parents[1] / "frontend" / "src" / "i18n" / "index.js"
    assert i18n_path.exists()
    i18n = i18n_path.read_text(encoding="utf-8")

    assert "gt-language" in i18n
    assert "LANGUAGE_PREFERENCE_AUTO = 'auto'" in i18n
    assert "SUPPORTED_LANGUAGE_PREFERENCES = ['auto', 'en', 'zh-CN']" in i18n
    assert "startsWith('zh')" in i18n or 'startsWith("zh")' in i18n
    assert "return 'zh-CN'" in i18n
    assert "return 'en'" in i18n
    assert "normalizeLanguagePreference" in i18n
    assert "resolveLanguage" in i18n
    assert "translate" in i18n


def test_create_local_project_config_defaults_to_local_only_task_backend(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()

    payload = create_local_project_config(
        workspace=tmp_path,
        project_name="AI Content Studio",
        project_key="CONTENTOPS",
        docs_path=str(docs),
    )

    content = Path(payload["project_config"]).read_text(encoding="utf-8")
    assert "task_backend:" in content
    assert "  mode: local" in content
    assert "  sync:" in content
    assert "delivery_mode: init_project" in content
    assert payload["task_backend"] == {"mode": "local", "sync": []}
    assert payload["deployment_backend"] == {"mode": ""}
    assert payload["delivery_contract"]["required"] is False
    assert payload["delivery_mode"] == "init_project"


def test_start_workflow_writes_state_and_checkpoint(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Build an AI content studio that collects source material and drafts posts for TikTok, Reddit, X, and LinkedIn.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "CONTENTOPS"
    assert payload["delivery_mode"] == "init_project"
    assert payload["next_node"] == "await_prd_approval"
    assert payload["dry_run"] is False
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert (tmp_path / "state" / "projects" / "CONTENTOPS" / "current.json").is_file()
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", payload["run_id"], "prd_review_gate").is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    assert [action["action"] for action in actions] == ["workflow_started", "prd_generated"]
    assert actions[0]["actor"] == "human"
    assert actions[1]["actor"] == "system"
    assert actions[1]["artifact"] == "prd"


def test_start_workflow_does_not_publish_requested_run_before_execution_completes(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    config = _write_context_project(tmp_path, project_root, key="TEAM")
    observed = {}

    def fail_after_claim(*, state, workspace, project_config):
        current = StateStore(workspace).read_project_current("TEAM")
        observed["run_id"] = current.run_id if current is not None else ""
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(web, "run_adoption_until_next_gate", fail_after_claim)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        web.start_workflow(
            workspace=tmp_path,
            idea="修复登录问题",
            project_key="TEAM",
            model_config=Path("models.yaml"),
            model_profile="balanced",
            source_type="existing_project",
            project_config=config,
            run_id="task-proposal-1",
        )

    assert observed["run_id"] == ""
    assert StateStore(tmp_path).read_project_current("TEAM") is None


def test_start_workflow_custom_delivery_standards_are_transient_state_and_prd(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Build an AI content studio with custom delivery.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        delivery_mode="custom",
        delivery_standards=[" Run local preview. ", "", "Document rollback."],
    )

    assert payload["delivery_mode"] == "custom"
    assert payload["delivery_standards"] == ["Run local preview.", "Document rollback."]
    prd = Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8")
    assert "- Run local preview." in prd
    assert "- Document rollback." in prd
    assert "Treat this as a real internet product starter" not in prd


def test_start_workflow_ignores_transient_standards_for_preset_delivery_mode(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Build a static AI content studio page.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        delivery_mode="static_site",
        delivery_standards="Run local preview.\nDocument rollback.",
    )

    assert payload["delivery_mode"] == "static_site"
    assert payload["delivery_standards"] == []
    prd = Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8")
    assert "- Deliver a static site or landing page with a buildable artifact and local preview path." in prd
    assert "- Run local preview." not in prd


def test_start_workflow_notifies_when_prd_approval_gate_is_ready(tmp_path, monkeypatch):
    notifications = []

    def fake_notification(**kwargs):
        kwargs.pop("workspace", None)
        notifications.append(kwargs)
        return {"status": "sent"}

    monkeypatch.setattr("goodteam_os.web.send_approval_notification", fake_notification)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Build an AI content studio that collects source material and drafts posts for TikTok, Reddit, X, and LinkedIn.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert notifications == [
        {
            "project_key": "CONTENTOPS",
            "run_id": payload["run_id"],
            "gate": "prd",
            "artifact_path": payload["artifacts"]["prd"],
            "summary": "GoodTeam is waiting for PRD approval on CONTENTOPS.",
        }
    ]


def test_start_workflow_can_adopt_existing_project_first(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "codex.md").write_text(
        "# Memory\n- The product collects source material and drafts platform-specific AI usage posts.\n",
        encoding="utf-8",
    )
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\nworker_backend:\n  mode: fake\n",
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=tmp_path,
        idea="Take over the AI content studio and prepare cross-platform publishing workflows.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="existing_project",
        project_config=project_config,
    )

    assert payload["source_type"] == "existing_project"
    assert payload["next_node"] == "await_takeover_approval"
    assert payload["dry_run"] is False
    assert Path(payload["artifacts"]["takeover"]).is_file()
    assert "prd" not in payload["artifacts"]
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", payload["run_id"], "takeover_review_gate").is_file()


def test_start_workflow_keeps_external_project_config_discoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda *args, **kwargs: {"status": "skipped"})
    workspace = tmp_path / "run"
    project_dir = tmp_path / "projects"
    target_root = tmp_path / "target"
    project_dir.mkdir()
    target_root.mkdir()
    project_config = project_dir / "check.local.yaml"
    project_config.write_text(
        f"name: Runtime Check\nkey: CHECK\nrepos:\n  app: {target_root}\nworker_backend:\n  mode: fake\n",
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=workspace,
        idea="Build a task tracker",
        project_key="CHECK",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        project_config=project_config,
    )

    assert payload["source_ref"] == str(project_config.resolve())
    assert web._project_config_for_key(workspace, "CHECK") == project_config.resolve()
    assert build_project_overview_items(workspace)[0]["project_key"] == "CHECK"


def test_discover_project_configs_reads_sibling_projects_for_legacy_runs(tmp_path):
    workspace = tmp_path / "run"
    project_dir = tmp_path / "projects"
    target_root = tmp_path / "target"
    workspace.mkdir()
    project_dir.mkdir()
    target_root.mkdir()
    project_config = project_dir / "check.local.yaml"
    project_config.write_text(
        f"name: Runtime Check\nkey: CHECK\nrepos:\n  app: {target_root}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CHECK", "Build a task tracker")
    StateStore(workspace).write_project_current(state)

    assert web._project_config_for_key(workspace, "CHECK") == project_config.resolve()
    assert build_project_overview_items(workspace)[0]["project_key"] == "CHECK"


def test_start_workflow_notifies_when_takeover_approval_gate_is_ready(tmp_path, monkeypatch):
    notifications = []

    def fake_notification(**kwargs):
        kwargs.pop("workspace", None)
        notifications.append(kwargs)
        return {"status": "sent"}

    monkeypatch.setattr("goodteam_os.web.send_approval_notification", fake_notification)
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "content-research"
    docs.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\nworker_backend:\n  mode: fake\n",
        encoding="utf-8",
    )

    start_workflow(
        workspace=tmp_path,
        idea="Take over the AI content studio and prepare cross-platform publishing workflows.",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        source_type="existing_project",
        project_config=project_config,
    )

    # takeover is an internal gate and is not in the default TG notify set
    assert notifications == []


def test_start_workflow_rejects_new_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(existing)

    try:
        start_workflow(
            workspace=tmp_path,
            idea="Start another AI content studio run",
            project_key="CONTENTOPS",
            model_config=Path("models.yaml"),
            model_profile="balanced",
        )
    except ValueError as exc:
        assert "active run already exists" in str(exc)
    else:
        raise AssertionError("Expected active run guard to reject a new run")


def test_start_workflow_allows_new_run_after_project_run_is_terminated(tmp_path):
    existing = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(existing)

    terminate_project_run(workspace=tmp_path, project_key="CONTENTOPS")

    payload = start_workflow(
        workspace=tmp_path,
        idea="Restart the AI content studio workflow",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "CONTENTOPS"
    assert payload["run_id"] != "run-1"
    assert payload["next_node"] == "await_prd_approval"
    assert StateStore(tmp_path).read_project_current("CONTENTOPS").run_id == payload["run_id"]


def test_start_workflow_dry_run_does_not_write_state(tmp_path):
    payload = start_workflow(
        workspace=tmp_path,
        idea="Preview AI content studio planning without writing state",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="cheap",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert payload["model_profile"] == "cheap"
    assert "planned_artifacts" in payload
    assert not (tmp_path / "state" / "projects" / "CONTENTOPS" / "current.json").exists()


def test_start_workflow_allows_dry_run_when_current_run_is_active(tmp_path):
    existing = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    existing.current_node = "prd_review_gate"
    existing.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(existing)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Preview another AI content studio run",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="balanced",
        dry_run=True,
    )

    assert payload["dry_run"] is True
    assert StateStore(tmp_path).read_project_current("CONTENTOPS").run_id == "run-1"


def test_start_workflow_allows_different_projects_to_run_in_parallel(tmp_path):
    content_ops = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    content_ops.current_node = "prd_review_gate"
    content_ops.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(content_ops)

    payload = start_workflow(
        workspace=tmp_path,
        idea="Start GoodTeam run",
        project_key="GTO",
        model_config=Path("models.yaml"),
        model_profile="balanced",
    )

    assert payload["project_key"] == "GTO"
    assert StateStore(tmp_path).read_project_current("CONTENTOPS").run_id == "run-1"
    assert StateStore(tmp_path).read_project_current("GTO").run_id == payload["run_id"]


def test_start_workflow_uses_project_model_profile_and_overrides(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: cheap",
                "delivery_mode: mvp",
                "role_model_overrides:",
                "  architect: codex:gpt-5.5",
                "  frontend: codex:gpt-5.3-codex",
            ]
        ),
        encoding="utf-8",
    )

    payload = start_workflow(
        workspace=tmp_path,
        idea="Use project models for the AI content studio",
        project_key="CONTENTOPS",
        model_config=Path("models.yaml"),
        model_profile="",
        project_config=project_config,
    )

    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "mvp"
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.4"
    assert payload["role_models"]["architect"]["primary"] == "codex:gpt-5.5"
    assert payload["role_models"]["frontend"]["primary"] == "codex:gpt-5.3-codex"


def test_start_workflow_rejects_empty_goal(tmp_path):
    try:
        start_workflow(
            workspace=tmp_path,
            idea=" ",
            project_key="CONTENTOPS",
            model_config=Path("models.yaml"),
            model_profile="balanced",
        )
    except ValueError as exc:
        assert "Workflow goal is required" in str(exc)
    else:
        raise AssertionError("Expected empty workflow goal to fail")


def test_decide_approval_approves_prd_and_generates_rfc(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="approved",
    )

    assert payload["approvals"]["prd"] == "approved"
    assert payload["current_node"] == "rfc_review_gate"
    assert payload["next_node"] == "await_rfc_approval"
    assert "rfc" in payload["artifacts"]
    assert Path(payload["artifacts"]["rfc"]).is_file()
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", "run-1", "rfc_review_gate").is_file()


def test_decide_approval_rejects_stale_artifact_version_before_mutation(tmp_path):
    artifact = tmp_path / "prd.md"
    artifact.write_text("# PRD\n\nVersion A\n", encoding="utf-8")
    state = create_initial_state("run-stale", "CONTENTOPS", "Approve an exact PRD")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    shown_version = web.artifact_version_for_path(str(artifact))
    artifact.write_text("# PRD\n\nVersion B\n", encoding="utf-8")

    with pytest.raises(web.ProjectApiError) as raised:
        decide_approval(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            approval="prd",
            decision="approved",
            expected_run_id="run-stale",
            expected_target_type="review",
            expected_target_id="prd",
            expected_artifact_key="prd",
            expected_artifact_version=shown_version,
        )

    assert raised.value.code == "stale_approval"
    assert raised.value.status == 409
    assert raised.value.details["mismatches"] == ["artifact_version"]

    current_version = web.artifact_version_for_path(str(artifact))
    with pytest.raises(web.ProjectApiError) as wrong_run:
        decide_approval(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            approval="prd",
            decision="approved",
            expected_run_id="run-replaced",
            expected_target_type="review",
            expected_target_id="prd",
            expected_artifact_key="prd",
            expected_artifact_version=current_version,
        )
    assert wrong_run.value.code == "stale_approval"
    assert wrong_run.value.details["mismatches"] == ["run_id"]

    persisted = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert persisted is not None
    assert persisted.approvals["prd"] == ApprovalDecision.PENDING
    assert persisted.next_node == "await_prd_approval"
    assert build_action_items(tmp_path, project_key="CONTENTOPS") == []


def test_decide_approval_records_exact_approved_artifact_version(tmp_path):
    artifact = tmp_path / "prd.md"
    artifact.write_text("# PRD\n\nExact version\n", encoding="utf-8")
    artifact_version = web.artifact_version_for_path(str(artifact))
    state = create_initial_state("run-bound", "CONTENTOPS", "Approve an exact PRD")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="approved",
        expected_run_id="run-bound",
        expected_target_type="review",
        expected_target_id="prd",
        expected_artifact_key="prd",
        expected_artifact_version=artifact_version,
    )

    assert payload["approvals"]["prd"] == "approved"
    approval_event = next(
        item
        for item in build_action_items(tmp_path, project_key="CONTENTOPS")
        if item["action"] == "approval_decided"
    )
    assert approval_event["artifact"] == "prd"
    assert approval_event["artifact_version"] == artifact_version


def test_decide_approval_notifies_when_takeover_approval_generates_prd_gate(tmp_path, monkeypatch):
    notifications = []

    def fake_notification(**kwargs):
        kwargs.pop("workspace", None)
        notifications.append(kwargs)
        return {"status": "sent"}

    monkeypatch.setattr("goodteam_os.web.send_approval_notification", fake_notification)
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "content-research"
    docs.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Take over AI Content Studio")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="takeover",
        decision="approved",
    )

    assert payload["next_node"] == "await_prd_approval"
    assert notifications == [
        {
            "project_key": "CONTENTOPS",
            "run_id": "run-1",
            "gate": "prd",
            "artifact_path": payload["artifacts"]["prd"],
            "summary": "GoodTeam is waiting for PRD approval on CONTENTOPS.",
        }
    ]


def test_decide_approval_approves_rfc_and_runs_to_release_gate(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: contentops-staging",
                "  staging_remote_dir: /srv/contentops",
                "  staging_git_ref: main",
                "  staging_public_url: https://staging.contentops.example.com",
                "  staging_deploy_command: docker compose up -d --build",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
                "  staging_smoke_command: curl -fsS https://staging.contentops.example.com/health",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    state.approvals["prd"] = "approved"
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)
    monkeypatch.setattr(
        "goodteam_os.graph.run_staging_backend",
        lambda project_config, command_runner=None: {
            "status": "passed",
            "failed_step": "",
            "steps": [
                {"label": "deploy", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 1800},
                {"label": "healthcheck", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 300},
                {"label": "smoke", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 300},
            ],
            "ssh_target": "contentops-staging",
            "remote_dir": "/srv/contentops",
            "git_ref": "main",
            "public_url": "https://staging.contentops.example.com",
        },
    )

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="approved",
    )

    assert payload["approvals"]["rfc"] == "approved"
    assert payload["current_node"] == "security_review_gate"
    assert payload["next_node"] == "await_security_approval"
    assert "implementation_handoff" not in payload["artifacts"]

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="security",
        decision="approved",
    )

    assert payload["approvals"]["security"] == "approved"
    assert payload["current_node"] == "staging_review_gate"
    assert payload["next_node"] == "await_staging_approval"
    for artifact in ["task_breakdown", "ui_spec", "implementation_handoff", "qa_report", "code_review", "staging_report"]:
        assert artifact in payload["artifacts"]
        assert Path(payload["artifacts"][artifact]).is_file()
    assert "release_notes" not in payload["artifacts"]


def test_decide_approval_approves_staging_and_generates_release_notes(tmp_path):
    staging_report = tmp_path / "artifacts" / "runs" / "run-1" / "staging_report" / "staging-report.md"
    staging_report.parent.mkdir(parents=True)
    staging_report.write_text("# Staging Report\n\nPassed", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "staging_review_gate"
    state.next_node = "await_staging_approval"
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved", "staging": "pending"})
    state.artifacts["staging_report"] = str(staging_report)
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="staging",
        decision="approved",
    )

    assert payload["approvals"]["staging"] == "approved"
    assert payload["current_node"] == "release_review_gate"
    assert payload["next_node"] == "await_release_approval"
    assert "release_notes" in payload["artifacts"]
    assert Path(payload["artifacts"]["release_notes"]).is_file()


def test_decide_approval_blocks_when_staging_runner_fails(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: contentops-staging",
                "  staging_remote_dir: /srv/contentops",
                "  staging_deploy_command: docker compose up -d --build",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    state.approvals["prd"] = "approved"
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)
    monkeypatch.setattr(
        "goodteam_os.graph.run_staging_backend",
        lambda project_config, command_runner=None: {
            "status": "failed",
            "failed_step": "deploy",
            "steps": [{"label": "deploy", "exit_code": 1, "stdout": "", "stderr": "git pull failed", "timeout": 1800}],
            "ssh_target": "contentops-staging",
            "remote_dir": "/srv/contentops",
            "git_ref": "",
            "public_url": "",
        },
    )

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="approved",
    )

    assert payload["current_node"] == "security_review_gate"
    assert payload["next_node"] == "await_security_approval"

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="security",
        decision="approved",
    )

    assert payload["current_node"] == "implementation"
    assert payload["next_node"] == "blocked"
    assert payload["blockers"] == ["staging deploy failed"]
    assert "staging_report" in payload["artifacts"]


def test_decide_approval_approves_security_and_runs_planner(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "security_review_gate"
    state.next_node = "await_security_approval"
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "pending"})
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="security",
        decision="approved",
    )

    assert payload["approvals"]["security"] == "approved"
    assert payload["approvals"]["staging"] == "approved"
    assert payload["current_node"] == "release_review_gate"
    assert payload["next_node"] == "await_release_approval"
    assert "implementation_handoff" in payload["artifacts"]
    assert "release_notes" in payload["artifacts"]


def test_decide_approval_blocks_when_staging_functional_evidence_is_insufficient(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: contentops-staging",
                "  staging_remote_dir: /srv/contentops",
                "  staging_deploy_command: docker compose up -d --build",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
                "delivery_contract:",
                "  required: true",
                "  minimum_evidence: functional",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    state.approvals["prd"] = "approved"
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)
    monkeypatch.setattr(
        "goodteam_os.graph.run_staging_backend",
        lambda project_config, command_runner=None: {
            "status": "failed",
            "failed_step": "acceptance",
            "evidence_level": "infrastructure",
            "evidence_sufficient": False,
            "evidence_gap": "functional validation missing",
            "steps": [
                {"label": "deploy", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 1800},
                {"label": "healthcheck", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 300},
            ],
            "ssh_target": "contentops-staging",
            "remote_dir": "/srv/contentops",
            "git_ref": "",
            "public_url": "",
        },
    )

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="approved",
    )

    assert payload["current_node"] == "security_review_gate"
    assert payload["next_node"] == "await_security_approval"

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="security",
        decision="approved",
    )

    assert payload["current_node"] == "implementation"
    assert payload["next_node"] == "blocked"
    assert payload["blockers"] == ["staging acceptance failed: functional validation missing"]
    assert "staging_report" in payload["artifacts"]
    assert "Evidence sufficient: `False`" in Path(payload["artifacts"]["staging_report"]).read_text(encoding="utf-8")


def test_retry_blocked_workflow_reruns_only_staging_after_staging_failure(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: contentops-staging",
                "  staging_remote_dir: /srv/contentops",
                "  staging_deploy_command: docker compose up -d --build",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.approvals.update({"prd": "approved", "rfc": "approved", "staging": "pending", "release": "pending"})
    state.blockers = ["staging deploy failed"]
    state.artifacts.update(
        {
            "review_report": str(tmp_path / "artifacts" / "runs" / "run-1" / "review_report" / "review-report.md"),
            "task_breakdown": str(tmp_path / "artifacts" / "runs" / "run-1" / "task_breakdown" / "task-breakdown.md"),
            "ui_spec": str(tmp_path / "artifacts" / "runs" / "run-1" / "ui_spec" / "ui-spec.md"),
            "implementation_handoff": str(tmp_path / "artifacts" / "runs" / "run-1" / "implementation_handoff" / "implementation-handoff.md"),
            "qa_report": str(tmp_path / "artifacts" / "runs" / "run-1" / "qa_report" / "qa-report.md"),
            "code_review": str(tmp_path / "artifacts" / "runs" / "run-1" / "code_review" / "code-review.md"),
        }
    )
    for path in state.artifacts.values():
        artifact_path = Path(path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("# Artifact\n", encoding="utf-8")
    StateStore(tmp_path).write_project_current(state)

    run_jobs = {"count": 0}

    def fake_run_job(self, job_id):
        run_jobs["count"] += 1
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)
    monkeypatch.setattr(
        "goodteam_os.web.run_staging_backend",
        lambda project_config, command_runner=None: {
            "status": "passed",
            "failed_step": "",
            "steps": [
                {"label": "deploy", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 1800},
                {"label": "healthcheck", "exit_code": 0, "stdout": "ok", "stderr": "", "timeout": 300},
            ],
            "ssh_target": "contentops-staging",
            "remote_dir": "/srv/contentops",
            "git_ref": "",
            "public_url": "",
        },
    )

    payload = retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")

    assert run_jobs["count"] == 0
    assert payload["current_node"] == "staging_review_gate"
    assert payload["next_node"] == "await_staging_approval"
    assert payload["blockers"] == []


def test_decide_approval_approves_release_and_completes_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "release_review_gate"
    state.next_node = "await_release_approval"
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved", "release": "pending"})
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="release",
        decision="approved",
    )

    assert payload["approvals"]["release"] == "approved"
    assert payload["current_node"] == "complete"
    assert payload["next_node"] == ""
    assert "retro_memory" in payload["artifacts"]
    assert Path(payload["artifacts"]["retro_memory"]).is_file()


def test_decide_approval_rejects_gate_that_is_not_actionable(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.blockers = ["implementation worker failed: Codex CLI exited with code 1."]
    StateStore(tmp_path).write_project_current(state)

    try:
        decide_approval(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            approval="release",
            decision="approved",
        )
    except ValueError as exc:
        assert "not currently actionable" in str(exc)
    else:
        raise AssertionError("Expected blocked release approval to fail")


def test_retry_blocked_workflow_reruns_workers_to_release_gate(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved", "release": "pending"})
    state.blockers = ["implementation worker failed: Codex CLI exited with code 1."]
    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["current_node"] == "release_review_gate"
    assert payload["next_node"] == "await_release_approval"
    assert payload["blockers"] == []
    assert "release_notes" in payload["artifacts"]
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", "run-1", "release_review_gate").is_file()


def test_retry_blocked_workflow_refreshes_role_models_before_dispatch(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: balanced",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.model_profile = "balanced"
    state.role_models = {
        "implementation": {"primary": "codex:gpt-5.5"},
        "qa": {"primary": "codex:gpt-5.5"},
    }
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "pending"})
    state.blockers = ["implementation worker failed: Codex CLI exited with code 1."]
    StateStore(tmp_path).write_project_current(state)

    def fake_advance_approved_workflow(*, state, workspace, project_config):
        persisted = StateStore(workspace).read_project_current("CONTENTOPS")
        assert persisted.role_models["implementation"]["primary"] == "codex:gpt-5.4"
        assert persisted.role_models["qa"]["primary"] == "codex:gpt-5.4"
        state.current_node = "release_review_gate"
        state.next_node = "await_release_approval"
        return state

    monkeypatch.setattr("goodteam_os.web.advance_approved_workflow", fake_advance_approved_workflow)

    payload = retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["role_models"]["implementation"]["primary"] == "codex:gpt-5.4"
    assert payload["role_models"]["qa"]["primary"] == "codex:gpt-5.4"


def test_retry_blocked_workflow_rejects_non_blocked_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "release_review_gate"
    state.next_node = "await_release_approval"
    StateStore(tmp_path).write_project_current(state)

    try:
        retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")
    except ValueError as exc:
        assert "not blocked" in str(exc)
    else:
        raise AssertionError("Expected retry to reject non-blocked run")


def test_decide_approval_approves_takeover_and_generates_prd(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "codex.md").write_text(
        "# Memory\n- Existing content workflow collects AI usage source material and drafts posts.\n",
        encoding="utf-8",
    )
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    state.artifacts["takeover"] = str(tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-brief.md")
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="takeover",
        decision="approved",
    )

    assert payload["approvals"]["takeover"] == "approved"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", "run-1", "prd_review_gate").is_file()


def test_legacy_takeover_review_recovers_a_human_brief_without_exposing_source_packet(tmp_path):
    docs = tmp_path / "content-research"
    docs.mkdir()
    (docs / "README.md").write_text("# Project notes\n\n- Demo project context.\n", encoding="utf-8")
    project_config = tmp_path / "project.local.yaml"
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    context = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md"
    context.parent.mkdir(parents=True)
    context.write_text("# AI-only source packet\n\n/private/path/that-is-not-a-brief\n", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the existing project")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "revise_takeover"
    state.approvals["takeover"] = ApprovalDecision.CHANGES_REQUESTED
    state.artifacts["takeover"] = str(context)
    StateStore(tmp_path).write_project_current(state)
    web.append_action(
        tmp_path,
        run_id="run-1",
        actor="human",
        action="approval_decided",
        project_key="CONTENTOPS",
        gate="takeover",
        decision="changes_requested",
        before_node="await_takeover_approval",
        after_node="revise_takeover",
        note="Please explain the most important current risk.",
    )

    review = next(item for item in build_approval_reviews(state, workspace=tmp_path) if item["approval"] == "takeover")

    assert review["presentation"] == "legacy_takeover_context"
    assert review["content"] == ""
    assert review["is_current"] is True
    assert review["recovery"] == {
        "available": True,
        "action": "regenerate_takeover_brief",
        "note": "Please explain the most important current risk.",
    }

    payload = web.recover_legacy_takeover_review(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["recovered"] is True
    assert payload["approvals"]["takeover"] == "pending"
    assert payload["current_node"] == "takeover_review_gate"
    assert payload["next_node"] == "await_takeover_approval"
    brief = Path(payload["artifacts"]["takeover"])
    assert brief.name == "takeover-brief.md"
    content = brief.read_text(encoding="utf-8")
    assert "/private/path/that-is-not-a-brief" not in content
    assert "Please explain the most important current risk." in content
    actions = build_action_items(tmp_path, project_key="CONTENTOPS")
    assert actions[0]["action"] == "takeover_recovery_reopened"
    assert StateStore(tmp_path).latest_project_checkpoint_path("CONTENTOPS", "run-1", "takeover_review_gate").is_file()


def test_legacy_takeover_recovery_rejects_any_other_state(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the existing project")
    state.source_type = "existing_project"
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = ApprovalDecision.PENDING
    StateStore(tmp_path).write_project_current(state)

    with pytest.raises(ValueError, match="does not need recovery"):
        web.recover_legacy_takeover_review(workspace=tmp_path, project_key="CONTENTOPS", note="Anything")


def test_decide_approval_can_request_changes(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        note="Tighten the acceptance criteria.",
    )

    assert payload["approvals"]["prd"] == "pending"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).is_file()
    assert "prd" not in payload["change_requests"]


def test_decide_approval_writes_default_web_audit_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("GOODTEAM_OPERATOR_REF", raising=False)
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"
    state.blockers = ["missing acceptance evidence"]
    StateStore(tmp_path).write_project_current(state)

    decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        debate_ref="debate-1",
        note="Address the acceptance evidence.",
    )

    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    approval_action = actions[0]
    assert approval_action["action"] == "approval_decided"
    assert approval_action["decision_origin"] == "web"
    assert approval_action["actor_ref"] == "web:local"
    assert approval_action["evidence_snapshot"] == {
        "artifact_refs": ["prd"],
        "worker_job_refs": [],
        "ci_status": "unknown",
        "smoke_status": "unknown",
        "qa_status": "unknown",
        "screenshot_refs": [],
        "debate_refs": ["debate-1"],
        "blocker_count": 1,
        "provenance_refs": [],
        "smoke_refs": [],
    }

    items = build_action_items(tmp_path, project_key="CONTENTOPS")
    approval_item = next(item for item in items if item["action"] == "approval_decided")
    assert approval_item["decision_origin"] == "web"
    assert approval_item["actor_ref"] == "web:local"
    assert approval_item["evidence_snapshot"]["debate_refs"] == ["debate-1"]


def test_decide_approval_uses_operator_ref_env_for_web_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_OPERATOR_REF", "web:user:luke")
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        note="Revise the PRD scope.",
    )

    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert actions[0]["decision_origin"] == "web"
    assert actions[0]["actor_ref"] == "web:user:luke"


def test_decide_approval_accepts_existing_metadata_less_callers(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        note="Revise the PRD scope.",
    )

    assert payload["approvals"]["prd"] == "pending"
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert actions[0]["actor_ref"] == "web:local"


def test_decide_approval_attaches_reviewer_evidence_snapshot(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_reviewer_dispatch_project(tmp_path, project_root, acknowledged=True)
    artifact = tmp_path / "staging.md"
    artifact.write_text("# Staging\n\nReady.\n", encoding="utf-8")
    artifact_version = web.artifact_version_for_path(str(artifact))
    job = WorkerJobStore(project_root).create_job(
        run_id="run-1",
        role="code_review",
        adapter="codex",
        task="Review staging",
        context="Context",
        profile_id="correctness-security",
        approval="staging",
        gate="staging",
        phase="implementation",
        artifact_key="staging_report",
        artifact_path=str(artifact),
        artifact_version=artifact_version,
        dispatch_group_id="reviewer-dispatch-current",
        idempotency_key="reviewer-key",
    )
    job_root = project_root / ".goodteam" / "workers" / "jobs" / job["id"]
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "summary": "Codex reviewer completed.",
                "updated_at": "2026-07-05T10:00:00+00:00",
                "result_path": str(job_root / "result.md"),
                "invocation_evidence": {
                    "kind": "provider",
                    "adapter": "codex",
                    "provider": "openai",
                    "external_invocation": True,
                    "command_available": True,
                },
            }
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.current_node = "staging_review_gate"
    state.next_node = "await_staging_approval"
    state.artifacts["staging_report"] = str(artifact)
    StateStore(tmp_path).write_project_current(state)
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda *_args, **_kwargs: None)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="DEMO",
        approval="staging",
        decision="rejected",
        note="Reviewer evidence blocks staging.",
    )

    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "DEMO" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    snapshot = actions[0]["reviewer_evidence_snapshot"]
    assert actions[0]["action"] == "approval_decided"
    assert actions[0]["decision"] == "rejected"
    assert payload["decision"] == "rejected"
    assert snapshot["schema"] == "reviewer_evidence_snapshot.v1"
    assert snapshot["approval"] == "staging"
    assert snapshot["artifact"]["key"] == "staging_report"
    assert snapshot["reviewers"][0]["profile_id"] == "correctness-security"
    assert snapshot["reviewers"][0]["job_id"] == job["id"]
    assert snapshot["reviewers"][0]["status"] == "completed"
    assert snapshot["operator_message"] == "Reviewer evidence is advisory. Human operator remains authoritative."


def test_decide_approval_snapshot_error_does_not_block_human_decision(tmp_path, monkeypatch):
    state = create_initial_state("run-1", "DEMO", "Build diagnostics")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.current_node = "staging_review_gate"
    state.next_node = "await_staging_approval"
    StateStore(tmp_path).write_project_current(state)
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda *_args, **_kwargs: None)

    def fail_snapshot(*_args, **_kwargs):
        raise ValueError("malformed reviewer data")

    def fail_worker_action(*_args, **_kwargs):
        raise AssertionError("approval decision must not run, retry, or dispatch reviewer jobs")

    monkeypatch.setattr(web, "build_reviewer_evidence_snapshot", fail_snapshot)
    monkeypatch.setattr(web, "run_queued_reviewer_jobs_for_gate", fail_worker_action)
    monkeypatch.setattr(web, "retry_failed_reviewer_jobs_for_gate", fail_worker_action)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="DEMO",
        approval="staging",
        decision="rejected",
        note="Malformed reviewer evidence requires rejection.",
    )

    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "DEMO" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    snapshot = actions[0]["reviewer_evidence_snapshot"]
    assert payload["decision"] == "rejected"
    assert snapshot["errors"] == ["malformed reviewer data"]
    assert snapshot["operator_message"] == "Reviewer evidence is advisory. Human operator remains authoritative."


def test_build_approval_evidence_snapshot_skips_worker_jobs_without_run_id(tmp_path):
    job = WorkerJobStore(tmp_path).create_job(
        run_id="run-other",
        role="qa",
        adapter="noop",
        task="Verify another run",
        context="Context",
    )
    state = create_initial_state("", "CONTENTOPS", "Existing AI content studio run")

    snapshot = build_approval_evidence_snapshot(
        state=state,
        workspace=tmp_path,
        project_key="CONTENTOPS",
        debate_ref="",
    )

    assert job["id"]
    assert snapshot["worker_job_refs"] == []


def test_build_approval_evidence_snapshot_is_failure_safe_on_missing_data(tmp_path):
    snapshot = build_approval_evidence_snapshot(
        state=object(),
        workspace=tmp_path / "missing-workspace",
        project_key="MISSING",
        debate_ref="",
    )

    assert snapshot == {
        "artifact_refs": [],
        "worker_job_refs": [],
        "ci_status": "unknown",
        "smoke_status": "unknown",
        "qa_status": "unknown",
        "screenshot_refs": [],
        "debate_refs": [],
        "blocker_count": 0,
        "provenance_refs": [],
        "smoke_refs": [],
    }


def _seed_smoke_report(workspace, status):
    from goodteam_os.smoke import write_smoke_report

    write_smoke_report(
        workspace,
        {
            "status": status,
            "suite": ["contentops"],
            "cycles": 1,
            "runs": [
                {
                    "project_key": "CONTENTOPS",
                    "suite": "contentops",
                    "cycle": 1,
                    "status": status,
                    "run_id": "run-smoke",
                    "classification": "",
                    "commands": [
                        {
                            "name": "build",
                            "command": "npm run build",
                            "cwd": str(workspace / "contentops"),
                            "exit_code": 0 if status == "passed" else 1,
                        }
                    ],
                    "evidence": {
                        "smoke_report": str(workspace / "state" / "smoke-runs" / "latest.json"),
                        "worker_result_paths": [str(workspace / "contentops" / ".goodteam" / "workers" / "jobs" / "job-1" / "result.md")]
                    },
                    "worker_job_ids": ["job-1"],
                }
            ],
        },
    )


def test_build_approval_evidence_snapshot_smoke_status_passed(tmp_path):
    _seed_smoke_report(tmp_path, "passed")
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "passed"
    assert snapshot["smoke_refs"][0]["report_path"] == str(tmp_path / "state" / "smoke-runs" / "latest.json")
    assert snapshot["smoke_refs"][0]["run_id"] == "run-smoke"
    assert snapshot["smoke_refs"][0]["worker_job_ids"] == ["job-1"]
    assert snapshot["smoke_refs"][0]["worker_result_paths"]
    assert snapshot["smoke_refs"][0]["commands"][0]["command"] == "npm run build"


def test_build_approval_evidence_snapshot_smoke_status_failed(tmp_path):
    _seed_smoke_report(tmp_path, "failed")
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "failed"


def test_build_approval_evidence_snapshot_smoke_status_unknown_when_no_reports(tmp_path):
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "unknown"
    assert snapshot["smoke_refs"] == []


def test_build_approval_evidence_snapshot_smoke_status_ignores_other_projects(tmp_path):
    _seed_smoke_report(tmp_path, "passed")
    state = create_initial_state("run-smoke", "GTO", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["smoke_status"] == "unknown"
    assert snapshot["smoke_refs"] == []


def test_build_approval_evidence_snapshot_qa_status_passed_on_approved(tmp_path):
    state = create_initial_state("run-qa", "GTO", "QA run")
    state.approvals["qa"] = ApprovalDecision.APPROVED

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["qa_status"] == "passed"


def test_build_approval_evidence_snapshot_qa_status_failed_on_rejected(tmp_path):
    state = create_initial_state("run-qa", "GTO", "QA run")
    state.approvals["qa"] = ApprovalDecision.REJECTED

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["qa_status"] == "failed"


def test_build_approval_evidence_snapshot_qa_status_unknown_when_pending(tmp_path):
    state = create_initial_state("run-qa", "GTO", "QA run")
    state.approvals["qa"] = ApprovalDecision.PENDING

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["qa_status"] == "unknown"


def _seed_ci_manifest(tmp_path, ci_status, run_id=""):
    project_root = tmp_path / "repo"
    project_root.mkdir(exist_ok=True)
    config_dir = tmp_path / "projects"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "gto.local.yaml").write_text(
        f"name: GoodTeam\nkey: GTO\nrepos:\n  app: {project_root}\n",
        encoding="utf-8",
    )
    manifest_dir = project_root / ".goodteam" / "provenance" / "pr-7"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"pr": 7, "branch": "feat/x", "ci_status": ci_status}
    if run_id:
        manifest["run_id"] = run_id
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return config_dir


def test_build_approval_evidence_snapshot_ci_status_passed(tmp_path, monkeypatch):
    config_dir = _seed_ci_manifest(tmp_path, "success", run_id="run-ci")
    monkeypatch.setattr(web, "_project_config_for_key", lambda workspace, key: config_dir / "gto.local.yaml")
    state = create_initial_state("run-ci", "GTO", "CI run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["ci_status"] == "passed"


def test_build_approval_evidence_snapshot_ci_status_failed(tmp_path, monkeypatch):
    config_dir = _seed_ci_manifest(tmp_path, "failure", run_id="run-ci")
    monkeypatch.setattr(web, "_project_config_for_key", lambda workspace, key: config_dir / "gto.local.yaml")
    state = create_initial_state("run-ci", "GTO", "CI run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["ci_status"] == "failed"


def test_build_approval_evidence_snapshot_ci_status_unknown_without_manifest(tmp_path):
    state = create_initial_state("run-ci", "GTO", "CI run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["ci_status"] == "unknown"


def test_build_approval_evidence_snapshot_bare_state_all_unknown(tmp_path):
    state = create_initial_state("run-bare", "GTO", "Bare run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="GTO")

    assert snapshot["smoke_status"] == "unknown"
    assert snapshot["qa_status"] == "unknown"
    assert snapshot["ci_status"] == "unknown"


def test_decide_approval_rejects_prd_by_regenerating_for_review(tmp_path):
    state = create_initial_state("run-1", "GTO", "Bad PRD")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True)
    prd.write_text("# Bad PRD\n\nToo vague.", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="GTO",
        approval="prd",
        decision="rejected",
        note="The PRD is too vague.",
    )

    assert payload["approvals"]["prd"] == "pending"
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8").startswith("# Product Roadmap PRD")
    assert "Too vague." not in Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8")
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "GTO" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [action["action"] for action in actions] == ["approval_decided", "prd_regenerated"]
    assert actions[0]["actor"] == "human"
    assert actions[0]["gate"] == "prd"
    assert actions[0]["decision"] == "rejected"
    assert actions[1]["actor"] == "system"
    assert actions[1]["artifact"] == "prd"


def test_decide_approval_rejects_rfc_by_regenerating_for_review(tmp_path):
    project_config = tmp_path / "project.local.yaml"
    docs = tmp_path / "contentops" / "docs"
    docs.mkdir(parents=True)
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nworker_backend:\n  mode: fake\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build ContentOps Studio")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals["prd"] = "approved"
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    rfc = tmp_path / "artifacts" / "runs" / "run-1" / "rfc" / "rfc.md"
    rfc.parent.mkdir(parents=True)
    rfc.write_text("# Bad RFC\n\nToo vague.", encoding="utf-8")
    state.artifacts["rfc"] = str(rfc)
    StateStore(tmp_path).write_project_current(state)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="rejected",
        note="The RFC is too vague.",
    )

    assert payload["approvals"]["rfc"] == "pending"
    assert payload["current_node"] == "rfc_review_gate"
    assert payload["next_node"] == "await_rfc_approval"
    content = Path(payload["artifacts"]["rfc"]).read_text(encoding="utf-8")
    assert content.startswith("# Technical RFC")
    assert "## Architecture Decision" in content
    assert "Too vague." not in content
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [action["action"] for action in actions] == ["approval_decided", "rfc_regenerated"]
    assert actions[1]["artifact"] == "rfc"


def test_build_action_items_reads_action_log_newest_first(tmp_path):
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    action_log.parent.mkdir(parents=True)
    action_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-21T10:00:00+00:00",
                        "run_id": "run-1",
                        "actor": "human",
                        "action": "approval_decided",
                        "gate": "prd",
                        "decision": "rejected",
                        "before_node": "await_prd_approval",
                        "after_node": "rejected",
                        "artifact": "prd",
                        "artifact_path": "/tmp/prd.md",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-21T10:00:01+00:00",
                        "run_id": "run-1",
                        "actor": "system",
                        "action": "prd_regenerated",
                        "gate": "prd",
                        "decision": "",
                        "before_node": "rejected",
                        "after_node": "await_prd_approval",
                        "artifact": "prd",
                        "artifact_path": "/tmp/prd.md",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    items = build_action_items(tmp_path, project_key="CONTENTOPS")

    assert [item["action"] for item in items] == ["prd_regenerated", "approval_decided"]
    assert items[0]["title"] == "System regenerated PRD"
    assert items[1]["title"] == "Human rejected PRD"
    assert items[1]["node_transition"] == "await_prd_approval -> rejected"


def test_decide_approval_rejects_unknown_gate(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    StateStore(tmp_path).write_project_current(state)

    try:
        decide_approval(workspace=tmp_path, project_key="CONTENTOPS", approval="unknown", decision="approved")
    except ValueError as exc:
        assert "Unknown approval gate" in str(exc)
    else:
        raise AssertionError("Expected unknown approval gate to fail")


def test_terminate_project_run_archives_current_state_and_clears_active_run(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(tmp_path).write_project_current(state)

    payload = terminate_project_run(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["terminated"] is True
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["run_id"] == "run-1"
    assert Path(payload["archive_path"]).is_file()
    assert StateStore(tmp_path).read_project_current("CONTENTOPS") is None
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert actions[-1]["action"] == "run_terminated"


def test_delete_project_removes_goodteam_config_and_project_state_only(tmp_path):
    repo = tmp_path / "ai-content-studio"
    repo.mkdir()
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {repo}\n",
        encoding="utf-8",
    )
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    StateStore(tmp_path).write_project_current(state)
    state_dir = tmp_path / "state" / "projects" / "CONTENTOPS"
    (state_dir / "action-log.jsonl").write_text("{}\n", encoding="utf-8")

    payload = delete_project(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["deleted"] is True
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["project_config"] == str(project_config)
    assert payload["state_dir"] == str(state_dir)
    assert project_config.exists() is False
    assert state_dir.exists() is False
    assert repo.is_dir()


def test_delete_project_rejects_unknown_project(tmp_path):
    try:
        delete_project(workspace=tmp_path, project_key="UNKNOWN")
    except ValueError as exc:
        assert "No local project config found" in str(exc)
    else:
        raise AssertionError("Expected unknown project delete to fail")


def test_tg_notify_gate_default_filter_skips_non_notify_gate(monkeypatch):
    monkeypatch.delenv("GOODTEAM_TG_NOTIFY_GATES", raising=False)
    called = []

    def fake_send(**kwargs):
        called.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(web, "send_approval_notification", fake_send)

    state = create_initial_state("r1", "TEST", "Test project")
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"

    result = web.notify_current_approval_gate(state)

    assert result == {"status": "skipped", "reason": "gate_not_in_notify_set"}
    assert called == []


def test_tg_notify_gate_default_filter_sends_for_prd_gate(monkeypatch):
    monkeypatch.delenv("GOODTEAM_TG_NOTIFY_GATES", raising=False)
    called = []

    def fake_send(**kwargs):
        called.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(web, "send_approval_notification", fake_send)

    state = create_initial_state("r1", "TEST", "Test project")
    state.next_node = "await_prd_approval"
    state.approvals["prd"] = "pending"

    web.notify_current_approval_gate(state)

    assert len(called) == 1


def test_tg_notify_gate_env_override_routes_correctly(monkeypatch):
    monkeypatch.setenv("GOODTEAM_TG_NOTIFY_GATES", "rfc")
    called = []

    def fake_send(**kwargs):
        called.append(kwargs["gate"])
        return {"status": "ok"}

    monkeypatch.setattr(web, "send_approval_notification", fake_send)

    # rfc gate IS in override set → should call send
    rfc_state = create_initial_state("r1", "TEST", "Test project")
    rfc_state.next_node = "await_rfc_approval"
    rfc_state.approvals["rfc"] = "pending"

    web.notify_current_approval_gate(rfc_state)

    assert called == ["rfc"]

    called.clear()

    # prd gate NOT in override set → should skip
    prd_state = create_initial_state("r2", "TEST", "Test project")
    prd_state.next_node = "await_prd_approval"
    prd_state.approvals["prd"] = "pending"

    result = web.notify_current_approval_gate(prd_state)

    assert result == {"status": "skipped", "reason": "gate_not_in_notify_set"}
    assert called == []



def test_frontend_dev_server_uses_goodteam_specific_default_port():
    root = Path(__file__).resolve().parents[1]
    vite_config = (root / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    dev_up = (root / "scripts" / "dev-up.sh").read_text(encoding="utf-8")

    assert "port: 8790" in vite_config
    assert "strictPort: true" in vite_config
    assert "'/api': 'http://127.0.0.1:8788'" in vite_config
    assert "npm run dev → :8790" in dev_up
    assert ":5173" not in dev_up


def test_context_estimate_returns_estimate_without_writing_cache(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    readme = project_root / "README.md"
    readme.write_text("# Context\n\nSeeded docs.\n", encoding="utf-8")
    _write_context_project(tmp_path, project_root)

    status, payload = _post_web_json(
        tmp_path,
        "/api/context-estimate",
        {"project_key": "CTX", "sources": [{"kind": "file", "value": str(readme)}]},
    )

    assert status == 200
    assert payload["estimate"]["bytes"] == readme.stat().st_size
    assert payload["estimate"]["est_tokens"] == readme.stat().st_size // 4
    assert payload["estimate"]["source_count"] == 1
    assert payload["needs_generation"] is True
    assert payload["recommended_model"] == "gpt-5.4-mini"
    assert not (project_root / ".goodteam" / "context" / "understanding.md").exists()


def test_api_project_config_create_accepts_workers_without_update_branch(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()

    status, payload = _post_web_json(
        tmp_path,
        "/api/project-config",
        {
            "project_name": "Worker Bucket Project",
            "project_key": "WBP",
            "project_root": str(project_root),
            "worker_backend_mode": "codex",
            "workers": {"implementation": "opencode", "qa": "codex"},
            "role_model_overrides": {"implementation": "opencode:opencode-go/qwen3.7-max"},
        },
    )

    assert status == 200
    config = Path(payload["project_config"])
    assert config == tmp_path / "projects" / "wbp.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "workers:" in content
    assert "  implementation: opencode" in content
    assert "  qa: codex" in content
    assert "role_model_overrides:" in content
    assert '  implementation: "opencode:opencode-go/qwen3.7-max"' in content


def test_api_project_config_update_still_accepts_project_key_with_workers(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="WBP")

    status, payload = _post_web_json(
        tmp_path,
        "/api/project-config",
        {
            "project_key": "WBP",
            "worker_backend_mode": "codex",
            "workers": {"implementation": "opencode", "qa": "codex"},
            "role_models": {"implementation": "opencode:opencode-go/qwen3.7-max"},
            "worker_type_settings": {
                "implementation": {
                    "reasoning_depth": "high",
                    "additional_prompt": "Keep APIs stable.",
                    "desired_instances": 2,
                }
            },
        },
    )

    assert status == 200
    assert payload["role_worker_backends"]["implementation"] == "opencode"
    assert payload["worker_type_settings"]["implementation"] == {
        "reasoning_depth": "high",
        "additional_prompt": "Keep APIs stable.",
        "desired_instances": 2,
    }
    config = tmp_path / "projects" / "wbp.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "workers:" in content
    assert "  implementation: opencode" in content
    assert "role_model_overrides:" in content
    assert "  implementation: opencode:opencode-go/qwen3.7-max" in content
    assert "worker_type_settings:" in content
    assert '    additional_prompt: "Keep APIs stable."' in content


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"unknown": {"reasoning_depth": "high"}}, "Unknown worker role: unknown"),
        ({"qa": {"reasoning_depth": "extreme"}}, "reasoning_depth must be one of"),
        ({"qa": {"desired_instances": 9}}, "desired_instances must be between 1 and 8"),
        ({"qa": {"additional_prompt": "x" * 4001}}, "additional_prompt must be at most 4000 characters"),
    ],
)
def test_api_project_config_rejects_invalid_worker_type_settings(tmp_path, settings, message):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root, key="WBP")

    status, payload = _post_web_json(
        tmp_path,
        "/api/project-config",
        {
            "project_key": "WBP",
            "worker_backend_mode": "codex",
            "workers": {},
            "worker_type_settings": settings,
        },
    )

    assert status == 400
    assert message in payload["message"]


def test_manager_worker_type_rejects_multiple_instances() -> None:
    with pytest.raises(ValueError, match="Task Assistant desired_instances must be 1"):
        web.normalize_worker_type_settings(
            {"orchestrator": {"desired_instances": 2}}
        )


def test_context_generate_requires_confirmation(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    (project_root / "README.md").write_text("# Context\n", encoding="utf-8")
    _write_context_project(tmp_path, project_root)

    status, payload = _post_web_json(
        tmp_path,
        "/api/context-generate",
        {"project_key": "CTX", "sources": [{"kind": "dir", "value": str(project_root)}]},
    )

    assert status == 400
    assert payload["code"] == "invalid_request"
    assert payload["message"] == "confirmation required"
    assert payload["request_id"]
    assert not (project_root / ".goodteam" / "context" / "understanding.md").exists()


def test_context_generate_writes_cached_entry(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    docs = project_root / "docs"
    docs.mkdir()
    guide = docs / "guide.md"
    guide.write_text("# Guide\n\nContext guide.\n", encoding="utf-8")
    _write_context_project(tmp_path, project_root)

    status, payload = _post_web_json(
        tmp_path,
        "/api/context-generate",
        {
            "project_key": "CTX",
            "confirm": True,
            "sources": [{"kind": "dir", "value": str(docs)}],
        },
    )

    assert status == 200
    assert payload["generated"] is True
    assert payload["estimate"]["source_count"] == 1
    assert Path(payload["understanding_path"]).is_file()
    assert Path(payload["index_path"]).is_file()
    assert "Project context directory" in Path(payload["understanding_path"]).read_text(encoding="utf-8")
    sources_yaml = project_root / ".goodteam" / "context" / "sources.yaml"
    assert "kind: dir" in sources_yaml.read_text(encoding="utf-8")


def test_context_estimate_rejects_cross_origin(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root)

    status, payload = _post_web_json(
        tmp_path,
        "/api/context-estimate",
        {"project_key": "CTX"},
        origin="https://evil.example",
    )

    assert status == 403
    assert payload["code"] == "same_origin_required"
    assert payload["message"] == "Cross-origin POST requests are not allowed."
    assert payload["request_id"]


def test_context_estimate_enforces_url_size_cap(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    _write_context_project(tmp_path, project_root)

    def fake_fetch_url_source(url: str, *, per_source_cap: int, timeout: float) -> dict[str, object]:
        return {
            "url": url,
            "content": "x" * (per_source_cap + 1),
            "purpose": "Untrusted URL context source",
            "kind": "url",
            "value": url,
        }

    monkeypatch.setattr(web, "_fetch_context_url_source", fake_fetch_url_source)

    status, payload = _post_web_json(
        tmp_path,
        "/api/context-estimate",
        {"project_key": "CTX", "sources": [{"kind": "url", "value": "https://example.com/context.md"}]},
    )

    assert status == 400
    assert payload["code"] == "invalid_request"
    assert payload["message"] == "URL source exceeds 1048576 byte cap: https://example.com/context.md"
    assert payload["request_id"]


def test_discover_project_configs_discovers_registry_projects(tmp_path: Path) -> None:
    from goodteam_os.project_registry import ProjectRegistry

    external_dir = tmp_path / "external"
    external_dir.mkdir()
    ext_config = external_dir / "external-app.local.yaml"
    ext_config.write_text("name: External App\nkey: EXTERNALAPP\n", encoding="utf-8")

    registry = ProjectRegistry()
    registry.register_project(ext_config, key="EXTERNALAPP")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    configs = discover_project_configs(workspace)
    assert ext_config.resolve() in configs


def test_build_project_home_loads_registered_project_with_remote_state(tmp_path: Path) -> None:
    from dataclasses import asdict
    from goodteam_os.project_readiness import ProjectReadinessStore
    from goodteam_os.project_registry import ProjectRegistry

    external_root = tmp_path / "my-external-repo"
    external_root.mkdir()
    external_config_dir = tmp_path / "configs"
    external_config_dir.mkdir()
    ext_config = external_config_dir / "my-app.local.yaml"
    ext_config.write_text(
        f"name: My App\nkey: MYAPP\nrepos:\n  app: {external_root}\n",
        encoding="utf-8",
    )

    external_state_dir = tmp_path / "external_workspace" / "state" / "projects" / "MYAPP"
    external_state_dir.mkdir(parents=True)
    state = create_initial_state(
        "run-ext-1",
        "MYAPP",
        "Build a cat app",
    )
    (external_state_dir / "current.json").write_text(
        json.dumps(asdict(state)),
        encoding="utf-8",
    )
    ProjectReadinessStore(tmp_path / "external_workspace").initialize("MYAPP", external_root)

    registry = ProjectRegistry()
    registry.register_project(
        ext_config,
        root=external_root,
        key="MYAPP",
        state_path=external_state_dir,
    )

    workspace = tmp_path / "fresh_workspace"
    workspace.mkdir()

    home = web.build_project_home(workspace, "MYAPP")
    assert home["project"]["project_key"] == "MYAPP"
    assert home["project"]["display_name"] == "My App"
    assert home["facts"]["active_run"] is not None
    assert home["facts"]["active_run"]["run_id"] == "run-ext-1"
