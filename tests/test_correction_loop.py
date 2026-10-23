"""Real local Git correction/check rounds; no worker/provider is invoked."""
import sys
from http_transport_helpers import prepare_body_reader

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.web import handle_execution_post
from test_candidate_validation import candidate, ctx, git, protected  # noqa: F401


@pytest.fixture
def loop_context(request):
    c = request.getfixturevalue("candidate")
    c.argv = [sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    c.first = c.validation.validate(c.execution_id, operation_id="initial-check", head_sha=c.head,
                                    argv=c.argv, criteria=[0])
    c.validation.attest_review(c.execution_id, receipt_id=c.first["receipt_id"], head_sha=c.head,
                              reviewer="operator-reviewer", decision="request_changes", reason="Fix the original VALUE condition")
    c.loop = CorrectionLoopService(c.project)
    return c


def begin(c, **kwargs):
    return c.loop.start(c.execution_id, receipt_id=c.first["receipt_id"], head_sha=c.head, **kwargs)


def fix_and_commit(c, value):
    (c.project / "feature.py").write_text(f"VALUE = {value!r}\n")
    git(c.project, "commit", "-qam", "Explicit synthetic correction")
    return git(c.project, "rev-parse", "HEAD")


def check(c, head, operation, **kwargs):
    return c.validation.validate(c.execution_id, operation_id=operation, head_sha=head,
                                 argv=c.argv, criteria=[0], **kwargs)


def review(c, result, decision):
    return c.validation.attest_review(c.execution_id, receipt_id=result["receipt_id"],
        head_sha=result["candidate"]["head_sha"], reviewer="operator-reviewer", decision=decision,
        reason="Actually inspected this candidate and check")


def test_real_fix_recheck_bound_ui_review_and_human_handoff(loop_context):
    c = loop_context
    before = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    html = render_review_workbench_html(before, decision_actions_enabled=True)
    assert 'data-action="start"' in html and "up to 3 rounds" in html
    code, opened = handle_execution_post(f"/executions/{c.execution_id}/correction/start",
        {"project_root": str(c.project), "head": c.head, "validation_receipt": c.first["receipt_id"]})
    assert code == 200 and opened["status"] == "awaiting_fix"
    assert opened["feedback"] == "Fix the original VALUE condition"
    head = fix_and_commit(c, "fixed")
    result = check(c, head, "round-1")
    assert result["observation"]["status"] == "passed"
    evidence = ExecutionAPI(c.project).get_evidence(c.execution_id)
    assert evidence["correction_loop"]["status"] == "awaiting_review"
    assert evidence["task_input"]["acceptance_coverage"][0]["status"] == "unknown"
    code, recorded = handle_execution_post(f"/executions/{c.execution_id}/review-workbench/decision",
        {"project_root": str(c.project), "head": head, "validation_receipt": result["receipt_id"],
         "reviewer": "operator-reviewer", "decision": "approve", "reason": "Inspected the new candidate"})
    assert code == 200 and recorded["acceptance_status"] == "unknown"
    assert recorded["human_record"]["producer"] == "explicit_operator_workbench"
    end = c.loop.evidence(c.execution_id)
    assert end["status"] == "reviewed" and end["completed_rounds"] == 1
    assert end["automatic_provider_invocations"] == 0
    assert c.orchestrator.ledger.read_run(c.execution_id).status == "queued"
    assert "reviewed" in render_review_workbench_html(ExecutionAPI(c.project).get_review_workbench(c.execution_id))


def test_start_replay_cannot_reset_budgets_or_feedback(loop_context):
    c = loop_context
    first = begin(c, max_rounds=1)
    assert begin(c, max_rounds=1) == first
    with pytest.raises(TaskInputError, match="correction_already_started"):
        begin(c, max_rounds=5)


def test_missing_review_or_wrong_candidate_cannot_start(loop_context):
    c = loop_context
    with pytest.raises(TaskInputError, match="correction_feedback_mismatch"):
        c.loop.start(c.execution_id, receipt_id="wrong", head_sha=c.head)
    assert not (protected(c) / "correction-required.json").exists()


def test_unchanged_tree_stops_without_invoking_check(loop_context, monkeypatch):
    c = loop_context
    begin(c)
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("no progress must not invoke"))
    with pytest.raises(TaskInputError, match="correction_no_progress"):
        check(c, c.head, "unchanged")
    assert c.loop.evidence(c.execution_id)["status"] == "needs_human"


