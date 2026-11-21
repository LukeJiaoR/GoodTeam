"""Unit tests for SQLite Store."""
from __future__ import annotations

from goodteam.store import Store


def test_store_project_role_task_run_delivery_lifecycle(tmp_path):
    store = Store(state_dir=tmp_path)
    try:
        proj = store.create_project(name="demo", path=str(tmp_path / "repo"), default_branch="main")
        assert proj["name"] == "demo"
        assert store.get_project_by_path(str(tmp_path / "repo"))["id"] == proj["id"]

        role = store.upsert_role(
            project_id=proj["id"],
            key="analyst",
            name="Data Analyst",
            description="Analyzes logs",
            system_prompt="Stay read-only",
            execution_policy="read_only",
            write_domains=[],
            allowed_tools=["Read", "Grep"],
            adapter="claude",
            model="sonnet",
            skill_bindings=[{"skill_id": "builtin:research", "mode": "required"}],
        )
        assert role["key"] == "analyst"
        assert role["allowed_tools"] == ["Read", "Grep"]

        task = store.create_task(project_id=proj["id"], title="Inspect logs", assigned_role="analyst")
        assert task["status"] == "todo"
        updated = store.update_task(task["id"], status="in_progress")
        assert updated["status"] == "in_progress"

        msg = store.add_message(project_id=proj["id"], role="user", content="Check logs")
        assert len(store.list_messages(proj["id"])) == 1

        run = store.create_run(
            project_id=proj["id"],
            task_id=task["id"],
            parent_message_id=msg["id"],
            role_key="analyst",
            adapter="claude",
            prompt="Analyze logs",
            execution_policy="read_only",
            base_ref="main",
            branch_name="goodteam/run-1",
        )
        store.append_run_event(run["id"], 1, "text", {"delta": "reading"})
        assert len(store.list_run_events(run["id"])) == 1

        finished = store.update_run(
            run["id"], status="completed", summary="Done", changed_files=[], scope_ok=True
        )
        assert finished["status"] == "completed"
        assert finished["scope_ok"] is True

        deliv = store.create_delivery(
            project_id=proj["id"], run_id=run["id"], branch_name="goodteam/run-1", base_ref="main"
        )
        assert deliv["status"] == "pending"
        store.record_usage(
            project_id=proj["id"], run_id=run["id"], provider_id=None,
            adapter="claude", model="sonnet", input_tokens=100, output_tokens=40,
        )
        summary = store.usage_summary(proj["id"])
        assert summary[0]["input_tokens"] == 100
        assert summary[0]["output_tokens"] == 40

        assert store.delete_project(proj["id"]) is True
        assert store.get_project(proj["id"]) is None
        assert store.list_tasks(proj["id"]) == []
        assert store.list_runs(proj["id"]) == []
    finally:
        store.close()
