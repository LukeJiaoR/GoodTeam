"""Real protected task/source/result persistence; captured-channel fixtures, not live models."""
from copy import deepcopy
import json

import pytest

from goodteam_os.skills.content import SkillContextProvider
from goodteam_os.skills.models import SkillConflict, SkillError, canonical_json, content_digest
from goodteam_os.skills.review_capture import capture_final_turn
from goodteam_os.skills.review_report import REPORT_SCHEMA, validate_report
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.workers import WorkerJobStore
from test_skill_content import FixtureAuthority
from test_skill_review_protocol import direct_request
from test_task_skill_entrypoints import bind, capture, method, ctx as ctx

SOURCE = b"def add(a, b):\n    return a + b\n"


def report(**changes):
    return {
        "schema_version": REPORT_SCHEMA,
        "summary": "No finding from the inspected expression. Runtime tests were not performed.",
        "findings": [],
        "evidence": [{"id": "E1", "path": "candidate", "line_start": 2, "line_end": 2,
                      "quote": "    return a + b", "observation": "The return expression adds the two parameters."}],
        "checks": [{"name": "Return expression", "status": "checked", "evidence_ids": ["E1"],
                    "detail": "Inspected the captured source; did not execute it."},
                   {"name": "Runtime tests", "status": "not_run", "evidence_ids": [],
                    "detail": "No tests were invoked in this review."}],
        **changes,
    }


def channel(payload=None, *, thread="fixture-review-thread"):
    return b"\n".join(canonical_json(item).encode() for item in [
        {"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": canonical_json(payload or report())}},
        {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}},
    ]) + b"\n"


def prepared(ctx, *, logical="report-run", activation="required", required=False):
    selected = {**method(ctx), "activation": activation}
    bind(ctx, selected, subject="role:code_review", key="bind:" + logical)
    context = capture(ctx, logical, subjects=("role:code_review",))
    candidate = ctx.root / (logical + ".py")
    candidate.write_bytes(SOURCE)
    job = ctx.workers.create_job(**direct_request(
        context, idempotency_key="review:" + logical, artifact_key="review_source",
        artifact_path=str(candidate), artifact_version=content_digest(SOURCE),
        required_skill_ids=(selected["skill_id"],) if required else (),
    ))
    provider = SkillContextProvider(ctx.workers, job["id"], authority=FixtureAuthority())
    ref = provider.prepare("context:" + logical)
    results = ReviewResultStore(ctx.workers, job["id"])
    results.begin(provider, ref)
    return results, job, context, selected, candidate, provider, ref


def test_source_backed_zero_findings_is_valid_and_explicitly_not_independent_confirmation():
    value = validate_report(canonical_json(report()).encode(), sources={"candidate": SOURCE})
    assert value["findings"] == []
    assert value["checks"][1]["status"] == "not_run"
    assert value["evidence"][0]["source_digest"] == content_digest(SOURCE)
    assert value["advisory"] is True
    assert value["judgment_verification"] == "not_independently_verified"


@pytest.mark.parametrize("change", [
    {"findings": "none"}, {"evidence": []}, {"summary": ""}, {"checks": []},
    {"schema_version": "model-owned-version"}, {"job_id": "some-other-worker"},
    {"approval": "approved"}, {"task_skill_context": {}},
    {"checks": [{"name": "Nothing", "status": "not_run", "evidence_ids": [], "detail": "Not performed."}]},
    {"evidence": [{"id": "E1", "path": "../../private", "line_start": 1, "line_end": 1,
                   "quote": "secret", "observation": "Not a captured source."}]},
    {"evidence": [{"id": "E1", "path": "candidate", "line_start": True, "line_end": 1,
                   "quote": "def add(a, b):", "observation": "Boolean is not a line number."}]},
    {"evidence": [{"id": "E1", "path": "candidate", "line_start": 2, "line_end": 2,
                   "quote": "    return a - b", "observation": "Wrong candidate bytes."}]},
])
def test_invalid_or_identity_claiming_report_is_not_accepted(change):
    with pytest.raises(SkillError):
        validate_report(canonical_json(report(**change)).encode(), sources={"candidate": SOURCE})


@pytest.mark.parametrize("raw", [b"done", b"{}", b'{"summary":NaN}', b'{"summary":"a","summary":"b"}',
                                  b"```json\n{}\n```", b"\xff", b"[1]", b"x" * (256 * 1024 + 1)])
def test_invalid_raw_results_fail_closed(raw):
    with pytest.raises(SkillError):
        validate_report(raw, sources={"candidate": SOURCE})


