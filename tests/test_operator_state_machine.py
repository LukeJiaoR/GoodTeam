from __future__ import annotations

from collections import Counter

import pytest

from goodteam_os.operator import (
    OperatorExecutionStateMachine,
    OperatorService,
    _structured_review_result,
    record_operator_decision,
)
from goodteam_os.operator_queue import DurableOperatorQueue, IllegalQueueTransition
from goodteam_os import telegram


class FakeActions:
    def __init__(self, *, reviews=None, ci=None):
        self.calls = Counter()
        self.reconciled = Counter()
        self.reviews = list(reviews or [{"head_sha": "head-1", "blockers": [], "recommendation": "approve"}])
        self.ci = list(ci or [{"status": "passed", "head_sha": "head-1", "checks": [{"id": 1}]}])

    def perform(self, kind, item):
        self.calls[kind] += 1
        if kind == "hydrate":
            return {"source_revision": "rev-2", "task_contract": {"schema_version": "task_contract.v1"}}
        if kind == "preflight":
            return {"ready": True, "worker_backend": "fake"}
        if kind == "execute":
            return {"status": "succeeded", "execution_id": "exec-1", "worker_job_id": "builder-1"}
        if kind == "ensure_pr":
            return {
                "pr_number": 77,
                "pr_url": "https://github.com/LukeJiaoR/GoodTeam/pull/77",
                "head_sha": "head-1",
                "base_sha": "base-1",
                "branch_name": "gt/exec-1",
            }
        if kind == "review":
            return self.reviews.pop(0)
        if kind == "send_approval":
            return {"status": "sent", "session_id": f"card-{item['attempt']}-{item['metadata']['head_sha']}"}
        if kind == "fix":
            return {"status": "succeeded", "head_sha": "head-2", "fix_worker_job_id": "fix-1"}
        if kind == "merge":
            return {"merged": True, "merge_commit_sha": "merge-1"}
        if kind == "writeback":
            return {"issue_closed": True, "comment_id": 9}
        if kind == "reject":
            return {"rejected": True}
        raise AssertionError(kind)

    def reconcile(self, kind, item, operation):
        self.reconciled[kind] += 1
        return self.perform(kind, item)

    def observe_ci(self, item):
        self.calls["observe_ci"] += 1
        if len(self.ci) > 1:
            return self.ci.pop(0)
        return self.ci[0]


def _queue(tmp_path):
    queue = DurableOperatorQueue(tmp_path)
    item, _ = queue.enqueue(
        idempotency_key="GOODTEAM:340",
        source_url="https://github.com/LukeJiaoR/GoodTeam/issues/340",
        source_revision="rev-1",
        project_key="GOODTEAM",
        repository_identity="LukeJiaoR/GoodTeam",
        metadata={
            "head_sha": "",
            "decision": {},
            "review": {},
            "ci": {},
            "merge_method": "squash",
        },
    )
    return queue, item


def _advance_to_waiting(machine, queue):
    for _ in range(8):
        machine.tick()
    item = queue.active_item()
    assert item["phase"] == "waiting_telegram"
    assert item["status"] == "waiting"
    return item


