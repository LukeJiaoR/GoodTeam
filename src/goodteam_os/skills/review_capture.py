"""Decode the parent-owned Codex exec JSONL channel, never a report's claims.

The caller owns the process, stdout pipe and exit status. This decoder does not
turn a model-written identity or a JSON snippet inside an agent message into
execution evidence, and it does not certify Runtime isolation or resource reads.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from .models import SkillError, canonical_json, content_digest
from .review_report import MAX_REPORT_BYTES

MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 512 * 1024


@dataclass(frozen=True)
class CapturedCommand:
    item_id: str
    command: str
    output: str
    started_digest: str
    completed_digest: str


@dataclass(frozen=True)
class CapturedReviewTurn:
    report_bytes: bytes
    transcript_digest: str
    thread_id: str
    event_count: int
    commands: tuple[CapturedCommand, ...] = ()


def capture_final_turn(stdout: bytes, *, returncode: int) -> CapturedReviewTurn:
    """Accept one successful new-session turn after the parent reaped the CLI."""
    if type(returncode) is not int or returncode != 0:
        raise SkillError("review_process_failed", "The Reviewer process did not exit successfully.")
    if not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_CAPTURE_BYTES:
        raise SkillError("review_capture_size", "The controlled result channel is empty or exceeds its limit.")
    thread_id = None
    started = completed = False
    last_message = None
    count = 0
    pending_commands = {}
    finished_commands = set()
    commands = []

    def pairs(entries):
        value = {}
        for key, item in entries:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def constant(_value):
        raise ValueError("nonfinite value")

    for line in stdout.splitlines():
        if not line.strip() or len(line) > MAX_EVENT_BYTES:
            raise SkillError("review_capture_json", "The controlled result channel contains an invalid event.")
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
            canonical_json(event).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise SkillError("review_capture_json", "The controlled result channel contains invalid JSON.") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise SkillError("review_capture_json", "The controlled result channel requires typed object events.")
        count += 1
        kind = event["type"]
        if completed:
            raise SkillError("review_capture_order", "Events appeared after the terminal Reviewer turn.")
        if kind in {"error", "turn.failed"}:
            raise SkillError("review_turn_failed", "The Reviewer channel reported a failed turn.")
        if kind == "thread.started":
            identity = event.get("thread_id")
            if thread_id is not None or started or not isinstance(identity, str) or not identity.strip() or len(identity) > 256:
                raise SkillError("review_capture_order", "The Reviewer requires one new bounded thread identity.")
            thread_id = identity
        elif kind == "turn.started":
            if thread_id is None or started:
                raise SkillError("review_capture_order", "The Reviewer requires exactly one started turn.")
            started = True
        elif kind == "turn.completed":
            if not started or last_message is None:
                raise SkillError("review_capture_incomplete", "A successful Reviewer turn must contain a final message.")
            if pending_commands:
                raise SkillError("review_capture_incomplete", "A native command has no terminal event.")
            completed = True
        elif kind in {"item.started", "item.updated", "item.completed"}:
            if not started:
                raise SkillError("review_capture_order", "Reviewer items must belong to its started turn.")
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise SkillError("review_capture_json", "The Reviewer emitted an invalid item.")
            if item["type"] == "command_execution":
                identity, command = item.get("id"), item.get("command")
                if (not isinstance(identity, str) or not identity or len(identity) > 128
                        or not isinstance(command, str) or not command or len(command) > 32768):
                    raise SkillError("review_capture_command", "The native command event has invalid identity.")
                if kind == "item.started":
                    if identity in pending_commands or identity in finished_commands:
                        raise SkillError("review_capture_command", "Duplicate native command identity.")
                    pending_commands[identity] = (command, content_digest(line))
                elif kind == "item.completed":
                    previous = pending_commands.pop(identity, None)
                    if previous is None or previous[0] != command:
                        raise SkillError("review_capture_command", "The native command lacks its matching start event.")
                    finished_commands.add(identity)
                    if item.get("status") == "completed" and type(item.get("exit_code")) is int and item["exit_code"] == 0:
                        output = item.get("aggregated_output")
                        if not isinstance(output, str):
                            raise SkillError("review_capture_command", "A completed native command has no textual output.")
                        commands.append(CapturedCommand(identity, command, output, previous[1], content_digest(line)))
                elif identity not in pending_commands or pending_commands[identity][0] != command:
                    raise SkillError("review_capture_command", "A native command update changed its identity.")
            if kind == "item.completed" and item["type"] == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise SkillError("review_capture_json", "The Reviewer final message must be text.")
                last_message = text.encode("utf-8")
                if len(last_message) > MAX_REPORT_BYTES:
                    raise SkillError("review_report_size", "The Reviewer message exceeds its report limit.")
        else:
            # Version-specific drift must be investigated, not interpreted as
            # a compatible completed turn or an observed resource read.
            raise SkillError("review_capture_event_unsupported", "This Reviewer channel event is not supported by the pinned adapter.")
    if not completed or last_message is None:
        raise SkillError("review_capture_incomplete", "The Reviewer channel ended without a completed result turn.")
    return CapturedReviewTurn(last_message, content_digest(stdout), thread_id, count, tuple(commands))
