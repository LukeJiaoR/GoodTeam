import hashlib
import json
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import goodteam_os.web as web
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message
from goodteam_os.resources import register_markdown_artifact, register_task_ledger
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import serve


class FakeRequest:
    def makefile(self, *args, **kwargs):
        return BytesIO()


def capture_get_handler(tmp_path, monkeypatch, *, frontend_dist=None):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788, frontend_dist=frontend_dist)
    assert captured_handler_class is not None

    def simulate_get(path, headers=None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                self.headers = headers or {}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO()
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
        handler.do_GET()
        return handler

    return simulate_get


def capture_post_handler(tmp_path, monkeypatch, *, host: str = "127.0.0.1", port: int = 8788):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host=host, port=port)
    assert captured_handler_class is not None

    def simulate_post(path, body_dict, headers=None, wfile=None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes)), **(headers or {})}
                self.path = path
                self.wfile = wfile if wfile is not None else BytesIO()
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


def write_project_config(workspace: Path, *, key: str = "REAL", repos: dict[str, Path] | None = None) -> Path:
    projects = workspace / "projects"
    projects.mkdir(exist_ok=True)
    config = projects / f"{key.lower()}.local.yaml"
    lines = ["name: Real Product", f"key: {key}"]
    if repos:
        lines.append("repos:")
        lines.extend(f"  {name}: {path}" for name, path in repos.items())
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def test_api_git_init_initializes_non_git_project_root(tmp_path, monkeypatch):
    project_root = tmp_path / "product"
    project_root.mkdir()
    write_project_config(tmp_path, repos={"root": project_root})
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/git-init",
        {"project_key": "REAL", "create_remote": False},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 200 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["initialized"] is True
    assert payload["remote_created"] is False
    assert (project_root / ".git").exists()


def test_api_git_init_existing_git_project_skips_without_error(tmp_path, monkeypatch):
    project_root = tmp_path / "product"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    write_project_config(tmp_path, repos={"root": project_root})
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/git-init",
        {"project_key": "REAL"},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 200 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["initialized"] is False
    assert payload["remote_created"] is False
    assert "git init skipped: project root already contains .git" in payload["warnings"]


def event_handle(event: dict[str, object]) -> str:
    identity_fields = ("timestamp", "role_key", "horizon", "action", "source", "text")
    payload = json.dumps({key: event.get(key) for key in identity_fields}, sort_keys=True, ensure_ascii=False)
    return "mem:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def test_api_workflow_graph_route_returns_static_graph_and_project_run(tmp_path, monkeypatch):
    state = create_initial_state("run-graph", "CONTENTOPS", "Build AI Content Studio")
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    StateStore(tmp_path).write_project_current(state)
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/workflow/graph?project=contentops")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert {"from": "prd", "to": "architect", "type": "approved"} in payload["edges"]
    assert payload["run"]["run_id"] == "run-graph"
    assert payload["run"]["current_node"] == "rfc_review_gate"
    assert payload["run"]["next_node"] == "planner"


def test_api_artifact_route_rejects_unregistered_absolute_path(tmp_path, monkeypatch):
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n\nDo not expose this.", encoding="utf-8")
    state = create_initial_state("run-1", "REAL", "Build the product.")
    StateStore(tmp_path).write_project_current(state)
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get(f"/api/artifact?project=REAL&key={secret}")

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "Artifact is not registered" in payload["message"]
    assert payload["request_id"]
    assert "Do not expose this" not in handler.wfile.getvalue().decode("utf-8")


def test_api_devloop_route_returns_contract_shape(tmp_path, monkeypatch):
    received_roots = []

    def fake_build_dev_loop(root):
        received_roots.append(root)
        return {
            "worktrees": [],
            "pull_requests": [
                {
                    "number": 57,
                    "title": "Add devloop",
                    "head_branch": "feat/devloop",
                    "ci": "pending",
                    "url": "https://github.com/org/repo/pull/57",
                }
            ],
            "pre_reviews": [{"pr": 57, "present": True, "report_path": "/tmp/pre-review-57-report.md"}],
            "warnings": ["no project selected"],
            "generated_at": "2026-06-04T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "goodteam_os.web.build_dev_loop",
        fake_build_dev_loop,
    )
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/devloop")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert set(payload) == {"worktrees", "pull_requests", "pre_reviews", "warnings", "generated_at"}
    assert received_roots == [None]
    assert payload["worktrees"] == []
    assert payload["pull_requests"][0]["head_branch"] == "feat/devloop"
    assert payload["pull_requests"][0]["ci"] == "pending"
    assert payload["pre_reviews"][0]["pr"] == 57
    assert payload["generated_at"]


def test_api_server_actions_reports_disabled_destructive_capabilities(tmp_path, monkeypatch):
    received_roots = []

    def fake_build_server_action_capabilities(root):
        received_roots.append(root)
        readiness = {"server_execution": "disabled_by_default", "pull_requests": []}
        return {
            "actions": {
                "dispatch_track": {"available": True, "mode": "execute", "reason": ""},
                "dry_smoke": {"available": True, "mode": "execute", "reason": ""},
                "land_pr": {
                    "available": False,
                    "mode": "human_command_only",
                    "reason": "disabled_by_default",
                    "readiness": readiness,
                },
                "tag_release": {
                    "available": False,
                    "mode": "human_command_only",
                    "reason": "disabled_by_default",
                    "readiness": readiness,
                },
                "graduate_release": {
                    "available": False,
                    "mode": "human_command_only",
                    "reason": "disabled_by_default",
                },
            },
            "execution_policy": {
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
                    "land_pr": ["disabled_by_default"],
                    "graduate_release": ["disabled_by_default"],
                    "tag_release": ["tag_release_safety_design_required"],
                },
            },
            "generated_at": "2026-06-08T00:00:00+00:00",
        }

    monkeypatch.setattr(
        "goodteam_os.web.build_server_action_capabilities",
        fake_build_server_action_capabilities,
        raising=False,
    )
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/server-actions")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert received_roots == [None]
    assert payload["actions"]["dispatch_track"]["available"] is True
    assert payload["actions"]["dry_smoke"]["available"] is True
    assert payload["actions"]["land_pr"] == {
        "available": False,
        "mode": "human_command_only",
        "reason": "disabled_by_default",
        "readiness": {"server_execution": "disabled_by_default", "pull_requests": []},
    }
    assert payload["actions"]["tag_release"]["reason"] == "disabled_by_default"
    assert payload["actions"]["tag_release"]["readiness"]["server_execution"] == "disabled_by_default"
    assert payload["actions"]["graduate_release"]["mode"] == "human_command_only"
    assert payload["execution_policy"]["same_origin_required"] is True
    assert payload["execution_policy"]["audit_dir"] == str(tmp_path / ".goodteam" / "server-actions")


