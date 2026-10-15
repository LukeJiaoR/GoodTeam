"""Narrow startup composition and interrupted recheck recovery, entirely offline."""
import json
from pathlib import Path

import pytest

from goodteam_os import cli
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills.codex_runtime import CodexReviewAdmission
from test_automatic_candidate_review import automatic as automatic_fixture, admit_correction  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401


class UnusedReviewer(CodexReviewAdmission):
    def acquire(self, *args, **kwargs):
        pytest.fail("Composition must not acquire Reviewer access")

    def check(self, *args, **kwargs):
        pytest.fail("Composition must not check Reviewer access")

    def finish(self, *args, **kwargs):
        pytest.fail("Composition must not finish Reviewer access")


def correction_composition(monkeypatch):
    """Stand in only for the separately tested typed authority constructor."""
    from goodteam_os.execution import correction_native

    admission = object()

    def kwargs(value):
        assert value is None or value is admission
        return {} if value is None else {"codex_correction_admission": value}

    monkeypatch.setattr(correction_native, "correction_worker_kwargs", kwargs)
    return correction_native, admission


def continuation_args(command):
    return ["execution", command, "--project-root", "/offline/project", "--execution-id", "offline-execution",
            *(["--job-id", "offline-correction"] if command == "run-correction-worker" else [])]


@pytest.mark.parametrize("command", ["run-correction-worker", "continue-automatic-review"])
def test_narrow_cli_composes_existing_correction_policy_separately(monkeypatch, capsys, command):
    native, admission = correction_composition(monkeypatch)
    constructed, calls = [], []

    def construct(path):
        constructed.append(path)
        return admission

    def dispatch(args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "offline_composition_only"}

    monkeypatch.setattr(native, "TrustedCodexCorrectionAdmission", construct)
    monkeypatch.setattr(cli, "_run_execution_command", dispatch)
    reviewer = UnusedReviewer() if command == "continue-automatic-review" else None
    args = continuation_args(command) + ["--trusted-codex-correction-policy", "/offline/existing-policy.json",
                                         "--acknowledge-external-cost"]
    assert cli.main(args, codex_review_admission=reviewer) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "offline_composition_only"
    assert constructed == [Path("/offline/existing-policy.json")]
    assert len(calls) == 1 and calls[0][0].acknowledge_external_cost is True
    assert calls[0][1] == {"codex_correction_admission": admission,
                           **({"codex_review_admission": reviewer} if reviewer else {})}


@pytest.mark.parametrize("args", [
    ["serve"],
    ["execution", "run", "--project-root", "/offline/project", "--execution-id", "offline-execution"],
    continuation_args("stop-automatic-review"),
    ["execution", "validate-candidate", "--project-root", "/offline/project", "--execution-id", "offline-execution",
     "--head", "0" * 40, "--operation-id", "check", "--criterion", "0"],
])
def test_correction_composition_cannot_enable_unrelated_cli_entries(monkeypatch, args):
    from goodteam_os.execution import correction_native

    monkeypatch.setattr(correction_native, "correction_worker_kwargs",
                        lambda *_: pytest.fail("Unsupported entry reached authority composition"))
    with pytest.raises(ValueError, match="supported only"):
        cli.main(args, codex_correction_admission=object())
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(args + ["--trusted-codex-correction-policy", "/offline/policy.json"])
    assert exc.value.code == 2


def test_cli_rejects_two_correction_authorities_before_reading_policy(monkeypatch):
    from goodteam_os.execution import correction_native

    monkeypatch.setattr(correction_native, "TrustedCodexCorrectionAdmission",
                        lambda *_: pytest.fail("Conflicting policy must not be read"))
    with pytest.raises(ValueError, match="Choose one correction"):
        cli.main(continuation_args("continue-automatic-review") +
                 ["--trusted-codex-correction-policy", "/offline/policy.json"],
                 codex_correction_admission=object())


