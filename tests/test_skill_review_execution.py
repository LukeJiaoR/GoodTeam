"""Full Worker/result/API contract with a real, model-free subprocess.

This is not Codex conformance or a review judgment. The fixture is composed only
inside tests; the production factory has no public/configurable fixture mode.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import sys
import time

import pytest

from goodteam_os.skills import review_execution
from goodteam_os.skills.content import SkillContextProvider
from goodteam_os.skills.models import SkillError, content_digest
from goodteam_os.skills.review_api import task_review_details
from goodteam_os.skills.review_capture import capture_final_turn
from goodteam_os.skills.review_process import ReviewProcess, run_review_process
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from test_skill_content import FixtureAuthority
from test_skill_review_protocol import direct_request
from test_skill_review_results import SOURCE, channel, report
from test_task_skill_entrypoints import bind, capture, forbid_runtime, method, ctx as ctx


class ProcessFixture(FixtureAuthority):
    def __init__(self, root, output=None, *, body="", deadline=5):
        super().__init__()
        self.root, self.output = root, output if output is not None else channel()
        self.body, self.deadline = body, deadline
        self.prompts, self.executions = [], 0

    @contextmanager
    def execution(self, record, provider, ref):
        stage = self.root / ("execution-" + str(self.executions))
        self.executions += 1
        prompt = review_execution.materialize_review_inputs(provider, ref, record, stage)
        self.prompts.append(prompt)
        body = ("import os,sys\nfrom pathlib import Path\n"
                "data=sys.stdin.buffer.read()\nassert data\n"
                "assert 'UNRELATED_CREDENTIAL' not in os.environ\n"
                "assert Path('candidate').read_bytes()==" + repr(SOURCE) + "\n"
                + self.body + "\nsys.stdout.buffer.write(" + repr(self.output) + ")\nsys.stdout.buffer.flush()\n")
        spec = ReviewProcess((sys.executable, "-I", "-c", body), stage, {"PATH": "/usr/bin:/bin"}, self.deadline)
        yield spec, prompt

    def check_current(self, provider, ref):
        provider.private_input(ref)


def ready_job(ctx, *, logical="review-worker", activation="required", required=False):
    item = {**method(ctx, "Inspect USER_BOUNDARY_X42; never use a built-in checklist."), "activation": activation}
    bind(ctx, item, subject="role:code_review", key="method:" + logical)
    context = capture(ctx, logical, subjects=("role:code_review",))
    candidate = ctx.root / (logical + ".py")
    candidate.write_bytes(SOURCE)
    job = ctx.workers.create_job(**direct_request(context, idempotency_key="execute:" + logical,
        artifact_key="review_source", artifact_path=str(candidate), artifact_version=content_digest(SOURCE),
        required_skill_ids=(item["skill_id"],) if required else ()))
    state = create_initial_state(logical, "DEMO", "Review exact source")
    state.task_skill_context = context
    StateStore(ctx.workspace).write_project_current(state)
    return job, context, item, candidate


@pytest.mark.parametrize("activation,required", [("required", False), ("on_demand", False), ("on_demand", True)])
def test_actual_worker_persists_parent_capture_and_api_after_role_edit(ctx, monkeypatch, activation, required):
    forbidden = forbid_runtime(monkeypatch)
    monkeypatch.setenv("UNRELATED_CREDENTIAL", "test-only-never-inherited")
    job, context, item, _candidate = ready_job(ctx, activation=activation, required=required)
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["status"] == "review_received", result
    assert result["artifacts"] == []  # Never approve/advance using a legacy artifact.
    assert result["changed_files"] == []
    assert forbidden == []
    assert len(runtime.prompts) == 1
    assert (b"USER_BOUNDARY_X42" in runtime.prompts[0]) == (activation == "required" or required)
    assert (ctx.root / "execution-0" / "methods" / item["skill_id"] / "SKILL.md").read_text().endswith("Inspect USER_BOUNDARY_X42; never use a built-in checklist.")
    other_context = capture(ctx, "another-task", subjects=("role:code_review",))
    bind(ctx, None, subject="role:code_review", key="restore-after-execution")
    code, detail = task_review_details(WorkerJobStore(ctx.repo), context, job_id=job["id"], include_report=True)
    assert code == 200
    assert detail["report"]["findings"] == []
    assert detail["methods"][0]["version_id"] == item["version_id"]
    assert detail["runtime_verification"] == "contract_fixture"
    assert detail["report_available"] is True
    replay = WorkerJobStore(ctx.repo).run_job(job["id"])
    assert replay["status"] == "review_received"
    assert runtime.executions == 1  # Recovery never invokes again, even after Role changes.
    code, other = task_review_details(ctx.workers, other_context, job_id=job["id"])
    assert code == 404
    assert other["error"] == "review_not_found"


def test_malformed_zero_exit_through_worker_produces_no_report(ctx, monkeypatch):
    job, context, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, channel(report(summary="")))
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["status"] == "failed"
    assert result["skill_error_code"] == "review_report_text"
    code, response = task_review_details(ctx.workers, context, job_id=job["id"], include_report=True)
    assert code == 409
    assert response["execution"]["report_available"] is False
    assert response["execution"]["status"] == "invalid"


def test_nonstring_event_type_ends_as_invalid_capture_instead_of_sticking_running(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, b'{"type":[]}\n' + channel())
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["status"] == "failed"
    assert result["skill_error_code"] == "review_capture_json"
    detail = ReviewResultStore(ctx.workers, job["id"]).detail()
    assert detail["status"] == "invalid" and not detail["report_available"]
    assert detail["process_progress"]["termination_confirmed"] is True
    assert detail["process_progress"]["error_code"] == "review_capture_json"
    assert ctx.workers.run_job(job["id"])["status"] == "failed"
    assert runtime.executions == 1


def test_task_http_path_is_readonly_bound_and_survives_reload(ctx, monkeypatch):
    from goodteam_os.web import handle_project_api_get

    job, context, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    assert ctx.workers.run_job(job["id"])["status"] == "review_received"
    path = "/api/projects/DEMO/runs/review-worker/skill-reviews"
    code, payload = handle_project_api_get(ctx.workspace, path)
    assert code == 200
    assert payload["project_key"] == "DEMO" and payload["run_id"] == "review-worker"
    assert payload["reviews"][0]["job_id"] == job["id"]
    assert "report" not in payload["reviews"][0]
    for _ in range(2):
        code, report_payload = handle_project_api_get(ctx.workspace, path + "/" + job["id"] + "/report")
        assert code == 200
        assert report_payload["report"]["findings"] == []
        assert str(ctx.root) not in json.dumps(report_payload)
    code, _ = handle_project_api_get(ctx.workspace, path.replace("review-worker", "other-task"))
    assert code == 404
    assert runtime.executions == 1
    assert handle_project_api_get(ctx.workspace, path + "/" + job["id"] + "/submit") is None
    state = StateStore(ctx.workspace).read_project_current("DEMO")
    assert state.task_skill_context == context


def test_worker_timeout_is_terminal_not_a_report_or_retry(ctx, monkeypatch):
    job, context, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, body="import time\ntime.sleep(5)", deadline=0.1)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    start = time.monotonic()
    result = ctx.workers.run_job(job["id"])
    assert time.monotonic() - start < 3
    assert result["skill_error_code"] == "review_timeout"
    assert result["skill_runtime_status"] == "contract_fixture"
    assert ReviewResultStore(ctx.workers, job["id"]).detail()["process_progress"]["termination_confirmed"] is True
    assert not task_review_details(ctx.workers, context)[1]["reviews"][0]["report_available"]
    ctx.workers.run_job(job["id"])
    assert runtime.executions == 1


def test_transport_rejection_before_launch_remains_blocked_without_native_progress(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root, deadline=900)  # Regular fixture transport has not selected a long bound.
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_process_invalid"
    assert result["skill_runtime_status"] == "blocked"
    detail = ReviewResultStore(ctx.workers, job["id"]).detail()
    assert "process_progress" not in detail and not detail["report_available"]


def test_unterminated_or_missing_result_never_reinvokes_on_worker_restart(ctx, monkeypatch):
    job, context, *_ = ready_job(ctx)
    provider = SkillContextProvider(ctx.workers, job["id"], authority=FixtureAuthority())
    ref = provider.prepare("interrupted-context")
    results = ReviewResultStore(ctx.workers, job["id"])
    results.begin(provider, ref)
    results.mark_running()
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["skill_runtime_status"] == "recovery_required"
    assert runtime.executions == 0
    assert task_review_details(ctx.workers, context)[1]["reviews"][0]["report_available"] is False


def test_corrupt_terminal_capture_never_reappears_on_api(ctx, monkeypatch):
    from goodteam_os.skills.review_results import _bytes, _read_optional, _replace

    job, context, *_ = ready_job(ctx)
    runtime = ProcessFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    ctx.workers.run_job(job["id"])
    results = ReviewResultStore(ctx.workers, job["id"])
    operator = results._registered()[0]
    with results._store(operator) as fd:
        outcome = _read_optional(fd, "outcome.json")
        del outcome["capture"]
        _replace(fd, "outcome.json", outcome)
        state = _read_optional(fd, "state.json")
        state["outcome_digest"] = content_digest(_bytes(outcome))
        _replace(fd, "state.json", state)
    code, response = task_review_details(ctx.workers, context, job_id=job["id"], include_report=True)
    assert code == 409
    assert response["execution"]["report_available"] is False


def test_child_transport_bounds_stderr_and_requires_complete_turn(tmp_path):
    spec = ReviewProcess((sys.executable, "-I", "-c", "import sys;sys.stdin.buffer.read();sys.stderr.write('x'*3000000)"),
                         tmp_path, {}, deadline_seconds=2)
    with pytest.raises(SkillError) as error:
        run_review_process(spec, b"input", check_current=lambda: None)
    assert error.value.code == "review_capture_size"
    invalid = replace(spec, command=("relative-python",))
    with pytest.raises(SkillError):
        run_review_process(invalid, b"input", check_current=lambda: None)
    assert capture_final_turn(channel(), returncode=0).event_count == 4
