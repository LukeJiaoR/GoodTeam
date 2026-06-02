import json

import pytest

from goodteam_os.messages import append_message, read_messages


def test_append_read_message_roundtrip(tmp_path):
    message = append_message(
        tmp_path,
        "real product",
        "run-1",
        from_role="implementation",
        to=["qa"],
        kind="ask",
        body="Can you verify the smoke check?",
        refs=["job-1/result.md"],
    )

    assert message["id"].startswith("msg:")
    assert message["run_id"] == "run-1"
    assert message["from_role"] == "implementation"
    assert message["to"] == ["qa"]
    assert message["kind"] == "ask"
    assert message["gate"] == ""
    assert message["body"] == "Can you verify the smoke check?"
    assert message["refs"] == ["job-1/result.md"]
    assert message["in_reply_to"] == ""
    assert read_messages(tmp_path, "REAL-PRODUCT", "run-1") == [message]


def test_append_message_dedups_identical_message(tmp_path):
    first = append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="architect",
        to=["implementation"],
        kind="assert",
        body="Use the existing state store.",
    )
    second = append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="architect",
        to=["broadcast"],
        kind="assert",
        body="Use the existing state store.",
        gate="rfc",
        refs=["prd"],
    )

    path = tmp_path / "state" / "projects" / "REAL" / "messages" / "run-1.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    assert second["id"] == first["id"]
    assert len(rows) == 1


def test_append_message_rejects_bad_kind(tmp_path):
    with pytest.raises(ValueError, match="Unknown message kind"):
        append_message(
            tmp_path,
            "REAL",
            "run-1",
            from_role="qa",
            to=["implementation"],
            kind="note",
            body="This should not append.",
        )


def test_read_messages_filters_gate(tmp_path):
    prd = append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="product",
        to=["architect"],
        kind="propose",
        body="Keep the first increment small.",
        gate="prd",
    )
    append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="architect",
        to=["implementation"],
        kind="ask",
        body="Can the implementation stay additive?",
        gate="rfc",
    )

    assert read_messages(tmp_path, "REAL", "run-1", gate="prd") == [prd]


def test_append_only_second_append_preserves_first_row(tmp_path):
    append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="planner",
        to=["implementation"],
        kind="assert",
        body="Implement the message substrate only.",
    )
    path = tmp_path / "state" / "projects" / "REAL" / "messages" / "run-1.jsonl"
    first_row = path.read_text(encoding="utf-8").splitlines()[0]

    append_message(
        tmp_path,
        "REAL",
        "run-1",
        from_role="implementation",
        to=["qa"],
        kind="propose",
        body="Add pure file IO tests.",
    )

    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert rows[0] == first_row
    assert json.loads(rows[0])["body"] == "Implement the message substrate only."
