"""Real discovery/grant/ledger path; original Git verifier and Manager are fixtures."""

from copy import deepcopy
import pytest
from test_delivery_feedback_drain import scenario
from goodteam_os import project_iteration_continuation as continuation
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


@pytest.mark.parametrize("mode", ["head", "backlog", "lost_ack"])
def test_completed_discovery_keeps_evidence_and_rejudges_changed_context(tmp_path, monkeypatch, mode):
    root, store, batches, deliver, _, _ = scenario(tmp_path, monkeypatch)
    deliver(1)
    ledger = LocalTaskStore(root, "DEMO")
    calls = []
    head = ["head-before"]
    monkeypatch.setattr(discovery, "_head", lambda _: head[0])
    if mode == "lost_ack":
        original = TaskAssistantSessionStore._mutate
        lost = []

        def write(self, mutate):
            before = self.read()
            result = original(self, mutate)
            old = [e for e in before["timeline"] if e.get("event") == "project_discovery_decision_superseded"]
            new = [e for e in result["timeline"] if e.get("event") == "project_discovery_decision_superseded"]
            if not old and new and not lost:
                lost.append(True)
                raise OSError("Committed superseded discovery acknowledgement lost")
            return result

        monkeypatch.setattr(TaskAssistantSessionStore, "_mutate", write)

    def manager(**kwargs):
        packet = kwargs["context"]
        calls.append(deepcopy(packet))
        if len(calls) == 1:
            if mode == "backlog":
                ledger.update_task(batches["batch-0"]["task_ids"][0], status="blocked")
            else:
                head[0] = "head-after"
            return {
                "selection": {
                    "new_task": {
                        "title": "Improve API examples",
                        "description": "Improve the approved endpoint documentation examples using the verified outcome.",
                        "acceptance_criteria": ["Examples explain the approved API behavior."],
                        "write_domains": ["docs/examples"],
                        "dependencies": [],
                        "source_refs": [packet["verified_outcome"]["delivery"]["receipt_id"]],
                    },
                    "rationale": "The original verified outcome supports improving these bounded examples.",
                },
                "manager_execution": {"fixture": "completed original Manager result"},
            }
        return {
            "selection": {"new_task": None, "rationale": "The newest Context needs no additional task at this point."}
        }

    def tick():
        return continuation.tick_project_continuation(
            workspace=tmp_path,
            project_key="DEMO",
            root=root,
            project_config=tmp_path / "projects/demo.local.yaml",
            manager_runner=manager,
            batch_runner=lambda **_: pytest.fail("No stale follow-up may execute."),
        )

    first = tick()
    assert first["status"] == "active" and first["reason"] == "discovery_context_changed", first.get("reason")
    assert first["discovery_pending"] is None and not first["discovery_consumed"]
    assert first["manager_calls"] == 1 and first["new_tasks_created"] == 0
    assert len(ledger.list_tasks()) == 2 and not store.read().get("queued_task_proposals")
    events = [e for e in store.read()["timeline"] if e.get("event") == "project_discovery_decision_superseded"]
    assert len(events) == 1 and events[0]["details"]["pending"]["packet"] == calls[0]
    assert events[0]["details"]["selection"]["new_task"]["title"] == "Improve API examples"
    second = tick()
    assert len(calls) == 2 and calls[1] != calls[0]
    assert second["manager_calls"] == 2 and second["new_tasks_created"] == 0
    assert len(ledger.list_tasks()) == 2
    assert tick()["manager_calls"] == 2 and len(calls) == 2


@pytest.mark.parametrize(
    "mode", ["budget", "stop", "replacement", "intent", "receipt", "packet", "unknown", "invalid", "history"]
)
def test_refresh_boundaries(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GOODTEAM_HOME", str(tmp_path / "home"))
    root, store, batches, deliver, _, _ = scenario(tmp_path, monkeypatch)
    deliver(1)
    ledger = LocalTaskStore(root, "DEMO")
    calls = []
    if mode == "budget":
        store._mutate(lambda s: {**s, "project_continuation": {**s["project_continuation"], "manager_calls": 4}})

    def runner(**kwargs):
        calls.append(kwargs)
        ledger.update_task(batches["batch-0"]["task_ids"][0], status="blocked")
        if mode == "intent":
            ledger.update_task(batches["batch-0"]["task_ids"][0], description="Unauthorized new description")

        def mutate(s):
            v = s["project_continuation"]
            if mode == "stop":
                v["status"] = "stopped"
            if mode == "replacement":
                v["id"] = "replacement"
            if mode == "packet":
                v["discovery_pending"]["packet"]["project_key"] = "OTHER"
            if mode == "receipt":
                s["project_task_batches"]["batch-1"]["delivery_receipt"]["receipt_id"] = "tampered"
            if mode == "history":
                v["discovery_pending"]["packet"]["batch_history"] = [{"observation_key": "fake"}]
            return s

        store._mutate(mutate)
        if mode == "unknown":
            raise OSError("unknown result")
        if mode == "invalid":
            return {"selection": {"bad": True}}
        return {"selection": {"new_task": None, "rationale": "No follow-up is justified from the original evidence."}}

    def tick():
        return continuation.tick_project_continuation(
            workspace=tmp_path,
            project_key="DEMO",
            root=root,
            project_config=tmp_path / "projects/demo.local.yaml",
            manager_runner=runner,
            batch_runner=lambda **k: pytest.fail("no dispatch"),
        )

    first = tick()
    events = [e for e in store.read()["timeline"] if e.get("event") == "project_discovery_decision_superseded"]
    assert not store.read().get("queued_task_proposals")
    if mode == "budget":
        assert len(events) == 1 and first["manager_calls"] == 5 and first["discovery_pending"] is None
        assert tick()["status"] == "exhausted" and len(calls) == 1
    else:
        assert not events
        if mode == "replacement":
            assert store.read()["project_continuation"]["id"] == "replacement", store.read()["project_continuation"]
        assert store.read()["project_continuation"]["discovery_pending"] is not None
        if mode == "replacement":
            assert store.read()["project_continuation"]["id"] == "replacement"
        elif mode == "stop":
            assert first["status"] == "stopped"
        elif mode == "unknown":
            assert first["status"] == "active" and first["reason"] == "discovery_publication_pending"
        else:
            assert first["status"] == "blocked"
