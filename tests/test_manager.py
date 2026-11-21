"""Unit tests for ManagerToolset, ManagerHub streaming, auto-wakeup, and app-server protocol."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
import time
import pytest

from goodteam.agents.adapters import AdapterResult, set_mock_handler
from goodteam.agents.roles import save_role, seed_project_roles
from goodteam.agents.runner import SubagentRunner
from goodteam.agents.worktree import WorktreeError
from goodteam.api.events import EventBus
from goodteam.manager.session import ManagerHub
from goodteam.manager.tools import ManagerToolset
from goodteam.providers import ProviderStore
from goodteam.skills.installer import ensure_builtins
from goodteam.store import Store


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def test_manager_toolset_instructions_tools_and_integrate_run(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    bus = EventBus()

    try:
        ensure_builtins(store)
        proj = store.create_project(
            name="demo",
            path=str(repo),
            settings={"manager": {"additional_prompt": "Always cite files."}},
        )
        seed_project_roles(store, proj["id"])
        save_role(store, proj["id"], {
            "key": "coder",
            "name": "Coder",
            "execution_policy": "workspace_write",
            "write_domains": ["src"],
            "adapter": "mock",
        })

        def mock_run(ctx, on_event, stop_ev):
            if "bad" in ctx.prompt:
                (ctx.worktree_path / "outside.txt").write_text("nope\n")
                return AdapterResult(summary="violated", exit_code=0)
            (ctx.worktree_path / "src" / "app.py").write_text("VALUE = 2\n")
            return AdapterResult(summary="Updated app.py", exit_code=0)

        set_mock_handler(mock_run)
        runner = SubagentRunner(store, providers, state_dir=state_dir, event_bus=bus)
        sub_q = bus.subscribe(proj["id"])
        try:
            toolset = ManagerToolset(
                store, runner, proj["id"], state_dir=state_dir,
                base_ref_override="goodteam/iter-it1", iteration_id="it1",
            )
            instr = toolset.instructions()
            assert "Always cite files." in instr
            assert "builtin:project-memory" in instr
            assert "goodteam/iter-it1" in instr
            spec_names = {s["name"] for s in toolset.specs()}
            assert {"spawn_agent", "manage_tasks", "read_memory", "write_memory", "integrate_run", "skill_resource"} <= spec_names

            # Tasks emit SSE event
            t_res = toolset.call("manage_tasks", {"action": "create", "title": "Add v2 feature"})
            task_id = t_res["task"]["id"]
            ev_kind, ev_data = sub_q.get(timeout=2)
            assert ev_kind == "task_updated" and ev_data["id"] == task_id

            toolset.call("manage_tasks", {"action": "update", "task_id": task_id, "status": "in_progress"})
            assert store.get_task(task_id)["status"] == "in_progress"

            # Memory read/write
            toolset.call("write_memory", {"filename": "project.md", "content": "Uses mock adapter.", "mode": "replace"})
            assert "Uses mock adapter." in toolset.call("read_memory", {"filename": "project.md"})["content"]
            assert len(toolset.call("read_memory", {"query": "mock"})["hits"]) == 1

            # Skill resource read
            res = toolset.call("skill_resource", {"skill_id": "builtin:project-memory", "path": "SKILL.md"})
            assert "project-memory" in res["content"].lower() or "memory" in res["content"].lower()

            # Spawn & integrate valid run
            spawned = toolset.call("spawn_agent", {"role": "coder", "task": "Update VALUE", "base_ref": "main"})
            run = runner.wait(spawned["run_id"], timeout=10)
            assert run["status"] == "completed" and run["scope_ok"] is True

            integrated = toolset.call("integrate_run", {"run_id": run["id"], "iteration_id": "it1"})
            assert integrated["iteration_branch"] == "goodteam/iter-it1"
            assert integrated["merge_commit"]
            # Main checkout remains untouched
            assert "VALUE = 1" in (repo / "src" / "app.py").read_text()

            # Out-of-scope run cannot be integrated
            bad = toolset.call("spawn_agent", {"role": "coder", "task": "bad edit", "base_ref": "main"})
            bad_run = runner.wait(bad["run_id"], timeout=10)
            assert bad_run["status"] == "failed" and bad_run["scope_ok"] is False
            with pytest.raises(WorktreeError):
                toolset.call("integrate_run", {"run_id": bad_run["id"], "iteration_id": "it1"})
        finally:
            bus.unsubscribe(proj["id"], sub_q)
            runner.shutdown()
    finally:
        set_mock_handler(None)
        store.close()


def test_manager_hub_auto_wakeup_on_subagent_completion(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    bus = EventBus()
    wake_seen = threading.Event()
    prompts_seen: list[str] = []

    def mock_turn(project_id, text, toolset, on_delta):
        prompts_seen.append(text)
        if "Sub-agent execution finished" in text:
            on_delta("All done: ")
            on_delta("summarized.")
            wake_seen.set()
            return "All done: summarized."
        spawned = toolset.call("spawn_agent", {"role": "coder", "task": "Write greeting"})
        on_delta(f"Spawned {spawned['run_id']}")
        return f"Spawned {spawned['run_id']}"

    try:
        proj = store.create_project(name="demo", path=str(repo))
        save_role(store, proj["id"], {
            "key": "coder", "name": "Coder", "execution_policy": "workspace_write",
            "write_domains": ["src"], "adapter": "mock",
        })
        set_mock_handler(lambda ctx, on_ev, stop_ev: AdapterResult(summary="Wrote greeting", exit_code=0))

        hub: ManagerHub | None = None
        runner = SubagentRunner(
            store, providers, state_dir=state_dir, event_bus=bus,
            on_complete=lambda r: hub.on_subagent_finished(r) if hub else None,
        )
        hub = ManagerHub(store, providers, runner, state_dir=state_dir, event_bus=bus, mock_turn_fn=mock_turn)
        try:
            res = hub.session(proj["id"]).send("Please write greeting", sync=True)
            assert res["assistant_message"]["meta"]["status"] == "completed"
            assert wake_seen.wait(timeout=10), "Manager auto-wakeup did not trigger after subagent completed"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                msgs = store.list_messages(proj["id"])
                if any("All done: summarized." in m["content"] for m in msgs):
                    break
                time.sleep(0.05)
            msgs = store.list_messages(proj["id"])
            assert any(m["role"] == "system" and "Sub-agent execution finished" in m["content"] for m in msgs)
            assert any(m["role"] == "manager" and "All done: summarized." in m["content"] for m in msgs)
        finally:
            hub.close()
            runner.shutdown()
    finally:
        set_mock_handler(None)
        store.close()


def test_manager_codex_app_server_protocol_and_error_status(tmp_path, monkeypatch):
    """Test real CodexAppServer JSONL transport with a deterministic fake codex CLI."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "def send(obj):\n"
        "    sys.stdout.write(json.dumps(obj) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    rid = msg.get('id')\n"
        "    if m == 'initialize':\n"
        "        send({'id': rid, 'result': {'serverInfo': {'name': 'fake'}}})\n"
        "    elif m == 'thread/start':\n"
        "        send({'id': rid, 'result': {'thread': {'id': 'thr-1'}}})\n"
        "    elif m == 'thread/resume':\n"
        "        send({'id': rid, 'result': {'thread': {'id': msg['params']['threadId']}}})\n"
        "    elif m == 'turn/start':\n"
        "        txt = msg['params']['input'][0]['text']\n"
        "        send({'id': rid, 'result': {'turn': {'id': 'turn-1'}}})\n"
        "        if 'FAIL' in txt:\n"
        "            send({'method': 'turn/completed', 'params': {'threadId': 'thr-1', 'turn': {'id': 'turn-1', 'status': 'failed', 'error': {'message': 'simulated failure'}}}})\n"
        "        else:\n"
        "            send({'id': 'srv-tool-1', 'method': 'item/tool/call', 'params': {'tool': 'write_memory', 'arguments': {'filename': 'project.md', 'content': 'From tool', 'mode': 'replace'}}})\n"
        "    elif rid == 'srv-tool-1':\n"
        "        send({'method': 'item/agentMessage/delta', 'params': {'delta': 'Tool saved memory.'}})\n"
        "        send({'method': 'turn/completed', 'params': {'threadId': 'thr-1', 'turn': {'id': 'turn-1', 'status': 'completed'}}})\n",
        encoding="utf-8",
    )
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GOODTEAM_CODEX_PATH", str(fake_codex))

    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    runner = SubagentRunner(store, providers, state_dir=state_dir)
    hub = ManagerHub(store, providers, runner, state_dir=state_dir)
    try:
        proj = store.create_project(name="demo", path=str(repo))
        ok = hub.session(proj["id"]).send("Save memory please", sync=True)
        assert ok["assistant_message"]["meta"]["status"] == "completed"
        assert "Tool saved memory." in ok["assistant_message"]["content"]
        assert "From tool" in (repo / ".goodteam" / "memory" / "project.md").read_text()
        assert store.get_kv(f"manager_thread:{proj['id']}") == "thr-1"

        # Regression test: failed turn with empty deltas must save status='failed'
        fail = hub.session(proj["id"]).send("Trigger FAIL", sync=True)
        assert fail["assistant_message"]["meta"]["status"] == "failed"
        assert "simulated failure" in fail["assistant_message"]["content"]
    finally:
        hub.close()
        runner.shutdown()
        os.chmod(fake_codex, 0o644)
        store.close()
