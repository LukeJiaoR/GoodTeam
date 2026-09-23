"""Native-format stream and immutable read receipts; all channels are fixtures."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from goodteam_os.skills import codex_container, review_results
from goodteam_os.skills.codex_reads import _path
from goodteam_os.skills.models import SkillError, canonical_json, content_digest
from goodteam_os.skills.review_api import task_review_details
from goodteam_os.skills.review_capture import capture_final_turn
from goodteam_os.skills.review_results import ReviewResultStore, _read_optional, _replace
from goodteam_os.workers import WorkerJobStore
from test_codex_review_runtime import AdmissionFixture, ScriptedEngine
from test_skill_review_execution import ready_job
from test_skill_review_results import channel, report
from test_task_skill_entrypoints import ctx as ctx

SKILL = "skill-" + "a"*64
PATH = "/review/methods/" + SKILL + "/references/check.md"


def stream(*, command=None, output="original\n", exit_code=0, before=(), extra=(), skip_start=False):
    command = command or "cat -- " + PATH
    start = {"type": "item.started", "item": {"id": "item_0", "type": "command_execution", "command": command,
             "status": "in_progress", "exit_code": None, "aggregated_output": ""}}
    complete = deepcopy(start)
    complete["type"] = "item.completed"
    complete["item"].update(status="completed" if exit_code == 0 else "failed", exit_code=exit_code, aggregated_output=output)
    messages = [{"type": "thread.started", "thread_id": "fixture-thread"}, {"type": "turn.started"},
                *before, *([] if skip_start else [start]), complete, *extra,
                {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": canonical_json(report())}},
                {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}]
    return ("\n".join(canonical_json(x) for x in messages) + "\n").encode()


def test_only_paired_successful_native_command_events_are_captured():
    captured = capture_final_turn(stream(), returncode=0)
    assert len(captured.commands) == 1
    item = captured.commands[0]
    assert item.item_id == "item_0" and item.output == "original\n"
    assert item.started_digest != item.completed_digest
    assert capture_final_turn(stream(exit_code=1), returncode=0).commands == ()
    assert capture_final_turn(channel(report(summary=json.dumps({"type":"command_execution","command":"cat -- " + PATH}))), returncode=0).commands == ()


@pytest.mark.parametrize("changes", [
    {"skip_start": True},
    {"extra": [{"type":"item.completed","item":{"type":"command_execution","id":"item_0","command":"cat -- " + PATH}}]},
    {"before": [{"type":"item.started","item":{"type":"command_execution","id":"item_0","command":"cat -- " + PATH}}]},
    {"extra": [{"type":"item.started","item":{"type":"command_execution","id":"unfinished","command":"cat -- " + PATH}}]},
])
def test_incomplete_duplicate_or_reordered_command_events_fail(changes):
    with pytest.raises(SkillError):
        capture_final_turn(stream(**changes), returncode=0)


@pytest.mark.parametrize("command", [
    "cat " + PATH, "cat -- " + PATH + ";echo forged", "echo 'cat -- " + PATH + "'",
    "cat -- " + PATH + " | head", "cat -- " + PATH + " " + PATH,
    "cat -- " + PATH.replace("references/check.md", "../../auth.json"),
    "cat -- " + PATH.replace("/review/", ""), "cat -- $(printf '" + PATH + "')",
    "/bin/bash -lc 'cat -- " + PATH + "'", "/bin/bash -c 'cat -- " + PATH + "; true'",
    "python3 " + PATH, "cat -- /review/sources/head/SKILL.md", "cat -- '" + PATH + "'",
])
def test_shell_expressions_relative_or_source_paths_do_not_become_method_read_evidence(command):
    assert _path(command) is None


@pytest.mark.parametrize("command", ["cat -- " + PATH, "/usr/bin/cat -- " + PATH, "/bin/bash -c 'cat -- " + PATH + "'"])
def test_exact_one_file_read_recognized(command):
    assert _path(command) == (SKILL, "references/check.md")


@pytest.mark.parametrize("mutation", [
    {"context_digest":"sha256:"+"f"*64}, {"version_id":"version-"+"f"*64},
    {"skill_id":{}}, {"resource_digest":"sha256:"+"f"*64}, {"runtime_session_id":"other-session"},
    {"path":"../../private"}, {"size":True}, {"transcript_digest":"sha256:"+"f"*64},
])
def test_read_receipt_corruption_never_survives_refresh_as_success(ctx, monkeypatch, mutation):
    job, context, *_ = ready_job(ctx)
    authority = AdmissionFixture()
    engine = ScriptedEngine(authority.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=authority)
    assert workers.run_job(job["id"])["status"] == "review_received"
    results = ReviewResultStore(workers, job["id"])
    operator = results._registered()[0]
    with results._store(operator) as fd:
        outcome = _read_optional(fd, "outcome.json")
        outcome["resource_reads"][0].update(mutation)
        _replace(fd, "outcome.json", outcome)
        state = _read_optional(fd, "state.json")
        state["outcome_digest"] = content_digest(review_results._bytes(outcome))
        _replace(fd, "state.json", state)
    code, payload = task_review_details(workers, context, job_id=job["id"], include_report=True)
    assert code == 409 and not payload["execution"]["report_available"]


def test_read_receipts_and_result_survive_save_interruption_without_new_child(ctx, monkeypatch):
    job, context, *_ = ready_job(ctx)
    authority = AdmissionFixture()
    engine = ScriptedEngine(authority.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    original = review_results._replace
    def crash(fd, name, value):
        if name == "state.json" and value.get("status") == "received":
            raise OSError("fixture interruption after immutable outcome")
        return original(fd, name, value)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=authority)
    with monkeypatch.context() as patch:
        patch.setattr(review_results, "_replace", crash)
        assert workers.run_job(job["id"])["status"] == "failed"
    result = ReviewResultStore(workers, job["id"])
    assert result.detail()["status"] == "recovery_required"
    assert workers.run_job(job["id"])["status"] == "review_received"
    assert result.detail()["resource_reads"][0]["origin"] == "native_command_event"
    assert engine.attaches == 1


def test_truncated_tool_output_is_not_recorded_as_a_complete_read(ctx, monkeypatch):
    from goodteam_os.skills.codex_reads import resource_witnesses

    job, *_ = ready_job(ctx)
    authority = AdmissionFixture()
    engine = ScriptedEngine(authority.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=authority)
    workers.run_job(job["id"])
    results = ReviewResultStore(workers, job["id"])
    record = results.private_attempt()
    method = record["methods"][0]
    raw = stream(command="cat -- /review/methods/"+method["skill_id"]+"/SKILL.md", output="truncated")
    capture = capture_final_turn(raw, returncode=0)
    packages = ctx.service.locks.packages
    assert resource_witnesses(capture, record, packages) == []
    foreign = replace(capture.commands[0], command="cat -- " + PATH)
    with pytest.raises(SkillError):
        resource_witnesses(replace(capture, commands=(foreign,)), record, packages)
