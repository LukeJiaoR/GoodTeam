import json
import inspect
import threading
import urllib.error
import urllib.request
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import goodteam_os.web as web
from goodteam_os.messages import append_message
from goodteam_os.staging import delivery_contract_config
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore
from goodteam_os.web import (
    build_approval_evidence_snapshot,
    build_approval_recommendation,
    build_action_items,
    build_agile_board,
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


def _post_web_json(workspace: Path, route: str, payload: dict[str, object], *, origin: str | None = None):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.build_dashboard_handler(workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
            return exc.code, json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    assert "gpt-5.4" in html
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
    worker_hook = (root / "frontend" / "src" / "hooks" / "useWorkerBackendSettings.js").read_text(encoding="utf-8")

    assert 'MODEL_UNAVAILABLE_BACKENDS = ["fake", "noop", "human"]' in workers_constants
    assert "Model is not applicable for Antigravity" not in app
    assert "export function canonicalBackendForOption" in workers_constants
    assert "import {" in worker_hook and "canonicalBackendForOption" in worker_hook


def test_frontend_contract_exposes_open_questions_at_approval_gate():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    approval_utils = (root / "frontend" / "src" / "utils" / "approvals.js").read_text(encoding="utf-8")
    approval_metrics = (root / "frontend" / "src" / "utils" / "approvalMetrics.js").read_text(encoding="utf-8")

    assert "openQuestionsGateCount" in approval_metrics
    assert "Questions" in router
    assert "handleOpenQuestionsFromApproval" in app
    assert "export function suggestedAnswersForQuestion" in approval_utils
    assert "suggestedAnswersForQuestion={suggestedAnswersForQuestion}" in router


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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
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
    assert "import { FALLBACK_AGENT_MODES, WORKER_ROUTING_BUCKETS } from '../constants/workers';" in router
    assert "const FALLBACK_AGENT_MODES" not in app


def test_frontend_contract_exposes_release_readiness_without_execution_controls():
    root = Path(__file__).resolve().parents[1]
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    release_card = (root / "frontend" / "src" / "components" / "ReleaseReadinessCard.jsx").read_text(
        encoding="utf-8"
    )
    server_actions_card = (
        root / "frontend" / "src" / "components" / "ServerActionsCard.jsx"
    ).read_text(encoding="utf-8")

    assert "<DevLoopTab" in router
    assert "copyLandCommand={copyCommand}" in router
    assert "devLoopData.release_readiness" in dev_loop_tab
    assert "devLoopData.server_actions" in dev_loop_tab
    assert "Release Readiness" in release_card
    assert "Server Action Capabilities" in server_actions_card
    assert "Destructive actions stay human-command-only" in server_actions_card
    assert "release_up_command" in release_card
    assert "graduate_command" in release_card
    assert "Copy release-up command" in release_card
    assert "Copy graduate command" in release_card
    assert "copyLandCommand(command)" in release_card
    assert "devLoopData.release_readiness?.available === true" in dev_loop_tab
    assert "Release Readiness unavailable" not in dev_loop_tab + release_card
    assert "Run release-up" not in dev_loop_tab + release_card
    assert "Run graduate" not in dev_loop_tab + release_card
    assert "Run land" not in dev_loop_tab
    assert "Run tag" not in dev_loop_tab


def test_server_actions_card_is_extracted_from_dev_loop_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    server_actions_card = root / "frontend" / "src" / "components" / "ServerActionsCard.jsx"

    assert server_actions_card.exists()
    card_source = server_actions_card.read_text(encoding="utf-8")
    assert "export default function ServerActionsCard" in card_source
    assert "Server Action Capabilities" in card_source
    assert "Read-only capability report. Destructive actions stay human-command-only." in card_source
    assert "serverActions?.actions" in card_source
    assert "name.replaceAll('_', ' ')" in card_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4" in card_source
    assert "bg-status-success-bg text-status-success-text border-status-success/30" in card_source
    assert "bg-surface-raised text-text-muted border-border-subtle" in card_source
    assert "function ServerActionsCard" not in dev_loop_tab
    assert "import ServerActionsCard from '../ServerActionsCard';" in dev_loop_tab
    assert "<ServerActionsCard serverActions={devLoopData.server_actions} />" in dev_loop_tab


def test_dev_loop_warnings_panel_is_extracted_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    warnings_panel = root / "frontend" / "src" / "components" / "DevLoopWarningsPanel.jsx"

    assert warnings_panel.exists()
    panel_source = warnings_panel.read_text(encoding="utf-8")
    assert "export default function DevLoopWarningsPanel" in panel_source
    assert "System Warnings" in panel_source
    assert "Array.isArray(warnings)" in panel_source
    assert "bg-status-warning-bg border-2 border-status-warning/30" in panel_source
    assert "text-status-warning-text" in panel_source
    assert "AlertTriangle" in panel_source
    assert "function WarningsPanel" not in dev_loop_tab
    assert "import DevLoopWarningsPanel from '../DevLoopWarningsPanel';" in dev_loop_tab
    assert "<DevLoopWarningsPanel warnings={devLoopData.warnings} />" in dev_loop_tab


def test_dev_loop_worktrees_panel_is_extracted_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    worktrees_panel = root / "frontend" / "src" / "components" / "DevLoopWorktreesPanel.jsx"

    assert worktrees_panel.exists()
    panel_source = worktrees_panel.read_text(encoding="utf-8")
    assert "export default function DevLoopWorktreesPanel" in panel_source
    assert "Active Worktrees" in panel_source
    assert "Array.isArray(worktrees)" in panel_source
    assert "No active git worktrees." in panel_source
    assert "FilePath path={worktree?.path} segments={2}" in panel_source
    assert "worktree.head.slice(0, 7)" in panel_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl overflow-hidden" in panel_source
    assert "bg-surface-sunken border-b-2 border-border-subtle" in panel_source
    assert "function WorktreesPanel" not in dev_loop_tab
    assert "import DevLoopWorktreesPanel from '../DevLoopWorktreesPanel';" in dev_loop_tab
    assert "<DevLoopWorktreesPanel worktrees={devLoopData.worktrees} FilePath={FilePath} />" in dev_loop_tab


def test_dev_loop_pull_requests_panel_is_extracted_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    pull_requests_panel = root / "frontend" / "src" / "components" / "DevLoopPullRequestsPanel.jsx"

    assert pull_requests_panel.exists()
    panel_source = pull_requests_panel.read_text(encoding="utf-8")
    assert "export default function DevLoopPullRequestsPanel" in panel_source
    assert "function CommandCopyButton" in panel_source
    assert "Open PRs & CI Status" in panel_source
    assert "Array.isArray(devLoopData.pull_requests)" in panel_source
    assert "Copy land command" in panel_source
    assert "Ready to land" in panel_source
    assert "pre-reviewed" in panel_source
    assert "No open pull requests." in panel_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5" in panel_source
    assert "bg-status-success-bg text-status-success-text border-status-success/30" in panel_source
    assert "bg-status-info-bg text-status-info-text border-status-info/30" in panel_source
    assert "function PullRequestsPanel" not in dev_loop_tab
    assert "function CommandCopyButton" not in dev_loop_tab
    assert "import DevLoopPullRequestsPanel from '../DevLoopPullRequestsPanel';" in dev_loop_tab
    assert "<DevLoopPullRequestsPanel" in dev_loop_tab


def test_dev_loop_dispatch_form_is_extracted_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    dispatch_form = root / "frontend" / "src" / "components" / "DevLoopDispatchForm.jsx"

    assert dispatch_form.exists()
    form_source = dispatch_form.read_text(encoding="utf-8")
    assert "export default function DevLoopDispatchForm" in form_source
    assert "Dispatch a Track" in form_source
    assert "Track started asynchronously!" in form_source
    assert "Track Name" in form_source
    assert "Custom Role Name" in form_source
    assert "Custom Adapter Name" in form_source
    assert "Task Details" in form_source
    assert "Verification Command (Optional)" in form_source
    assert "Dispatching..." in form_source
    assert "const textInputClass" in form_source
    assert "const textAreaClass" in form_source
    assert "visibleRoles" in form_source
    assert "roleBackendOptions.map" in form_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card p-5 rounded-2xl space-y-4" in form_source
    assert "bg-status-success-bg border-2 border-status-success/30" in form_source
    assert "bg-status-danger-bg border-2 border-status-danger/30" in form_source
    assert "function DispatchForm" not in dev_loop_tab
    assert "const textInputClass" not in dev_loop_tab
    assert "const textAreaClass" not in dev_loop_tab
    assert "import DevLoopDispatchForm from '../DevLoopDispatchForm';" in dev_loop_tab
    assert "<DevLoopDispatchForm" in dev_loop_tab


def test_dev_loop_git_init_panel_is_extracted_and_wired_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(encoding="utf-8")
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    git_init_hook = (root / "frontend" / "src" / "hooks" / "useGitInitAction.js").read_text(encoding="utf-8")
    git_init_panel = root / "frontend" / "src" / "components" / "DevLoopGitInitPanel.jsx"

    assert git_init_panel.exists()
    panel_source = git_init_panel.read_text(encoding="utf-8")
    assert "export default function DevLoopGitInitPanel" in panel_source
    assert "Initialize Git repository" in panel_source
    assert "Create GitHub repository & push" in panel_source
    assert "Repository Visibility" in panel_source
    assert "Warnings during Git Init" in panel_source
    assert "GITHUB_TOKEN" in panel_source
    assert "remotePrivate" in panel_source
    assert "setRemotePrivate" in panel_source
    assert "createRemote" in panel_source
    assert "setCreateRemote" in panel_source
    assert "onInitializeGit" in panel_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card" in panel_source
    assert "bg-status-warning-bg border-2 border-status-warning/30" in panel_source
    assert "bg-accent border-2 border-accent-text shadow-button" in panel_source
    assert "text-2xs" in panel_source

    assert "import DevLoopGitInitPanel from '../DevLoopGitInitPanel';" in dev_loop_tab
    assert "<DevLoopGitInitPanel" in dev_loop_tab
    assert "devLoopData.is_git_repo === false" in dev_loop_tab
    assert "gitInitWarnings={gitInitWarnings}" in dev_loop_tab
    assert "onInitializeGit={handleGitInit}" in dev_loop_tab

    for source in [app, dashboard, router]:
        assert "handleGitInit" in source
        assert "gitInitLoading" in source
        assert "gitInitWarnings" in source
        assert "devLoopCreateRemote" in source
        assert "devLoopRemotePrivate" in source

    assert "import { useGitInitAction } from './hooks/useGitInitAction';" in app
    assert "useGitInitAction({" in app
    assert "projectKey," in app
    assert "fetchDevLoop," in app
    assert "showToast," in app
    assert "/api/git-init" in git_init_hook
    assert "project_key: projectKey" in git_init_hook
    assert "create_remote: devLoopCreateRemote" in git_init_hook
    assert "remote_private: devLoopRemotePrivate" in git_init_hook
    assert "fetchDevLoop(projectKey)" in git_init_hook
    assert "showToast('Git repository initialized successfully!')" in git_init_hook

    assert "This project isn't a Git repository yet" not in app
    assert "Create GitHub repository & push" not in app


def test_release_readiness_card_is_extracted_from_dev_loop_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    release_card = root / "frontend" / "src" / "components" / "ReleaseReadinessCard.jsx"

    assert release_card.exists()
    release_card_source = release_card.read_text(encoding="utf-8")
    assert "export default function ReleaseReadinessCard" in release_card_source
    assert "Release Readiness" in release_card_source
    assert "release_up_command" in release_card_source
    assert "graduate_command" in release_card_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4" in release_card_source
    assert "bg-status-success-bg text-status-success-text border-status-success/30" in release_card_source
    assert "bg-status-info-bg text-status-info-text border-status-info/30" in release_card_source
    assert "function ReleaseReadinessCard" not in dev_loop_tab
    assert "import ReleaseReadinessCard from '../ReleaseReadinessCard';" in dev_loop_tab
    assert "releaseReadiness={devLoopData.release_readiness}" in dev_loop_tab


def test_frontend_contract_exposes_server_action_audit_without_execution_controls():
    root = Path(__file__).resolve().parents[1]
    dev_loop_hook = (root / "frontend" / "src" / "hooks" / "useDevLoopData.js").read_text(encoding="utf-8")
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    audit_card = (root / "frontend" / "src" / "components" / "ServerActionAuditCard.jsx").read_text(
        encoding="utf-8"
    )

    assert "/api/server-actions/audit" in dev_loop_hook
    assert "server_action_audit" in dev_loop_hook
    assert "server_action_audit" in dev_loop_tab
    assert "Server Action Audit" in audit_card
    assert "audit.records" in audit_card
    assert "audit.warnings" in audit_card
    assert "returncode" in audit_card
    assert "stdout_tail" in audit_card
    assert "Run audit" not in dev_loop_tab + audit_card
    assert "Retry audit" not in dev_loop_tab + audit_card
    assert "POST /api/server-actions/audit" not in dev_loop_hook


def test_server_action_audit_card_is_extracted_from_dev_loop_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    dev_loop_tab = (root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx").read_text(
        encoding="utf-8"
    )
    audit_card = root / "frontend" / "src" / "components" / "ServerActionAuditCard.jsx"

    assert audit_card.exists()
    audit_card_source = audit_card.read_text(encoding="utf-8")
    assert "export default function ServerActionAuditCard" in audit_card_source
    assert "Server Action Audit" in audit_card_source
    assert "audit.records" in audit_card_source
    assert "audit.warnings" in audit_card_source
    assert "bg-surface-raised border-2 border-border-subtle shadow-card rounded-2xl p-5 space-y-4" in audit_card_source
    assert "bg-status-danger-bg text-status-danger-text border-status-danger/30" in audit_card_source
    assert "bg-status-success-bg text-status-success-text border-status-success/30" in audit_card_source
    assert "function ServerActionAuditCard" not in dev_loop_tab
    assert "import ServerActionAuditCard from '../ServerActionAuditCard';" in dev_loop_tab
    assert "<ServerActionAuditCard audit={devLoopData.server_action_audit} />" in dev_loop_tab


def test_frontend_contract_uses_theme_tokens_for_common_palette_and_small_type():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    shell_sources = "\n".join(
        [
            app,
            (root / "frontend" / "src" / "components" / "ApprovalBanner.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "GlobalToast.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "GitCreationNotice.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "MemoryEventDetailModal.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "ProjectMainPanel.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "ProjectHeader.jsx").read_text(encoding="utf-8"),
            (root / "frontend" / "src" / "components" / "ProjectSidebar.jsx").read_text(encoding="utf-8"),
        ]
    )

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
    assert {token: shell_sources.count(token) for token in disallowed_palette_tokens if token in shell_sources} == {}
    assert "text-[10px]" not in shell_sources
    assert "text-[9px]" not in shell_sources
    assert "text-[8px]" not in shell_sources

    for semantic_class in [
        "border-border-subtle",
        "text-text-muted",
        "bg-surface-sunken",
        "text-accent-text",
        "bg-status-danger-bg",
        "text-2xs",
    ]:
        assert semantic_class in shell_sources


def test_file_path_component_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    file_path_component = root / "frontend" / "src" / "components" / "FilePath.jsx"

    assert file_path_component.exists()
    component_source = file_path_component.read_text(encoding="utf-8")
    assert "export default function FilePath" in component_source
    assert "segments = 1" in component_source
    assert "not available" in component_source
    assert "bottom-full" in component_source
    assert "function FilePath" not in app
    assert "import FilePath from './FilePath';" in router


def test_spinner_component_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    spinner_component = root / "frontend" / "src" / "components" / "Spinner.jsx"

    assert spinner_component.exists()
    component_source = spinner_component.read_text(encoding="utf-8")
    assert "export default function Spinner" in component_source
    assert "Loading..." in component_source
    assert "RefreshCw" in component_source
    assert "sr-only" in component_source
    assert "function Spinner" not in app
    assert "import Spinner from './Spinner';" in router


def test_frontend_contract_approvals_tab_audit_and_evidence():
    root = Path(__file__).resolve().parents[1]
    approvals_tab = (root / "frontend" / "src" / "components" / "tabs" / "ApprovalsTab.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(encoding="utf-8")

    # Assert ApprovalsTab is mounted in ProjectTabRouter with actionItems prop
    assert "actionItems={actionItems}" in router

    # Assert ApprovalsTab filters actionItems for approval_decided
    assert "action === 'approval_decided'" in approvals_tab or 'action === "approval_decided"' in approvals_tab

    # Assert ApprovalsTab renders audit panel and origin badges
    assert "Approval Audit Trail" in approvals_tab
    assert "getOriginBadgeStyle" in approvals_tab

    # Assert ApprovalsTab renders evidence matrix and missing-evidence callouts
    assert "Evidence Matrix" in approvals_tab
    assert "MISSING-EVIDENCE" in approvals_tab

    # Assert Submit Request is disabled on empty note
    assert "changesRequestedNote.trim() === ''" in approvals_tab


def test_frontend_contract_approvals_tab_recommendation_and_provenance():
    root = Path(__file__).resolve().parents[1]
    approvals_tab = (root / "frontend" / "src" / "components" / "tabs" / "ApprovalsTab.jsx").read_text(encoding="utf-8")

    # Assert Recommendation card and eyebrow are rendered when recommendation is present
    assert "selectedReview.recommendation" in approvals_tab
    assert "Recommendation · you decide" in approvals_tab

    # Assert Recommendation card badge is styled appropriately based on verdict
    assert "getVerdictBadgeStyle(selectedReview.recommendation.verdict)" in approvals_tab

    # Assert confidence and blockers are rendered
    assert "selectedReview.recommendation.confidence" in approvals_tab
    assert "selectedReview.recommendation.blocking_count" in approvals_tab

    # Assert reasons list is grouped by kind
    assert "selectedReview.recommendation.reasons" in approvals_tab
    assert "kindLabels" in approvals_tab
    assert "blocker" in approvals_tab
    assert "missing_evidence" in approvals_tab
    assert "open_objection" in approvals_tab
    assert "clean" in approvals_tab

    # Assert Provenance section is rendered when provenance_refs are present
    assert "provenanceRefs" in approvals_tab
    assert "selectedReview.provenance_refs" in approvals_tab
    assert "evidenceSnapshot?.provenance_refs" in approvals_tab
    assert "uniquePaths" in approvals_tab
    assert "<FilePath path={path}" in approvals_tab

    # Assert both degrade cleanly (sections omitted when missing)
    assert "recommendation && (" in approvals_tab
    assert "provenanceRefs.length === 0" in approvals_tab


def test_status_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    status_utils = root / "frontend" / "src" / "utils" / "status.jsx"

    assert status_utils.exists()
    utils_source = status_utils.read_text(encoding="utf-8")
    assert "export function getBadgeStyle" in utils_source
    assert "export function getStatusLabel" in utils_source
    assert "bg-status-success-bg" in utils_source
    assert "chrome.noActiveRun" in utils_source
    assert "function getBadgeStyle" not in app
    assert "function getStatusLabel" not in app
    assert "import { getBadgeStyle, getStatusLabel } from '../utils/status';" in dashboard


def test_time_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    time_utils = root / "frontend" / "src" / "utils" / "time.js"

    assert time_utils.exists()
    utils_source = time_utils.read_text(encoding="utf-8")
    assert "export function formatHumanTime" in utils_source
    assert "May 27, 02:44" in utils_source
    assert "padStart(2, '0')" in utils_source
    assert "function formatHumanTime" not in app
    assert "import { formatHumanTime } from '../utils/time';" in router


def test_mission_summary_helper_is_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    mission_utils = root / "frontend" / "src" / "utils" / "missionSummary.js"

    assert mission_utils.exists()
    utils_source = mission_utils.read_text(encoding="utf-8")
    assert "export function getMissionSummary" in utils_source
    assert "if (!text) return '';" in utils_source
    assert "text.match(/[^.!?]+[.!?]+/g) || [text]" in utils_source
    assert "sentences.length <= 2" in utils_source
    assert "sentences.slice(0, 2).join(' ')" in utils_source
    assert "import { getMissionSummary } from './utils/missionSummary';" in app
    assert "const getMissionSummary = (text) =>" not in app


def test_role_model_override_helpers_are_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    override_utils = root / "frontend" / "src" / "utils" / "roleModelOverrides.js"

    assert override_utils.exists()
    utils_source = override_utils.read_text(encoding="utf-8")
    assert "export function parseRoleModelOverridesMap" in utils_source
    assert "export function parseRoleModelOverrideLines" in utils_source
    assert "export function formatRoleModelOverridesText" in utils_source
    assert ".split('\\n')" in utils_source
    assert ".map((line) => line.trim())" in utils_source
    assert ".filter(Boolean)" in utils_source
    assert ".map((line) => line.split('=', 2))" in utils_source
    assert ".filter((parts) => parts.length === 2 && parts[0] && parts[1])" in utils_source
    assert "Object.entries(roleModelOverrides)" in utils_source
    assert "`${role}=${model}`" in utils_source
    assert "Object.fromEntries" in utils_source
    assert "from './utils/roleModelOverrides';" in app
    assert "formatRoleModelOverridesText" in app
    assert "parseRoleModelOverrideLines" in app
    assert "parseRoleModelOverridesMap" in app
    assert "parseRoleModelOverridesMap(roleModelOverrides)" in app
    assert "parseRoleModelOverrideLines(roleModelOverrides)" in app
    assert "setRoleModelOverrides(formatRoleModelOverridesText(sc.role_model_overrides));" in app
    assert "const parsedOverrides = Object.fromEntries" not in app
    assert "const overrides = roleModelOverrides" not in app
    assert "Object.entries(sc.role_model_overrides).map" not in app


def test_frontend_contract_renders_init_backend_model_buckets():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = (root / "frontend" / "src" / "hooks" / "useNewProjectForm.js").read_text(encoding="utf-8")
    modal = (root / "frontend" / "src" / "components" / "NewProjectModal.jsx").read_text(encoding="utf-8")
    container = (root / "frontend" / "src" / "components" / "NewProjectModalContainer.jsx").read_text(encoding="utf-8")
    helpers = (root / "frontend" / "src" / "utils" / "roleModelOverrides.js").read_text(encoding="utf-8")
    workers = (root / "frontend" / "src" / "constants" / "workers.js").read_text(encoding="utf-8")

    assert "INIT_WORKER_ROUTING_BUCKET_KEYS" in workers
    assert "opencode" in workers
    assert "initWorkerBucketSelections" in hook
    assert "setInitWorkerBucketSelections({})" in hook
    assert "expandInitBucketWorkerOverrides" in helpers
    assert "expandInitBucketModelOverrides" in helpers
    assert "selection.model === '__custom__'" in helpers
    assert "initBucketWorkers" in app
    assert "initBucketRoleModels" in app
    assert "action: 'update_worker_backends'" in app
    assert "fetchDiscoveredAgents();" in app
    assert "initWorkerRoutingBuckets" in container
    assert "initWorkerRoutingBuckets" in modal
    assert "newProject.workerBuckets.title" in modal
    assert "newProject.advanced.roleModelOverrides" in modal


def test_custom_delivery_payload_helper_is_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    delivery_utils = root / "frontend" / "src" / "utils" / "deliveryPayload.js"

    assert delivery_utils.exists()
    utils_source = delivery_utils.read_text(encoding="utf-8")
    assert "export function buildCustomDeliveryPayload" in utils_source
    assert "deliveryMode === 'custom'" in utils_source
    assert "{ delivery_standards: deliveryStandards }" in utils_source
    assert "return {};" in utils_source
    assert "import { buildCustomDeliveryPayload } from './utils/deliveryPayload';" in app
    assert "buildCustomDeliveryPayload(deliveryMode, deliveryStandards)" in app
    assert "const customDeliveryPayload = deliveryMode === 'custom'" not in app


def test_delivery_contract_formatters_are_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    delivery_contract_utils = root / "frontend" / "src" / "utils" / "deliveryContract.js"

    assert delivery_contract_utils.exists()
    utils_source = delivery_contract_utils.read_text(encoding="utf-8")
    assert "export function formatDeliveryContractFatalPatterns" in utils_source
    assert "export function formatDeliveryContractAcceptance" in utils_source
    assert "Object.values(fatalOutputPatterns).join('\\n')" in utils_source
    assert "Object.entries(stagingAcceptance)" in utils_source
    assert "`${id}|${item.type}|${item.command}`" in utils_source
    assert ".join('\\n')" in utils_source
    assert (
        "import { formatDeliveryContractAcceptance, formatDeliveryContractFatalPatterns } "
        "from './utils/deliveryContract';"
    ) in app
    assert (
        "formatDeliveryContractFatalPatterns(sc.project_report?.project?.delivery_contract?.fatal_output_patterns || {})"
        in app
    )
    assert (
        "formatDeliveryContractAcceptance(sc.project_report?.project?.delivery_contract?.staging_acceptance || {})"
        in app
    )
    assert "Object.values(fp).join('\\n')" not in app
    assert "Object.entries(sa).map" not in app


def test_project_root_resolver_is_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    project_utils = root / "frontend" / "src" / "utils" / "projectRoot.js"

    assert project_utils.exists()
    utils_source = project_utils.read_text(encoding="utf-8")
    assert "export function resolveProjectRootFromRepos" in utils_source
    assert "repos.root?.path" in utils_source
    assert "repos.docs?.path" in utils_source
    assert "repos.backend?.path" in utils_source
    assert "repos.frontend?.path" in utils_source
    assert "Object.values(repos)[0]?.path" in utils_source
    assert "|| ''" in utils_source
    assert "import { resolveProjectRootFromRepos } from './utils/projectRoot';" in app
    assert "setProjectRoot(resolveProjectRootFromRepos(repos));" in app
    assert (
        "setProjectRoot(repos.root?.path || repos.docs?.path || "
        "repos.backend?.path || repos.frontend?.path || Object.values(repos)[0]?.path || '');"
    ) not in app


def test_approval_helpers_are_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    approval_utils = root / "frontend" / "src" / "utils" / "approvals.js"

    assert approval_utils.exists()
    utils_source = approval_utils.read_text(encoding="utf-8")
    assert "export function suggestedAnswersForQuestion" in utils_source
    assert "export function computeLineDiff" in utils_source
    assert "optionPattern" in utils_source
    assert "new Int32Array" in utils_source
    assert "function suggestedAnswersForQuestion" not in app
    assert "function computeLineDiff" not in app
    assert "import { computeLineDiff, suggestedAnswersForQuestion } from '../utils/approvals';" in router


def test_approval_metrics_are_extracted_from_app_shell_without_behavior_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    approval_utils = root / "frontend" / "src" / "utils" / "approvalMetrics.js"

    assert approval_utils.exists()
    metrics_source = approval_utils.read_text(encoding="utf-8")
    assert "export function buildApprovalMetrics" in metrics_source
    assert "approvalReviews.filter(r => r.decision === 'approved').length" in metrics_source
    assert "approvalReviews.filter(r => r.is_actionable).length" in metrics_source
    assert "openQuestions.length" in metrics_source
    assert "approvalReviews.length || 6" in metrics_source
    assert "Math.min(100, Math.round((approvedCount / totalApprovalsCount) * 100))" in metrics_source
    assert "approvalBanner.questions" in metrics_source
    assert "plural: openQuestionsGateCount === 1 ? '' : 's'" in metrics_source
    assert "approvedCount" in metrics_source
    assert "actionableCount" in metrics_source
    assert "approvalProgressPercent" in metrics_source
    assert "approvalBannerQuestions" in metrics_source
    assert "import { buildApprovalMetrics } from '../utils/approvalMetrics';" in dashboard
    assert "buildApprovalMetrics({ approvalReviews, openQuestions, t })" in dashboard
    assert "const approvedCount = approvalReviews.filter" not in app
    assert "const actionableCount = approvalReviews.filter" not in app
    assert "approvalReviews.length || 6" not in app
    assert "approvalBanner.questions" not in app


def test_project_nav_groups_are_extracted_from_app_shell_without_chrome_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    navigation_utils = root / "frontend" / "src" / "utils" / "navigation.jsx"

    assert navigation_utils.exists()
    navigation_source = navigation_utils.read_text(encoding="utf-8")
    assert "export function buildProjectNavGroups" in navigation_source
    for icon_name in [
        "LayoutDashboard",
        "CheckCircle2",
        "FileCode",
        "Activity",
        "Kanban",
        "GitBranch",
        "GitPullRequest",
        "MessageSquare",
        "Users",
        "PlaySquare",
        "Settings",
    ]:
        assert icon_name in navigation_source

    for key in [
        "nav.groups.work",
        "nav.groups.observe",
        "nav.groups.inspect",
        "nav.groups.system",
        "nav.tabs.overview",
        "nav.tabs.approvals",
        "nav.tabs.artifacts",
        "nav.tabs.actions",
        "nav.tabs.board",
        "nav.tabs.workflow",
        "nav.tabs.devloop",
        "nav.tabs.messages",
        "nav.tabs.agentsMemory",
        "nav.tabs.smoke",
        "nav.tabs.system",
    ]:
        assert key in navigation_source

    assert "import { buildProjectNavGroups } from '../utils/navigation';" in dashboard
    assert "const navGroups = buildProjectNavGroups(t);" in dashboard
    assert "const navGroups = [" not in app
    assert "LayoutDashboard" not in app
    assert "nav.groups.work" not in app


def test_directory_picker_modal_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    picker = root / "frontend" / "src" / "components" / "DirectoryPickerModal.jsx"
    hook = root / "frontend" / "src" / "hooks" / "useDirectoryPicker.js"

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
    hook_source = hook.read_text(encoding="utf-8")
    assert "import DirectoryPickerModal from '../components/DirectoryPickerModal';" in hook_source
    assert "createElement(DirectoryPickerModal" in hook_source


def test_directory_picker_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useDirectoryPicker.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useDirectoryPicker" in hook_source
    assert "import DirectoryPickerModal from '../components/DirectoryPickerModal';" in hook_source
    assert "openDirectoryPicker" in hook_source
    assert "directoryPickerModal" in hook_source
    assert "import { useDirectoryPicker } from './hooks/useDirectoryPicker';" in app
    assert "const { directoryPickerModal, openDirectoryPicker } = useDirectoryPicker(setProjectRoot);" in app
    assert "const [pickerOpen" not in app
    assert "const [pickerInitialPath" not in app
    assert "const openDirectoryPicker" not in app
    assert "const directoryPickerModal" not in app
    assert "import DirectoryPickerModal from './components/DirectoryPickerModal';" not in app


def test_new_project_modal_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    modal = root / "frontend" / "src" / "components" / "NewProjectModal.jsx"
    container = root / "frontend" / "src" / "components" / "NewProjectModalContainer.jsx"

    assert modal.exists()
    modal_source = modal.read_text(encoding="utf-8")
    en_source = (root / "frontend" / "src" / "i18n" / "locales" / "en.js").read_text(encoding="utf-8")
    assert "export default function NewProjectModal" in modal_source
    assert "newProject.consoleEyebrow" in modal_source
    assert "newProject.title" in modal_source
    assert "newProject.projectLocation.title" in modal_source
    assert "newProject.launchCheck.title" in modal_source
    assert "Start Workflow Console" in en_source
    assert "Create or Adopt Project" in en_source
    assert "Project Location" in en_source
    assert "Launch Check" in en_source
    assert "DELIVERY_MODE_OPTIONS.map" in modal_source
    assert "Start Workflow Console" not in app
    assert "DELIVERY_MODE_OPTIONS.map" not in app
    assert container.exists()
    container_source = container.read_text(encoding="utf-8")
    assert "export default function NewProjectModalContainer" in container_source
    assert "import NewProjectModal from './NewProjectModal';" in container_source
    assert "<NewProjectModal" in container_source
    assert "newProjectModalOpen" in container_source
    assert "onClose={closeNewProjectModal}" in container_source
    assert "handleCreateConfig={handleCreateConfig}" in container_source
    assert "handleStartWorkflow={handleStartWorkflow}" in container_source
    assert "import NewProjectModalContainer from './components/NewProjectModalContainer';" in app
    assert "<NewProjectModalContainer" in app
    assert "import NewProjectModal from './components/NewProjectModal';" not in app
    assert "<NewProjectModal\n" not in app
    assert "<NewProjectModal " not in app
    assert "const newProjectModal = newProjectModalOpen &&" not in app


def test_new_project_form_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useNewProjectForm.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useNewProjectForm" in hook_source
    assert "openNewProjectModal" in hook_source
    assert "closeNewProjectModal" in hook_source
    assert "setInitGit(true)" in hook_source
    assert "setCreateRemote(false)" in hook_source
    assert "setRemotePrivate(true)" in hook_source
    assert "const isNewProjectMode = startMode === 'new_project' || startMode === 'new';" in hook_source
    assert "import { useNewProjectForm } from './hooks/useNewProjectForm';" in app
    assert "const newProjectForm = useNewProjectForm(setNewProjectModalOpen);" in app
    for state_name in [
        "projectName",
        "startProjectKey",
        "modelProfile",
        "deliveryMode",
        "projectRoot",
        "taskSync",
        "initGit",
        "planeApiHost",
        "stagingSshTarget",
        "deliveryContractRequired",
    ]:
        assert f"const [{state_name}" not in app


def test_dispatch_track_form_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useDispatchTrackForm.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useDispatchTrackForm" in hook_source
    assert "resetDispatchForm" in hook_source
    assert "setDispatchName('')" in hook_source
    assert "setDispatchTask('')" in hook_source
    assert "setDispatchConstraints('')" in hook_source
    assert "setDispatchVerify('')" in hook_source
    assert "import { useDispatchTrackForm } from './hooks/useDispatchTrackForm';" in app
    assert "const dispatchTrackForm = useDispatchTrackForm();" in app
    assert "resetDispatchForm();" in app
    for state_name in [
        "dispatchName",
        "dispatchRole",
        "dispatchCustomRole",
        "dispatchAdapter",
        "dispatchCustomAdapter",
        "dispatchTask",
        "dispatchConstraints",
        "dispatchVerify",
        "dispatchLoading",
        "dispatchError",
        "dispatchSuccess",
    ]:
        assert f"const [{state_name}" not in app


def test_clipboard_feedback_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useClipboardFeedback.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useClipboardFeedback" in hook_source
    assert "copyCommand" in hook_source
    assert "navigator.clipboard" in hook_source
    assert "document.execCommand('copy')" in hook_source
    assert "setTimeout" in hook_source
    assert "import { useClipboardFeedback } from './hooks/useClipboardFeedback';" in app
    assert "const { copiedText, copyFailedText, copyCommand } = useClipboardFeedback();" in app
    assert "const [copiedText" not in app
    assert "const [copyFailedText" not in app
    assert "const markLandCommandCopied" not in app
    assert "const markLandCommandCopyFailed" not in app
    assert "navigator.clipboard" not in app
    assert "document.execCommand('copy')" not in app


def test_workflow_graph_data_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useWorkflowGraphData.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useWorkflowGraphData" in hook_source
    assert "graphRequestSeq" in hook_source
    assert "/api/workflow/graph" in hook_source
    assert "fetchWorkflowGraph" in hook_source
    assert "import { useWorkflowGraphData } from './hooks/useWorkflowGraphData';" in app
    assert "const { graphData, loadingGraph, graphError, fetchWorkflowGraph } = useWorkflowGraphData();" in app
    assert "const [graphData" not in app
    assert "const [loadingGraph" not in app
    assert "const [graphError" not in app
    assert "const graphRequestSeq" not in app
    assert "const fetchWorkflowGraph = async" not in app
    assert "/api/workflow/graph" not in app


def test_dev_loop_data_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useDevLoopData.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useDevLoopData" in hook_source
    assert "devLoopRequestSeq" in hook_source
    assert "/api/devloop" in hook_source
    assert "fetchDevLoop" in hook_source
    assert "import { useDevLoopData } from './hooks/useDevLoopData';" in app
    assert "const { devLoopData, loadingDevLoop, devLoopError, fetchDevLoop } = useDevLoopData();" in app
    assert "const [devLoopData" not in app
    assert "const [loadingDevLoop" not in app
    assert "const [devLoopError" not in app
    assert "const devLoopRequestSeq" not in app
    assert "const fetchDevLoop = async" not in app
    assert "/api/devloop" not in app


def test_memory_data_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useMemoryData.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useMemoryData" in hook_source
    assert "memoryBrowseTimers" in hook_source
    assert "memoryBrowseRequestSeq" in hook_source
    assert "/api/memory?project=" in hook_source
    assert "/api/memory/event?" in hook_source
    assert "/api/memory/events?" in hook_source
    assert "fetchMemory" in hook_source
    assert "handleOpenMemoryEvent" in hook_source
    assert "handleToggleMemoryEvents" in hook_source
    assert "handleMemoryEventSearchChange" in hook_source
    assert "import { useMemoryData } from './hooks/useMemoryData';" in app
    assert "const memory = useMemoryData(projectKey);" in app
    for name in [
        "memoryData",
        "loadingMemory",
        "memoryError",
        "memoryEventDetail",
        "memoryEventBrowsers",
        "fetchMemory",
        "handleOpenMemoryEvent",
        "handleToggleMemoryEvents",
        "handleMemoryEventSearchChange",
    ]:
        assert f"{name}," in app
    assert "const [memoryData" not in app
    assert "const [loadingMemory" not in app
    assert "const [memoryError" not in app
    assert "const [memoryEventDetail" not in app
    assert "const [memoryEventBrowsers" not in app
    assert "const memoryBrowseTimers" not in app
    assert "const memoryBrowseRequestSeq" not in app
    assert "const fetchMemory = async" not in app
    assert "const handleOpenMemoryEvent = async" not in app
    assert "const fetchMemoryEvents = async" not in app
    assert "const handleToggleMemoryEvents" not in app
    assert "const handleMemoryEventSearchChange" not in app
    assert "/api/memory?project=" not in app
    assert "/api/memory/event?" not in app
    assert "/api/memory/events?" not in app


def test_cross_project_memory_search_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useCrossProjectMemorySearch.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useCrossProjectMemorySearch" in hook_source
    assert "crossSearchRequestSeq" in hook_source
    assert "setAllProjectKeys" in hook_source
    assert "/api/state" in hook_source
    assert "/api/memory/cross" in hook_source
    assert "handleCrossSearch" in hook_source
    assert "import { useCrossProjectMemorySearch } from './hooks/useCrossProjectMemorySearch';" in app
    assert "const crossMemorySearch = useCrossProjectMemorySearch(projectKey, activeTab, agentsMemorySubTab);" in app
    for name in [
        "crossRole",
        "setCrossRole",
        "crossQuery",
        "setCrossQuery",
        "crossScope",
        "setCrossScope",
        "crossLimit",
        "setCrossLimit",
        "crossLoading",
        "crossError",
        "crossResults",
        "handleCrossSearch",
    ]:
        assert f"{name}," in app
    for state_name in [
        "crossRole",
        "crossQuery",
        "crossScope",
        "crossLimit",
        "crossLoading",
        "crossError",
        "crossResults",
        "allProjectKeys",
    ]:
        assert f"const [{state_name}" not in app
    assert "const crossSearchRequestSeq" not in app
    assert "const handleCrossSearch = async" not in app
    assert "setAllProjectKeys" not in app
    assert "/api/memory/cross" not in app


def test_approval_editor_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useApprovalEditor.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useApprovalEditor" in hook_source
    for api_path in [
        "/api/artifact?project=",
        "/api/open-questions?",
        "/api/answer-open-questions",
        "/api/refine-artifact",
        "/api/accept-refinement",
        "/api/discard-refinement",
    ]:
        assert api_path in hook_source
        assert api_path not in app
    for hook_name in [
        "handleEditArtifact",
        "handleOpenQuestionsFromApproval",
        "handleSaveArtifact",
        "fetchOpenQuestions",
        "handleApplyAnswers",
        "handleRefineArtifact",
        "handleAcceptRefinement",
        "handleDiscardRefinement",
    ]:
        assert hook_name in hook_source
    assert "import { useApprovalEditor } from './hooks/useApprovalEditor';" in app
    assert "const approvalEditor = useApprovalEditor({" in app
    for state_name in [
        "editingArtifact",
        "editorContent",
        "refineLoading",
        "proposedRevision",
        "openQuestions",
        "openQuestionsAnswers",
        "fetchingOpenQuestions",
        "answeringOpenQuestions",
    ]:
        assert f"const [{state_name}" not in app
    assert "const handleEditArtifact = async" not in app
    assert "const handleOpenQuestionsFromApproval = async" not in app
    assert "const handleSaveArtifact = async" not in app
    assert "const fetchOpenQuestions = async" not in app
    assert "const handleApplyAnswers = async" not in app
    assert "const handleRefineArtifact = async" not in app
    assert "const handleAcceptRefinement = async" not in app
    assert "const handleDiscardRefinement = async" not in app


def test_worker_backend_settings_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useWorkerBackendSettings.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useWorkerBackendSettings" in hook_source
    assert "seedWorkerBackendSettings" in hook_source
    assert "safeDiscoveredAgents" in hook_source
    assert "discoveredAgentByBackend" in hook_source
    assert "/api/agents" in hook_source
    assert "/api/project-config" in hook_source
    assert "handleSaveWorkerBackends" in hook_source
    assert "import { useWorkerBackendSettings } from './hooks/useWorkerBackendSettings';" in app
    assert "const workerBackendSettings = useWorkerBackendSettings({" in app
    assert "seedWorkerBackendSettings" in app
    for state_name in [
        "panelGlobalMode",
        "panelRoleOverrides",
        "panelRoleModels",
        "workerRoutingMode",
        "simpleRoutingBackends",
        "customModelSelected",
        "configuringRoles",
        "backendsError",
        "backendsSuccess",
        "savingBackends",
        "discoveredAgents",
        "loadingDiscoveredAgents",
    ]:
        assert f"const [{state_name}" not in app
    for handler_name in [
        "handleAgentChange",
        "handleApplySimpleRouting",
        "handleModelDropdownChange",
        "handleCustomModelChange",
        "handleSaveWorkerBackends",
        "fetchDiscoveredAgents",
    ]:
        assert f"const {handler_name}" not in app
    assert "const safeDiscoveredAgents" not in app
    assert "const discoveredAgentByBackend" not in app
    assert "const getBackendOptionLabel" not in app
    assert "const usedBackends" not in app
    assert "/api/agents" not in app
    assert "workers: workers" not in app
    assert "role_models: role_models" not in app


def test_message_feeds_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useMessageFeeds.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useMessageFeeds" in hook_source
    assert "EMPTY_GATE_DEBATE" in hook_source
    assert "EMPTY_MESSAGE_FEED" in hook_source
    assert "/api/messages?" in hook_source
    assert "/api/debate" in hook_source
    assert "fetchGateDebate" in hook_source
    assert "fetchMessageFeed" in hook_source
    assert "handleRunDebate" in hook_source
    assert "getResolvingDebateRef" in hook_source
    assert "import { useMessageFeeds } from './hooks/useMessageFeeds';" in app
    assert "const messageFeeds = useMessageFeeds({" in app
    for state_name in [
        "gateDebate",
        "debateRun",
        "messageFeed",
    ]:
        assert f"const [{state_name}" not in app
    assert "const fetchGateDebate = async" not in app
    assert "const fetchMessageFeed = async" not in app
    assert "const handleRunDebate = async" not in app
    assert "const getResolvingDebateRef" not in app
    assert "EMPTY_GATE_DEBATE" not in app
    assert "EMPTY_MESSAGE_FEED" not in app
    assert "/api/messages?" not in app
    assert "/api/debate" not in app


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


def test_projects_home_page_does_not_render_empty_state_for_stale_project_data():
    root = Path(__file__).resolve().parents[1]
    page_source = (root / "frontend" / "src" / "components" / "ProjectsHomePage.jsx").read_text(encoding="utf-8")

    assert "const isStaleOverviewData = data && data.type !== 'overview';" in page_source
    assert "loading || isStaleOverviewData" in page_source


def test_overview_terminate_refreshes_project_overview_not_project_detail():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

    assert "projectKey === key ? key : ''" in app


def test_language_switcher_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    switcher = root / "frontend" / "src" / "components" / "LanguageSwitcher.jsx"

    assert switcher.exists()
    switcher_source = switcher.read_text(encoding="utf-8")
    assert "export default function LanguageSwitcher" in switcher_source
    assert "aria-label=\"Language\"" in switcher_source
    assert "options.map" in switcher_source
    assert "activeValue === option.value" in switcher_source
    assert "const renderLanguageSwitcher" not in app
    assert "languageOptions.map" not in app
    assert "import LanguageSwitcher from './components/LanguageSwitcher';" in app
    assert "<LanguageSwitcher" in app


def test_project_sidebar_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    sidebar = root / "frontend" / "src" / "components" / "ProjectSidebar.jsx"

    assert sidebar.exists()
    sidebar_source = sidebar.read_text(encoding="utf-8")
    assert "export default function ProjectSidebar" in sidebar_source
    assert "GoodTeam OS" in sidebar_source
    assert "lg:w-[220px]" in sidebar_source
    assert "navGroups.map" in sidebar_source
    assert "setAgentsMemorySubTab('roles')" in sidebar_source
    assert "LanguageSwitcher" in sidebar_source
    assert "theme === 'vivid'" in sidebar_source
    assert "theme === 'minimal'" in sidebar_source
    assert "import ProjectSidebar from './ProjectSidebar';" in dashboard
    assert "<ProjectSidebar" in dashboard
    assert "import ProjectSidebar from './components/ProjectSidebar';" not in app
    assert "<ProjectSidebar" not in app
    assert "GoodTeam OS" not in app
    assert "navGroups.map" not in app
    assert "lg:w-[220px]" not in app


def test_project_header_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    header = root / "frontend" / "src" / "components" / "ProjectHeader.jsx"

    assert header.exists()
    header_source = header.read_text(encoding="utf-8")
    assert "export default function ProjectHeader" in header_source
    assert "lg:hidden h-[60px]" in header_source
    assert "hidden lg:flex h-[60px]" in header_source
    assert "GoodTeam" in header_source
    assert "chrome.runId" in header_source
    assert "status.complete" in header_source
    assert "getStatusLabel(headerStatus, t)" in header_source
    assert "mobileMenuOpen ? <X" in header_source
    assert "import ProjectHeader from './ProjectHeader';" in dashboard
    assert "<ProjectHeader" in dashboard
    assert "import ProjectHeader from './components/ProjectHeader';" not in app
    assert "<ProjectHeader" not in app
    assert "lg:hidden h-[60px]" not in app
    assert "hidden lg:flex h-[60px]" not in app


def test_approval_banner_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    content_shell = (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8")
    banner = root / "frontend" / "src" / "components" / "ApprovalBanner.jsx"

    assert banner.exists()
    banner_source = banner.read_text(encoding="utf-8")
    assert "export default function ApprovalBanner" in banner_source
    assert "bg-status-warning-bg border-b-2 border-status-warning/30" in banner_source
    assert "approvalBanner.message" in banner_source
    assert "approvalBanner.review" in banner_source
    assert "setActiveTab('approvals')" in banner_source
    assert "duo-button-physics text-2xs cursor-pointer" in banner_source
    assert "import ApprovalBanner from './ApprovalBanner';" in content_shell
    assert "<ApprovalBanner" in content_shell
    assert "import ApprovalBanner from './components/ApprovalBanner';" not in app
    assert "<ApprovalBanner" not in app
    assert "Persistent Approvals Banner" not in app
    assert "bg-status-warning-bg border-b-2 border-status-warning/30" not in app


def test_git_creation_notice_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    content_shell = (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8")
    notice = root / "frontend" / "src" / "components" / "GitCreationNotice.jsx"

    assert notice.exists()
    notice_source = notice.read_text(encoding="utf-8")
    assert "export default function GitCreationNotice" in notice_source
    assert "CheckCircle" in notice_source
    assert "AlertTriangle" in notice_source
    assert "Git Repository Initialized" in notice_source
    assert "Git Warnings:" in notice_source
    assert "gitWarnings.map" in notice_source
    assert "setShowGitNotice(false)" in notice_source
    assert "duo-button-physics cursor-pointer shrink-0" in notice_source
    assert "import GitCreationNotice from './GitCreationNotice';" in content_shell
    assert "<GitCreationNotice" in content_shell
    assert "import GitCreationNotice from './components/GitCreationNotice';" not in app
    assert "<GitCreationNotice" not in app
    assert "Git Creation Notice" not in app
    assert "Git Repository Initialized" not in app


def test_global_toast_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    content_shell = (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8")
    toast = root / "frontend" / "src" / "components" / "GlobalToast.jsx"

    assert toast.exists()
    toast_source = toast.read_text(encoding="utf-8")
    assert "export default function GlobalToast" in toast_source
    assert "fixed top-4 right-4 z-50" in toast_source
    assert "animate-fade-in-down" in toast_source
    assert "message.isError" in toast_source
    assert "bg-status-danger-bg text-status-danger-text border-status-danger" in toast_source
    assert "bg-status-success-bg text-accent-text border-accent" in toast_source
    assert "AlertTriangle" in toast_source
    assert "Check" in toast_source
    assert "message.text" in toast_source
    assert "import GlobalToast from './GlobalToast';" in content_shell
    assert "<GlobalToast" in content_shell
    assert "import GlobalToast from './components/GlobalToast';" not in app
    assert "<GlobalToast" not in app
    assert "Global Toast" not in app
    assert "fixed top-4 right-4 z-50" not in app


def test_memory_event_detail_modal_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    content_shell = (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8")
    modal = root / "frontend" / "src" / "components" / "MemoryEventDetailModal.jsx"

    assert modal.exists()
    modal_source = modal.read_text(encoding="utf-8")
    assert "export default function MemoryEventDetailModal" in modal_source
    assert "memory-event-detail-title" in modal_source
    assert "fixed inset-0 z-40 flex items-center justify-center" in modal_source
    assert "Loading full memory event..." in modal_source
    assert "memoryEventDetail.error" in modal_source
    assert "memoryEventDetail.data.role" in modal_source
    assert "hasMemorySupersedeInfo" in modal_source
    assert "memorySupersededBy.map" in modal_source
    assert "renderMemoryEventLink(memorySupersedes)" in modal_source
    assert "setMemoryEventDetail(null)" in modal_source
    assert "No event text recorded." in modal_source
    assert "import MemoryEventDetailModal from './MemoryEventDetailModal';" in content_shell
    assert "<MemoryEventDetailModal" in content_shell
    assert "import MemoryEventDetailModal from './components/MemoryEventDetailModal';" not in app
    assert "<MemoryEventDetailModal" not in app
    assert "memory-event-detail-title" not in app
    assert "fixed inset-0 z-40 flex items-center justify-center" not in app


def test_project_main_panel_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    content_shell = (root / "frontend" / "src" / "components" / "ProjectContentShell.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    panel = root / "frontend" / "src" / "components" / "ProjectMainPanel.jsx"

    assert panel.exists()
    panel_source = panel.read_text(encoding="utf-8")
    assert "export default function ProjectMainPanel" in panel_source
    assert "flex-grow p-8 overflow-y-auto max-w-7xl w-full mx-auto space-y-6" in panel_source
    assert "<Spinner label={loadingLabel} />" in panel_source
    assert "bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl" in panel_source
    assert "text-status-danger-text flex items-center space-x-3 shadow-xs" in panel_source
    assert "AlertTriangle" in panel_source
    assert "children" in panel_source
    assert "import ProjectMainPanel from './ProjectMainPanel';" in content_shell
    assert "<ProjectMainPanel" in content_shell
    assert "import ProjectMainPanel from './components/ProjectMainPanel';" not in app
    assert "<ProjectMainPanel" not in app
    assert "loadingLabel={t('chrome.loading')}" in dashboard
    assert "flex-grow p-8 overflow-y-auto max-w-7xl w-full mx-auto space-y-6" not in app
    assert "bg-status-danger-bg border-2 border-status-danger p-6 rounded-2xl" not in app


def test_project_content_shell_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    shell = root / "frontend" / "src" / "components" / "ProjectContentShell.jsx"

    assert shell.exists()
    shell_source = shell.read_text(encoding="utf-8")
    assert "export default function ProjectContentShell" in shell_source
    assert "flex-grow flex flex-col min-w-0 min-h-screen" in shell_source
    assert "<ApprovalBanner" in shell_source
    assert "<GitCreationNotice" in shell_source
    assert "<GlobalToast" in shell_source
    assert "<MemoryEventDetailModal" in shell_source
    assert "<ProjectMainPanel" in shell_source
    assert "newProjectModal" in shell_source
    assert "directoryPickerModal" in shell_source
    assert "mainPanelChildren" in shell_source
    assert "import ProjectContentShell from './ProjectContentShell';" in dashboard
    assert "<ProjectContentShell" in dashboard
    assert "mainPanelChildren={" in dashboard
    assert "import ProjectContentShell from './components/ProjectContentShell';" not in app
    assert "<ProjectContentShell" not in app
    assert "flex-grow flex flex-col min-w-0 min-h-screen" not in app
    assert "<ApprovalBanner" not in app
    assert "<GitCreationNotice" not in app
    assert "<GlobalToast" not in app
    assert "<MemoryEventDetailModal" not in app


def test_project_dashboard_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = root / "frontend" / "src" / "components" / "ProjectDashboard.jsx"

    assert dashboard.exists()
    dashboard_source = dashboard.read_text(encoding="utf-8")
    assert "export default function ProjectDashboard" in dashboard_source
    assert "min-h-screen bg-surface-base text-text-primary transition-colors flex flex-col lg:flex-row font-sans antialiased" in dashboard_source
    assert "<ProjectSidebar" in dashboard_source
    assert "<ProjectHeader" in dashboard_source
    assert "<ProjectContentShell" in dashboard_source
    assert "<ProjectTabRouter" in dashboard_source
    assert "buildApprovalMetrics({ approvalReviews, openQuestions, t })" in dashboard_source
    assert "buildProjectNavGroups(t)" in dashboard_source
    assert "renderMemoryEventLink" in dashboard_source
    assert "import ProjectDashboard from './components/ProjectDashboard';" in app
    assert "<ProjectDashboard" in app
    assert "import ProjectSidebar from './components/ProjectSidebar';" not in app
    assert "import ProjectHeader from './components/ProjectHeader';" not in app
    assert "import ProjectContentShell from './components/ProjectContentShell';" not in app
    assert "import ProjectTabRouter from './components/ProjectTabRouter';" not in app
    assert "<ProjectSidebar" not in app
    assert "<ProjectHeader" not in app
    assert "<ProjectContentShell" not in app
    assert "<ProjectTabRouter" not in app


def test_project_tab_router_is_extracted_from_app_shell_without_theme_changes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(
        encoding="utf-8"
    )
    router = root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx"

    assert router.exists()
    router_source = router.read_text(encoding="utf-8")
    assert "export default function ProjectTabRouter" in router_source
    for tab_component in [
        "WorkflowGraphTab",
        "OverviewTab",
        "AgentsMemoryTab",
        "RolesTab",
        "ArtifactsTab",
        "ActionsTab",
        "AgileBoardTab",
        "SmokeRunsTab",
        "WorkersTab",
        "ApprovalsTab",
        "MessagesTab",
        "MemoryTab",
        "DevLoopTab",
        "SystemTab",
    ]:
        assert f"<{tab_component}" in router_source
        assert f"import {tab_component}" in router_source
        assert f"import {tab_component}" not in app

    assert "import ProjectTabRouter from './ProjectTabRouter';" in dashboard
    assert "<ProjectTabRouter" in dashboard
    assert "import ProjectTabRouter from './components/ProjectTabRouter';" not in app
    assert "<ProjectTabRouter" not in app
    assert "<WorkflowGraphTab" not in app
    assert "<ApprovalsTab" not in app
    assert "<DevLoopTab" not in app
    assert "mainPanelChildren={" in dashboard


def test_project_url_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useProjectUrl.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useProjectUrl" in hook_source
    assert "URLSearchParams" in hook_source
    assert "popstate" in hook_source
    assert "window.history.pushState" in hook_source
    assert "import { useProjectUrl } from './hooks/useProjectUrl';" in app
    assert "const { projectKey, updateUrl } = useProjectUrl();" in app
    assert "const getUrlProject" not in app
    assert "window.history.pushState" not in app
    assert "addEventListener('popstate'" not in app
    assert "removeEventListener('popstate'" not in app


def test_appearance_preferences_hook_is_extracted_from_app_shell():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    hook = root / "frontend" / "src" / "hooks" / "useAppearancePreferences.js"

    assert hook.is_file()
    hook_source = hook.read_text(encoding="utf-8")
    assert "export function useAppearancePreferences" in hook_source
    assert "localStorage.getItem('color-scheme')" in hook_source
    assert "localStorage.setItem('color-scheme'" in hook_source
    assert "localStorage.getItem('gt-theme')" in hook_source
    assert "document.documentElement.dataset.theme" in hook_source
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in hook_source
    assert "resolveLanguage(languagePreference)" in hook_source
    assert "translate(language" in hook_source
    assert "import { useAppearancePreferences } from './hooks/useAppearancePreferences';" in app
    assert "const appearance = useAppearancePreferences();" in app
    assert "const [darkMode" not in app
    assert "const [theme" not in app
    assert "const [languagePreference" not in app
    assert "localStorage.setItem('color-scheme'" not in app
    assert "document.documentElement.dataset.theme" not in app
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" not in app


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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Memory */}")
    end = router.index("{/* Tab: Dev Loop */}", start)
    memory_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Workflow Graph */}")
    end = router.index("{/* Tab: Overview */}", start)
    workflow_mount = router[start:end]

    assert "<WorkflowGraphTab" in workflow_mount
    assert "computeGraphLayout" not in router
    assert "drawEdgePath" not in router

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Overview */}")
    end = router.index("{activeTab === 'agents_memory'", start)
    overview_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{activeTab === 'agents_memory'")
    end = router.index("{/* Tab: Roles */}", start)
    agents_memory_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Messages */}")
    end = router.index("{/* Tab: Memory */}", start)
    messages_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Roles */}")
    end = router.index("{/* Tab: Artifacts */}", start)
    roles_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Artifacts */}")
    end = router.index("{/* Tab: Actions */}", start)
    artifacts_mount = router[start:end]

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


def test_artifacts_page_filters_task_ledger_from_editable_artifacts():
    root = Path(__file__).resolve().parents[1]
    artifacts_page = (root / "frontend" / "src" / "components" / "tabs" / "ArtifactsTab.jsx").read_text(
        encoding="utf-8"
    )

    assert "resources" in artifacts_page
    assert "capabilities" in artifacts_page
    assert "state.artifacts" not in artifacts_page


def test_actions_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Actions */}")
    end = router.index("{/* Tab: Agile Board */}", start)
    actions_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Agile Board */}")
    end = router.index("{/* Tab: Smoke Runs */}", start)
    board_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Smoke Runs */}")
    end = router.index("{/* Tab: Workers */}", start)
    smoke_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Workers */}")
    end = router.index("{/* Tab: Approvals */}", start)
    workers_mount = router[start:end]

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Approvals */}")
    end = router.index("{/* Tab: Messages */}", start)
    approvals_mount = router[start:end]

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


