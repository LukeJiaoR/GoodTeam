from __future__ import annotations

import json
import os
import time
from pathlib import Path

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


def _active_operator_queue(tmp_path, *, phase: str = "executing") -> DurableOperatorQueue:
    project_root = tmp_path / "project"
    project_root.mkdir()
    queue = DurableOperatorQueue(tmp_path / "state")
    queue.set_desired_state("running")
    item, _created = queue.enqueue(
        idempotency_key="issue:1",
        source_url="https://github.com/example/repo/issues/1",
        source_revision="issue-revision",
        project_key="TEST",
        repository_identity="example/repo",
        metadata={"project_root": str(project_root)},
    )
    claimed = queue.claim_next()
    assert claimed is not None
    previous_operation_id = f"{item['id']}:1:preflight:none"
    queue.record_operation_intent(
        item["id"],
        previous_operation_id,
        kind="preflight",
        payload={"phase": "preflight"},
    )
    queue.record_operation_result(item["id"], previous_operation_id, result={"ready": True})
    queue.transition(
        item["id"],
        expected_phase="hydrating",
        phase=phase,
        event="test_phase_selected",
    )
    return queue


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


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


def test_component_tokens_that_start_with_dash_are_single_cli_arguments(tmp_path, monkeypatch):
    tokens = iter(
        [
            "-web-ready",
            "-web-heartbeat",
            "-telegram-ready",
            "-telegram-heartbeat",
            "-operator-ready",
            "-operator-heartbeat",
        ]
    )
    monkeypatch.setattr(supervisor.secrets, "token_urlsafe", lambda _size: next(tokens))

    components = supervisor._component_specs(
        workspace=tmp_path / "workspace",
        host="127.0.0.1",
        port=21001,
        frontend_dist=tmp_path / "frontend",
        state_dir=tmp_path / "state",
    )

    assert "--ready-token=-web-ready" in components[0].command
    assert "--heartbeat-token=-web-heartbeat" in components[0].command
    assert "--ready-token=-telegram-ready" in components[1].command
    assert "--heartbeat-token=-telegram-heartbeat" in components[1].command
    assert "--ready-token=-operator-ready" in components[2].command
    assert "--heartbeat-token=-operator-heartbeat" in components[2].command


def test_projection_does_not_manufacture_heartbeat_from_pid_liveness(tmp_path):
    component = _component(tmp_path)
    component.last_heartbeat = 123.5
    component.heartbeat_details = {"phase": "executing", "operation_progress_sequence": 3}
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
    assert projection["components"]["operator"]["heartbeat_details"] == {
        "phase": "executing",
        "operation_progress_sequence": 3,
    }


def test_operator_heartbeat_surfaces_current_worker_progress(tmp_path):
    queue = _active_operator_queue(tmp_path)
    active = queue.active_item()
    assert active is not None
    jobs_root = Path(active["metadata"]["project_root"]) / ".goodteam" / "workers" / "jobs"
    job_root = jobs_root / "job-builder"
    job_root.mkdir(parents=True)
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": "job-builder",
                "run_id": "exec-builder",
                "role": "implementation",
                "status": "running",
                "heartbeat_at": "2026-07-15T03:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    heartbeat_path = tmp_path / "operator.heartbeat.json"
    liveness = supervisor.OperatorLivenessHeartbeat(
        queue=queue,
        emitter=supervisor.ComponentHeartbeat(heartbeat_path, "heartbeat-token"),
        heartbeat_interval=0.01,
        long_deadline=1.0,
    )

    liveness.start()
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    finally:
        liveness.stop()

    assert payload["details"]["worker"] == {
        "job_id": "job-builder",
        "role": "implementation",
        "status": "running",
        "heartbeat_at": "2026-07-15T03:00:00+00:00",
    }


def test_live_process_with_stale_child_heartbeat_is_restartable(tmp_path, monkeypatch):
    component = _component(tmp_path)
    component.last_heartbeat = time.time() - 10
    monkeypatch.setattr(supervisor, "COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", 1.0)

    failure = supervisor._component_failure(component)

    assert "heartbeat is stale" in failure


def test_healthy_operator_action_longer_than_component_timeout_is_not_restarted(tmp_path, monkeypatch):
    queue = _active_operator_queue(tmp_path)
    heartbeat_path = tmp_path / "operator.heartbeat.json"
    emitter = supervisor.ComponentHeartbeat(heartbeat_path, "heartbeat-token")
    liveness = supervisor.OperatorLivenessHeartbeat(
        queue=queue,
        emitter=emitter,
        heartbeat_interval=0.01,
        idle_deadline=0.2,
        short_deadline=0.2,
        long_deadline=1.0,
    )
    component = _component(tmp_path, pid=os.getpid())
    component.heartbeat_path = heartbeat_path
    monkeypatch.setattr(supervisor, "COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", 0.05)

    liveness.start()
    try:
        first_sequence = json.loads(heartbeat_path.read_text(encoding="utf-8"))["sequence"]
        time.sleep(0.15)
        assert supervisor._component_failure(component) == ""
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        assert payload["sequence"] > first_sequence
        assert payload["details"]["phase"] == "executing"
        assert payload["details"]["task_id"]
        assert payload["details"]["operation_id"].endswith(":execute:none")
        assert payload["details"]["deadline_at"] > time.time()
    finally:
        liveness.stop()


def test_frozen_operator_expires_operation_deadline_and_is_restarted(tmp_path, monkeypatch):
    queue = _active_operator_queue(tmp_path)
    heartbeat_path = tmp_path / "operator.heartbeat.json"
    emitter = supervisor.ComponentHeartbeat(heartbeat_path, "heartbeat-token")
    liveness = supervisor.OperatorLivenessHeartbeat(
        queue=queue,
        emitter=emitter,
        heartbeat_interval=0.01,
        idle_deadline=0.04,
        short_deadline=0.04,
        long_deadline=0.04,
    )
    component = _component(tmp_path, pid=os.getpid())
    component.heartbeat_path = heartbeat_path
    monkeypatch.setattr(supervisor, "COMPONENT_HEARTBEAT_TIMEOUT_SECONDS", 0.03)

    liveness.start()
    try:
        _wait_until(lambda: "heartbeat is stale" in supervisor._component_failure(component))
        stale_failure = supervisor._component_failure(component)
        expired_payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    finally:
        liveness.stop()

    assert expired_payload["details"]["deadline_expired"] is True

    stopped: list[int] = []

    def stop(candidate):
        stopped.append(candidate.process.pid)
        candidate.process.alive = False

    def start(candidate, *, state_dir):
        candidate.process = FakeProcess(9876)
        candidate.status = "starting"
        candidate.ready = False

    def ready(candidate):
        candidate.status = "running"
        candidate.ready = True
        candidate.last_heartbeat = time.time()

    monkeypatch.setattr(supervisor, "_stop_component", stop)
    monkeypatch.setattr(supervisor, "_start_component", start)
    monkeypatch.setattr(supervisor, "_wait_for_component", ready)
    outcome = supervisor._restart_component(
        component,
        failure=stale_failure,
        state_dir=tmp_path,
        state_path=tmp_path / "server.json",
        state={"supervisor": {"status": "degraded", "ready": False}},
        components=[component],
        queue=queue,
    )

    assert outcome == "running"
    assert component.restart_count == 1
    assert component.process.pid == 9876
    assert stopped == [os.getpid()]


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