def test_api_keeps_reviewer_and_correction_dependencies_separate(tmp_path, monkeypatch):
    from goodteam_os.execution import automatic_review, correction_worker

    _native, correction = correction_composition(monkeypatch)
    reviewer, calls = UnusedReviewer(), []

    class Automatic:
        def __init__(self, root, **kwargs):
            calls.append(("automatic", root, kwargs))

        def advance(self, execution_id, **kwargs):
            return {"execution_id": execution_id, **kwargs}

    class Correction:
        def __init__(self, root, **kwargs):
            calls.append(("correction", root, kwargs))

        def run(self, execution_id, job_id, **kwargs):
            return {"execution_id": execution_id, "job_id": job_id, **kwargs}

    monkeypatch.setattr(automatic_review, "AutomaticCandidateReviewService", Automatic)
    monkeypatch.setattr(correction_worker, "CorrectionWorkerService", Correction)
    api = ExecutionAPI(tmp_path, codex_review_admission=reviewer, codex_correction_admission=correction)
    assert api.advance_automatic_review("execution", acknowledge_external_cost=True)["acknowledge_external_cost"]
    assert api.run_correction_worker("execution", "correction", acknowledge_external_cost=True)["job_id"] == "correction"
    assert calls == [
        ("automatic", tmp_path, {"codex_review_admission": reviewer, "codex_correction_admission": correction}),
        ("correction", tmp_path, {"codex_correction_admission": correction}),
    ]


def test_cli_execution_bridge_preserves_both_typed_dependencies(monkeypatch):
    _native, correction = correction_composition(monkeypatch)
    reviewer, calls = UnusedReviewer(), []

    class API:
        def __init__(self, root, **kwargs):
            calls.append((root, kwargs))

        def advance_automatic_review(self, execution_id, **kwargs):
            return {"execution_id": execution_id, **kwargs}

    monkeypatch.setattr(cli, "ExecutionAPI", API)
    args = cli.build_parser().parse_args(continuation_args("continue-automatic-review"))
    assert cli._run_execution_command(args, codex_review_admission=reviewer,
        codex_correction_admission=correction)["execution_id"] == "offline-execution"
    assert calls == [(Path("/offline/project"),
                      {"codex_review_admission": reviewer, "codex_correction_admission": correction})]


def test_completed_worker_restart_finalizes_before_recheck_without_redispatch(request, monkeypatch):
    from goodteam_os.execution.correction_worker import CorrectionWorkerService

    c = request.getfixturevalue("automatic_fixture")
    admit_correction(monkeypatch)
    calls, original_validate = [], c.validation.validate

    def interrupted(_service, execution_id, job_id):
        calls.append((execution_id, job_id))
        # Interrupt the handoff before seal-started is published. Actual
        # interrupted sealing remains fail-closed in the host-seal tests.
        raise TaskInputError("correction_finalization_not_started")

    monkeypatch.setattr(CorrectionWorkerService, "finalize", interrupted)
    outcome = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert outcome["reason"] == "correction_finalization_not_started", outcome
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 1
    assert CorrectionLoopService(c.project).evidence(c.execution_id)["status"] == "awaiting_recheck"
    assert len(calls) == 1
    job_id = calls[0][1]

    def finalized(_service, execution_id, current_job):
        assert (execution_id, current_job) == (c.execution_id, job_id)
        calls.append((execution_id, current_job))

    def checked(execution_id, **kwargs):
        assert calls == [(c.execution_id, job_id)] * 2
        assert kwargs["correction_worker"] == job_id
        assert kwargs["argv"] == c.argv and kwargs["criteria"] == [0, 1]
        return original_validate(execution_id, **kwargs)

    monkeypatch.setattr(CorrectionWorkerService, "finalize", finalized)
    resumed = AutomaticCandidateReviewService(c.project)
    monkeypatch.setattr(resumed.validation, "validate", checked)
    outcome = resumed.advance(c.execution_id)
    assert outcome["status"] == "reviewed", outcome
    assert outcome["review"]["candidate"]["head_sha"] != c.head
    assert outcome["review"]["base_sha"] == c.base
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2
    assert c.runtime.records[0]["target"]["head_sha"] != c.runtime.records[1]["target"]["head_sha"]
    monkeypatch.setattr(CorrectionWorkerService, "finalize",
                        lambda *_: pytest.fail("Terminal evidence must not finalize or dispatch again"))
    assert AutomaticCandidateReviewService(c.project).advance(c.execution_id)["status"] == "reviewed"
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2
