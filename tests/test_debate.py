import json

from goodteam_os.debate import record_gate_decision, run_gate_debate
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message, read_messages
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import decide_approval


def test_run_gate_debate_posts_capped_challenges_and_linked_rebuttals(tmp_path):
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        if kwargs["kind"] == "challenge":
            return [
                "ask: Which ledger entry owns this gate?",
                "reject: The RFC does not name the rollback path.",
                "ask: What happens on partial failure?",
                "reject: This fourth objection must be capped.",
            ]
        assert [message["body"] for message in kwargs["challenges"]] == [
            "Which ledger entry owns this gate?",
            "The RFC does not name the rollback path.",
            "What happens on partial failure?",
        ]
        return [
            "assert: The ledger entry is task-7.",
            "propose: Add rollback wording before approval.",
            "assert: Partial failure keeps the gate pending.",
        ]

    debate = run_gate_debate(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        challenger_role="qa",
        owner_role="architect",
        dispatch_fn=fake_dispatch,
        artifact_ref="artifacts/rfc.md",
    )

    assert [call["kind"] for call in calls] == ["challenge", "rebut"]
    assert [message["kind"] for message in debate] == [
        "ask",
        "reject",
        "ask",
        "assert",
        "propose",
        "assert",
    ]
    assert [message["gate"] for message in debate] == ["rfc"] * 6
    assert [message["refs"] for message in debate[:3]] == [["artifacts/rfc.md"]] * 3
    assert [message["to"] for message in debate[:3]] == [["architect"]] * 3
    assert [message["to"] for message in debate[3:]] == [["qa"]] * 3
    assert [message["in_reply_to"] for message in debate[3:]] == [message["id"] for message in debate[:3]]
    assert read_messages(tmp_path, "CONTENTOPS", "run-1", gate="rfc") == debate


def test_record_gate_decision_appends_decision_and_writes_memory_event(tmp_path):
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="qa",
        to=["architect"],
        kind="ask",
        gate="rfc",
        body="Can this fail closed?",
    )
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="qa",
        to=["architect"],
        kind="reject",
        gate="rfc",
        body="The gate should not pass yet.",
    )
    memory_store = RoleMemoryStore(tmp_path)

    decision = record_gate_decision(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        decision="approved",
        by_role="human",
        memory_store=memory_store,
    )

    assert decision["kind"] == "assert"
    assert decision["body"] == "DECISION: approved"
    assert decision["gate"] == "rfc"
    assert read_messages(tmp_path, "CONTENTOPS", "run-1", gate="rfc")[-1] == decision
    events = [
        json.loads(line)
        for line in memory_store.event_path("human").read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 1
    assert events[0]["horizon"] == "mid-term"
    assert events[0]["source"] == f"messages/run-1.jsonl#{decision['id']}"
    assert events[0]["text"] == "Gate rfc decision: approved. Challenger messages: 2."


def test_record_gate_decision_is_failure_safe_when_memory_raises(tmp_path):
    class FailingMemoryStore:
        def append_memory_event(self, *args, **kwargs):
            raise RuntimeError("memory unavailable")

    decision = record_gate_decision(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        decision="changes_requested",
        by_role="human",
        memory_store=FailingMemoryStore(),
    )

    assert decision["body"] == "DECISION: changes_requested"
    assert read_messages(tmp_path, "CONTENTOPS", "run-1", gate="rfc") == [decision]


def test_decide_approval_records_optional_debate_ref_only_when_provided(tmp_path):
    without_ref = tmp_path / "without-ref"
    state = create_initial_state("run-1", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(without_ref).write_project_current(state)

    decide_approval(
        workspace=without_ref,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
    )
    actions = [
        json.loads(line)
        for line in (without_ref / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert actions[0]["action"] == "approval_decided"
    assert "debate_ref" not in actions[0]

    with_ref = tmp_path / "with-ref"
    state = create_initial_state("run-2", "CONTENTOPS", "Existing AI content studio run")
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    StateStore(with_ref).write_project_current(state)

    decide_approval(
        workspace=with_ref,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        debate_ref="messages/run-2.jsonl#msg:abc123",
    )
    actions = [
        json.loads(line)
        for line in (with_ref / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert actions[0]["action"] == "approval_decided"
    assert actions[0]["debate_ref"] == "messages/run-2.jsonl#msg:abc123"