def test_changed_command_cannot_relabel_a_recheck(loop_context):
    c = loop_context
    begin(c)
    head = fix_and_commit(c, "fixed")
    with pytest.raises(TaskInputError, match="correction_check_changed"):
        c.validation.validate(c.execution_id, operation_id="weaker-check", head_sha=head,
                              argv=[sys.executable, "-c", "pass"], criteria=[0])
    assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 0


def test_round_limit_requires_human_without_an_extra_invocation(loop_context, monkeypatch):
    c = loop_context
    begin(c, max_rounds=1)
    head = fix_and_commit(c, "still-bad")
    result = check(c, head, "round-1")
    review(c, result, "request_changes")
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_round_budget_exhausted"
    next_head = fix_and_commit(c, "fixed")
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("budget must block"))
    with pytest.raises(TaskInputError, match="correction_round_budget_exhausted"):
        check(c, next_head, "round-2")


def test_same_failure_after_two_real_corrections_stops(loop_context):
    c = loop_context
    begin(c)
    first = check(c, fix_and_commit(c, "still-bad-1"), "round-1")
    review(c, first, "request_changes")
    second = check(c, fix_and_commit(c, "still-bad-2"), "round-2")
    assert second["observation"]["status"] == "failed"
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_no_progress"


def test_wall_budget_expiry_and_clock_rollback_are_visible(loop_context, monkeypatch):
    c = loop_context
    opened = begin(c, wall_seconds=30)
    monkeypatch.setattr(c.loop, "clock", lambda: opened["deadline_at"] + 1)
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    monkeypatch.setattr(c.loop, "clock", lambda: opened["deadline_at"] - 31)
    assert c.loop.evidence(c.execution_id)["status"] == "needs_human"


@pytest.mark.parametrize("lost", ["correction-loop.json", "correction-required.json"])
def test_partial_loop_proof_loss_cannot_restart_budget(loop_context, lost):
    c = loop_context
    begin(c)
    (protected(c) / lost).unlink()
    with pytest.raises(TaskInputError, match="correction_state_unavailable"):
        begin(c)
    assert c.loop.evidence(c.execution_id)["status"] == "needs_human"


def test_result_loss_after_round_call_remains_indeterminate(loop_context):
    c = loop_context
    begin(c)
    result = check(c, fix_and_commit(c, "fixed"), "round-1")
    (protected(c) / ("validation-result-" + result["operation_key"] + ".json")).unlink()
    evidence = c.loop.evidence(c.execution_id)
    assert evidence["status"] == "needs_human" and evidence["reason"] == "correction_attempt_indeterminate"


def test_stop_is_exact_idempotent_and_blocks_next_check(loop_context):
    c = loop_context
    state = begin(c)
    with pytest.raises(TaskInputError, match="correction_identity_mismatch"):
        c.loop.stop(c.execution_id, loop_id="wrong")
    stopped = c.loop.stop(c.execution_id, loop_id=state["loop_id"])
    assert c.loop.stop(c.execution_id, loop_id=state["loop_id"]) == stopped
    head = fix_and_commit(c, "fixed")
    with pytest.raises(TaskInputError, match="correction_stopped_by_operator"):
        check(c, head, "round-1")


def test_edit_after_review_never_presents_current_handoff(loop_context):
    c = loop_context
    begin(c)
    result = check(c, fix_and_commit(c, "fixed"), "round-1")
    review(c, result, "approve")
    (c.project / "feature.py").write_text("VALUE='later'\n")
    evidence = c.loop.evidence(c.execution_id)
    assert evidence["status"] == "needs_human" and evidence["reason"] == "validation_worktree_dirty"


