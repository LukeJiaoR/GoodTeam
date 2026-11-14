"""Pinned native JSONL compatibility; no provider or project acceptance claim."""
import json

import pytest

from goodteam_os.skills.models import SkillError, content_digest
from goodteam_os.skills.review_capture import MAX_CAPTURE_BYTES, MAX_EVENT_BYTES, capture_final_turn


def test_native_startup_warning_keeps_one_turn_and_command_provenance():
    # Observed with the existing native CLI: configuration warnings are
    # item.completed/error between thread.started and turn.started.
    warning = {"type": "item.completed", "item": {
        "id": "warning-0", "type": "error", "message": "An unrecognized setting is ignored."}}
    command = {"id": "command-0", "type": "command_execution", "command": "read captured source"}
    # A real completed command event was 1,165,676 bytes, below
    # the whole-channel limit but above the ordinary event limit.
    output = "source bytes\n" * 90000
    events = [
        {"type": "thread.started", "thread_id": "new-native-thread"}, warning,
        {"type": "turn.started"},
        {"type": "item.started", "item": command},
        {"type": "item.completed", "item": {
            **command, "status": "completed", "exit_code": 0, "aggregated_output": output}},
        {"type": "item.completed", "item": {"id": "report-0", "type": "agent_message", "text": "report"}},
        {"type": "turn.completed"},
    ]

    def channel(items):
        return b"\n".join(json.dumps(item).encode() for item in items) + b"\n"

    raw = channel(events)
    captured = capture_final_turn(raw, returncode=0)
    assert captured.report_bytes == b"report" and captured.event_count == len(events)
    assert captured.transcript_digest == content_digest(raw)
    assert captured.commands[0].output == output
    assert captured.commands[0].started_digest == content_digest(json.dumps(events[3]).encode())

    # Only the native diagnostic shape is allowed outside the single turn.
    for invalid in (
        [warning, *events],
        [events[0], events[3], *events[2:]],
        [events[0], events[5], *events[2:]],
        [*events, warning],
    ):
        with pytest.raises(SkillError, match="Reviewer") as error:
            capture_final_turn(channel(invalid), returncode=0)
        assert error.value.code == "review_capture_order"
    with pytest.raises(SkillError) as error:
        capture_final_turn(channel([events[0], {"type": "error", "message": "failed"}, *events[2:]]), returncode=0)
    assert error.value.code == "review_turn_failed"
    with pytest.raises(SkillError) as error:
        capture_final_turn(raw, returncode=0, forbid_tools=True)
    assert error.value.code == "local_codex_tool_observed"

    oversized_reasoning = {"type": "item.completed", "item": {
        "id": "reasoning-0", "type": "reasoning", "text": "x" * MAX_EVENT_BYTES}}
    with pytest.raises(SkillError) as error:
        capture_final_turn(channel([*events[:3], oversized_reasoning, *events[3:]]), returncode=0)
    assert error.value.code == "review_capture_json"
    too_large = {"type": "item.completed", "item": {
        **command, "status": "completed", "exit_code": 0, "aggregated_output": "x" * MAX_CAPTURE_BYTES}}
    with pytest.raises(SkillError) as error:
        capture_final_turn(channel([*events[:4], too_large, *events[5:]]), returncode=0)
    assert error.value.code == "review_capture_size"
