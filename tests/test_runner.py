"""Unit tests for custom Roles, Skill injection in Runner, concurrency, and Stop."""
from __future__ import annotations

import subprocess
import threading
import time

from goodteam.agents.adapters import AdapterResult, set_mock_handler
from goodteam.agents.roles import save_role, seed_project_roles
from goodteam.agents.runner import SubagentRunner
from goodteam.providers import ProviderStore
from goodteam.skills.installer import ensure_builtins
from goodteam.store import Store


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "src").mkdir()
    (path / "src" / "a.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def test_custom_role_skill_injection_and_delivery_flow(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    completed: list[dict] = []

    try:
        ensure_builtins(store)
        proj = store.create_project(name="demo", path=str(repo))
        roles = seed_project_roles(store, proj["id"])
        assert {r["key"] for r in roles} == {"researcher", "planner", "coder", "reviewer", "qa"}

        custom = save_role(store, proj["id"], {
            "key": "docs_writer",
            "name": "Docs Writer",
            "description": "Writes docs",
            "system_prompt": "Only write markdown in docs/.",
            "execution_policy": "workspace_write",
            "write_domains": ["docs"],
            "adapter": "mock",
            "skill_bindings": [{"skill_id": "builtin:coding", "mode": "required"}],
        })
        assert custom["key"] == "docs_writer"

        observed_skills = {}

        def handler(ctx, on_event, stop_ev):
            observed_skills["text"] = ctx.skill_prompt
            observed_skills["staged"] = ctx.staged_skills_dir
            assert ctx.staged_skills_dir is not None
            assert (ctx.staged_skills_dir / "builtin__coding" / "SKILL.md").is_file()
            # Ensure staged skills did NOT pollute git worktree!
            assert not (ctx.worktree_path / "SKILL.md").exists()
            (ctx.worktree_path / "docs").mkdir(exist_ok=True)
            (ctx.worktree_path / "docs" / "guide.md").write_text("# Guide\n")
            on_event("message", {"text": "Created guide.md"})
            return AdapterResult(
                summary="Created docs/guide.md",
                usage={"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 20},
                exit_code=0,
            )

        set_mock_handler(handler)
        runner = SubagentRunner(store, providers, state_dir=state_dir, on_complete=completed.append)
        try:
            run = runner.spawn(proj["id"], "docs_writer", "Write user guide")
            finished = runner.wait(run["id"], timeout=10)
        finally:
            runner.shutdown()

        assert finished["status"] == "completed"
        assert finished["scope_ok"] is True
        assert finished["changed_files"] == ["docs/guide.md"]
        assert "Scoped Implementation" in observed_skills["text"]
        assert len(completed) == 1
        # Staged skill dir cleaned up after run
        assert not observed_skills["staged"].exists()
        # Delivery auto-created for write run
        deliveries = store.list_deliveries(proj["id"])
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "pending"
        # Usage recorded
        assert store.usage_summary(proj["id"])[0]["input_tokens"] == 50
    finally:
        set_mock_handler(None)
        store.close()


def test_runner_scope_violation_and_stop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    started = threading.Event()

    try:
        proj = store.create_project(name="demo", path=str(repo))
        save_role(store, proj["id"], {
            "key": "scoped_coder",
            "name": "Scoped",
            "execution_policy": "workspace_write",
            "write_domains": ["src"],
            "adapter": "mock",
        })

        def slow_or_violate(ctx, on_event, stop_ev):
            if "violate" in ctx.prompt:
                (ctx.worktree_path / "README.md").write_text("forbidden\n")
                return AdapterResult(summary="edited readme", exit_code=0)
            started.set()
            while not stop_ev.is_set():
                time.sleep(0.02)
            return AdapterResult(summary="", exit_code=1, error="Stopped by user.")

        set_mock_handler(slow_or_violate)
        runner = SubagentRunner(store, providers, state_dir=state_dir)
        try:
            r1 = runner.spawn(proj["id"], "scoped_coder", "please violate scope")
            f1 = runner.wait(r1["id"], timeout=10)
            assert f1["status"] == "failed"
            assert f1["scope_ok"] is False

            r2 = runner.spawn(proj["id"], "scoped_coder", "long running task")
            assert started.wait(timeout=5)
            runner.stop(r2["id"])
            f2 = runner.wait(r2["id"], timeout=10)
            assert f2["status"] == "stopped"
        finally:
            runner.shutdown()
    finally:
        set_mock_handler(None)
        store.close()