def test_expired_budget_blocks_real_call_admission(loop_context, monkeypatch):
    c = loop_context
    opened = begin(c, wall_seconds=30)
    head = fix_and_commit(c, "fixed")
    monkeypatch.setattr("goodteam_os.execution.correction_loop.time.time", lambda: opened["deadline_at"] + 1)
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("expired budget cannot invoke"))
    with pytest.raises(TaskInputError, match="correction_time_budget_exhausted"):
        check(c, head, "round-1", seconds=1)


def test_known_current_receipt_detects_rolled_back_round_journal(loop_context):
    c = loop_context
    begin(c)
    path = protected(c) / "correction-loop.json"
    before = path.read_bytes()
    check(c, fix_and_commit(c, "fixed"), "round-1")
    path.write_bytes(before)
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_state_unavailable"


def test_admission_rejection_does_not_replace_current_validation(loop_context):
    c = loop_context
    begin(c)
    old_current = (protected(c) / "validation-current.json").read_bytes()
    head = fix_and_commit(c, "fixed")
    with pytest.raises(TaskInputError, match="correction_check_changed"):
        c.validation.validate(c.execution_id, operation_id="different", head_sha=head,
                               argv=[sys.executable, "-c", "pass"], criteria=[0])
    assert (protected(c) / "validation-current.json").read_bytes() == old_current


def test_actual_dashboard_routes_bind_feedback_and_budget(loop_context, monkeypatch):
    import json
    from test_review_workbench_browser_decisions import _capture_dashboard_requests
    c = loop_context
    send = _capture_dashboard_requests(c.project, monkeypatch)
    status, _headers, body = send("POST", f"/api/executions/{c.execution_id}/correction/start",
        {"project_root": str(c.project), "head": c.head, "validation_receipt": c.first["receipt_id"]})
    payload = json.loads(body)
    assert status == 200 and payload["status"] == "awaiting_fix"
    html = render_review_workbench_html(ExecutionAPI(c.project).get_review_workbench(c.execution_id),
                                        decision_actions_enabled=True)
    assert 'data-action="stop"' in html and "Fix the original VALUE condition" in html
    assert "correctionPending" in html and "decisionPending" in html
    status, _headers, body = send("POST", f"/api/executions/{c.execution_id}/correction/stop",
        {"project_root": str(c.project), "loop_id": payload["loop_id"]})
    assert status == 200 and json.loads(body)["reason"] == "correction_stopped_by_operator"


@pytest.mark.parametrize("stop", [False, True])
def test_prepared_round_resume_respects_stop_and_never_repeats_call(loop_context, monkeypatch, stop):
    from goodteam_os.execution import candidate_validation as module
    c = loop_context
    opened = begin(c)
    head = fix_and_commit(c, "fixed")
    original = module._write
    def fail_before_marker(path, value):
        if path.name.startswith("validation-started-"):
            raise OSError("synthetic pre-call interruption")
        original(path, value)
    monkeypatch.setattr(module, "_write", fail_before_marker)
    with pytest.raises(ValueError, match="memory_storage_unavailable"):
        check(c, head, "round-1")
    monkeypatch.setattr(module, "_write", original)
    assert c.loop.evidence(c.execution_id)["status"] == "awaiting_recheck"
    if stop:
        c.loop.stop(c.execution_id, loop_id=opened["loop_id"])
        monkeypatch.setattr(module, "run_explicit_command", lambda *a, **k: pytest.fail("stopped prepared round cannot run"))
        with pytest.raises(TaskInputError, match="correction_stopped_by_operator"):
            check(c, head, "round-1")
    else:
        assert check(c, head, "round-1")["observation"]["status"] == "passed"
        assert c.loop.evidence(c.execution_id)["reserved_rounds"] == 1


def _competing_round(project, execution_id, head, argv, operation, pipe):
    from goodteam_os.execution.candidate_validation import CandidateValidationService
    try:
        result = CandidateValidationService(project).validate(execution_id, operation_id=operation,
            head_sha=head, argv=argv, criteria=[0])
        pipe.send(result["observation"]["status"])
    except TaskInputError as exc:
        pipe.send(exc.code)
    finally:
        pipe.close()


