"""Real HTTP/background service/run/Worker evidence, with synthetic local models."""
import pytest
from goodteam_os._trusted_system_executable import resolve_trusted_system_git, TrustedExecutableError

from workflow_http_helpers import RecoveryServer, approval_body, prepare_security_gate, wait_until


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
