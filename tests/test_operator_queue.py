from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from goodteam_os.operator_queue import (
    DurableOperatorQueue,
    IllegalQueueTransition,
    QueueCorruptionError,
)


def _enqueue(queue: DurableOperatorQueue, number: int = 1):
    return queue.enqueue(
        idempotency_key=f"GOODTEAM:issue:{number}",
        source_url=f"https://github.com/LukeJiaoR/GoodTeam/issues/{number}",
        source_revision=f"revision-{number}",
        project_key="GOODTEAM",
        repository_identity="LukeJiaoR/GoodTeam",
        metadata={"issue_number": number},
    )


def test_queue_is_fifo_and_only_claims_one_active_item(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    first, _ = _enqueue(queue, 1)
    second, _ = _enqueue(queue, 2)

    assert queue.claim_next()["id"] == first["id"]
    assert queue.claim_next() is None
    queue.transition(first["id"], status="completed", phase="completed", expected_phase="hydrating")
    assert queue.claim_next()["id"] == second["id"]


def test_duplicate_enqueue_returns_original_item(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    first, created = _enqueue(queue)
    duplicate, duplicate_created = _enqueue(queue)

    assert created is True
    assert duplicate_created is False
    assert duplicate == first
    assert len(queue.list_items()) == 1


def test_concurrent_duplicate_enqueue_is_process_lock_safe(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: _enqueue(queue), range(20)))

    assert sum(created for _item, created in results) == 1
    assert len({item["id"] for item, _created in results}) == 1
    assert len(queue.list_items()) == 1


def test_drain_prevents_new_claim_but_preserves_fifo(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = _enqueue(queue)
    queue.set_desired_state("draining")

    assert queue.claim_next() is None
    assert queue.get(item["id"])["status"] == "queued"
    queue.set_desired_state("running")
    assert queue.claim_next()["id"] == item["id"]


def test_illegal_status_and_expected_phase_transitions_fail_closed(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = _enqueue(queue)

    with pytest.raises(IllegalQueueTransition, match="illegal queue status"):
        queue.transition(item["id"], status="completed")
    with pytest.raises(IllegalQueueTransition, match="expected one of"):
        queue.transition(item["id"], expected_phase="executing", phase="failed")


def test_operation_intent_and_result_are_idempotent_and_conflicts_fail(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = _enqueue(queue)
    operation, created = queue.record_operation_intent(item["id"], "op-1", kind="merge")
    duplicate, duplicate_created = queue.record_operation_intent(item["id"], "op-1", kind="merge")

    assert created is True
    assert duplicate_created is False
    assert duplicate == operation
    result = queue.record_operation_result(item["id"], "op-1", result={"sha": "abc"})
    assert result["state"] == "result"
    assert queue.record_operation_result(item["id"], "op-1", result={"sha": "abc"}) == result
    with pytest.raises(QueueCorruptionError, match="conflicting result"):
        queue.record_operation_result(item["id"], "op-1", result={"sha": "other"})


def test_result_without_intent_is_rejected(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = _enqueue(queue)
    with pytest.raises(IllegalQueueTransition, match="no persisted intent"):
        queue.record_operation_result(item["id"], "missing", result={})


def test_corrupted_snapshot_fails_closed_without_overwrite(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    queue.root.mkdir(parents=True)
    queue.state_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(QueueCorruptionError, match="malformed"):
        queue.list_items()
    assert queue.state_path.read_text(encoding="utf-8") == "{bad json"


def test_multiple_active_records_fail_closed(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    first, _ = _enqueue(queue, 1)
    second, _ = _enqueue(queue, 2)
    state = json.loads(queue.state_path.read_text(encoding="utf-8"))
    for item in state["items"]:
        item["status"] = "active"
        item["phase"] = "executing"
    queue.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(QueueCorruptionError, match="multiple active"):
        queue.active_item()
    with pytest.raises(QueueCorruptionError, match="multiple active"):
        queue.active_item_snapshot()
    assert {first["id"], second["id"]}


def test_active_item_snapshot_is_read_only_and_uses_the_validated_projection(tmp_path):
    empty = DurableOperatorQueue(tmp_path / "empty")
    assert empty.active_item_snapshot() is None
    assert not empty.root.exists()

    queue = DurableOperatorQueue(tmp_path / "populated")
    item, _ = _enqueue(queue)
    queue.claim_next()
    before = {
        path.relative_to(queue.root): path.read_bytes()
        for path in queue.root.rglob("*")
        if path.is_file()
    }

    assert queue.active_item_snapshot()["id"] == item["id"]
    assert {
        path.relative_to(queue.root): path.read_bytes()
        for path in queue.root.rglob("*")
        if path.is_file()
    } == before


def test_restart_reopens_same_projection_and_append_only_events(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = _enqueue(queue)
    queue.claim_next()
    reopened = DurableOperatorQueue(tmp_path)

    assert reopened.active_item()["id"] == item["id"]
    events = [json.loads(line) for line in reopened.events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["enqueued", "claimed"]