def test_happy_path_completes_once_with_exact_head_approval(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions()
    machine = OperatorExecutionStateMachine(queue, actions)
    waiting = _advance_to_waiting(machine, queue)

    record_operator_decision(
        queue=queue,
        item_id=waiting["id"],
        decision="approve",
        approved_head_sha="head-1",
        actor_ref="telegram:user:111",
    )
    machine.tick()
    machine.tick()
    completed = machine.tick()

    assert completed["phase"] == "completed"
    assert completed["status"] == "completed"
    assert completed["metadata"]["merge"]["merge_commit_sha"] == "merge-1"
    assert completed["metadata"]["writeback"]["issue_closed"] is True
    assert actions.calls["merge"] == 1
    assert actions.calls["writeback"] == 1
    assert machine.tick() is None


def test_duplicate_ticks_do_not_repeat_completed_external_effects(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions()
    machine = OperatorExecutionStateMachine(queue, actions)
    machine.tick()
    claimed = queue.active_item()
    operation_id = f"{claimed['id']}:1:hydrate:none"
    queue.record_operation_intent(claimed["id"], operation_id, kind="hydrate")

    machine.tick()
    assert queue.active_item()["phase"] == "preflight"
    assert actions.reconciled["hydrate"] == 1
    machine.tick()
    assert actions.calls["preflight"] == 1


def test_stale_head_approval_is_rejected_without_state_change(tmp_path):
    queue, _ = _queue(tmp_path)
    machine = OperatorExecutionStateMachine(queue, FakeActions())
    waiting = _advance_to_waiting(machine, queue)

    with pytest.raises(IllegalQueueTransition, match="stale"):
        record_operator_decision(
            queue=queue,
            item_id=waiting["id"],
            decision="approve",
            approved_head_sha="old-head",
        )
    assert queue.active_item()["phase"] == "waiting_telegram"


@pytest.mark.parametrize("decision", ["request_changes", "reject"])
def test_decisions_that_need_reason_are_rejected_without_one(tmp_path, decision):
    queue, _ = _queue(tmp_path)
    machine = OperatorExecutionStateMachine(queue, FakeActions())
    waiting = _advance_to_waiting(machine, queue)
    with pytest.raises(ValueError, match="requires a reason"):
        record_operator_decision(
            queue=queue,
            item_id=waiting["id"],
            decision=decision,
            approved_head_sha="head-1",
        )


def test_request_changes_updates_same_task_and_requires_new_head_card(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions(
        reviews=[
            {"head_sha": "head-1", "blockers": [], "recommendation": "approve"},
            {"head_sha": "head-2", "blockers": [], "recommendation": "approve"},
        ],
        ci=[
            {"status": "passed", "head_sha": "head-1", "checks": []},
            {"status": "passed", "head_sha": "head-2", "checks": []},
        ],
    )
    machine = OperatorExecutionStateMachine(queue, actions)
    waiting = _advance_to_waiting(machine, queue)
    original_id = waiting["id"]
    original_pr = waiting["metadata"]["pr_number"]
    record_operator_decision(
        queue=queue,
        item_id=original_id,
        decision="request_changes",
        approved_head_sha="head-1",
        reason="add the missing recovery assertion",
    )

    for _ in range(6):
        machine.tick()
    waiting_again = queue.active_item()
    assert waiting_again["id"] == original_id
    assert waiting_again["metadata"]["pr_number"] == original_pr
    assert waiting_again["metadata"]["head_sha"] == "head-2"
    assert waiting_again["attempt"] == 2
    assert waiting_again["metadata"]["approval"]["session_id"] == "card-2-head-2"
    assert actions.calls["fix"] == 1


def test_reject_is_terminal_and_never_merges_or_completes_issue(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions()
    machine = OperatorExecutionStateMachine(queue, actions)
    waiting = _advance_to_waiting(machine, queue)
    record_operator_decision(
        queue=queue,
        item_id=waiting["id"],
        decision="reject",
        approved_head_sha="head-1",
        reason="product direction changed",
    )
    rejected = machine.tick()

    assert rejected["status"] == "rejected"
    assert rejected["phase"] == "rejected"
    assert actions.calls["reject"] == 1
    assert actions.calls["merge"] == 0
    assert actions.calls["writeback"] == 0


def test_deterministic_review_blocker_enters_bounded_fix(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions(
        reviews=[
            {
                "head_sha": "head-1",
                "blockers": ["focused test failed"],
                "deterministic": True,
                "recommendation": "fix",
            }
        ]
    )
    machine = OperatorExecutionStateMachine(queue, actions)
    for _ in range(7):
        machine.tick()

    assert queue.active_item()["phase"] == "fixing"
    machine.tick()
    assert queue.active_item()["phase"] == "observing_ci"
    assert queue.active_item()["attempt"] == 2


def test_pending_ci_is_not_treated_as_passed(tmp_path):
    queue, _ = _queue(tmp_path)
    actions = FakeActions(ci=[{"status": "pending", "head_sha": "head-1", "checks": []}])
    machine = OperatorExecutionStateMachine(queue, actions)
    for _ in range(6):
        machine.tick()

    assert queue.active_item()["phase"] == "observing_ci"
    assert actions.calls["review"] == 0


def test_unknown_recovery_phase_pauses_fail_closed(tmp_path):
    queue, _ = _queue(tmp_path)
    queue.claim_next()
    item = queue.active_item()
    queue.transition(item["id"], expected_phase="hydrating", phase="mystery")
    service = OperatorService(workspace=tmp_path, state_dir=tmp_path, actions=FakeActions())

    recovered = service.recover()
    assert recovered["phase"] == "paused"
    assert recovered["status"] == "paused"
    assert recovered["last_failure_class"] == "ambiguous_recovery_state"


def test_telegram_operator_callback_records_exact_head_decision(tmp_path, monkeypatch):
    queue, _ = _queue(tmp_path / "state")
    machine = OperatorExecutionStateMachine(queue, FakeActions())
    waiting = _advance_to_waiting(machine, queue)
    store = telegram.TelegramReviewSessionStore(tmp_path)
    store.save(
        "card01",
        {
            "session_id": "card01",
            "run_id": waiting["id"],
            "project_key": "GOODTEAM",
            "gate": "operator-head-1",
            "chat_id": 12345,
            "message_id": 88,
            "items": [],
            "decided": False,
            "operator_task_id": waiting["id"],
            "operator_head_sha": "head-1",
            "operator_state_dir": str(tmp_path / "state"),
        },
    )
    monkeypatch.setattr(telegram, "_telegram_request", lambda *args, **kwargs: {"ok": True})
    result = telegram.process_telegram_update(
        {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 111},
                "data": "v1:A:card01",
                "message": {"message_id": 88, "chat": {"id": 12345}},
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result == {"status": "processed", "callback_id": "callback-1"}
    assert queue.active_item()["metadata"]["decision"]["head_sha"] == "head-1"
    assert store.get("card01")["decided"] is True


def test_telegram_operator_reject_button_requires_text_reason(tmp_path, monkeypatch):
    queue, _ = _queue(tmp_path / "state")
    machine = OperatorExecutionStateMachine(queue, FakeActions())
    waiting = _advance_to_waiting(machine, queue)
    store = telegram.TelegramReviewSessionStore(tmp_path)
    store.save(
        "card02",
        {
            "session_id": "card02",
            "run_id": waiting["id"],
            "project_key": "GOODTEAM",
            "gate": "operator-head-1",
            "chat_id": 12345,
            "message_id": 89,
            "items": [],
            "decided": False,
            "operator_task_id": waiting["id"],
            "operator_head_sha": "head-1",
            "operator_state_dir": str(tmp_path / "state"),
        },
    )
    sent = []
    monkeypatch.setattr(
        telegram,
        "_telegram_request",
        lambda token, method, params: sent.append(params) or {"ok": True},
    )
    result = telegram.process_telegram_update(
        {
            "callback_query": {
                "id": "callback-2",
                "from": {"id": 111},
                "data": "v1:J:card02",
                "message": {"message_id": 89, "chat": {"id": 12345}},
            }
        },
        workspace=tmp_path,
        allowed_users=["111"],
        processed_callbacks=set(),
    )

    assert result["reason"] == "reject requires a reason"
    assert queue.active_item()["metadata"]["decision"] == {}
    assert "requires a reason" in sent[-1]["text"]


def test_independent_review_requires_structured_machine_readable_verdict(tmp_path):
    result = tmp_path / "review.md"
    result.write_text(
        "Review complete.\n\n"
        '{"verdict":"request_changes","blockers":["missing recovery test"],'
        '"deterministic":true,"summary":"One blocker."}\n',
        encoding="utf-8",
    )
    assert _structured_review_result(result) == {
        "verdict": "request_changes",
        "blockers": ["missing recovery test"],
        "deterministic": True,
        "summary": "One blocker.",
    }
    result.write_text("Looks fine but has no structured verdict.", encoding="utf-8")
    assert _structured_review_result(result) is None