def test_build_server_action_audit_log_reads_recent_records_without_execution(tmp_path):
    audit_dir = tmp_path / ".goodteam" / "server-actions"
    audit_dir.mkdir(parents=True)
    old = audit_dir / "old.json"
    old.write_text(
        json.dumps(
            {
                "id": "server-action-old",
                "timestamp": "2026-06-08T00:00:00+00:00",
                "action": "land_pr",
                "returncode": 0,
                "timed_out": False,
                "argv": ["scripts/land-pr.sh", "1"],
                "audit_path": str(old),
                "stdout_tail": "old stdout",
                "stderr_tail": "",
                "rollback_hint": "old rollback",
            }
        ),
        encoding="utf-8",
    )
    new = audit_dir / "new.json"
    new.write_text(
        json.dumps(
            {
                "id": "server-action-new",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "started_at": "2026-06-08T23:59:59+00:00",
                "finished_at": "2026-06-09T00:00:00+00:00",
                "actor": "local-dashboard",
                "origin": "http://127.0.0.1:8788",
                "action": "graduate_release",
                "returncode": None,
                "timed_out": True,
                "argv": ["scripts/graduate-release.sh", "--ref", "abc1234", "--yes"],
                "typed_confirmation": "GRADUATE RELEASE abc1234",
                "audit_path": str(new),
                "stdout_tail": "new stdout",
                "stderr_tail": "new stderr",
                "rollback_hint": "new rollback",
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "broken.json").write_text("{not json", encoding="utf-8")

    log = web.build_server_action_audit_log(tmp_path, limit=1)

    assert log == {
        "available": True,
        "audit_dir": str(audit_dir),
        "records": [
            {
                "id": "server-action-new",
                "timestamp": "2026-06-09T00:00:00+00:00",
                "started_at": "2026-06-08T23:59:59+00:00",
                "finished_at": "2026-06-09T00:00:00+00:00",
                "actor": "local-dashboard",
                "origin": "http://127.0.0.1:8788",
                "action": "graduate_release",
                "returncode": None,
                "timed_out": True,
                "argv": ["scripts/graduate-release.sh", "--ref", "abc1234", "--yes"],
                "typed_confirmation": "GRADUATE RELEASE abc1234",
                "audit_path": str(new),
                "stdout_tail": "new stdout",
                "stderr_tail": "new stderr",
                "rollback_hint": "new rollback",
            }
        ],
        "warnings": ["ignored malformed audit record: broken.json"],
        "generated_at": log["generated_at"],
    }


def test_api_server_actions_audit_route_resolves_project_root_from_config(tmp_path, monkeypatch):
    target_root = tmp_path / "target"
    target_root.mkdir()
    write_project_config(tmp_path, key="REAL", repos={"root": target_root})
    received_roots = []

    def fake_build_server_action_audit_log(root, *, limit=25):
        received_roots.append((root, limit))
        return {"available": True, "audit_dir": "/tmp/audit", "records": [], "warnings": [], "generated_at": "now"}

    monkeypatch.setattr("goodteam_os.web.build_server_action_audit_log", fake_build_server_action_audit_log)
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get("/api/server-actions/audit?project=REAL&limit=5")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["available"] is True
    assert received_roots == [(target_root, 5)]


def test_api_server_action_run_rejects_until_env_nonce_and_confirmation_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "0")
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    disabled = simulate_post(
        "/api/server-actions/run",
        {"action": "land_pr"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 403 in disabled.responses
    disabled_payload = json.loads(disabled.wfile.getvalue().decode("utf-8"))
    assert disabled_payload["code"] == "permission_denied"
    assert "disabled" in disabled_payload["message"]
    assert disabled_payload["request_id"]

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    missing_nonce = simulate_post(
        "/api/server-actions/run",
        {"action": "land_pr", "confirm": "LAND PR 57", "pr": 57},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    assert 403 in missing_nonce.responses
    missing_nonce_payload = json.loads(missing_nonce.wfile.getvalue().decode("utf-8"))
    assert missing_nonce_payload["code"] == "permission_denied"
    assert "nonce" in missing_nonce_payload["message"]

    wrong_confirmation = simulate_post(
        "/api/server-actions/run",
        {"action": "land_pr", "nonce": "nonce-123", "confirm": "land pr 57", "pr": 57},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    assert 400 in wrong_confirmation.responses
    wrong_confirmation_payload = json.loads(wrong_confirmation.wfile.getvalue().decode("utf-8"))
    assert wrong_confirmation_payload["code"] == "invalid_request"
    assert "confirmation" in wrong_confirmation_payload["message"]

    unsafe_action = simulate_post(
        "/api/server-actions/run",
        {"action": "tag_release", "nonce": "nonce-123", "confirm": "TAG RELEASE"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    assert 400 in unsafe_action.responses
    unsafe_payload = json.loads(unsafe_action.wfile.getvalue().decode("utf-8"))
    assert unsafe_payload["code"] == "invalid_request"
    assert "tag" in unsafe_payload["message"]


def test_api_server_action_run_tag_release_stays_report_only_without_subprocess_or_audit(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return web.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_run)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "action": "tag_release",
            "nonce": "nonce-123",
            "confirm": "TAG RELEASE v1.2.3",
            "tag": "v1.2.3",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "report-only" in payload["message"]
    assert calls == []
    assert not list((tmp_path / ".goodteam" / "server-actions").glob("*.json"))


def test_api_server_action_run_rejects_unsafe_release_refs_before_subprocess(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return web.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_run)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    for ref in ("../../abc1234", "feature/../main", "--abc1234", "feature//@{abc1234}"):
        handler = simulate_post(
            "/api/server-actions/run",
            {
                "action": "graduate_release",
                "nonce": "nonce-123",
                "confirm": f"GRADUATE RELEASE {ref}",
                "ref": ref,
            },
            headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
        )

        assert 400 in handler.responses
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert payload["code"] == "invalid_request"
        assert "ref" in payload["message"]

    assert calls == []


def test_api_server_action_run_executes_allowlisted_land_pr_and_writes_audit(tmp_path, monkeypatch):
    calls = []
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "pre-review-57-report.md").write_text("reviewed\n", encoding="utf-8")
    rationale_path = tmp_path / ".goodteam" / "provenance" / "pr-57" / "review-rationale.md"
    rationale_path.parent.mkdir(parents=True)
    rationale_path.write_text("Approved because the regression slice covers the change.\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(
                returncode=0,
                stdout="""worktree /repo/ready
HEAD abc1234567890
branch refs/heads/feat/ready

""",
                stderr="",
            )
        if command[:3] == ["git", "-C", "/repo/ready"] and command[-2:] == ["status", "--short"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["gh", "pr"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 57,
                            "title": "Ready",
                            "headRefName": "feat/ready",
                            "url": "https://github.com/org/repo/pull/57",
                            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                        }
                    ]
                ),
                stderr="",
            )
        calls.append((command, kwargs))
        return web.subprocess.CompletedProcess(command, 0, stdout="merged\n", stderr="")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr(web, "PRE_REVIEW_REPORT_DIR", report_dir)
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_run)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "action": "land_pr",
            "nonce": "nonce-123",
            "confirm": "LAND PR 57",
            "pr": 57,
            "worktree": "/tmp/spoofed-worktree",
            "review_rationale": "/tmp/spoofed-rationale.md",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["status"] == "completed"
    assert payload["action"] == "land_pr"
    assert payload["argv"] == [
        "scripts/land-pr.sh",
        "57",
        "--worktree",
        "/repo/ready",
        "--review-rationale",
        str(rationale_path),
    ]
    assert payload["stdout_tail"] == "merged\n"
    assert calls == [
        (
            payload["argv"],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "timeout": web.SERVER_ACTION_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    audit_files = sorted((tmp_path / ".goodteam" / "server-actions").glob("*.json"))
    assert len(audit_files) == 1
    audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit["action"] == "land_pr"
    assert audit["argv"] == payload["argv"]
    assert audit["returncode"] == 0
    assert audit["origin"] == "http://127.0.0.1:8788"
    assert audit["typed_confirmation"] == "LAND PR 57"
    assert audit["rollback_hint"] == "Use GitHub PR/release rollback guidance from the audit context; do not rerun blindly."


def test_api_server_action_run_derives_land_paths_from_readiness_not_request(tmp_path, monkeypatch):
    calls = []
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "pre-review-57-report.md").write_text("reviewed\n", encoding="utf-8")
    rationale_path = tmp_path / ".goodteam" / "provenance" / "pr-57" / "review-rationale.md"
    rationale_path.parent.mkdir(parents=True)
    rationale_path.write_text("Approved because the regression slice covers the change.\n", encoding="utf-8")

    def fake_command(command, **kwargs):
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n", stderr="")
        if command[-3:] == ["worktree", "list", "--porcelain"]:
            return SimpleNamespace(
                returncode=0,
                stdout="""worktree /repo/ready
HEAD abc1234567890
branch refs/heads/feat/ready

""",
                stderr="",
            )
        if command[:3] == ["git", "-C", "/repo/ready"] and command[-2:] == ["status", "--short"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["gh", "pr"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 57,
                            "title": "Ready",
                            "headRefName": "feat/ready",
                            "url": "https://github.com/org/repo/pull/57",
                            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                        }
                    ]
                ),
                stderr="",
            )
        calls.append((command, kwargs))
        return web.subprocess.CompletedProcess(command, 0, stdout="merged\n", stderr="")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr(web, "PRE_REVIEW_REPORT_DIR", report_dir)
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_command)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "action": "land_pr",
            "nonce": "nonce-123",
            "confirm": "LAND PR 57",
            "pr": 57,
            "worktree": "/tmp/spoofed-worktree",
            "review_rationale": "/tmp/spoofed-rationale.md",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["argv"] == [
        "scripts/land-pr.sh",
        "57",
        "--worktree",
        "/repo/ready",
        "--review-rationale",
        str(rationale_path),
    ]
    assert calls == [
        (
            payload["argv"],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "timeout": web.SERVER_ACTION_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]


def test_api_server_action_run_executes_graduate_release_non_interactively(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return web.subprocess.CompletedProcess(command, 0, stdout="graduated\n", stderr="")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_run)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "action": "graduate_release",
            "nonce": "nonce-123",
            "confirm": "GRADUATE RELEASE abc1234",
            "ref": "abc1234",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["status"] == "completed"
    assert payload["argv"] == ["scripts/graduate-release.sh", "--ref", "abc1234", "--yes"]
    assert calls == [
        (
            payload["argv"],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "timeout": web.SERVER_ACTION_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]


def test_api_server_action_run_writes_audit_when_subprocess_times_out(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise web.subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial\n", stderr="timed out\n")

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr("goodteam_os.web.subprocess.run", fake_run)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "action": "graduate_release",
            "nonce": "nonce-123",
            "confirm": "GRADUATE RELEASE abc1234",
            "ref": "abc1234",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["status"] == "failed"
    assert payload["timed_out"] is True
    assert payload["stdout_tail"] == "partial\n"
    assert payload["stderr_tail"] == "timed out\n"
    audit_files = sorted((tmp_path / ".goodteam" / "server-actions").glob("*.json"))
    assert len(audit_files) == 1
    audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit["timed_out"] is True
    assert audit["returncode"] is None
    assert audit["argv"] == ["scripts/graduate-release.sh", "--ref", "abc1234", "--yes"]


def test_api_server_action_run_resolves_project_root_from_project_config(tmp_path, monkeypatch):
    target_root = tmp_path / "target"
    target_root.mkdir()
    write_project_config(tmp_path, key="REAL", repos={"root": target_root})
    received_roots = []

    def fake_run_gated_server_action(root, request, **kwargs):
        received_roots.append(root)
        return {"status": "completed", "action": request["action"], "audit_path": "/tmp/audit.json"}

    monkeypatch.setenv("GOODTEAM_SERVER_ACTIONS", "1")
    monkeypatch.setenv("GOODTEAM_SERVER_ACTION_NONCE", "nonce-123")
    monkeypatch.setattr("goodteam_os.web.run_gated_server_action", fake_run_gated_server_action)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/server-actions/run",
        {
            "project": "REAL",
            "action": "graduate_release",
            "nonce": "nonce-123",
            "confirm": "GRADUATE RELEASE abc1234",
            "ref": "abc1234",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in handler.responses
    assert received_roots == [target_root]


def test_api_smoke_run_starts_background_dry_run(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    smoke_entered = threading.Event()
    completed_status_written = threading.Event()
    calls = []

    def fake_run_smoke_suite(**kwargs):
        calls.append(kwargs)
        smoke_entered.set()
        return {
            "status": "planned",
            "suite": kwargs["suites"],
            "cycles": kwargs["cycles"],
            "runs": [],
        }

    monkeypatch.setattr("goodteam_os.web.run_smoke_suite", fake_run_smoke_suite)
    original_write_status = web._write_smoke_run_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "completed":
            completed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_smoke_run_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/smoke-run",
        {"suite": "self", "cycles": 1, "dry_run": True, "worker_mode": "fake"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 202 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["run_id"].startswith("smoke-")
    assert payload == {
        "run_id": payload["run_id"],
        "suites": ["self"],
        "cycles": 1,
        "dry_run": True,
        "worker_mode": "fake",
        "status": "started",
    }
    assert smoke_entered.wait(timeout=1)
    assert completed_status_written.wait(timeout=1)
    assert calls[0]["workspace"] == workspace
    assert calls[0]["suites"] == ["self"]
    assert calls[0]["cycles"] == 1
    assert calls[0]["dry_run"] is True
    assert calls[0]["worker_mode"] == "fake"
    status_path = workspace / "state" / "smoke-runs" / f"smoke-run-{payload['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["report"]["status"] == "planned"


def test_api_smoke_run_rejects_cross_origin_before_start(tmp_path, monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("smoke run should not start for cross-origin requests")

    monkeypatch.setattr("goodteam_os.web.run_smoke_suite", fail_if_called)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/smoke-run",
        {"suite": "self"},
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.test"},
    )

    assert 403 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "same_origin_required"
    assert payload["request_id"]


def test_smoke_run_items_include_async_status_report_runs(tmp_path):
    smoke_root = tmp_path / "state" / "smoke-runs"
    smoke_root.mkdir(parents=True)
    status_path = smoke_root / "smoke-run-smoke-123.json"
    status_path.write_text(
        json.dumps(
            {
                "run_id": "smoke-123",
                "status": "completed",
                "report": {
                    "status": "planned",
                    "runs": [
                        {
                            "suite": "self",
                            "project_key": "GOODTEAM-SELF",
                            "cycle": 1,
                            "status": "planned",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    items = web.build_smoke_run_items(tmp_path)

    assert items == [
        {
            "suite": "self",
            "project_key": "GOODTEAM-SELF",
            "cycle": "1",
            "status": "planned",
            "report_path": str(status_path),
            "report_status": "planned",
        }
    ]


def test_api_fs_list_route_returns_directory_listing(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".git").mkdir()
    (tmp_path / "file.txt").write_text("nope", encoding="utf-8")
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    handler = simulate_get(f"/api/fs/list?path={tmp_path}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "path": str(tmp_path.resolve()),
        "parent": str(tmp_path.resolve().parent),
        "entries": [
            {
                "name": "file.txt",
                "path": str((tmp_path / "file.txt").resolve()),
                "is_dir": False,
                "is_git_repo": False,
            },
            {
                "name": "repo",
                "path": str((tmp_path / "repo").resolve()),
                "is_dir": True,
                "is_git_repo": True,
            },
        ],
    }


def test_api_fs_list_route_status_codes(tmp_path, monkeypatch):
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    bad_request = simulate_get("/api/fs/list?path=relative")
    missing = simulate_get(f"/api/fs/list?path={tmp_path / 'missing'}")
    file_path = tmp_path / "file.txt"
    file_path.write_text("nope", encoding="utf-8")
    not_directory = simulate_get(f"/api/fs/list?path={file_path}")

    assert 400 in bad_request.responses
    assert 404 in missing.responses
    assert 404 in not_directory.responses
    assert json.loads(bad_request.wfile.getvalue().decode("utf-8"))["code"] == "invalid_request"
    assert json.loads(missing.wfile.getvalue().decode("utf-8"))["code"] == "path_not_found"
    assert json.loads(not_directory.wfile.getvalue().decode("utf-8"))["code"] == "path_not_found"


def test_api_fs_list_route_maps_permission_error_to_403(tmp_path, monkeypatch):
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    def fake_list_directory(path_value):
        raise web.FsListPermissionError(f"blocked: {path_value}")

    monkeypatch.setattr(web, "list_directory_for_picker", fake_list_directory)

    handler = simulate_get(f"/api/fs/list?path={tmp_path}")

    assert 403 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "permission_denied"
    assert payload["message"] == f"blocked: {tmp_path}"
    assert payload["request_id"]


def test_api_fs_mkdir_route_creates_child_directory(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path), "name": "docs"})

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"path": str((tmp_path / "docs").resolve())}
    assert (tmp_path / "docs").is_dir()


def test_api_fs_mkdir_route_rejects_invalid_names(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    for name in ["", "..", "bad/name", "bad\\name"]:
        handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path), "name": name})
        assert 400 in handler.responses


def test_api_fs_mkdir_route_missing_parent_returns_404(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post("/api/fs/mkdir", {"parent": str(tmp_path / "missing"), "name": "docs"})

    assert 404 in handler.responses


def test_api_fs_choose_route_returns_native_picker_selection(tmp_path, monkeypatch):
    selected = tmp_path / "项目 with spaces"
    selected.mkdir()
    captured = {}

    def fake_choose(mode, initial_path, *, host):
        captured.update(mode=mode, initial_path=initial_path, host=host)
        return {"status": "selected", "path": str(selected)}

    monkeypatch.setattr(web, "choose_native_path", fake_choose)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/fs/choose",
        {"mode": "directory", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )

    assert 200 in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == {
        "status": "selected",
        "path": str(selected),
    }
    assert captured == {"mode": "directory", "initial_path": str(tmp_path), "host": "127.0.0.1"}


def test_api_fs_choose_rejects_unknown_mode_and_cross_origin_before_adapter(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "choose_native_path", lambda *args, **kwargs: calls.append((args, kwargs)))
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    unknown = simulate_post(
        "/api/fs/choose",
        {"mode": "command", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    cross_origin = simulate_post(
        "/api/fs/choose",
        {"mode": "directory", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.example"},
    )

    assert 400 in unknown.responses
    unknown_payload = json.loads(unknown.wfile.getvalue().decode("utf-8"))
    assert unknown_payload["code"] == "invalid_request"
    assert "mode" in unknown_payload["message"]
    assert 403 in cross_origin.responses
    assert calls == []


def test_api_fs_choose_rejects_dns_rebinding_authority_before_adapter(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "choose_native_path", lambda *args, **kwargs: calls.append((args, kwargs)))
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/fs/choose",
        {"mode": "directory", "initial_path": str(tmp_path)},
        headers={"Host": "evil.example:8788", "Origin": "http://evil.example:8788"},
    )

    assert 403 in response.responses
    assert calls == []


def test_api_fs_choose_rejects_other_local_origin_port_before_adapter(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "choose_native_path", lambda *args, **kwargs: calls.append((args, kwargs)))
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/fs/choose",
        {"mode": "directory", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:3000"},
    )

    assert 403 in response.responses
    assert calls == []


def test_api_fs_choose_allows_explicit_vite_development_origin(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    selected.mkdir()
    monkeypatch.setattr(
        web,
        "choose_native_path",
        lambda *args, **kwargs: {"status": "selected", "path": str(selected)},
    )
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/fs/choose",
        {"mode": "directory", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://localhost:8790"},
    )

    assert 200 in response.responses


@pytest.mark.parametrize(
    ("picker_payload", "expected_status"),
    [
        ({"status": "cancelled", "path": ""}, 200),
        ({"status": "unsupported", "reason": "native_picker_unavailable"}, 200),
        ({"status": "error", "error": "native_picker_timeout"}, 500),
    ],
)
def test_api_fs_choose_preserves_stable_status_payloads(tmp_path, monkeypatch, picker_payload, expected_status):
    monkeypatch.setattr(web, "choose_native_path", lambda *args, **kwargs: picker_payload)
    handler = capture_post_handler(tmp_path, monkeypatch)(
        "/api/fs/choose",
        {"mode": "file", "initial_path": str(tmp_path)},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert expected_status in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == picker_payload


def test_api_handler_do_get_and_do_options(tmp_path, monkeypatch):
    # 1. Capture the DashboardHandler class
    captured_handler_class = None
    application_dist = tmp_path / "application" / "frontend" / "dist"
    application_dist.mkdir(parents=True)
    (application_dist / "index.html").write_text("Hello React", encoding="utf-8")
    style_css = application_dist / "assets" / "style.css"
    style_css.parent.mkdir(parents=True)
    style_css.write_text("body { color: red; }", encoding="utf-8")
    workspace_dist = tmp_path / "frontend" / "dist"
    workspace_dist.mkdir(parents=True)
    (workspace_dist / "index.html").write_text("Wrong workspace UI", encoding="utf-8")

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class
        class FakeServer:
            def serve_forever(self):
                pass
        return FakeServer()

    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788, frontend_dist=application_dist)
    
    assert captured_handler_class is not None

    # Helper to simulate a GET request
    def simulate_get(path, headers=None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                self.headers = headers or {}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO()
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
        handler.do_GET()
        return handler

    # Helper to simulate an OPTIONS request
    def simulate_options(path, headers=None):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                self.headers = headers or {}
                self.path = path
                self.wfile = BytesIO()
                self.rfile = BytesIO()
                self.responses = []
                self.response_headers = {}

            def send_response(self, code, message=None):
                self.responses.append(code)

            def send_header(self, keyword, value):
                self.response_headers[keyword] = value

            def end_headers(self):
                pass

        handler = SimulatedHandler()
        handler.do_OPTIONS()
        return handler

    # 2. Test GET /api/state on empty workspace
    handler = simulate_get("/api/state")
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["type"] == "overview"
    assert payload["project_items"] == []
    assert payload["legacy_state"] is None
    assert handler.response_headers.get("Access-Control-Allow-Origin") is None

    handler = simulate_get("/api/state", headers={"Origin": "http://127.0.0.1:8788"})
    assert 200 in handler.responses
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:8788"
    handler = simulate_get("/api/state", headers={"Origin": "https://evil.example"})
    assert 200 in handler.responses
    assert handler.response_headers.get("Access-Control-Allow-Origin") is None

    # 3. Test OPTIONS preflight request
    handler = simulate_options("/api/state", headers={"Origin": "http://localhost:8788"})
    assert 204 in handler.responses
    assert handler.response_headers.get("Access-Control-Allow-Origin") == "http://localhost:8788"
    assert "GET" in handler.response_headers.get("Access-Control-Allow-Methods", "")

    # 4. Product pages always come from application assets, never the data workspace.
    handler = simulate_get("/")
    assert 200 in handler.responses
    assert handler.response_headers.get("Content-Type") == "text/html"
    assert handler.wfile.getvalue() == b"Hello React"
    assert b"Wrong workspace UI" not in handler.wfile.getvalue()

    # 5. Legacy root bookmarks reach React so the product router can migrate them.
    handler = simulate_get("/?project=UNKNOWN")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"Hello React"

    # The explicit compatibility route also reaches React; the router redirects
    # it and the product destination renders a recoverable project error.
    handler = simulate_get("/legacy?project=UNKNOWN")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"Hello React"

    # 6. The resolved application dist also serves hashed assets.
    handler = simulate_get("/")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"Hello React"
    assert handler.response_headers.get("Content-Type") == "text/html"

    handler = simulate_get("/projects/UNKNOWN/work")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"Hello React"

    handler = simulate_get("/assets/style.css")
    assert 200 in handler.responses
    assert handler.wfile.getvalue() == b"body { color: red; }"
    assert handler.response_headers.get("Content-Type") == "text/css"


def test_missing_frontend_returns_actionable_diagnostic_instead_of_legacy(tmp_path, monkeypatch):
    from goodteam_os.frontend_assets import FrontendAssetsNotFound

    missing_dist = tmp_path / "missing-dist"
    monkeypatch.setattr(
        web,
        "resolve_frontend_dist",
        lambda: (_ for _ in ()).throw(FrontendAssetsNotFound([missing_dist])),
    )
    simulate_get = capture_get_handler(tmp_path, monkeypatch)

    for path in ("/", "/?project=UNKNOWN"):
        handler = simulate_get(path)

        assert 503 in handler.responses
        body = handler.wfile.getvalue().decode("utf-8")
        assert "GoodTeam React frontend is unavailable" in body
        assert str(tmp_path.resolve()) in body
        assert "Attempted paths" in body
        assert "npm --prefix frontend run build" in body
        assert "GOODTEAM_FRONTEND_DIST" in body
        assert "GoodTeam Projects" not in body


def test_api_handler_do_post_routing(tmp_path, monkeypatch):
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

    # Helper to simulate a POST request
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

    # 1. Test POST /api/start validation (should return 400 because idea is missing)
    handler = simulate_post("/api/start", {"project_key": "CONTENTOPS"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]

    # 2. Test POST /start legacy path validation (should return 400 because idea is missing)
    handler = simulate_post("/start", {"project_key": "CONTENTOPS"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]

    # 3. Test POST /generate-prd routes through generate_prd and returns payload
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "prd_review_gate"},
    )
    handler = simulate_post("/generate-prd", {"project_key": "CONTENTOPS"})
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["current_node"] == "prd_review_gate"

    # 4. Test POST /generate-prd returns 400 when underlying call errors
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: (_ for _ in ()).throw(ValueError("No active run for project: BAD")),
    )
    handler = simulate_post("/generate-prd", {"project_key": "BAD"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]

    # 5. Test POST /generate-rfc routes through generate_rfc and returns payload
    monkeypatch.setattr(
        "goodteam_os.web.generate_rfc",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "rfc_review_gate"},
    )
    handler = simulate_post("/generate-rfc", {"project_key": "CONTENTOPS"})
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "CONTENTOPS"
    assert payload["current_node"] == "rfc_review_gate"

    # 6. Test POST /generate-rfc returns 400 when underlying call errors
    monkeypatch.setattr(
        "goodteam_os.web.generate_rfc",
        lambda workspace, project_key: (_ for _ in ()).throw(ValueError("No active run for project: BAD")),
    )
    handler = simulate_post("/generate-rfc", {"project_key": "BAD"})
    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]

    monkeypatch.setattr(
        "goodteam_os.web.refine_artifact",
        lambda workspace, project_key, artifact_key, content: {
            "artifact": artifact_key,
            "base_path": "/tmp/prd.md",
            "proposed_path": "/tmp/prd.proposed.md",
            "diff_summary": {"added_lines": 1, "removed_lines": 0},
        },
    )
    handler = simulate_post(
        "/api/refine-artifact",
        {"project_key": "CONTENTOPS", "artifact": "prd", "content": "# Edited"},
    )
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["artifact"] == "prd"
    assert payload["proposed_path"] == "/tmp/prd.proposed.md"

    state = create_initial_state("run-1", "CONTENTOPS", "Build a content workflow")
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text(
        "# PRD\n\n"
        "## Risks And Open Questions\n\n"
        "- Which market launches first?\n"
        "- What retention period is required?\n",
        encoding="utf-8",
    )
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state, "CONTENTOPS")

    simulate_get = capture_get_handler(tmp_path, monkeypatch)
    handler = simulate_get("/api/open-questions?project=CONTENTOPS&artifact=prd")
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "artifact": "prd",
        "questions": [
            "Which market launches first?",
            "What retention period is required?",
        ],
    }

    seen = {}

    def fake_refine_artifact(workspace, project_key, artifact_key, content, guidance=""):
        seen.update(
            {
                "project_key": project_key,
                "artifact_key": artifact_key,
                "content": content,
                "guidance": guidance,
            }
        )
        return {
            "artifact": artifact_key,
            "base_path": "/tmp/prd.md",
            "proposed_path": "/tmp/prd.proposed.md",
            "diff_summary": {"added_lines": 1, "removed_lines": 1},
        }

    monkeypatch.setattr("goodteam_os.web.refine_artifact", fake_refine_artifact)
    handler = simulate_post(
        "/api/answer-open-questions",
        {
            "project_key": "CONTENTOPS",
            "artifact": "prd",
            "answers": [
                {"question": "Which market launches first?", "answer": "Launch in the US first."},
                {"question": "What retention period is required?", "answer": "   "},
            ],
        },
    )
    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["proposed_path"] == "/tmp/prd.proposed.md"
    assert "Q: Which market launches first?\nA: Launch in the US first.\n\n" in seen["guidance"]
    assert "What retention period is required?" not in seen["guidance"]
    assert "Launch in the US first." in seen["guidance"]
    assert seen["content"] == prd.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "goodteam_os.web.accept_refinement",
        lambda workspace, project_key, artifact_key: {"artifact": artifact_key, "accepted": True},
    )
    handler = simulate_post(
        "/api/accept-refinement",
        {"project_key": "CONTENTOPS", "artifact": "prd"},
    )
    assert 200 in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8"))["accepted"] is True

    monkeypatch.setattr(
        "goodteam_os.web.discard_refinement",
        lambda workspace, project_key, artifact_key: {"artifact": artifact_key, "discarded": True},
    )
    handler = simulate_post(
        "/api/discard-refinement",
        {"project_key": "CONTENTOPS", "artifact": "prd"},
    )
    assert 200 in handler.responses
    assert json.loads(handler.wfile.getvalue().decode("utf-8"))["discarded"] is True


def test_api_post_same_origin_guard_rejects_cross_origin_and_allows_trusted_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "goodteam_os.web.generate_prd",
        lambda workspace, project_key: {"project_key": project_key, "current_node": "prd_review_gate"},
    )
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    cross_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://evil.test"},
    )
    assert 403 in cross_origin.responses
    cross_origin_payload = json.loads(cross_origin.wfile.getvalue().decode("utf-8"))
    assert cross_origin_payload["code"] == "same_origin_required"
    assert cross_origin_payload["request_id"]

    same_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
    )
    assert 200 in same_origin.responses
    assert json.loads(same_origin.wfile.getvalue().decode("utf-8"))["project_key"] == "CONTENTOPS"

    absent_origin = simulate_post(
        "/api/generate-prd",
        {"project_key": "CONTENTOPS"},
        headers={"Host": "127.0.0.1:8788"},
    )
    assert 200 in absent_origin.responses


def test_api_approve_accepts_optional_note_without_response_shape_change(tmp_path, monkeypatch):
    captured = {}

    def fake_decide_approval(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run-1", "decision": kwargs["decision"], "next_node": "await_prd_approval"}

    monkeypatch.setattr("goodteam_os.web.decide_approval", fake_decide_approval)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler = simulate_post(
        "/api/approve",
        {
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "changes_requested",
            "note": "Tighten acceptance criteria.",
        },
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 200 in handler.responses
    assert captured["note"] == "Tighten acceptance criteria."
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == {
        "run_id": "run-1",
        "decision": "changes_requested",
        "next_node": "await_prd_approval",
    }


def test_api_approve_forwards_rejected_note(tmp_path, monkeypatch):
    captured = {}

    def fake_decide_approval(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run-1", "decision": kwargs["decision"], "next_node": "await_prd_approval"}

    monkeypatch.setattr("goodteam_os.web.decide_approval", fake_decide_approval)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    handler_ok = simulate_post(
        "/api/approve",
        {
            "project_key": "CONTENTOPS",
            "approval": "prd",
            "decision": "rejected",
            "note": "Safety validation failed.",
        },
        headers={"Host": "127.0.0.1:8788"},
    )
    assert 200 in handler_ok.responses
    assert captured["note"] == "Safety validation failed."



def test_api_dispatch_track_starts_background_dispatch_without_blocking(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    response_flushed = threading.Event()
    dispatch_entered = threading.Event()
    dispatch_can_finish = threading.Event()
    completed_status_written = threading.Event()
    calls = []
    thread_errors = []

    class ResponseStream(BytesIO):
        def flush(self):
            super().flush()
            response_flushed.set()

    def fake_ensure_worktree(workspace, *, name, branch):
        assert workspace == tmp_path / "workspace"
        assert name == "my-track"
        assert branch == "feat/my-track"
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_dispatch_tracks(**kwargs):
        if not response_flushed.is_set():
            thread_errors.append("dispatch started before the 202 response was flushed")
        calls.append(kwargs)
        dispatch_entered.set()
        dispatch_can_finish.wait(timeout=5)
        return {"all_ok": True, "run_id": "dispatch-worker"}

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    def fake_safety_check(project_root, *, projects_dir):
        assert project_root == worktree
        assert projects_dir == tmp_path / "workspace" / "projects"
        return {"safe": True, "reason": ""}

    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "completed":
            completed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
            "constraints": "Keep changes scoped.",
            "verify": "pytest tests/test_api.py -q",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
        wfile=ResponseStream(),
    )
    assert 202 in response.responses
    assert response_flushed.is_set()

    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["run_id"].startswith("dispatch-")
    assert payload == {
        "run_id": payload["run_id"],
        "name": "my-track",
        "branch": "feat/my-track",
        "worktree": str(worktree),
        "status": "started",
    }
    assert dispatch_entered.wait(timeout=1)
    assert not thread_errors
    assert calls[0]["project_root"] == worktree
    assert calls[0]["tracks"][0]["role"] == "backend"
    assert calls[0]["tracks"][0]["adapter"] == "codex"
    assert calls[0]["tracks"][0]["task"] == "Implement backend dispatch."
    assert calls[0]["verify_commands"] == {"backend": "pytest tests/test_api.py -q"}
    assert calls[0]["constraints_file"].read_text(encoding="utf-8") == "Keep changes scoped."
    assert calls[0]["projects_dir"] == workspace / "projects"
    dispatch_can_finish.set()
    assert completed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{payload['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == payload["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "completed"
    assert status["error"] == ""


def test_api_dispatch_track_background_failure_writes_status(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    response_flushed = threading.Event()
    dispatch_entered = threading.Event()
    failed_status_written = threading.Event()
    thread_errors = []

    class ResponseStream(BytesIO):
        def flush(self):
            super().flush()
            response_flushed.set()

    def fake_ensure_worktree(workspace, *, name, branch):
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_safety_check(project_root, *, projects_dir):
        return {"safe": True, "reason": ""}

    def fake_dispatch_tracks(**kwargs):
        if not response_flushed.is_set():
            thread_errors.append("dispatch started before the 202 response was flushed")
        dispatch_entered.set()
        raise RuntimeError("dispatch failed after acceptance")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "failed":
            failed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
        wfile=ResponseStream(),
    )

    assert 202 in response.responses
    assert response_flushed.is_set()
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert dispatch_entered.wait(timeout=1)
    assert not thread_errors
    assert failed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{payload['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == payload["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "failed"
    assert status["error"] == "dispatch failed after acceptance"


def test_api_dispatch_track_releases_background_when_accepted_response_write_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    worktree = tmp_path / "goodteam-dev-my-track"
    dispatch_entered = threading.Event()
    completed_status_written = threading.Event()
    captured_response_written = None
    captured_result = None

    class BrokenResponseStream(BytesIO):
        def write(self, data):
            raise OSError("client disconnected")

    def fake_ensure_worktree(workspace, *, name, branch):
        worktree.mkdir(exist_ok=True)
        return worktree

    def fake_safety_check(project_root, *, projects_dir):
        return {"safe": True, "reason": ""}

    def fake_dispatch_tracks(**kwargs):
        dispatch_entered.set()
        return {"all_ok": True, "run_id": "dispatch-worker"}

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fake_dispatch_tracks)
    original_start_dispatch_track = web.start_dispatch_track

    def start_dispatch_track_and_capture(*args, response_written=None, **kwargs):
        nonlocal captured_response_written, captured_result
        captured_response_written = response_written
        captured_result = original_start_dispatch_track(*args, response_written=response_written, **kwargs)
        return captured_result

    monkeypatch.setattr("goodteam_os.web.start_dispatch_track", start_dispatch_track_and_capture)
    original_write_status = web._write_dispatch_track_status

    def write_status_and_signal(*args, **kwargs):
        original_write_status(*args, **kwargs)
        if kwargs.get("status") == "completed":
            completed_status_written.set()

    monkeypatch.setattr("goodteam_os.web._write_dispatch_track_status", write_status_and_signal)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    try:
        simulate_post(
            "/api/dispatch-track",
            {
                "name": "my-track",
                "role": "backend",
                "adapter": "codex",
                "task": "Implement backend dispatch.",
            },
            headers={"Host": "127.0.0.1:8788", "Origin": "http://127.0.0.1:8788"},
            wfile=BrokenResponseStream(),
        )
    except OSError as exc:
        assert str(exc) == "client disconnected"
    else:
        raise AssertionError("broken response stream should raise")

    assert captured_response_written is not None
    assert captured_response_written.is_set()
    assert captured_result is not None
    assert dispatch_entered.wait(timeout=1)
    assert completed_status_written.wait(timeout=1)
    status_path = workspace / "state" / "dispatch-tracks" / f"dispatch-track-{captured_result['run_id']}.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["run_id"] == captured_result["run_id"]
    assert status["name"] == "my-track"
    assert status["worktree"] == str(worktree)
    assert status["status"] == "completed"
    assert status["error"] == ""


def test_api_dispatch_track_rejects_cross_origin_before_dispatch(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dispatch setup should not run for cross-origin requests")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fail_if_called)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.test"},
    )

    assert 403 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "same_origin_required"
    assert payload["request_id"]


def test_api_dispatch_track_legacy_path_rejects_cross_origin_before_dispatch(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("dispatch setup should not run for cross-origin requests")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fail_if_called)
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/dispatch-track",
        {
            "name": "my-track",
            "role": "backend",
            "adapter": "codex",
            "task": "Implement backend dispatch.",
        },
        headers={"Host": "127.0.0.1:8788", "Origin": "https://evil.test"},
    )

    assert 403 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "same_origin_required"
    assert payload["request_id"]


def test_api_dispatch_track_rejects_bad_input(tmp_path, monkeypatch):
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "Bad Name", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "name" in payload["message"]


def test_api_dispatch_track_rejects_unsafe_target(tmp_path, monkeypatch):
    worktree = tmp_path / "goodteam-dev-my-track"

    def fake_ensure_worktree(workspace, *, name, branch):
        raise AssertionError("worktree must not be created before safety passes")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr(
        "goodteam_os.web.ensure_dispatch_project_root_is_safe",
        lambda project_root, *, projects_dir: {"safe": False, "reason": "project_root is inside configured project repo"},
    )
    simulate_post = capture_post_handler(tmp_path, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "configured project repo" in payload["message"]
    assert not worktree.exists()


def test_api_dispatch_track_rejects_unsafe_target_without_creating_worktree(tmp_path, monkeypatch):
    configured_repo = tmp_path / "served-repo"
    workspace = configured_repo / "workspace"
    workspace.mkdir(parents=True)
    write_project_config(workspace, repos={"app": configured_repo})
    worktree = configured_repo / "goodteam-dev-my-track"

    def fail_if_git_runs(*args, **kwargs):
        raise AssertionError("git worktree add must not run for unsafe targets")

    monkeypatch.setattr("goodteam_os.web.subprocess.run", fail_if_git_runs)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "configured project repo" in payload["message"]
    assert not worktree.exists()


def test_link_dispatch_worktree_venv_uses_absolute_target_for_relative_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "goodteam-dev-my-track"
    (workspace / ".venv" / "bin").mkdir(parents=True)
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)

    web._link_dispatch_worktree_venv(Path("workspace"), worktree)

    bin_link = worktree / ".venv" / "bin"
    assert bin_link.is_symlink()
    assert bin_link.readlink().is_absolute()
    assert bin_link.resolve() == workspace.resolve() / ".venv" / "bin"


def test_api_dispatch_track_safety_uses_served_workspace_projects_dir(tmp_path, monkeypatch):
    configured_repo = tmp_path / "served-repo"
    workspace = configured_repo / "workspace"
    worktree = configured_repo / "goodteam-dev-my-track"
    workspace.mkdir(parents=True)
    write_project_config(workspace, repos={"app": configured_repo})

    def fake_ensure_worktree(workspace, *, name, branch):
        assert workspace == configured_repo / "workspace"
        assert name == "my-track"
        assert branch == "feat/my-track"
        worktree.mkdir(parents=True)
        return worktree

    def fail_if_dispatch_starts(**kwargs):
        raise AssertionError("dispatch should not start for unsafe targets")

    monkeypatch.setattr("goodteam_os.web._ensure_dispatch_worktree", fake_ensure_worktree)
    monkeypatch.setattr("goodteam_os.web.dispatch_tracks", fail_if_dispatch_starts)
    simulate_post = capture_post_handler(workspace, monkeypatch)

    response = simulate_post(
        "/api/dispatch-track",
        {"name": "my-track", "role": "backend", "adapter": "codex", "task": "Implement."},
        headers={"Host": "127.0.0.1:8788"},
    )

    assert 400 in response.responses
    payload = json.loads(response.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert "configured project repo" in payload["message"]


def test_api_state_exposes_worker_backend_options_and_roles(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "real.local.yaml").write_text(
        "name: Real Product\nkey: REAL\nworker_backend:\n  mode: codex\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    class SimulatedHandler(captured_handler_class):
        def __init__(self):
            self.request = FakeRequest()
            self.client_address = ("127.0.0.1", 12345)
            self.server = None
            self.headers = {}
            self.path = "/api/state?project=REAL"
            self.wfile = BytesIO()
            self.rfile = BytesIO()
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
    handler.do_GET()

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    context = payload["start_context"]
    assert context["worker_backend_options"] == [
        "codex",
        "claude",
        "gemini",
        "agy",
        "opencode",
        "pi",
        "fake",
        "noop",
        "human",
    ]
    assert context["worker_roles"] == [
        "orchestrator",
        "research",
        "product_requirement",
        "architect",
        "review",
        "planner",
        "ui",
        "implementation",
        "frontend",
        "backend",
        "qa",
        "code_review",
        "release",
        "retro_memory",
    ]


def test_api_state_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-runtime"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-runtime",
                "role": "implementation",
                "adapter": "codex",
                "status": "completed",
                "updated_at": "2026-05-31T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/state?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["type"] == "project"
    assert payload["worker_jobs"] == [
        {
            "id": "job-runtime",
            "role": "implementation",
            "adapter": "codex",
            "status": "completed",
            "updated_at": "2026-05-31T00:00:00+00:00",
            "heartbeat_at": "",
            "pid": "",
            "summary": "",
        }
    ]


def test_api_state_includes_resource_descriptors(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    write_project_config(tmp_path, repos={"root": project_root})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    register_markdown_artifact(state, "prd", prd)
    register_task_ledger(state, project_root / ".goodteam" / "tasks" / "tasks.json")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/state?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    resources = {item["key"]: item for item in payload["resources"]}
    assert resources["prd"]["kind"] == "markdown_artifact"
    assert "edit" in resources["prd"]["capabilities"]
    assert resources["task_ledger"]["kind"] == "task_ledger"
    assert "board_source" in resources["task_ledger"]["capabilities"]


def test_api_resources_returns_resource_descriptors(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    project_root.mkdir()
    write_project_config(tmp_path, repos={"root": project_root})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    register_task_ledger(state, project_root / ".goodteam" / "tasks" / "tasks.json")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/resources?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert payload["resources"][0]["key"] == "task_ledger"
    assert payload["resources"][0]["kind"] == "task_ledger"


def test_api_project_config_updates_worker_backends(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    projects = tmp_path / "projects"
    projects.mkdir()
    config = projects / "real.local.yaml"
    config.write_text(
        """name: Real Product
key: REAL
repos:
  app: /tmp/app
worker_backend:
  mode: fake
workers:
  implementation: fake
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    def simulate_post(body_dict):
        class SimulatedHandler(captured_handler_class):
            def __init__(self):
                self.request = FakeRequest()
                self.client_address = ("127.0.0.1", 12345)
                self.server = None
                body_bytes = json.dumps(body_dict).encode("utf-8")
                self.headers = {"Content-Length": str(len(body_bytes))}
                self.path = "/api/project-config"
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

    handler = simulate_post(
        {
            "project": "REAL",
            "worker_backend_mode": "codex",
            "workers": {
                "product_requirement": "anthropic",
                "architect": "claude",
                "planner": "gemini",
                "ui": "antigravity",
                "implementation": "agy",
                "qa": "codex",
            },
            "role_models": {
                "architect": "claude-sonnet-4-6",
                "implementation": "gemini-2.5-pro",
                "qa": "default",
            },
        }
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["role_worker_backends"] == {
        "product_requirement": "anthropic",
        "architect": "claude",
        "planner": "gemini",
        "ui": "antigravity",
        "implementation": "agy",
        "qa": "codex",
    }
    assert payload["role_model_overrides"] == {
        "architect": "claude-sonnet-4-6",
        "implementation": "gemini-2.5-pro",
    }
    assert set(payload["worker_backends_preflight"]) == {
        "codex",
        "anthropic",
        "claude",
        "gemini",
        "agy",
        "antigravity",
    }
    assert "mode: codex" in config.read_text(encoding="utf-8")
    assert "  product_requirement: anthropic" in config.read_text(encoding="utf-8")
    assert "  architect: claude" in config.read_text(encoding="utf-8")
    assert "  planner: gemini" in config.read_text(encoding="utf-8")
    assert "  ui: antigravity" in config.read_text(encoding="utf-8")
    assert "  implementation: agy" in config.read_text(encoding="utf-8")
    assert "  qa: codex" in config.read_text(encoding="utf-8")
    content = config.read_text(encoding="utf-8")
    role_model_block = content.split("role_model_overrides:\n", 1)[1]
    assert "  architect: claude-sonnet-4-6" in role_model_block
    assert "  implementation: gemini-2.5-pro" in role_model_block
    assert "  qa:" not in role_model_block

    invalid = simulate_post(
        {
            "project": "REAL",
            "worker_backend_mode": "not-real",
            "workers": {},
        }
    )

    assert 400 in invalid.responses
    invalid_payload = json.loads(invalid.wfile.getvalue().decode("utf-8"))
    assert invalid_payload["code"] == "invalid_request"
    assert invalid_payload["request_id"]


def test_api_project_config_still_creates_local_project_config(tmp_path, monkeypatch):
    captured_handler_class = None

    def mock_server(addr, handler_class):
        nonlocal captured_handler_class
        captured_handler_class = handler_class

        class FakeServer:
            def serve_forever(self):
                pass

        return FakeServer()

    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr("goodteam_os.web.ThreadingHTTPServer", mock_server)
    serve(tmp_path, host="127.0.0.1", port=8788)

    class SimulatedHandler(captured_handler_class):
        def __init__(self):
            self.request = FakeRequest()
            self.client_address = ("127.0.0.1", 12345)
            self.server = None
            body_bytes = json.dumps(
                {
                    "project_name": "Real Product",
                    "project_key": "REAL",
                    "docs_path": str(docs),
                    "worker_backend_mode": "fake",
                }
            ).encode("utf-8")
            self.headers = {"Content-Length": str(len(body_bytes))}
            self.path = "/api/project-config"
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

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert Path(payload["project_config"]).is_file()


def test_api_memory_returns_empty_roles_without_memory(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"project_key": "REAL", "roles": []}
    assert handler.response_headers.get("Access-Control-Allow-Origin") is None


def test_api_agents_returns_discovered_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "goodteam_os.web.discover_installed_agents",
        lambda: [
            {
                "backend": "claude",
                "aliases": ["anthropic", "claude"],
                "available": True,
                "ready": True,
                "command": "claude",
                "command_path": "/fake/bin/claude",
                "hint": "ready",
                "models": ["claude-sonnet-4-6", "claude-opus-4-5"],
                "model_default": "claude-sonnet-4-6",
            }
        ],
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/agents")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "agents": [
            {
                "backend": "claude",
                "aliases": ["anthropic", "claude"],
                "available": True,
                "ready": True,
                "command": "claude",
                "command_path": "/fake/bin/claude",
                "hint": "ready",
                "models": ["claude-sonnet-4-6", "claude-opus-4-5"],
                "model_default": "claude-sonnet-4-6",
            }
        ]
    }
    assert handler.response_headers.get("Access-Control-Allow-Origin") is None


def test_api_messages_returns_messages_for_run(tmp_path, monkeypatch):
    message = append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="implementation",
        to=["qa"],
        kind="ask",
        body="Please review the message ledger.",
        gate="rfc",
    )
    append_message(
        tmp_path,
        "REAL",
        "run-2",
        from_role="implementation",
        to=["qa"],
        kind="ask",
        body="A different run should not be returned.",
        gate="rfc",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/messages?project=REAL&run=run-1")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"messages": [message]}
    assert handler.response_headers.get("Access-Control-Allow-Origin") is None


def test_api_memory_returns_role_summaries_event_count_and_latest_retrieval(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.ensure_role_memory("architect", "Architect", "Real Product")
    memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Architecture decisions must keep the dashboard API read-only.",
        source="job-old/result.md",
    )
    memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory panel should lazy-load per-role retrieval packets.",
        source="job-new/result.md",
    )
    jobs_root = project_root / ".goodteam" / "workers" / "jobs"
    old_job = jobs_root / "job-old"
    new_job = jobs_root / "job-new"
    old_job.mkdir(parents=True)
    new_job.mkdir(parents=True)
    (old_job / "job.json").write_text(
        json.dumps({"id": "job-old", "role": "architect", "adapter": "fake", "status": "completed", "updated_at": "2026-05-30T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (old_job / "role_pickup_packet.json").write_text(
        json.dumps({"relevant_events": [{"id": "mem:old", "score": 0.1, "horizon": "long-term", "text": "old", "source": "old", "timestamp": "old"}]}),
        encoding="utf-8",
    )
    (new_job / "job.json").write_text(
        json.dumps({"id": "job-new", "role": "architect", "adapter": "fake", "status": "completed", "updated_at": "2026-05-31T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (new_job / "role_pickup_packet.json").write_text(
        json.dumps(
            {
                "relevant_events": [
                    {
                        "id": "mem:panel",
                        "score": 1.2,
                        "horizon": "mid-term",
                        "text": "Memory panel should lazy-load per-role retrieval packets.",
                        "source": "job-new/result.md",
                        "timestamp": "2026-05-31T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert len(payload["roles"]) == 1
    role = payload["roles"][0]
    assert role["role"] == "architect"
    assert "dashboard API read-only" in role["summaries"]["long-term"]
    assert "lazy-load per-role retrieval packets" in role["summaries"]["mid-term"]
    assert role["event_count"] == 2
    assert "No role-specific notes recorded yet" in role["short_term"]
    assert role["latest_retrieval"]["job_id"] == "job-new"
    assert role["latest_retrieval"]["relevant_events"] == [
        {
            "id": "mem:panel",
            "score": 1.2,
            "horizon": "mid-term",
            "text": "Memory panel should lazy-load per-role retrieval packets.",
            "source": "job-new/result.md",
            "timestamp": "2026-05-31T00:00:00+00:00",
        }
    ]


def test_api_memory_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)

    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    (projects / "real.local.yaml").write_text(
        f"name: Real Product\nkey: REAL\nrepos:\n  backend: {backend_repo}\n  frontend: {frontend_repo}\n",
        encoding="utf-8",
    )

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.ensure_role_memory("architect", "Architect", "Real Product")
    memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Runtime-root memory should load even when context packet path is empty.",
        source="job-runtime/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=REAL")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert len(payload["roles"]) == 1
    role = payload["roles"][0]
    assert role["role"] == "architect"
    assert "Runtime-root memory should load" in role["summaries"]["long-term"]
    assert role["event_count"] == 1


def test_api_memory_event_returns_full_event(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory event drill-down should expose the full source event.",
        source="job-x/result.md",
    )
    event_id = event_handle(event)

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "id": event_id,
        "role": "architect",
        "horizon": "mid-term",
        "timestamp": event["timestamp"],
        "source": "job-x/result.md",
        "text": "Memory event drill-down should expose the full source event.",
        "action": "add",
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
        "cited_by_jobs": [],
    }


def test_api_memory_events_returns_browsable_role_events(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    old_event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory browse API exposes the older card behavior.",
        source="job-old/result.md",
    )
    old_id = event_handle(old_event)
    new_event = memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory browse API exposes the current card behavior.",
        source="job-new/result.md",
        supersedes=old_id,
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=architect&limit=10")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["project_key"] == "REAL"
    assert payload["role"] == "architect"
    assert [event["id"] for event in payload["events"]] == [event_handle(new_event), old_id]
    assert payload["events"][0]["body"] == "Memory browse API exposes the current card behavior."
    assert payload["events"][0]["is_superseded"] is False
    assert payload["events"][1]["is_superseded"] is True


def test_api_memory_events_uses_query_and_limit(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Memory browse search mentions database only.",
        source="job-db/result.md",
    )
    best = memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Memory browse search mentions database and sqlite.",
        source="job-sqlite/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)(
        "/api/memory/events?project=REAL&role=implementation&q=database%20sqlite&limit=1"
    )

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert [event["id"] for event in payload["events"]] == [event_handle(best)]


def test_api_memory_events_returns_empty_for_unknown_role(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=missing")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {"project_key": "REAL", "role": "missing", "events": []}


def test_api_memory_events_caps_limit(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    for index in range(205):
        memory_store.append_memory_event(
            "qa",
            horizon="short-term",
            text=f"Memory browse API cap event {index}.",
            source=f"job-{index}/result.md",
        )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/events?project=REAL&role=qa&limit=500")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert len(payload["events"]) == 200


def test_api_memory_event_returns_supersede_details(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    old_event = memory_store.append_memory_event(
        "implementation",
        horizon="mid-term",
        text="API contract uses the v1 memory detail payload.",
        source="job-old/result.md",
    )
    old_id = event_handle(old_event)
    new_event = memory_store.append_memory_event(
        "implementation",
        horizon="mid-term",
        text="API contract uses the v2 memory detail payload.",
        source="job-new/result.md",
        supersedes=old_id,
    )
    new_id = event_handle(new_event)

    old_handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={old_id}")
    new_handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={new_id}")

    assert 200 in old_handler.responses
    old_payload = json.loads(old_handler.wfile.getvalue().decode("utf-8"))
    assert old_payload["supersedes"] == ""
    assert old_payload["is_superseded"] is True
    assert new_id in old_payload["superseded_by"]

    assert 200 in new_handler.responses
    new_payload = json.loads(new_handler.wfile.getvalue().decode("utf-8"))
    assert new_payload["supersedes"] == old_id
    assert new_payload["superseded_by"] == []
    assert new_payload["is_superseded"] is False


def test_api_memory_event_returns_citing_jobs(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "implementation",
        horizon="short-term",
        text="Cited detail payload includes jobs that used this memory.",
        source="job-memory/result.md",
    )
    event_id = event_handle(event)
    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-citing"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": "job-citing",
                "role": "implementation",
                "adapter": "fake",
                "status": "queued",
                "updated_at": "2026-06-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "summary": "Used cited memory.",
                "updated_at": "2026-06-02T00:00:00+00:00",
                "memory_citations": [event_id],
            }
        ),
        encoding="utf-8",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["cited_by_jobs"] == [
        {
            "id": "job-citing",
            "role": "implementation",
            "adapter": "fake",
            "status": "completed",
            "updated_at": "2026-06-02T00:00:00+00:00",
            "summary": "Used cited memory.",
        }
    ]


def test_api_memory_event_reads_runtime_root_when_context_packet_path_empty(tmp_path, monkeypatch):
    project_root = tmp_path / "target"
    backend_repo = project_root / "backend"
    frontend_repo = project_root / "frontend"
    backend_repo.mkdir(parents=True)
    frontend_repo.mkdir(parents=True)
    write_project_config(tmp_path, repos={"backend": backend_repo, "frontend": frontend_repo})

    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = ""
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    event = memory_store.append_memory_event(
        "architect",
        horizon="long-term",
        text="Runtime-root drill-down should load even when context packet path is empty.",
        source="job-runtime/result.md",
    )
    event_id = event_handle(event)

    handler = capture_get_handler(tmp_path, monkeypatch)(f"/api/memory/event?project=REAL&id={event_id}")

    assert 200 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload == {
        "id": event_id,
        "role": "architect",
        "horizon": "long-term",
        "timestamp": event["timestamp"],
        "source": "job-runtime/result.md",
        "text": "Runtime-root drill-down should load even when context packet path is empty.",
        "action": "add",
        "supersedes": "",
        "superseded_by": [],
        "is_superseded": False,
        "cited_by_jobs": [],
    }


def test_api_memory_event_returns_404_for_unknown_id(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    memory_store = RoleMemoryStore(project_root)
    memory_store.append_memory_event(
        "architect",
        horizon="mid-term",
        text="Memory event drill-down should expose the full source event.",
        source="job-x/result.md",
    )

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/event?project=REAL&id=mem:missing")

    assert 404 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "memory_event_not_found"
    assert payload["details"] == {"event_id": "mem:missing"}
    assert payload["request_id"]


def test_api_memory_event_returns_400_for_missing_id(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    project_root = tmp_path / "target"
    project_root.mkdir()
    state = create_initial_state("run-1", "REAL", "Build the product.")
    state.context_packet_path = str(project_root / ".goodteam" / "memory" / "project-memory.md")
    StateStore(tmp_path).write_project_current(state)

    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory/event?project=REAL")

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]


def test_api_memory_returns_400_for_bad_project(tmp_path, monkeypatch):
    handler = capture_get_handler(tmp_path, monkeypatch)("/api/memory?project=MISSING")

    assert 400 in handler.responses
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["code"] == "invalid_request"
    assert payload["request_id"]
