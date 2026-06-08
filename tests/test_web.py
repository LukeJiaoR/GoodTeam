import json
import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import goodteam_os.web as web
from goodteam_os.messages import append_message
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore
from goodteam_os.web import (
    build_action_items,
    build_agile_board,
    build_dev_loop,
    build_workflow_graph,
    build_project_overview_items,
    build_role_observations,
    build_approval_reviews,
    build_start_console_context,
    build_workflow_steps,
    decide_approval,
    discover_project_configs,
    discover_project_directories,
    create_local_project_config,
    delete_project,
    read_artifact,
    read_artifact_preview,
    render_markdown_preview,
    render_dashboard,
    render_project_overview,
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


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_build_dev_loop_without_project_returns_empty_note():
    dev_loop = build_dev_loop(None)

    assert dev_loop["worktrees"] == []
    assert dev_loop["pull_requests"] == []
    assert dev_loop["pre_reviews"] == []
    assert dev_loop["warnings"] == ["no project selected"]


def test_build_dev_loop_non_git_directory_does_not_use_parent_repo(tmp_path):
    parent_repo = tmp_path / "parent"
    project_root = parent_repo / "scaffold"
    project_root.mkdir(parents=True)
    _init_git_repo(parent_repo)

    dev_loop = build_dev_loop(project_root)

    assert dev_loop["worktrees"] == []
    assert dev_loop["pull_requests"] == []
    assert dev_loop["pre_reviews"] == []
    assert dev_loop["warnings"] == ["project repo has no git history yet"]


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
    monkeypatch.setattr(web, "PRE_REVIEW_REPORT_DIR", report_dir)
    git_output = """worktree /repo/ready
HEAD abc1234567890
branch refs/heads/feat/ready

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
            "land_command": "scripts/land-pr.sh 57 --worktree /repo/ready",
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
            "land_command": "scripts/land-pr.sh 59",
        },
    ]
    assert dev_loop["pre_reviews"] == [
        {"pr": 57, "present": True, "report_path": str(report_dir / "pre-review-57-report.md")},
        {"pr": 58, "present": False, "report_path": str(report_dir / "pre-review-58-report.md")},
        {"pr": 59, "present": True, "report_path": str(report_dir / "pre-review-59-report.md")},
    ]


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
        "Planning",
        "UI Design",
        "Impl",
        "QA",
        "Staging",
        "Security",
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
        "planner": 6,
        "ui": 7,
        "implementation": 8,
        "qa": 9,
        "code_review": 10,
        "staging": 11,
        "await_staging_approval": 11,
        "release": 12,
        "await_release_approval": 12,
        "retro_memory": 13,
        "complete": 14,
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
    node_names = {node["name"] for node in graph["nodes"]}
    edge_endpoints = {edge["from"] for edge in graph["edges"]} | {edge["to"] for edge in graph["edges"]}
    assert edge_endpoints <= node_names


def test_render_dashboard_shows_current_gate():
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    state.artifacts["prd"] = "/tmp/prd.md"
    state.context_packet_path = "/tmp/project/.goodteam/memory/project-memory.md"

    html = render_dashboard(state)

    assert "GoodTeam AI Team OS" in html
    assert "await_prd_approval" in html
    assert "PRD" in html
    assert "/tmp/prd.md" in html
    assert "Role Observability" in html
    assert "Product / Requirement" in html
    assert "Waiting for approval" in html
    assert "Architect" in html
    assert "Model" in html
    assert "Start Workflow" not in html
    assert "Start Run" not in html
    assert "Copy Command" not in html
    assert "Enter the real workflow goal" not in html
    assert "Start a workflow run to populate this dashboard" not in html
    assert "Overview" in html
    assert "Roles" in html
    assert "Artifacts" in html
    assert "Actions" in html
    assert "Agile Board" in html
    assert "Approvals" in html
    assert "System" in html
    assert "Worker Jobs" in html
    assert "Role Summary" in html
    assert "Workflow Waterfall" in html
    assert "Needs review" in html
    assert "Review PRD" in html
    assert "PRD Review" in html
    assert "Approve" in html
    assert "Request Changes" in html
    assert "Reject" in html
    assert "Edit Artifact" in html
    assert "Save Markdown" in html
    assert "Worker Model" in html
    assert "gpt-5.3-codex" in html
    assert "Build AI Team OS" in html
    assert "artifact-preview" in html
    assert "Project Target" in html
    assert "/tmp/project/.goodteam/memory/project-memory.md" in html
    assert "Local Task Ledger" in html
    assert "Task Backend" in html
    assert "Worker Backend" in html
    assert "Active run in progress" not in html


def test_dashboard_renders_agile_board_from_project_task_ledger(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()
    store = LocalTaskStore(project_root)
    task = store.create_task(
        title="Run CONTENTOPS smoke",
        description="CONTENTOPS must leave a smoke report.",
        role="qa",
        priority="high",
        acceptance_criteria=["state/smoke-runs/latest.json exists"],
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.artifacts["task_ledger"] = str(project_root / ".goodteam" / "tasks" / "tasks.json")

    board = build_agile_board(state)
    html = render_dashboard(state)

    assert board["counts"]["backlog"] == 1
    assert board["columns"]["backlog"][0]["id"] == task["id"]
    assert "Agile Board" in html
    assert "Run CONTENTOPS smoke" in html
    assert "state/smoke-runs/latest.json exists" in html


def test_dashboard_worker_tab_shows_live_worker_metadata(tmp_path):
    project_root = tmp_path / "target"
    job_store = WorkerJobStore(project_root)
    job = job_store.create_job(run_id="run-1", role="qa", adapter="codex", task="Verify", context="Context")
    job_root = project_root / ".goodteam" / "workers" / "jobs" / job["id"]
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "run_id": "run-1",
                "role": "qa",
                "adapter": "codex",
                "status": "running",
                "summary": "Codex CLI is running.",
                "updated_at": "2026-05-27T00:00:00+00:00",
                "heartbeat_at": "2026-05-27T00:00:00+00:00",
                "pid": 4242,
            }
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")

    html = render_dashboard(state)

    assert "Worker Jobs" in html
    assert "qa / codex" in html
    assert "4242" in html
    assert "Codex CLI is running." in html
    assert "heartbeat" in html.lower()


def test_render_dashboard_shows_worker_blockers():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "implementation"
    state.next_node = "blocked"
    state.blockers = [
        "implementation worker failed: Codex CLI exited with code 1.",
        "qa worker failed: Codex CLI exited with code 1.",
    ]

    html = render_dashboard(state)

    assert "Blocked" in html
    assert "implementation worker failed" in html
    assert "qa worker failed" in html
    assert "Retry blocked workers" in html
    assert "/retry" in html
    assert "await_release_approval" not in html


def test_render_dashboard_shows_complete_status_when_run_is_closed():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "complete"
    state.next_node = ""

    html = render_dashboard(state)

    assert '<div class="status">complete</div>' in html
    assert "<div class=\"signal\"><span>Next</span>complete</div>" in html


def test_render_dashboard_prioritizes_completion_summary_for_closed_run():
    state = create_initial_state("run-1", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "complete"
    state.next_node = ""

    html = render_dashboard(state)

    assert '<link rel="icon" href="data:,">' in html
    assert "Run Complete" in html
    assert "Open project overview" in html
    assert '<section class="hero wide">' in html
    assert '<details class="start-workflow-drawer">' in html
    assert '<h2>Start Workflow</h2>' not in html


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


def test_render_dashboard_without_state_shows_only_real_empty_status():
    html = render_dashboard(None)

    assert "no_active_run" in html
    assert "No active run" in html
    assert "Start New Project" in html
    assert "Adopt Existing Project" in html
    assert "Start new project" in html
    assert "Start adoption" in html
    assert "Enter the real workflow goal" in html
    assert "no-run-yet" not in html
    assert "Start a workflow run to populate this dashboard" not in html
    assert "Approve PRD" not in html


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
    orchestrator = next(role for role in observations if role["name"] == "Orchestrator / PM")
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
    state.artifacts["takeover"] = "/tmp/takeover-context.md"
    state.approvals["takeover"] = "pending"

    steps = build_workflow_steps(state)
    takeover = steps[0]
    prd = next(step for step in steps if step["key"] == "product_requirement")

    assert takeover["key"] == "takeover_review"
    assert takeover["status"] == "review-needed"
    assert takeover["action_label"] == "Review TAKEOVER"
    assert takeover["artifact_path"] == "/tmp/takeover-context.md"
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


def test_render_dashboard_renders_markdown_artifact_preview(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# PRD\n\nReview **me**", encoding="utf-8")
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")
    state.artifacts["prd"] = str(prd)
    state.next_node = "await_prd_approval"

    html = render_dashboard(state)

    assert '<div class="artifact-preview markdown-preview">' in html
    assert '<h1 id="prd">PRD</h1>' in html
    assert "<p>Review <strong>me</strong></p>" in html


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
    artifact = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Takeover\n\nReview me", encoding="utf-8")
    state = create_initial_state("run-1", "CONTENTOPS", "Take over the AI content studio")
    state.artifacts["takeover"] = str(artifact)
    StateStore(tmp_path).write_current(state)

    payload = read_artifact(workspace=tmp_path, artifact_key="takeover")

    assert payload["artifact"] == "takeover"
    assert payload["path"] == str(artifact)
    assert payload["content"] == "# Takeover\n\nReview me"


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
        "name: Real Product\nkey: REAL\nmodel_profile: cheap\ndelivery_mode: multi_service\nrole_model_overrides:\n  architect: codex:gpt-5.5\ndeployment_backend:\n  mode: ssh_compose\n  staging_ssh_target: real-staging\n  staging_remote_dir: /srv/real\n  staging_deploy_command: docker compose up -d\n  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health\ndelivery_contract:\n  required: true\n  minimum_evidence: functional\n  fatal_output_patterns:\n    migration_error: migrate: executable file not found\n  staging_acceptance:\n    critical-user-journey:\n      type: local_command\n      command: ./scripts/e2e-critical.sh\n",
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
    assert context["deployment_backend"]["mode"] == "ssh_compose"
    assert context["deployment_backend"]["staging_ssh_target"] == "real-staging"
    assert context["delivery_contract"]["required"] is True
    assert context["delivery_contract"]["acceptance_count"] == 1
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


def test_list_directory_for_picker_returns_dirs_only_filtered_sorted_and_git_flag(tmp_path):
    names = ["zeta", "Alpha", ".config", "beta"]
    for name in names:
        (tmp_path / name).mkdir()
    (tmp_path / "Alpha" / ".git").mkdir()
    for name in web.FS_LIST_IGNORED_NAMES:
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("nope", encoding="utf-8")

    payload = list_directory_for_picker(str(tmp_path))

    assert payload["path"] == str(tmp_path.resolve())
    assert payload["parent"] == str(tmp_path.resolve().parent)
    assert payload["entries"] == [
        {"name": ".config", "path": str((tmp_path / ".config").resolve()), "is_git_repo": False},
        {"name": "Alpha", "path": str((tmp_path / "Alpha").resolve()), "is_git_repo": True},
        {"name": "beta", "path": str((tmp_path / "beta").resolve()), "is_git_repo": False},
        {"name": "zeta", "path": str((tmp_path / "zeta").resolve()), "is_git_repo": False},
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
    assert "  plane:" in content
    assert "    api_host_url: https://api.plane.so/" in content
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
    assert "  staging_public_url: https://staging.contentops.example.com" in content
    assert "  staging_deploy_command: docker compose up -d --build" in content
    assert "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health" in content
    assert "  staging_smoke_command: curl -fsS https://staging.contentops.example.com/health" in content
    assert "delivery_contract:" in content
    assert "  required: true" in content
    assert "  minimum_evidence: functional" in content
    assert "  fatal_output_patterns:" in content
    assert "    pattern_1: migrate: executable file not found" in content
    assert "    pattern_2: relation .* does not exist" in content
    assert "  staging_acceptance:" in content
    assert "    migrations:" in content
    assert "      type: ssh_command" in content
    assert "      command: docker compose exec -T backend ./migrate status" in content
    assert "    critical-user-journey:" in content
    assert "      type: local_command" in content
    assert "      command: ./scripts/e2e-critical.sh" in content
    assert "role_model_overrides:" in content
    assert "  architect: codex:gpt-5.5" in content
    assert payload["worker_backend"] == {"mode": "codex"}
    assert payload["deployment_backend"]["mode"] == "ssh_compose"
    assert payload["delivery_contract"]["required"] is True
    assert payload["delivery_contract"]["acceptance_count"] == 2
    assert payload["model_profile"] == "cheap"
    assert payload["delivery_mode"] == "multi_service"
    assert payload["role_model_overrides"] == {"architect": "codex:gpt-5.5"}


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


def test_frontend_contract_reads_project_report_repos_from_top_level():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    overview_tab = root / "frontend" / "src" / "components" / "tabs" / "OverviewTab.jsx"
    frontend_source = app + (overview_tab.read_text(encoding="utf-8") if overview_tab.exists() else "")

    assert "startContext.project_report?.repos?.docs?.path" in frontend_source
    assert "startContext.project_report?.repos?.backend?.path" in frontend_source
    assert "startContext.project_report?.repos?.frontend?.path" in frontend_source
    assert "startContext.project_report?.project?.repos" not in frontend_source


def test_frontend_contract_keeps_agy_model_selectable_and_aliases_deduped():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    workers_constants = (root / "frontend" / "src" / "constants" / "workers.js").read_text(encoding="utf-8")

    assert 'MODEL_UNAVAILABLE_BACKENDS = ["fake", "noop", "human"]' in workers_constants
    assert "Model is not applicable for Antigravity" not in app
    assert "export function canonicalBackendForOption" in workers_constants
    assert "import {" in app and "canonicalBackendForOption" in app


def test_frontend_contract_exposes_open_questions_at_approval_gate():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    approval_utils = (root / "frontend" / "src" / "utils" / "approvals.js").read_text(encoding="utf-8")

    assert "openQuestionsGateCount" in app
    assert "Questions" in app
    assert "handleOpenQuestionsFromApproval" in app
    assert "export function suggestedAnswersForQuestion" in approval_utils
    assert "suggestedAnswersForQuestion={suggestedAnswersForQuestion}" in app


def test_frontend_contract_has_simple_advanced_worker_routing_modes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    workers_constants = (root / "frontend" / "src" / "constants" / "workers.js").read_text(encoding="utf-8")
    workers_tab = (root / "frontend" / "src" / "components" / "tabs" / "WorkersTab.jsx").read_text(
        encoding="utf-8"
    )

    assert "workerRoutingMode" in app
    assert "WORKER_ROUTING_BUCKETS" in workers_constants
    assert "DEFAULT_BACKEND_OPTIONS" in workers_constants
    assert "FALLBACK_AGENT_MODES" in workers_constants
    assert "Apply Simple Routing" in workers_tab
    assert "Advanced" in workers_tab


def test_worker_constants_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    workers_constants = root / "frontend" / "src" / "constants" / "workers.js"

    assert workers_constants.exists()
    constants_source = workers_constants.read_text(encoding="utf-8")
    for export_name in [
        "DEFAULT_BACKEND_OPTIONS",
        "DEFAULT_WORKER_ROLES",
        "MODEL_UNAVAILABLE_BACKENDS",
        "WORKER_ROUTING_BUCKETS",
        "FALLBACK_AGENT_MODES",
    ]:
        assert f"export const {export_name}" in constants_source
        assert f"const {export_name}" not in app

    assert "export function canonicalBackendForOption" in constants_source
    assert "function canonicalBackendForOption" not in app
    assert "import {" in app and "FALLBACK_AGENT_MODES" in app


def test_frontend_contract_exposes_release_readiness_without_execution_controls():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )

    assert "<DevLoopTab" in app
    assert "copyLandCommand={copyLandCommand}" in app
    assert "devLoopData.release_readiness" in dev_loop_tab
    assert "Release Readiness" in dev_loop_tab
    assert "release_up_command" in dev_loop_tab
    assert "graduate_command" in dev_loop_tab
    assert "Copy release-up command" in dev_loop_tab
    assert "Copy graduate command" in dev_loop_tab
    assert "copyLandCommand(command)" in dev_loop_tab
    assert "devLoopData.release_readiness?.available === true" in dev_loop_tab
    assert "Release Readiness unavailable" not in dev_loop_tab
    assert "Run release-up" not in dev_loop_tab
    assert "Run graduate" not in dev_loop_tab


def test_frontend_contract_uses_theme_tokens_for_common_palette_and_small_type():
    app = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

    disallowed_palette_tokens = [
        "#E5E5E5",
        "#AFAFAF",
        "#58CC02",
        "#D1D1D1",
        "#3D8B00",
        "#1a1a1a",
        "#F1F1F1",
        "#FF4B4B",
        "#1CB0F6",
        "#E8F9D9",
        "#FF9600",
    ]
    assert {token: app.count(token) for token in disallowed_palette_tokens if token in app} == {}
    assert "text-[10px]" not in app
    assert "text-[9px]" not in app
    assert "text-[8px]" not in app

    for semantic_class in [
        "border-border-subtle",
        "text-text-muted",
        "bg-surface-sunken",
        "text-accent-text",
        "bg-status-danger-bg",
        "text-2xs",
    ]:
        assert semantic_class in app


def test_file_path_component_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    file_path_component = root / "frontend" / "src" / "components" / "FilePath.jsx"

    assert file_path_component.exists()
    component_source = file_path_component.read_text(encoding="utf-8")
    assert "export default function FilePath" in component_source
    assert "segments = 1" in component_source
    assert "not available" in component_source
    assert "bottom-full" in component_source
    assert "function FilePath" not in app
    assert "import FilePath from './components/FilePath';" in app


def test_spinner_component_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    spinner_component = root / "frontend" / "src" / "components" / "Spinner.jsx"

    assert spinner_component.exists()
    component_source = spinner_component.read_text(encoding="utf-8")
    assert "export default function Spinner" in component_source
    assert "Loading..." in component_source
    assert "RefreshCw" in component_source
    assert "sr-only" in component_source
    assert "function Spinner" not in app
    assert "import Spinner from './components/Spinner';" in app


def test_status_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    status_utils = root / "frontend" / "src" / "utils" / "status.jsx"

    assert status_utils.exists()
    utils_source = status_utils.read_text(encoding="utf-8")
    assert "export function getBadgeStyle" in utils_source
    assert "export function getStatusLabel" in utils_source
    assert "bg-status-success-bg" in utils_source
    assert "chrome.noActiveRun" in utils_source
    assert "function getBadgeStyle" not in app
    assert "function getStatusLabel" not in app
    assert "import { getBadgeStyle, getStatusLabel } from './utils/status';" in app


def test_time_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    time_utils = root / "frontend" / "src" / "utils" / "time.js"

    assert time_utils.exists()
    utils_source = time_utils.read_text(encoding="utf-8")
    assert "export function formatHumanTime" in utils_source
    assert "May 27, 02:44" in utils_source
    assert "padStart(2, '0')" in utils_source
    assert "function formatHumanTime" not in app
    assert "import { formatHumanTime } from './utils/time';" in app


def test_approval_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    approval_utils = root / "frontend" / "src" / "utils" / "approvals.js"

    assert approval_utils.exists()
    utils_source = approval_utils.read_text(encoding="utf-8")
    assert "export function suggestedAnswersForQuestion" in utils_source
    assert "export function computeLineDiff" in utils_source
    assert "optionPattern" in utils_source
    assert "new Int32Array" in utils_source
    assert "function suggestedAnswersForQuestion" not in app
    assert "function computeLineDiff" not in app
    assert "import { computeLineDiff, suggestedAnswersForQuestion } from './utils/approvals';" in app


def test_directory_picker_modal_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    picker = root / "frontend" / "src" / "components" / "DirectoryPickerModal.jsx"

    assert picker.exists()
    picker_source = picker.read_text(encoding="utf-8")
    assert "export default function DirectoryPickerModal" in picker_source
    assert "/api/fs/list" in picker_source
    assert "/api/fs/mkdir" in picker_source
    assert "Directory Browser" in picker_source
    assert "Select this folder" in picker_source
    assert "const directoryPickerOverlay" not in app
    assert "fetchDirectory" not in app
    assert "handleCreateFolder" not in app
    assert "pickerEntries.map" not in app
    assert "import DirectoryPickerModal from './components/DirectoryPickerModal';" in app
    assert "<DirectoryPickerModal" in app


def test_new_project_modal_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    modal = root / "frontend" / "src" / "components" / "NewProjectModal.jsx"

    assert modal.exists()
    modal_source = modal.read_text(encoding="utf-8")
    assert "export default function NewProjectModal" in modal_source
    assert "Start Workflow Console" in modal_source
    assert "Create or Adopt Project" in modal_source
    assert "Project Location" in modal_source
    assert "Launch Check" in modal_source
    assert "DELIVERY_MODE_OPTIONS.map" in modal_source
    assert "Start Workflow Console" not in app
    assert "DELIVERY_MODE_OPTIONS.map" not in app
    assert "import NewProjectModal from './components/NewProjectModal';" in app
    assert "<NewProjectModal" in app


def test_projects_home_page_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    page = root / "frontend" / "src" / "components" / "ProjectsHomePage.jsx"

    assert page.exists()
    page_source = page.read_text(encoding="utf-8")
    assert "export default function ProjectsHomePage" in page_source
    assert "Registered Projects" in page_source
    assert "No Projects Configured" in page_source
    assert "Legacy Active Run Detected" in page_source
    assert "FilePath" in page_source
    assert "getBadgeStyle" in page_source
    assert "Registered Projects" not in app
    assert "No Projects Configured" not in app
    assert "projectItems.map" not in app
    assert "import ProjectsHomePage from './components/ProjectsHomePage';" in app
    assert "<ProjectsHomePage" in app


def test_message_feed_uses_theme_tokens_and_small_type_floor():
    message_feed = (
        Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "MessageFeed.jsx"
    ).read_text(encoding="utf-8")

    disallowed_palette_tokens = [
        "#E5E5E5",
        "#AFAFAF",
        "#58CC02",
        "#D1D1D1",
        "#3D8B00",
        "#1a1a1a",
        "#F1F1F1",
        "#FF4B4B",
        "#EA2B2B",
        "#FFE2E2",
        "#1CB0F6",
        "#0A7BAF",
        "#D0F0FF",
        "#E8F9D9",
        "#FF9600",
        "#B36B00",
        "#FFF0D4",
        "#777777",
    ]
    assert {token: message_feed.count(token) for token in disallowed_palette_tokens if token in message_feed} == {}
    assert "text-[10px]" not in message_feed
    assert "text-[9px]" not in message_feed
    assert "text-[8px]" not in message_feed

    for semantic_class in [
        "border-border-subtle",
        "text-text-muted",
        "text-text-primary",
        "bg-surface-raised",
        "bg-surface-sunken",
        "shadow-card",
        "text-accent",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "border-l-status-warning",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "border-l-status-info",
        "bg-status-info-bg",
        "text-status-info-text",
        "border-l-status-success",
        "bg-status-success-bg",
        "text-status-success-text",
        "text-2xs",
    ]:
        assert semantic_class in message_feed


def test_memory_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Memory */}")
    end = app.index("{/* Tab: Dev Loop */}", start)
    memory_mount = app[start:end]

    assert "<MemoryTab" in memory_mount
    assert "memoryData.roles.map" not in memory_mount
    assert "crossResults.map" not in memory_mount

    memory_tab = root / "frontend" / "src" / "components" / "tabs" / "MemoryTab.jsx"
    assert memory_tab.exists()
    memory_panel = memory_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-[#FDFDFD]",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
        "bg-zinc-50",
        "bg-zinc",
        "dark:bg-zinc-900/30",
        "dark:bg-zinc-900/20",
        "dark:bg-zinc-900/10",
        "dark:",
        "border-zinc-200",
        "border-zinc-250",
        "border-zinc",
        "text-zinc-750",
        "dark:text-zinc-250",
        "text-zinc-700",
        "dark:text-zinc-300",
        "text-zinc-500",
        "text-zinc",
    ]
    assert {snippet: memory_panel.count(snippet) for snippet in disallowed_snippets if snippet in memory_panel} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "bg-status-info-bg",
        "text-status-info-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
    ]:
        assert semantic_class in memory_panel


def test_workflow_graph_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Workflow Graph */}")
    end = app.index("{/* Tab: Overview */}", start)
    workflow_mount = app[start:end]

    assert "<WorkflowGraphTab" in workflow_mount
    assert "computeGraphLayout" not in app
    assert "drawEdgePath" not in app

    workflow_tab = root / "frontend" / "src" / "components" / "tabs" / "WorkflowGraphTab.jsx"
    assert workflow_tab.exists()
    workflow_graph = workflow_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: workflow_graph.count(snippet) for snippet in disallowed_snippets if snippet in workflow_graph} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "bg-status-info-bg",
        "text-status-info-text",
    ]:
        assert semantic_class in workflow_graph


def test_overview_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Overview */}")
    end = app.index("{activeTab === 'agents_memory'", start)
    overview_mount = app[start:end]

    assert "<OverviewTab" in overview_mount
    assert "Current Mission" not in overview_mount
    assert "Run Environment Configuration" not in overview_mount
    assert "Start another run for this project" not in overview_mount

    overview_tab = root / "frontend" / "src" / "components" / "tabs" / "OverviewTab.jsx"
    assert overview_tab.exists()
    overview_panel = overview_tab.read_text(encoding="utf-8")

    for required_text in [
        "Current Mission",
        "Run Environment Configuration",
        "Start another run for this project",
        "Retry Blocked Workers",
    ]:
        assert required_text in overview_panel

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: overview_panel.count(snippet) for snippet in disallowed_snippets if snippet in overview_panel} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "bg-status-danger-bg",
        "text-status-danger-text",
    ]:
        assert semantic_class in overview_panel


def test_agents_memory_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{activeTab === 'agents_memory'")
    end = app.index("{/* Tab: Roles */}", start)
    agents_memory_mount = app[start:end]

    assert "<AgentsMemoryTab" in agents_memory_mount
    assert "setAgentsMemorySubTab('roles')" not in agents_memory_mount
    assert "setAgentsMemorySubTab('memory')" not in agents_memory_mount
    assert "setAgentsMemorySubTab('workers')" not in agents_memory_mount

    agents_memory_tab = root / "frontend" / "src" / "components" / "tabs" / "AgentsMemoryTab.jsx"
    assert agents_memory_tab.exists()
    agents_memory_panel = agents_memory_tab.read_text(encoding="utf-8")

    for required_text in [
        "Roles",
        "Memory",
        "Jobs",
        "<RolesTab",
        "<MemoryTab",
        "<WorkersTab",
    ]:
        assert required_text in agents_memory_panel

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: agents_memory_panel.count(snippet) for snippet in disallowed_snippets if snippet in agents_memory_panel} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-secondary",
        "text-text-muted",
        "text-accent-text",
        "shadow-card",
    ]:
        assert semantic_class in agents_memory_panel


def test_messages_page_is_extracted_and_uses_message_feed_component():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Messages */}")
    end = app.index("{/* Tab: Memory */}", start)
    messages_mount = app[start:end]

    assert "<MessagesTab" in messages_mount
    assert "<MessageFeed" not in messages_mount
    assert "messageFeed.key === targetMessageFeedKey" not in messages_mount

    messages_tab = root / "frontend" / "src" / "components" / "tabs" / "MessagesTab.jsx"
    assert messages_tab.exists()
    messages_panel = messages_tab.read_text(encoding="utf-8")

    for required_text in [
        "<MessageFeed",
        "targetMessageFeedKey",
        "messageFeedProjectKey",
        "messageFeedRunId",
    ]:
        assert required_text in messages_panel


def test_roles_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Roles */}")
    end = app.index("{/* Tab: Artifacts */}", start)
    roles_mount = app[start:end]

    assert "<RolesTab" in roles_mount
    assert "roleObservations.map" not in roles_mount

    roles_tab = root / "frontend" / "src" / "components" / "tabs" / "RolesTab.jsx"
    assert roles_tab.exists()
    roles_page = roles_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: roles_page.count(snippet) for snippet in disallowed_snippets if snippet in roles_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "border-status-warning",
        "border-accent",
        "text-accent",
    ]:
        assert semantic_class in roles_page


def test_artifacts_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Artifacts */}")
    end = app.index("{/* Tab: Actions */}", start)
    artifacts_mount = app[start:end]

    assert "<ArtifactsTab" in artifacts_mount
    assert "Object.entries(state.artifacts).map" not in artifacts_mount

    artifacts_tab = root / "frontend" / "src" / "components" / "tabs" / "ArtifactsTab.jsx"
    assert artifacts_tab.exists()
    artifacts_page = artifacts_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: artifacts_page.count(snippet) for snippet in disallowed_snippets if snippet in artifacts_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "border-border-subtle",
        "text-text-primary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "bg-status-info-bg",
        "text-status-info-text",
        "border-status-info",
    ]:
        assert semantic_class in artifacts_page


def test_actions_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Actions */}")
    end = app.index("{/* Tab: Agile Board */}", start)
    actions_mount = app[start:end]

    assert "<ActionsTab" in actions_mount
    assert "actionItems.map" not in actions_mount

    actions_tab = root / "frontend" / "src" / "components" / "tabs" / "ActionsTab.jsx"
    assert actions_tab.exists()
    actions_page = actions_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: actions_page.count(snippet) for snippet in disallowed_snippets if snippet in actions_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-info-bg",
        "text-status-info-text",
        "bg-status-danger-bg",
        "border-status-danger",
    ]:
        assert semantic_class in actions_page


def test_agile_board_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Agile Board */}")
    end = app.index("{/* Tab: Smoke Runs */}", start)
    board_mount = app[start:end]

    assert "<AgileBoardTab" in board_mount
    assert "agileBoard.columns" not in board_mount

    board_tab = root / "frontend" / "src" / "components" / "tabs" / "AgileBoardTab.jsx"
    assert board_tab.exists()
    board_page = board_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: board_page.count(snippet) for snippet in disallowed_snippets if snippet in board_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "bg-status-info-bg",
        "text-status-info-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-danger-bg",
        "text-status-danger-text",
    ]:
        assert semantic_class in board_page


def test_smoke_runs_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Smoke Runs */}")
    end = app.index("{/* Tab: Workers */}", start)
    smoke_mount = app[start:end]

    assert "<SmokeRunsTab" in smoke_mount
    assert "smokeRunItems.map" not in smoke_mount

    smoke_tab = root / "frontend" / "src" / "components" / "tabs" / "SmokeRunsTab.jsx"
    assert smoke_tab.exists()
    smoke_page = smoke_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: smoke_page.count(snippet) for snippet in disallowed_snippets if snippet in smoke_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "border-status-danger",
        "bg-status-info-bg",
        "text-status-info-text",
        "bg-status-success-bg",
        "text-status-success-text",
    ]:
        assert semantic_class in smoke_page


def test_workers_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Workers */}")
    end = app.index("{/* Tab: Approvals */}", start)
    workers_mount = app[start:end]

    assert "<WorkersTab" in workers_mount
    assert "workerJobs.map" not in workers_mount
    assert "workerRoles.map" not in workers_mount

    workers_tab = root / "frontend" / "src" / "components" / "tabs" / "WorkersTab.jsx"
    assert workers_tab.exists()
    workers_page = workers_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
    ]
    assert {snippet: workers_page.count(snippet) for snippet in disallowed_snippets if snippet in workers_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "shadow-button",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "bg-status-info-bg",
        "text-status-info-text",
    ]:
        assert semantic_class in workers_page


def test_approvals_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Approvals */}")
    end = app.index("{/* Tab: Messages */}", start)
    approvals_mount = app[start:end]

    assert "<ApprovalsTab" in approvals_mount
    assert "approvalReviews.map" not in approvals_mount

    approvals_tab = root / "frontend" / "src" / "components" / "tabs" / "ApprovalsTab.jsx"
    assert approvals_tab.exists()
    approvals_page = approvals_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
        "bg-[#",
        "border-[#",
    ]
    assert {snippet: approvals_page.count(snippet) for snippet in disallowed_snippets if snippet in approvals_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "shadow-button",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "bg-status-info-bg",
        "text-status-info-text",
    ]:
        assert semantic_class in approvals_page


def test_dev_loop_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: Dev Loop */}")
    end = app.index("{/* Tab: System */}", start)
    dev_loop_mount = app[start:end]

    assert "<DevLoopTab" in dev_loop_mount
    assert "devLoopData.pull_requests.map" not in dev_loop_mount
    assert "devLoopData.worktrees.map" not in dev_loop_mount

    dev_loop_tab = root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx"
    assert dev_loop_tab.exists()
    dev_loop_page = dev_loop_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
        "bg-[#",
        "border-[#",
    ]
    assert {snippet: dev_loop_page.count(snippet) for snippet in disallowed_snippets if snippet in dev_loop_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
        "shadow-button",
        "bg-status-success-bg",
        "text-status-success-text",
        "bg-status-warning-bg",
        "text-status-warning-text",
        "bg-status-danger-bg",
        "text-status-danger-text",
        "bg-status-info-bg",
        "text-status-info-text",
    ]:
        assert semantic_class in dev_loop_page


def test_system_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    start = app.index("{/* Tab: System */}")
    end = app.index("</>", start)
    system_mount = app[start:end]

    assert "<SystemTab" in system_mount
    assert "startContext.task_backend?.sync?.join" not in system_mount

    system_tab = root / "frontend" / "src" / "components" / "tabs" / "SystemTab.jsx"
    assert system_tab.exists()
    system_page = system_tab.read_text(encoding="utf-8")

    disallowed_snippets = [
        "bg-white",
        "bg-zinc",
        "border-zinc",
        "text-zinc",
        "dark:",
        "text-[11px]",
        "text-[10px]",
        "text-[9px]",
        "text-[8px]",
        "shadow-[",
        "bg-[#",
        "border-[#",
    ]
    assert {snippet: system_page.count(snippet) for snippet in disallowed_snippets if snippet in system_page} == {}

    for semantic_class in [
        "bg-surface-raised",
        "bg-surface-sunken",
        "border-border-subtle",
        "text-text-primary",
        "text-text-secondary",
        "text-text-muted",
        "text-2xs",
        "shadow-card",
    ]:
        assert semantic_class in system_page


def test_frontend_contract_uses_i18n_for_dashboard_chrome():
    app = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

    assert "LANGUAGE_STORAGE_KEY" in app
    assert "normalizeLanguagePreference" in app
    assert "resolveLanguage" in app
    assert "translate(language" in app or "translate(resolvedLanguage" in app
    assert "languagePreference" in app
    assert "setLanguagePreference" in app
    assert "navGroups" in app
    assert "t('nav.groups.work')" in app
    assert "t('nav.tabs.overview')" in app
    assert "t('chrome.dashboard')" in app
    assert "t('approvalBanner.review')" in app
    assert "t('chrome.loading')" in app
    assert "language.useAuto" in app
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in app


def test_frontend_i18n_locale_files_exist_and_define_core_chrome_keys():
    root = Path(__file__).resolve().parents[1]
    en = root / "frontend" / "src" / "i18n" / "locales" / "en.js"
    zh = root / "frontend" / "src" / "i18n" / "locales" / "zh-CN.js"

    assert en.exists()
    assert zh.exists()

    en_text = en.read_text(encoding="utf-8")
    zh_text = zh.read_text(encoding="utf-8")
    for key in [
        "nav.groups.work",
        "nav.tabs.overview",
        "nav.tabs.approvals",
        "chrome.dashboard",
        "chrome.exit",
        "chrome.runId",
        "language.auto",
        "language.english",
        "language.chinese",
        "theme.toggle",
        "approvalBanner.review",
    ]:
        assert key in en_text
        assert key in zh_text


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
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
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
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
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

    assert notifications == [
        {
            "project_key": "CONTENTOPS",
            "run_id": payload["run_id"],
            "gate": "takeover",
            "artifact_path": payload["artifacts"]["takeover"],
            "summary": "GoodTeam is waiting for TAKEOVER approval on CONTENTOPS.",
        }
    ]


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
    assert payload["role_models"]["planner"]["primary"] == "codex:gpt-5.5"
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
    assert payload["current_node"] == "staging_review_gate"
    assert payload["next_node"] == "await_staging_approval"
    for artifact in ["review_report", "task_breakdown", "ui_spec", "implementation_handoff", "qa_report", "code_review", "staging_report"]:
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "staging": "pending"})
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

    assert payload["current_node"] == "implementation"
    assert payload["next_node"] == "blocked"
    assert payload["blockers"] == ["staging deploy failed"]
    assert "staging_report" in payload["artifacts"]


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
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "pending"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "pending"})
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
        assert persisted.role_models["implementation"]["primary"] == "codex:gpt-5.3-codex"
        assert persisted.role_models["qa"]["primary"] == "codex:gpt-5.3-codex"
        state.current_node = "release_review_gate"
        state.next_node = "await_release_approval"
        return state

    monkeypatch.setattr("goodteam_os.web.advance_approved_workflow", fake_advance_approved_workflow)

    payload = retry_blocked_workflow(workspace=tmp_path, project_key="CONTENTOPS")

    assert payload["role_models"]["implementation"]["primary"] == "codex:gpt-5.3-codex"
    assert payload["role_models"]["qa"]["primary"] == "codex:gpt-5.3-codex"


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
    state.artifacts["takeover"] = str(tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md")
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


def test_render_project_overview_includes_delete_project_action(tmp_path):
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\n", encoding="utf-8")

    html = render_project_overview(tmp_path)

    assert 'class="danger delete-project"' in html
    assert 'data-project="CONTENTOPS"' in html
    assert "Delete project" in html
    assert "/project-delete" in html


def test_render_dashboard_shows_deployment_backend_summary(tmp_path):
    project_config = tmp_path / "real-project.local.yaml"
    project_config.write_text(
        "\n".join(
            [
                "name: Real Product",
                "key: REAL",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: real-staging",
                "  staging_remote_dir: /srv/real",
                "  staging_public_url: https://staging.real.example.com",
                "  staging_deploy_command: docker compose up -d",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "REAL", "Deploy real product")
    state.current_node = "staging_review_gate"
    state.next_node = "await_staging_approval"
    context = build_start_console_context(workspace=tmp_path, model_config=Path("models.yaml"), project_config=project_config)

    html = render_dashboard(state, context)

    assert "Deployment Backend" in html
    assert "ssh_compose" in html
    assert "real-staging" in html
    assert "/srv/real" in html


def test_render_dashboard_shows_delivery_contract_summary(tmp_path):
    project_config = tmp_path / "projects" / "real.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: Real Product",
                "key: REAL",
                "deployment_backend:",
                "  mode: ssh_compose",
                "  staging_ssh_target: real-staging",
                "  staging_remote_dir: /srv/real",
                "  staging_deploy_command: docker compose up -d",
                "  staging_healthcheck_command: curl -fsS http://127.0.0.1:8080/health",
                "delivery_contract:",
                "  required: true",
                "  minimum_evidence: functional",
                "  staging_acceptance:",
                "    critical-user-journey:",
                "      type: local_command",
                "      command: ./scripts/e2e-critical.sh",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "REAL", "Ship the real product")
    state.source_ref = str(project_config)
    state.source_type = "existing_project"
    state.current_node = "staging_review_gate"
    state.next_node = "await_staging_approval"

    context = build_start_console_context(
        workspace=tmp_path,
        model_config=Path("models.yaml"),
        project_config=project_config,
    )
    html = render_dashboard(state, start_context=context)

    assert "Delivery Contract" in html
    assert "functional" in html
    assert "Acceptance Checks" in html
    assert "1" in html
    assert "Evidence Level" in html


def test_render_project_overview_does_not_link_to_missing_gto_project(tmp_path):
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text("name: AI Content Studio\nkey: CONTENTOPS\n", encoding="utf-8")

    html = render_project_overview(tmp_path)

    assert 'href="/?project=GTO"' not in html
