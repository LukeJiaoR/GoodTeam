"""Unit tests for bounded multi-round Autopilot controller."""
from __future__ import annotations

import subprocess
import threading
import time
import pytest

from goodteam.agents.adapters import AdapterResult, set_mock_handler
from goodteam.agents.roles import save_role
from goodteam.agents.runner import SubagentRunner
from goodteam.api.events import EventBus
from goodteam.manager.autopilot import AutopilotController
from goodteam.manager.session import ManagerHub
from goodteam.providers import ProviderStore
from goodteam.store import Store


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "src").mkdir()
    (path / "src" / "mod.py").write_text("X = 0\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def _wait_autopilot_done(ctrl: AutopilotController, project_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = ctrl.status(project_id)
        if st["status"] not in {"running", "stopping"}:
            return st
        time.sleep(0.05)
    raise AssertionError(f"Autopilot did not finish; last state: {ctrl.status(project_id)}")


def test_autopilot_multi_round_iteration_branch_and_delivery(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    bus = EventBus()

    round_counter = {"n": 0}
    last_spawned: list[str] = []

    def mock_turn(project_id, text, toolset, on_delta):
        if "[Autopilot Round 1/" in text:
            round_counter["n"] += 1
            res = toolset.call("spawn_agent", {"role": "coder", "task": "Increment X to 1"})
            last_spawned.append(res["run_id"])
            return "Spawned coder for round 1."
        if "[Autopilot Round 1 Results]" in text:
            toolset.call("integrate_run", {"run_id": last_spawned[-1], "iteration_id": toolset.iteration_id})
            return "Integrated round 1."
        if "[Autopilot Round 2/" in text:
            round_counter["n"] += 1
            return "Verified all tasks complete. [AUTOPILOT_DONE]"
        return "ok"

    def mock_subagent(ctx, on_ev, stop_ev):
        (ctx.worktree_path / "src" / "mod.py").write_text("X = 1\n")
        return AdapterResult(summary="Set X = 1", exit_code=0)

    try:
        proj = store.create_project(name="demo", path=str(repo))
        save_role(store, proj["id"], {
            "key": "coder", "name": "Coder", "execution_policy": "workspace_write",
            "write_domains": ["src"], "adapter": "mock",
        })
        set_mock_handler(mock_subagent)
        runner = SubagentRunner(store, providers, state_dir=state_dir, event_bus=bus)
        hub = ManagerHub(store, providers, runner, state_dir=state_dir, event_bus=bus, mock_turn_fn=mock_turn)
        ctrl = AutopilotController(store, runner, hub, event_bus=bus)
        try:
            started = ctrl.start(proj["id"], "Upgrade mod.py to v1", max_rounds=4)
            assert started["running"] is True
            assert started["branch_name"].startswith("goodteam/iter-")

            final = _wait_autopilot_done(ctrl, proj["id"])
            assert final["status"] == "completed"
            assert final["round"] == 2
            assert round_counter["n"] == 2

            # Main checkout stays clean and untouched!
            assert (repo / "src" / "mod.py").read_text() == "X = 0\n"
            # Exactly one pending delivery created for the iteration branch
            deliveries = store.list_deliveries(proj["id"])
            assert len(deliveries) == 1
            assert deliveries[0]["branch_name"] == final["branch_name"]
            assert deliveries[0]["status"] == "pending"
        finally:
            hub.close()
            runner.shutdown()
    finally:
        set_mock_handler(None)
        store.close()


def test_autopilot_needs_human_max_rounds_and_clean_stop(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    state_dir = tmp_path / "state"
    store = Store(state_dir=state_dir)
    providers = ProviderStore(state_dir=state_dir)
    runner = SubagentRunner(store, providers, state_dir=state_dir)

    mode = {"behavior": "needs_human"}
    in_turn = threading.Event()
    release_turn = threading.Event()

    def mock_turn(project_id, text, toolset, on_delta):
        if mode["behavior"] == "needs_human":
            return "Need credentials [NEEDS_HUMAN]"
        if mode["behavior"] == "loop":
            return "Still working..."
        if mode["behavior"] == "block":
            in_turn.set()
            release_turn.wait(timeout=5)
            return "Finished late"
        return "ok"

    hub = ManagerHub(store, providers, runner, state_dir=state_dir, mock_turn_fn=mock_turn)
    ctrl = AutopilotController(store, runner, hub)
    try:
        proj = store.create_project(name="demo", path=str(repo))

        # 1. [NEEDS_HUMAN]
        ctrl.start(proj["id"], "Check creds", max_rounds=3)
        st1 = _wait_autopilot_done(ctrl, proj["id"])
        assert st1["status"] == "needs_human"

        # 2. max_rounds_reached
        mode["behavior"] = "loop"
        ctrl.start(proj["id"], "Keep iterating", max_rounds=2)
        st2 = _wait_autopilot_done(ctrl, proj["id"])
        assert st2["status"] == "max_rounds_reached"
        assert st2["round"] == 2

        # 3. Clean stop mid-turn (must become 'stopped', never stuck 'stopping' or 'max_rounds_reached')
        mode["behavior"] = "block"
        ctrl.start(proj["id"], "Slow work", max_rounds=1)
        assert in_turn.wait(timeout=5)
        with pytest.raises(ValueError):
            ctrl.start(proj["id"], "Duplicate start", max_rounds=1)
        stopping = ctrl.stop(proj["id"])
        assert stopping["status"] in {"stopping", "stopped"}
        release_turn.set()
        st3 = _wait_autopilot_done(ctrl, proj["id"])
        assert st3["status"] == "stopped"
    finally:
        release_turn.set()
        hub.close()
        runner.shutdown()
        store.close()