def test_competing_new_rounds_do_not_double_run_before_review(request):
    import multiprocessing
    c = request.getfixturevalue("candidate")
    argv = [sys.executable, "-c", "from pathlib import Path; p=Path('.goodteam/check-count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    first = c.validation.validate(c.execution_id, operation_id="initial", head_sha=c.head, argv=argv, criteria=[0])
    c.validation.attest_review(c.execution_id, receipt_id=first["receipt_id"], head_sha=c.head,
                              reviewer="operator-label", decision="request_changes", reason="Fix VALUE")
    loop = CorrectionLoopService(c.project)
    loop.start(c.execution_id, receipt_id=first["receipt_id"], head_sha=c.head)
    head = fix_and_commit(c, "fixed")
    mp = multiprocessing.get_context("fork")
    pairs = [mp.Pipe(duplex=False) for _ in range(2)]
    children = [mp.Process(target=_competing_round,
        args=(c.project, c.execution_id, head, argv, f"contender-{i}", send))
        for i, (_receive, send) in enumerate(pairs)]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert sorted(receive.recv() for receive, _send in pairs) == ["correction_review_required", "passed"]
    assert (c.project / ".goodteam/check-count").read_text() == "xx"
    assert loop.evidence(c.execution_id)["reserved_rounds"] == 1


def test_observed_expiry_is_durable_across_clock_backstep_and_restart(loop_context, monkeypatch):
    c = loop_context
    stamp = [1000.0]
    monkeypatch.setattr("goodteam_os.execution.correction_loop.time.time", lambda: stamp[0])
    c.loop = CorrectionLoopService(c.project)
    begin(c, wall_seconds=60)
    head = fix_and_commit(c, "fixed")
    stamp[0] = 1061.0
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    stamp[0] = 1030.0
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("observed expiry cannot revive"))
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["status"] == "needs_human"
    with pytest.raises(TaskInputError, match="correction_time_budget_exhausted"):
        check(c, head, "after-expiry", seconds=1)


def test_clock_backstep_inside_budget_is_latched(loop_context, monkeypatch):
    c = loop_context
    stamp = [1000.0]
    monkeypatch.setattr("goodteam_os.execution.correction_loop.time.time", lambda: stamp[0])
    c.loop = CorrectionLoopService(c.project)
    begin(c, wall_seconds=60)
    stamp[0] = 1040.0
    assert c.loop.evidence(c.execution_id)["status"] == "awaiting_fix"
    stamp[0] = 1030.0
    assert c.loop.evidence(c.execution_id)["reason"] == "correction_time_budget_exhausted"
    stamp[0] = 1041.0
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["status"] == "needs_human"


def test_historical_cached_read_cannot_reselect_or_hide_round_rollback(loop_context, monkeypatch):
    c = loop_context
    begin(c, max_rounds=1)
    state_path = protected(c) / "correction-loop.json"
    initial = state_path.read_bytes()
    result = check(c, fix_and_commit(c, "still-bad"), "round-1")
    review(c, result, "request_changes")
    pointer = (protected(c) / "validation-current.json").read_bytes()
    git(c.project, "reset", "--hard", c.head)
    assert check(c, c.head, "initial-check") == c.first
    assert (protected(c) / "validation-current.json").read_bytes() == pointer
    state_path.write_bytes(initial)
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["reason"] == "correction_state_unavailable"
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("retained reservation cannot reset"))
    with pytest.raises(TaskInputError, match="correction_state_unavailable"):
        check(c, fix_and_commit(c, "fixed"), "extra-round")


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_reservation_loss_is_not_rebuilt(loop_context, monkeypatch, damage):
    c = loop_context
    begin(c)
    check(c, fix_and_commit(c, "fixed"), "round-1")
    marker = protected(c) / "correction-round-1.json"
    if damage == "missing":
        marker.unlink()
    else:
        marker.write_bytes(b"{}")
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("bad reservation must block"))
    with pytest.raises(TaskInputError):
        check(c, git(c.project, "rev-parse", "HEAD"), "round-1")
    assert c.loop.evidence(c.execution_id)["status"] == "needs_human"
    assert not marker.exists() if damage == "missing" else marker.read_bytes() == b"{}"