def test_frontend_i18n_localizes_approvals_tab_summary_slice():
    root = Path(__file__).resolve().parents[1]
    en = (root / "frontend" / "src" / "i18n" / "locales" / "en.js").read_text(encoding="utf-8")
    zh = (root / "frontend" / "src" / "i18n" / "locales" / "zh-CN.js").read_text(encoding="utf-8")
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(encoding="utf-8")
    approvals_tab = (root / "frontend" / "src" / "components" / "tabs" / "ApprovalsTab.jsx").read_text(encoding="utf-8")

    keys = [
        "approvals.title",
        "approvals.subtitle",
        "approvals.progress.title",
        "approvals.progress.gates",
        "approvals.card.review",
        "approvals.card.artifact",
        "approvals.card.filePath",
        "approvals.card.actionRequired",
        "approvals.actions.generating",
        "approvals.actions.generatePrd",
        "approvals.actions.edit",
        "approvals.actions.questions",
        "approvals.actions.approving",
        "approvals.actions.approve",
        "approvals.loadingHint",
        "approvals.emptyList",
        "approvals.emptyDetail",
    ]
    for key in keys:
        assert key in en
        assert key in zh
        assert f"t('{key}" in approvals_tab

    assert "t = (key) => key" in approvals_tab
    assert "t={t}" in router


