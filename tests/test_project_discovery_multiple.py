"""One decision publishes a bounded set through the original ledger and queue.

Delivery/authority are fixtures. Storage, queue handoff, budget and crash replay
are production paths; no Worker or model is launched.
"""
from copy import deepcopy

import pytest

from goodteam_os import assistant_queue
from goodteam_os import project_iteration_planner as planner
from goodteam_os import project_outcome_discovery as discovery
from goodteam_os.task_assistant_session import TaskAssistantSessionStore
from goodteam_os.tasks import LocalTaskStore


def test_multiple_followups_publish_once_after_partial_write_and_lost_queue_reply(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    config = tmp_path / "project.yaml"
    config.write_text("key: APP\n")
    ledger = LocalTaskStore(root, "APP")
    store = TaskAssistantSessionStore(tmp_path, "APP")
    outcome = {"batch_run_id": "seed-run", "delivery": {"receipt_id": "seed"}}
    value = dict(id="grant", status="active", seed_task_ids=[], seed_task_intents={},
        approved_backlog_version="baseline", task_ids=[], task_intents={}, authority={},
        max_batches=3, max_manager_calls=3, manager_calls=1, batches_started=0,
        new_tasks_created=0, discovered_task_ids=[], discovery_consumed=[],
        discovery=dict(objective="Improve two independent parts of the delivered project.",
            allowed_write_domains=["src"], max_new_tasks=2, max_planning_calls=3))
    value["authorization_digest"] = discovery.authorization_digest(value)
    packet = dict(multiple_followups_supported=True, discovery=value["discovery"],
        authorized_task_ids=[], authorized_done_task_ids=[], authorized_task_context=[],
        allowed_source_refs=["seed"], verified_outcome=outcome, human_feedback_supported=True)
    packet["packet_digest"] = discovery._digest(packet)
    tasks = [dict(title="Improve component " + name, description="Apply the bounded improvement to component " + name,
        acceptance_criteria=["The component preserves its existing contract."], write_domains=["src/" + name],
        dependencies=[], source_refs=["seed"]) for name in ("alpha", "beta")]
    selection = dict(new_tasks=tasks, rationale="Two independent improvements follow from the same delivered evidence.",
        human_question={"question": "Which optional integration should be investigated later?",
                        "why_human": "Only the owner can choose the later integration priority."})
    value["discovery_pending"] = dict(packet=packet, packet_digest=packet["packet_digest"],
        backlog_version="baseline", selection=selection)
    initial = deepcopy(value)

    def reset(value):
        store._mutate(lambda session: {**session, "project_continuation": deepcopy(value)})

    reset(value)
    monkeypatch.setattr(planner, "_require_planning_idle", lambda *_: None)
    monkeypatch.setattr(discovery, "authority_snapshot", lambda *_: {})
    monkeypatch.setattr(discovery, "verified_outcome", lambda **_: deepcopy(outcome))
    # The grant is a fixture, but keep the actual queue and its execution gates.
    monkeypatch.setattr(assistant_queue, "require_no_active_project_goal", lambda *_: None)

    def run():
        return discovery.discover_next_iteration(workspace=tmp_path, project_key="APP", root=root,
            project_config=config, value=store.read()["project_continuation"],
            manager_runner=lambda **_: pytest.fail("A saved decision must not call the Manager again."))

    # A remaining budget of one rejects the entire set, before the first card.
    reset({**initial, "new_tasks_created": 1})
    assert run()["reason"] == "discovery_task_budget_used"
    assert ledger.list_tasks() == []
    reset(initial)

    # Existing collision checks reject overlapping writers before publication.
    collision = deepcopy(initial)
    collision["discovery_pending"]["selection"]["new_tasks"][1]["write_domains"] = ["src/alpha/nested"]
    reset(collision)
    with pytest.raises(ValueError, match="collision"):
        run()
    assert ledger.list_tasks() == []
    reset(initial)

    # A historical partial publication already owns its first immutable card.
    first_ref = discovery._creation_ref(initial, packet)
    ledger.create_task_once(creation_ref=first_ref, intent=discovery._discovered_task_intent(
        packet, {"new_task": tasks[0], "rationale": selection["rationale"]}, first_ref))
    assert len(ledger.list_tasks()) == 1
    write = LocalTaskStore._write
    failed = False

    def lost_ledger_reply(self, data):
        nonlocal failed
        write(self, data)
        if not failed:
            failed = True
            raise OSError("lost acknowledgement after atomic task-set write")

    monkeypatch.setattr(LocalTaskStore, "_write", lost_ledger_reply)
    with pytest.raises(discovery.DiscoveryPublicationPending):
        run()
    assert len(ledger.list_tasks()) == 2
    assert store.read()["project_continuation"]["new_tasks_created"] == 0
    monkeypatch.setattr(LocalTaskStore, "_write", write)

    queue = assistant_queue.queue_manager_iteration_choice
    def lost_reply(**kwargs):
        queue(**kwargs)
        raise OSError("lost queue acknowledgement")
    monkeypatch.setattr(assistant_queue, "queue_manager_iteration_choice", lost_reply)
    with pytest.raises(discovery.DiscoveryPublicationPending):
        run()
    saved = store.read()
    identifiers = saved["project_continuation"]["discovery_pending"]["task_ids"]
    assert len(identifiers) == len(ledger.list_tasks()) == len(saved["queued_task_proposals"]) == 2
    assert saved["project_continuation"]["new_tasks_created"] == 2
    question = deepcopy(saved["project_continuation"]["human_questions"])

    monkeypatch.setattr(assistant_queue, "queue_manager_iteration_choice", queue)
    mutate = TaskAssistantSessionStore._mutate
    def lost_final_reply(self, operation):
        result = mutate(self, operation)
        if operation.__name__ == "queued_batch":
            raise OSError("lost acknowledgement after final queue binding")
        return result
    monkeypatch.setattr(TaskAssistantSessionStore, "_mutate", lost_final_reply)
    with pytest.raises(discovery.DiscoveryPublicationPending, match="committed follow-up"):
        run()
    monkeypatch.setattr(TaskAssistantSessionStore, "_mutate", mutate)
    final = store.read()["project_continuation"]
    assert final["pending_batch"]["task_ids"] == identifiers
    assert final["new_tasks_created"] == 2 and final["batches_started"] == final["manager_calls"] == 1
    assert final["human_questions"] == question
    assert len(ledger.list_tasks()) == len(store.read()["queued_task_proposals"]) == 2
    assert all(not item["confirmed"] and not item["run_id"] for item in store.read()["queued_task_proposals"])
    assert final["discovery_pending"] is None

    # Frozen single-task packets retain their protocol and task identity.
    legacy = {key: val for key, val in packet.items() if key != "multiple_followups_supported"}
    single = dict(new_task=tasks[0], rationale=selection["rationale"])
    assert discovery.validate_discovered_task(legacy, single) == single
    with pytest.raises(ValueError, match="current packet"):
        discovery.validate_discovered_task(legacy, selection)
    with pytest.raises(ValueError, match="unambiguous"):
        discovery.validate_discovered_task(packet, {**selection, "new_task": tasks[0]})
    assert "Multiple follow-ups extension" in planner._discovery_prompt(packet)
    assert "Multiple follow-ups extension" not in planner._discovery_prompt(legacy)


@pytest.mark.parametrize("existing", [False, True])
def test_equivalent_later_task_rejects_entire_set_without_new_cards(tmp_path, existing):
    ledger = LocalTaskStore(tmp_path, "APP")
    def intent(ref, title):
        return dict(title=title, description="Investigate the exact missing evidence.", role="research",
            priority="medium", dependencies=[], acceptance_criteria=["Return source-backed evidence."],
            write_domains=[], execution_policy="read_only", external_refs=[ref], artifact_refs=["seed"])
    first_ref, second_ref = "project-discovery:set", "project-discovery:set:1"
    if existing:
        ref = "project-discovery:previous"
        ledger.create_task_once(creation_ref=ref, intent=intent(ref, "Investigate beta"))
    before = ledger.path.read_bytes() if ledger.path.exists() else None
    first = intent(first_ref, "Investigate alpha")
    second = intent(second_ref, "INVESTIGATE  BETA" if existing else "INVESTIGATE  ALPHA")
    with pytest.raises(ValueError, match="equivalent"):
        ledger.create_tasks_once(items=[(first_ref, first), (second_ref, second)])
    assert (ledger.path.read_bytes() if ledger.path.exists() else None) == before
    assert len(ledger.list_tasks()) == int(existing)
