from __future__ import annotations

import json
import time

from goodteam_os import supervisor
from goodteam_os.operator_queue import DurableOperatorQueue


class FakeProcess:
    def __init__(self, pid: int, *, alive: bool = True):
        self.pid = pid
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


def _component(tmp_path, *, pid: int = 100) -> supervisor.Component:
    return supervisor.Component(
        name="operator",
        command=["operator"],
        ready_path=tmp_path / "operator.ready",
        ready_token="ready-token",
        heartbeat_path=tmp_path / "operator.heartbeat.json",
        heartbeat_token="heartbeat-token",
        process=FakeProcess(pid),
        ready=True,
        status="running",
        last_heartbeat=time.time(),
        heartbeat_sequence=4,
    )


def test_component_heartbeat_is_child_owned_and_pid_bound(tmp_path):
    path = tmp_path / "heartbeat.json"
    heartbeat = supervisor.ComponentHeartbeat(path, "token-1")

    heartbeat.beat()
    heartbeat.beat()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["token"] == "token-1"
    assert payload["pid"] > 0
    assert payload["sequence"] == 2
    assert payload["updated_at"] > 0


def test_projection_does_not_manufacture_heartbeat_from_pid_liveness(tmp_path):
    component = _component(tmp_path)
    component.last_heartbeat = 123.5
    state_path = tmp_path / "server.json"
    state = {"supervisor": {"status": "healthy", "ready": True}}

    supervisor._write_projection(
        state_path,
        state,
        [component],
        queue=DurableOperatorQueue(tmp_path / "state"),
    )

    projection = json.loads(state_path.read_text(encoding="utf-8"))
    assert projection["components"]["operator"]["last_heartbeat"] == 123.5
    assert projection["components"]["operator"]["heartbeat_sequence"] == 4


def test_live_process_with_stale_child_heartbeat_is_restartable(tmp_path, monkeypatch):
    component = _component(tmp_path)
    component.last_heartbeat = time.time() - 10
    monkeypatch.setattr(supervisor, "COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", 1.0)

    failure = supervisor._component_failure(component)

    assert "heartbeat is stale" in failure


def test_readiness_timeout_reaps_each_replacement_and_exhausts_budget(tmp_path, monkeypatch):
    component = _component(tmp_path)
    queue = DurableOperatorQueue(tmp_path / "state")
    state_path = tmp_path / "server.json"
    state = {"supervisor": {"status": "degraded", "ready": False}}
    stopped: list[int] = []
    started: list[int] = []

    def stop(candidate):
        stopped.append(candidate.process.pid)
        candidate.process.alive = False

    def start(candidate, *, state_dir):
        pid = 200 + len(started)
        started.append(pid)
        candidate.process = FakeProcess(pid)
        candidate.status = "starting"
        candidate.ready = False

    monkeypatch.setattr(supervisor, "_stop_component", stop)
    monkeypatch.setattr(supervisor, "_start_component", start)
    monkeypatch.setattr(
        supervisor,
        "_wait_for_component",
        lambda candidate: (_ for _ in ()).throw(RuntimeError("readiness timed out")),
    )

    outcomes = [
        supervisor._restart_component(
            component,
            failure="component exited",
            state_dir=tmp_path,
            state_path=state_path,
            state=state,
            components=[component],
            queue=queue,
        )
        for _ in range(supervisor.MAX_COMPONENT_RESTARTS)
    ]
    terminal = supervisor._restart_component(
        component,
        failure="replacement stayed alive without readiness",
        state_dir=tmp_path,
        state_path=state_path,
        state=state,
        components=[component],
        queue=queue,
    )

    assert outcomes == ["degraded"] * supervisor.MAX_COMPONENT_RESTARTS
    assert terminal == "failed"
    assert component.status == "failed"
    assert component.ready is False
    assert component.restart_count == supervisor.MAX_COMPONENT_RESTARTS
    assert len(started) == supervisor.MAX_COMPONENT_RESTARTS
    assert len(stopped) == supervisor.MAX_COMPONENT_RESTARTS * 2
    assert component.process.poll() is not None