def test_dev_loop_page_is_extracted_and_uses_theme_tokens():
    root = Path(__file__).resolve().parents[1]
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: Dev Loop */}")
    end = router.index("{/* Tab: System */}", start)
    dev_loop_mount = router[start:end]

    assert "<DevLoopTab" in dev_loop_mount
    assert "devLoopData.pull_requests.map" not in dev_loop_mount
    assert "devLoopData.worktrees.map" not in dev_loop_mount

    dev_loop_files = [
        root / "frontend" / "src" / "components" / "tabs" / "DevLoopTab.jsx",
        root / "frontend" / "src" / "components" / "DevLoopDispatchForm.jsx",
        root / "frontend" / "src" / "components" / "DevLoopGitInitPanel.jsx",
        root / "frontend" / "src" / "components" / "DevLoopPullRequestsPanel.jsx",
        root / "frontend" / "src" / "components" / "DevLoopWarningsPanel.jsx",
        root / "frontend" / "src" / "components" / "DevLoopWorktreesPanel.jsx",
        root / "frontend" / "src" / "components" / "ReleaseReadinessCard.jsx",
        root / "frontend" / "src" / "components" / "ServerActionAuditCard.jsx",
        root / "frontend" / "src" / "components" / "ServerActionsCard.jsx",
    ]
    for dev_loop_file in dev_loop_files:
        assert dev_loop_file.exists()
    dev_loop_page = "\n".join(dev_loop_file.read_text(encoding="utf-8") for dev_loop_file in dev_loop_files)

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
    router = (root / "frontend" / "src" / "components" / "ProjectTabRouter.jsx").read_text(
        encoding="utf-8"
    )
    start = router.index("{/* Tab: System */}")
    end = router.index("</>", start)
    system_mount = router[start:end]

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
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    approval_banner = (root / "frontend" / "src" / "components" / "ApprovalBanner.jsx").read_text(encoding="utf-8")
    dashboard = (root / "frontend" / "src" / "components" / "ProjectDashboard.jsx").read_text(encoding="utf-8")
    project_header = (root / "frontend" / "src" / "components" / "ProjectHeader.jsx").read_text(encoding="utf-8")
    hook = (root / "frontend" / "src" / "hooks" / "useAppearancePreferences.js").read_text(encoding="utf-8")
    navigation = (root / "frontend" / "src" / "utils" / "navigation.jsx").read_text(encoding="utf-8")

    assert "LANGUAGE_STORAGE_KEY" in hook
    assert "normalizeLanguagePreference" in hook
    assert "resolveLanguage" in hook
    assert "translate(language" in hook or "translate(resolvedLanguage" in hook
    assert "languagePreference" in app
    assert "setLanguagePreference" in app
    assert "navGroups" in dashboard
    assert "buildProjectNavGroups(t)" in dashboard
    assert "t('nav.groups.work')" in navigation
    assert "t('nav.tabs.overview')" in navigation
    assert "t('chrome.dashboard')" in project_header
    assert "t('chrome.runId')" in project_header
    assert "t('approvalBanner.message'" in approval_banner
    assert "t('approvalBanner.review')" in approval_banner
    assert "t('chrome.loading')" in dashboard
    assert "language.useAuto" in app
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in hook


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