def test_received_report_survives_restart_and_role_changes_without_private_method_dump(ctx):
    results, job, context, selected, _candidate, _provider, _ref = prepared(ctx)
    results.mark_running()
    accepted = results.receive(capture_final_turn(channel(), returncode=0))
    assert accepted["status"] == "received"
    assert accepted["finding_count"] == 0
    assert accepted["unfinished_check_count"] == 1
    assert accepted["methods"][0]["version_id"] == selected["version_id"]
    assert accepted["runtime_verification"] == "contract_fixture"
    assert accepted["report_available"] is True
    assert "report" not in accepted
    bind(ctx, None, subject="role:code_review", key="restore-after-report")
    restarted = ReviewResultStore(WorkerJobStore(ctx.repo), job["id"])
    detail = restarted.detail(expected_task_context=context, include_report=True)
    assert detail["report"]["findings"] == []
    assert detail["methods"] == accepted["methods"]
    assert "Method v1" not in json.dumps(detail)
    assert str(ctx.root) not in json.dumps(detail)
    assert "sources" not in detail
    assert all(value == "unobserved" for value in detail["stages"].values())


def test_duplicate_is_exact_idempotent_and_conflict_does_not_replace_report(ctx):
    results, *_rest = prepared(ctx)
    results.mark_running()
    captured = capture_final_turn(channel(), returncode=0)
    first = results.receive(captured)
    assert results.receive(captured) == first
    different = capture_final_turn(channel(report(summary="A different conclusion.")), returncode=0)
    with pytest.raises(SkillConflict) as error:
        results.receive(different)
    assert error.value.code == "review_result_conflict"
    assert results.detail() == first


def test_failed_or_cancelled_attempt_cannot_be_resurrected_by_late_result(ctx):
    results, *_rest = prepared(ctx)
    results.mark_running()
    results.fail("review_cancelled", status="cancelled")  # Fixture has no running process.
    with pytest.raises(SkillConflict):
        results.receive(capture_final_turn(channel(), returncode=0))
    assert results.detail()["report_available"] is False
    assert results.detail()["status"] == "cancelled"


def test_projection_crash_recovers_without_a_second_review(ctx, monkeypatch):
    from goodteam_os.skills import review_results

    results, job, context, *_rest = prepared(ctx)
    results.mark_running()
    original = review_results._replace
    def fail_after_result(fd, name, value):
        if name == "state.json" and value.get("status") == "received":
            raise OSError("simulated crash after durable outcome")
        return original(fd, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(review_results, "_replace", fail_after_result)
        with pytest.raises(SkillError):
            results.receive(capture_final_turn(channel(), returncode=0))
    assert results.detail()["status"] == "recovery_required"
    assert results.detail()["report_available"] is False
    restarted = ReviewResultStore(WorkerJobStore(ctx.repo), job["id"])
    recovered = restarted.recover()
    assert recovered["status"] == "received"
    assert restarted.detail(expected_task_context=context, include_report=True)["report"]["findings"] == []


def test_missing_terminal_capture_never_recovers_by_invoking_model_or_inventing_report(ctx):
    results, *_rest = prepared(ctx)
    results.mark_running()
    with pytest.raises(SkillError) as error:
        results.recover()
    assert error.value.code == "review_recovery_pending"
    assert results.detail()["report_available"] is False
    with pytest.raises(SkillConflict):
        results.mark_running()


def test_changed_candidate_and_other_task_cannot_receive_or_read_this_report(ctx):
    results, _job, context, _selected, candidate, _provider, _ref = prepared(ctx)
    results.mark_running()
    other = deepcopy(context)
    other["task"]["logical_id"] = "a-different-task"
    with pytest.raises(SkillError) as error:
        results.detail(expected_task_context=other, include_report=True)
    assert error.value.code == "snapshot_scope_mismatch"
    candidate.write_text("Changed source; not this task's review target.\n")
    with pytest.raises(SkillError) as error:
        results.receive(capture_final_turn(channel(), returncode=0))
    assert error.value.code == "review_target_changed"


def test_revocation_before_receive_blocks_report_without_releasing_historical_pins(ctx):
    results, _job, _context, selected, *_rest = prepared(ctx)
    results.mark_running()
    ctx.service.availability.revoke(selected["skill_id"], selected["version_id"], actor="user", reason="safety",
                                    command_key="revoke-review", expected_revision=0)
    with pytest.raises(SkillError):
        results.receive(capture_final_turn(channel(), returncode=0))
    results.fail("version_revoked")
    with pytest.raises(SkillError):
        results.detail(include_report=True)


def test_worker_status_and_result_md_do_not_authorize_a_report(ctx):
    results, job, *_rest = prepared(ctx)
    path = ctx.workers.root / job["id"]
    (path / "status.json").write_text('{"status":"completed", "report_available":true}')
    (path / "result.md").write_text("All good; approved by the model.")
    assert results.detail()["status"] == "prepared"
    assert results.detail()["report_available"] is False


@pytest.mark.parametrize("stdout,code", [
    (b"done", 0), (b"", 0), (channel(), 1),
    (b'{"type":"turn.completed"}\n', 0),
    (channel().replace(b'"turn.completed"', b'"turn.failed"'), 0),
    (channel() + b'{"type":"turn.completed"}\n', 0),
    (b'{"type":"thread.started","thread_id":"x"}\n{"type":"turn.started"}\n', 0),
])
def test_exit_zero_or_incomplete_failed_channel_is_not_a_successful_review(stdout, code):
    with pytest.raises(SkillError):
        capture_final_turn(stdout, returncode=code)
