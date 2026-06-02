import pytest

from goodteam_os.debate import gate_quorum_status, record_gate_vote, run_gate_debate
from goodteam_os.messages import append_message, read_messages


def test_run_gate_debate_two_rounds_threads_counters_to_prior_owner_messages(tmp_path):
    calls = []
    challenge_calls = 0
    rebut_calls = 0

    def fake_dispatch(**kwargs):
        nonlocal challenge_calls, rebut_calls
        calls.append(kwargs)
        if kwargs["kind"] == "challenge":
            challenge_calls += 1
            if challenge_calls == 1:
                assert "challenges" not in kwargs
                return [
                    "ask: Who owns the rollout?",
                    "reject: Missing rollback.",
                ]
            assert [message["body"] for message in kwargs["challenges"]] == [
                "The architect owns rollout.",
                "Add rollback before approval.",
            ]
            return [
                "reject: Rollout owner needs a named backup.",
                "ask: Which rollback command will be used?",
            ]

        rebut_calls += 1
        if rebut_calls == 1:
            assert [message["body"] for message in kwargs["challenges"]] == [
                "Who owns the rollout?",
                "Missing rollback.",
            ]
            return [
                "assert: The architect owns rollout.",
                "propose: Add rollback before approval.",
            ]
        assert [message["body"] for message in kwargs["challenges"]] == [
            "Rollout owner needs a named backup.",
            "Which rollback command will be used?",
        ]
        return [
            "propose: Name platform as backup owner.",
            "assert: Use docker compose rollback.",
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
        rounds=2,
    )

    assert [call["kind"] for call in calls] == ["challenge", "rebut", "challenge", "rebut"]
    assert [message["kind"] for message in debate] == [
        "ask",
        "reject",
        "assert",
        "propose",
        "reject",
        "ask",
        "propose",
        "assert",
    ]
    assert [message["in_reply_to"] for message in debate[:2]] == ["", ""]
    assert [message["in_reply_to"] for message in debate[2:4]] == [message["id"] for message in debate[:2]]
    assert [message["in_reply_to"] for message in debate[4:6]] == [message["id"] for message in debate[2:4]]
    assert [message["in_reply_to"] for message in debate[6:]] == [message["id"] for message in debate[4:6]]
    assert [message["refs"] for message in debate if message["from_role"] == "qa"] == [
        ["artifacts/rfc.md"],
        ["artifacts/rfc.md"],
        ["artifacts/rfc.md"],
        ["artifacts/rfc.md"],
    ]


def test_run_gate_debate_rounds_one_preserves_existing_call_shape(tmp_path):
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        if kwargs["kind"] == "challenge":
            return ["ask: First challenge."]
        return ["assert: First rebuttal."]

    debate = run_gate_debate(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        challenger_role="qa",
        owner_role="architect",
        dispatch_fn=fake_dispatch,
        artifact_ref="artifacts/rfc.md",
        rounds=1,
    )

    assert calls == [
        {
            "role": "qa",
            "kind": "challenge",
            "gate": "rfc",
            "artifact_ref": "artifacts/rfc.md",
        },
        {
            "role": "architect",
            "kind": "rebut",
            "gate": "rfc",
            "challenges": [debate[0]],
        },
    ]
    assert [message["body"] for message in debate] == ["First challenge.", "First rebuttal."]


def test_run_gate_debate_rounds_are_capped(tmp_path):
    calls = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        if kwargs["kind"] == "challenge":
            return ["ask: Continue?"]
        return ["assert: Continue."]

    debate = run_gate_debate(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        challenger_role="qa",
        owner_role="architect",
        dispatch_fn=fake_dispatch,
        rounds=99,
    )

    assert [call["kind"] for call in calls] == ["challenge", "rebut"] * 5
    assert len(debate) == 10


def test_record_gate_vote_appends_validated_vote_message(tmp_path):
    vote = record_gate_vote(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        role="security",
        vote="approve",
    )

    assert vote["kind"] == "assert"
    assert vote["body"] == "VOTE:approve"
    assert vote["gate"] == "rfc"
    assert vote["from_role"] == "security"
    assert vote["to"] == ["broadcast"]
    assert read_messages(tmp_path, "CONTENTOPS", "run-1", gate="rfc") == [vote]

    with pytest.raises(ValueError):
        record_gate_vote(
            tmp_path,
            "CONTENTOPS",
            "run-1",
            "rfc",
            role="security",
            vote="maybe",
        )


def test_gate_quorum_status_tallies_latest_votes_and_outstanding_roles(tmp_path):
    record_gate_vote(tmp_path, "CONTENTOPS", "run-1", "rfc", role="architect", vote="reject")
    record_gate_vote(tmp_path, "CONTENTOPS", "run-1", "rfc", role="security", vote="approve")
    record_gate_vote(tmp_path, "CONTENTOPS", "run-1", "rfc", role="architect", vote="approve")
    record_gate_vote(tmp_path, "CONTENTOPS", "run-1", "rfc", role="product", vote="abstain")
    record_gate_vote(tmp_path, "CONTENTOPS", "run-1", "prd", role="support", vote="approve")
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="observer",
        to=["broadcast"],
        kind="assert",
        gate="rfc",
        body="VOTE:approve",
    )

    status = gate_quorum_status(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        quorum_roles=["architect", "security", "product", "support"],
        threshold=2,
    )

    assert status == {
        "tally": {"approve": 2, "reject": 0, "abstain": 1},
        "met": True,
        "outstanding": ["support"],
    }

    unmet = gate_quorum_status(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        "rfc",
        quorum_roles=["architect", "security", "product", "support"],
        threshold=3,
    )

    assert unmet["met"] is False