def test_frontend_i18n_localizes_new_project_start_form_slice():
    root = Path(__file__).resolve().parents[1]
    en = (root / "frontend" / "src" / "i18n" / "locales" / "en.js").read_text(encoding="utf-8")
    zh = (root / "frontend" / "src" / "i18n" / "locales" / "zh-CN.js").read_text(encoding="utf-8")
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    container = (root / "frontend" / "src" / "components" / "NewProjectModalContainer.jsx").read_text(encoding="utf-8")
    modal = (root / "frontend" / "src" / "components" / "NewProjectModal.jsx").read_text(encoding="utf-8")

    keys = [
        "newProject.consoleEyebrow",
        "newProject.title",
        "newProject.mode.adopt",
        "newProject.mode.new",
        "newProject.goal.label",
        "newProject.projectName.label",
        "newProject.projectKey.label",
        "newProject.modelProfile.label",
        "newProject.deliveryMode.label",
        "newProject.projectLocation.title",
        "newProject.projectRoot.label",
        "newProject.projectRoot.browse",
        "newProject.git.title",
        "newProject.git.init",
        "newProject.git.createRemote",
        "newProject.git.visibility",
        "newProject.git.private",
        "newProject.git.public",
        "newProject.workerBuckets.title",
        "newProject.workerBuckets.loading",
        "newProject.advanced.servicesBackend",
        "newProject.advanced.taskSync",
        "newProject.advanced.taskSyncLocal",
        "newProject.advanced.taskSyncPlane",
        "newProject.advanced.workerExecution",
        "newProject.advanced.workerExecutionCodex",
        "newProject.advanced.planeCredentials",
        "newProject.advanced.planeApiHost",
        "newProject.advanced.deploymentContract",
        "newProject.advanced.deploymentBackend",
        "newProject.advanced.deploymentNone",
        "newProject.advanced.deploymentSshCompose",
        "newProject.advanced.sshDeployConfig",
        "newProject.advanced.stagingSshTarget",
        "newProject.advanced.requireFunctionalEvidence",
        "newProject.advanced.minimumEvidenceGate",
        "newProject.advanced.evidenceInfrastructure",
        "newProject.advanced.evidenceFunctional",
        "newProject.advanced.fatalPatterns",
        "newProject.advanced.acceptanceTests",
        "newProject.advanced.roleModelOverrides",
        "newProject.advanced.projectConfigPath",
        "newProject.launchCheck.title",
        "newProject.launchCheck.projectTarget",
        "newProject.launchCheck.sourceMode",
        "newProject.launchCheck.modelProfile",
        "newProject.launchCheck.deliveryMode",
        "newProject.launchCheck.workerBackend",
        "newProject.dryRun",
        "newProject.actions.createConfig",
        "newProject.actions.starting",
        "newProject.actions.preview",
        "newProject.actions.startAdoption",
        "newProject.actions.startNew",
        "newProject.startingHint",
    ]
    for key in keys:
        assert key in en
        assert key in zh
        assert f"t('{key}" in modal

    assert "t={t}" in app
    assert "t={t}" in container
    assert "t = (key) => key" in modal
    assert "aria-label={t('newProject.closeAria')}" in modal
    assert "DELIVERY_MODE_OPTIONS.map" in modal
    assert "const deliveryModeLabel = (option) =>" in modal
    assert "t(key)" in modal
    assert "label === key ? option.label : label" in modal
    assert "{deliveryModeLabel(option)}" in modal
    assert "newProject.deliveryModes.custom" in en
    assert "newProject.deliveryModes.custom" in zh


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

    payload = decide_approval(workspace=tmp_path, project_key="CONTENTOPS", approval="prd", decision="changes_requested")

    assert payload["approvals"]["prd"] == "pending"
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert actions[0]["actor_ref"] == "web:local"


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
                }
            ],
        },
    )


