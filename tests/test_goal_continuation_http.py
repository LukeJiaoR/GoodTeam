"""Real HTTP/background service/run/Worker evidence, with synthetic local models."""
from threading import Event

import pytest
from goodteam_os._trusted_system_executable import resolve_trusted_system_git, TrustedExecutableError

from workflow_http_helpers import RecoveryServer, approval_body, prepare_security_gate, wait_until


def test_service_ticks_project_and_issue_inputs_when_goal_scan_fails(tmp_path, monkeypatch, caplog):
    from goodteam_os import operator_manager_intake, project_iteration_continuation
    from goodteam_os.goal_continuation import start_goal_service
    from goodteam_os.goals import GoalCorruptState
    from goodteam_os.operator_queue import DurableOperatorQueue
    from goodteam_os.service import service_state_dir

    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    ledger = tmp_path / "state" / "goals.sqlite3"
    ledger.parent.mkdir()
    ledger.write_bytes(b"not a goal ledger")
    queue = DurableOperatorQueue(service_state_dir())
    queue.set_desired_state("stopped")

    calls = []
    consumed = Event()

    def tick_project(workspace):
        calls.append(("project", workspace))
        if len(calls) == 2:
            queue.set_desired_state("running")

    def consume_issues(workspace, *, stop):
        calls.append(("issues", workspace, stop))
        stop.set()
        consumed.set()

    monkeypatch.setattr(project_iteration_continuation, "tick_authorized_project_continuations", tick_project)
    monkeypatch.setattr(operator_manager_intake, "consume_manager_inputs", consume_issues)
    stop = start_goal_service(tmp_path)
    try:
        assert consumed.wait(timeout=5), "Goal scan failure blocked Manager input consumption"
    finally:
        stop.set()
        stop.join(timeout=5)

    assert calls == [("project", tmp_path), ("project", tmp_path), ("issues", tmp_path, stop)]
    assert any(record.getMessage() == "Goal continuation scan failed"
               and record.exc_info and isinstance(record.exc_info[1], GoalCorruptState)
               for record in caplog.records)


def test_service_continues_same_goal_from_observed_omission_and_budget_gate(tmp_path):
    try:
        resolve_trusted_system_git(tmp_path)
    except TrustedExecutableError as exc:
        pytest.skip(f"Isolated HTTP preparation requires trusted system Git: {exc}")
    server = RecoveryServer(tmp_path, fixture="goal_continuation_server.py")
    try:
        server.start()
        # Prepare the isolated project through existing HTTP, finish its setup
        # workflow, then explicitly authorize a Goal. No state-file seeding.
        binding = prepare_security_gate(server)
        server.ok("/api/approve", binding)
        server.ok("/api/approve", approval_body(server.state(), "release"))
        # Remove only this test's deliberately created regression file if setup
        # ever produced one; the Goal starts with an observable missing check.
        assert not (tmp_path / "target/test_feature.py").exists()
        base = "/api/projects/RECOVERY/task-assistant"
        created = server.ok(base + "/actions", {
            "action": "create_goal", "objective": "Make fixture feature and regression complete",
            "acceptance_criteria": ["Feature works", "Regression check exists"],
            "allowed_scope": ["Only feature.py and test_feature.py in this isolated fixture"],
            "max_work_units": 1, "confirmed": True, "billing_acknowledged": True, "idempotency_key": "goal-http"})["goal"]
        def snapshot():
            return server.state()["task_assistant_session"]["goals"][-1]
        def wait_for_gate(run_id, gate):
            def matching_gate():
                data = server.state()
                state = data["state"]
                # Starting a successor archives the previous current state
                # before publishing the new one. Wait through that gap and
                # bind every approval to this Goal work unit's run.
                if state and state["run_id"] == run_id and state["next_node"] == f"await_{gate}_approval":
                    return data
                return None
            return wait_until(matching_gate)
        wait_until(lambda: snapshot()["active_work"])
        first_run = snapshot()["active_work"]["run_id"]
        for gate in ("takeover", "prd", "rfc", "security", "release"):
            current = wait_for_gate(first_run, gate)
            assert current["state"]["next_node"] == f"await_{gate}_approval"
            server.ok("/api/approve", approval_body(current, gate))
        waiting = wait_until(lambda: next((g for g in [snapshot()] if g["status"] == "awaiting_approval"), None))
        assert "REGRESSION_MISSING" in waiting["work_units"][0]["gaps"][0]
        assert len(waiting["work_units"]) == 1
        gate_request = {"action": "decide_goal_gate", "goal_id": created["id"],
            "gate_id": waiting["gate"]["id"], "expected_revision": waiting["revision"],
            "decision": "approve", "idempotency_key": "goal-http-approve"}
        server.ok(base + "/actions", gate_request)
        second = wait_until(lambda: next((g for g in [snapshot()] if g["active_work"] and g["active_work"]["run_id"] != first_run), None))
        assert second["id"] == created["id"]
        assert second["work_units"][1]["criterion_indices"] == [1]
        for gate in ("takeover", "prd", "rfc", "security", "release"):
            current = wait_for_gate(second["active_work"]["run_id"], gate)
            assert current["state"]["next_node"] == f"await_{gate}_approval"
            server.ok("/api/approve", approval_body(current, gate))
        complete = wait_until(lambda: next((g for g in [snapshot()] if g["status"] == "complete"), None))
        assert len(complete["work_units"]) == 2
        assert all(work["evidence_refs"] for work in complete["work_units"])
        assert (tmp_path / "target/test_feature.py").exists()
        server.stop()
        server.start()
        assert snapshot()["status"] == "complete"
        assert len(snapshot()["work_units"]) == 2
    finally:
        server.stop()