@pytest.mark.parametrize("interruption", ["reservation", "halt"])
def test_independent_marker_precedes_journal_and_never_releases_budget(loop_context, monkeypatch, interruption):
    from goodteam_os.execution import correction_loop as module
    c = loop_context
    begin(c)
    original = module._write
    def interrupt_journal(path, value):
        if path.name == "correction-loop.json":
            raise OSError("synthetic interruption after durable independent marker")
        original(path, value)
    monkeypatch.setattr(module, "_write", interrupt_journal)
    with pytest.raises(ValueError, match="memory_storage_unavailable"):
        if interruption == "reservation":
            check(c, fix_and_commit(c, "fixed"), "round-1")
        else:
            c.loop.stop(c.execution_id, loop_id=c.loop.evidence(c.execution_id)["loop_id"])
    monkeypatch.setattr(module, "_write", original)
    expected = "correction_state_unavailable" if interruption == "reservation" else "correction_stopped_by_operator"
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["reason"] == expected
    assert not list(protected(c).glob("validation-started-*"))[1:]


def _dashboard_post_headers(workspace, monkeypatch, path, payload, headers):
    import json
    from io import BytesIO
    from goodteam_os import web
    handlers = []
    class Server:
        server_port = 8788
        def serve_forever(self):
            pass
    def factory(_address, handler):
        handlers.append(handler)
        return Server()
    monkeypatch.setattr(web, "ThreadingHTTPServer", factory)
    web.serve(workspace, host="127.0.0.1", port=8788)
    raw = json.dumps(payload).encode()
    class Request(handlers[0]):
        def __init__(self):
            self.client_address = ("127.0.0.1", 1234)
            self.server = Server()
            self.headers = {"Content-Length": str(len(raw)), "Host": "127.0.0.1:8788",
                            "Content-Type": "application/json", **headers}
            self.path, self.rfile, self.wfile = path, BytesIO(raw), BytesIO()
            prepare_body_reader(self)
            self.status = None
        def send_response(self, code, message=None):
            self.status = code
        def send_error(self, code, message=None, explain=None):
            self.status = code
        def send_header(self, *args):
            pass
        def end_headers(self):
            pass
    request = Request()
    request.do_POST()
    return request.status


@pytest.mark.parametrize("headers,status", [
    ({"Origin": "http://127.0.0.1:8788"}, 200),
    ({}, 200),  # Explicit non-browser JSON client; not an authenticated reviewer.
    ({"Origin": "http://127.0.0.1:9999"}, 403),
    ({"Origin": "http://localhost:8788"}, 403),
    ({"Origin": "https://127.0.0.1:8788"}, 403),
    ({"Origin": "http://untrusted.invalid:8788", "Host": "untrusted.invalid:8788"}, 403),
    ({"Origin": "null"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "http://127.0.0.1:8788", "Content-Type": "text/plain"}, 415),
    ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
])
def test_protected_dashboard_origin_and_json_contract(loop_context, monkeypatch, headers, status):
    c = loop_context
    assert _dashboard_post_headers(c.project, monkeypatch,
        f"/api/executions/{c.execution_id}/correction/start",
        {"project_root": str(c.project), "head": c.head, "validation_receipt": c.first["receipt_id"]}, headers) == status
    assert (protected(c) / "correction-loop.json").exists() == (status == 200)


@pytest.mark.parametrize("action", ["correction/stop", "review-workbench/decision"])
def test_other_local_origin_cannot_stop_or_attest(loop_context, monkeypatch, action):
    c = loop_context
    opened = begin(c)
    payload = {"project_root": str(c.project), "loop_id": opened["loop_id"],
               "head": c.head, "validation_receipt": c.first["receipt_id"],
               "reviewer": "unexpected", "decision": "reject", "reason": "cross origin"}
    assert _dashboard_post_headers(c.project, monkeypatch, f"/api/executions/{c.execution_id}/{action}",
        payload, {"Origin": "http://127.0.0.1:9999", "Content-Type": "text/plain"}) == 403
    assert c.loop.evidence(c.execution_id)["status"] == "awaiting_fix"