def test_build_approval_evidence_snapshot_smoke_status_passed(tmp_path):
    _seed_smoke_report(tmp_path, "passed")
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "passed"


def test_build_approval_evidence_snapshot_smoke_status_failed(tmp_path):
    _seed_smoke_report(tmp_path, "failed")
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "failed"


def test_build_approval_evidence_snapshot_smoke_status_unknown_when_no_reports(tmp_path):
    state = create_initial_state("run-smoke", "CONTENTOPS", "Smoke run")

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="CONTENTOPS")

    assert snapshot["smoke_status"] == "unknown"


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


def test_frontend_contract_wires_report_bug_fix_workflow():
    """The dashboard must expose a Report Bug affordance that POSTs to
    /api/report-bug and routes the user to the approvals gate for the new fix run."""
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    header = (root / "frontend" / "src" / "components" / "ProjectHeader.jsx").read_text(
        encoding="utf-8"
    )
    modal_path = root / "frontend" / "src" / "components" / "ReportBugModal.jsx"
    assert modal_path.exists()
    modal = modal_path.read_text(encoding="utf-8")

    # The header renders a Report Bug trigger button wired to a callback.
    assert "onReportBug" in header
    assert "Report Bug" in header

    # App wires the POST to the backend contract endpoint with the expected payload.
    assert "/api/report-bug" in app
    assert "<ReportBugModal" in app
    assert "project_key: projectKey" in app
    assert "severity: reportBugSeverity" in app
    # On success it refreshes state and routes to the approvals gate.
    assert "setActiveTab('approvals')" in app

    # The modal collects description (required) + severity and submits.
    assert "description" in modal
    assert "severity" in modal
    assert "Report Bug" in modal
    # Submit is gated on a non-empty description.
    assert "description.trim().length > 0" in modal

    # Theme tokens, not hardcoded hex/zinc colors.
    for token in ["bg-surface-overlay", "border-border-subtle", "text-text-muted"]:
        assert token in modal
    for banned in ["bg-[#", "border-[#", "text-[#"]:
        assert banned not in modal


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
        },
    )

    assert status == 200
    assert payload["role_worker_backends"]["implementation"] == "opencode"
    config = tmp_path / "projects" / "wbp.local.yaml"
    content = config.read_text(encoding="utf-8")
    assert "workers:" in content
    assert "  implementation: opencode" in content
    assert "role_model_overrides:" in content
    assert "  implementation: opencode:opencode-go/qwen3.7-max" in content


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
    assert payload["error"] == "confirmation required"
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
    assert payload["error"] == "cross-origin POST requests are not allowed"


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
    assert payload["error"] == "URL source exceeds 1048576 byte cap: https://example.com/context.md"


def test_frontend_contract_wires_context_sources_cost_preview():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    container = (root / "frontend" / "src" / "components" / "NewProjectModalContainer.jsx").read_text(encoding="utf-8")
    modal = (root / "frontend" / "src" / "components" / "NewProjectModal.jsx").read_text(encoding="utf-8")

    assert "/api/context-estimate" in app
    assert "/api/context-generate" in app
    assert "contextSources" in app
    assert "contextSources={contextSources}" in container
    assert "setContextSources={setContextSources}" in container
    assert "Project context" in modal
    assert "Preview cost" in modal
    assert "Generate context" in modal
    assert "contextSources.map" in modal
    assert "Trash2" in modal
    for token in ["bg-surface-sunken", "border-border-subtle", "text-text-muted"]:
        assert token in modal
    for banned in ["bg-[#", "border-[#", "text-[#"]:
        assert banned not in modal
