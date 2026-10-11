"""Explicit CLI checks on synthetic local Git artifacts; no model/provider use."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from goodteam_os.cli import main
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.candidate_validation import CandidateValidationService
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from goodteam_os.execution.task_snapshot import TaskInputError, execution_key
from test_task_input_snapshot import ctx, start  # noqa: F401


def git(root, *args):
    env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
           "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"}
    return subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def candidate(request):
    context = request.getfixturevalue("ctx")
    run = start(context)
    git(context.project, "init", "-q")
    (context.project / ".gitignore").write_text(".goodteam/\n")
    (context.project / "feature.py").write_text("VALUE = 'original'\n")
    git(context.project, "add", ".")
    git(context.project, "commit", "-qm", "Synthetic baseline")
    context.head = git(context.project, "rev-parse", "HEAD")
    context.execution_id = run.execution_id
    context.validation = CandidateValidationService(context.project)
    return context


def validate(c, **overrides):
    request = {"operation_id": "check-1", "head_sha": c.head,
               "argv": [sys.executable, "-c", "print('checked')"], "criteria": [0]}
    request.update(overrides)
    return c.validation.validate(c.execution_id, **request)


def protected(c):
    return c.store.root / execution_key(c.execution_id)


def test_actual_cli_to_manifest_and_existing_workbench(candidate, capsys):
    c = candidate
    assert main(["execution", "validate-candidate", "--project-root", str(c.project),
                 "--execution-id", c.execution_id, "--head", c.head, "--operation-id", "cli-check",
                 "--criterion", "0", "--", sys.executable, "-c", "print('checked')"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation"]["exit_code"] == 0
    api = ExecutionAPI(c.project)
    evidence = api.get_evidence(c.execution_id)
    validation = evidence["candidate_validation"]
    assert validation["check_status"] == "passed"
    assert validation["task_execution_mode"] == "simulated"  # From protected fake adapter.
    assert validation["verification"] == "unverified" and validation["acceptance_status"] == "unknown"
    coverage = evidence["task_input"]["acceptance_coverage"]
    assert coverage[0]["check_status"] == "passed" and coverage[0]["status"] == "unknown"
    assert coverage[1]["check_status"] == "unknown"  # Duplicate text has a distinct identity.
    review = api.get_review_workbench(c.execution_id)
    assert "Checks: passed" in render_review_workbench(review)
    html = render_review_workbench_html(review)
    assert "Candidate validation" in html and c.head in html and "Acceptance: unknown" in html


@pytest.mark.parametrize("change", ["dirty", "staged", "untracked", "assume_unchanged", "mode", "symlink"])
def test_dirty_or_unsupported_candidate_cannot_invoke(candidate, monkeypatch, change):
    c = candidate
    if change == "untracked":
        (c.project / "extra").write_text("unexpected")
    elif change == "mode":
        (c.project / "feature.py").chmod(0o755)
    elif change == "symlink":
        (c.project / "feature.py").unlink()
        (c.project / "feature.py").symlink_to(c.project / ".gitignore")
    else:
        if change == "assume_unchanged":
            git(c.project, "update-index", "--assume-unchanged", "feature.py")
        (c.project / "feature.py").write_text("changed")
        if change == "staged":
            git(c.project, "add", "feature.py")
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not invoke"))
    with pytest.raises(TaskInputError):
        validate(c)
    assert not list(protected(c).glob("validation-started-*"))


def test_original_issue_command_is_never_automatically_used(candidate):
    result = validate(candidate)
    assert result["observation"]["status"] == "passed"
    plans = list(protected(candidate).glob("validation-plan-*"))
    assert "proposed-command" not in plans[0].read_text()


def test_criterion_mapping_does_not_imply_acceptance(candidate):
    result = validate(candidate, criteria=[0, 1])
    assert result["observation"]["status"] == "passed"
    evidence = candidate.validation.evidence(candidate.execution_id)
    assert all(c["acceptance_status"] == "unknown" for c in evidence["criteria"])


def test_command_environment_has_no_inherited_provider_or_home(candidate, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-credential")
    script = "import os; assert 'OPENAI_API_KEY' not in os.environ; assert os.environ['HOME'] != " + repr(str(Path.home()))
    assert validate(candidate, argv=[sys.executable, "-c", script])["observation"]["status"] == "passed"


@pytest.mark.parametrize("script,reason,status", [("raise SystemExit(7)", "validation_command_failed", "failed"),
    ("import time; time.sleep(10)", "validation_timeout", "unknown"),
    ("print('x' * 1200000)", "validation_output_limit", "unknown"),
    ("open('feature.py','w').write('changed')", "validation_worktree_dirty", "unknown")])
def test_real_process_failure_or_candidate_drift_is_not_pass(candidate, script, reason, status):
    result = validate(candidate, argv=[sys.executable, "-c", script], seconds=1)
    assert result["observation"]["status"] == status
    assert result["observation"]["reason"] == reason
    assert "stdout_excerpt" not in result["observation"]


def test_finished_operation_dedup_and_different_content_conflict(candidate):
    c = candidate
    command = [sys.executable, "-c", "from pathlib import Path; p=Path('.goodteam/count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
    first = validate(c, argv=command)
    assert validate(c, argv=command) == first
    assert (c.project / ".goodteam/count").read_text() == "x"
    with pytest.raises(TaskInputError, match="validation_operation_conflict"):
        validate(c, criteria=[1])


def test_post_call_receipt_loss_never_reinvokes(candidate, monkeypatch):
    c = candidate
    validate(c)
    next(protected(c).glob("validation-result-*")).unlink()
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not retry"))
    with pytest.raises(TaskInputError, match="validation_attempt_indeterminate"):
        validate(c)
    assert c.validation.evidence(c.execution_id)["check_status"] == "unknown"


def test_marker_to_call_crash_is_indeterminate(candidate, monkeypatch):
    c = candidate
    def crash(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command", crash)
    with pytest.raises(KeyboardInterrupt):
        validate(c)
    monkeypatch.setattr("goodteam_os.execution.candidate_validation.run_explicit_command",
                        lambda *a, **k: pytest.fail("must not retry"))
    with pytest.raises(TaskInputError, match="validation_attempt_indeterminate"):
        validate(c)


def test_explicit_review_is_bound_but_does_not_verify_acceptance(candidate, capsys):
    c = candidate
    receipt = validate(c)
    args = ["execution", "record-review-decision", "--project-root", str(c.project),
            "--execution-id", c.execution_id, "--head", c.head, "--validation-receipt", receipt["receipt_id"],
            "--reviewer", "operator-reviewer", "--decision", "approve", "--reason", "Read the candidate and check result"]
    assert main(args) == 0
    attestation = json.loads(capsys.readouterr().out)
    assert attestation["independence"] == "not_authenticated"
    assert c.validation.evidence(c.execution_id)["review_status"] == "operator_recorded"
    assert c.validation.evidence(c.execution_id)["acceptance_status"] == "unknown"
    with pytest.raises(TaskInputError, match="validation_review_mismatch"):
        c.validation.attest_review(c.execution_id, receipt_id="wrong", head_sha=c.head,
                                  reviewer="operator-reviewer", decision="approve", reason="Read it")


def test_new_head_invalidates_current_check_display(candidate):
    c = candidate
    validate(c)
    (c.project / "feature.py").write_text("VALUE='new'\n")
    git(c.project, "commit", "-qam", "New candidate")
    evidence = c.validation.evidence(c.execution_id)
    assert evidence["check_status"] == "unknown" and evidence["reason"] == "validation_head_changed"


def test_missing_snapshot_blocks_and_cannot_use_ledger_fallback(candidate):
    c = candidate
    (protected(c) / "snapshot.json").unlink()
    with pytest.raises(TaskInputError, match="task_input_snapshot_missing"):
        validate(c)
    assert ExecutionAPI(c.project).get_evidence(c.execution_id)["candidate_validation"]["check_status"] == "unknown"


def test_result_publish_failure_preserves_attempt_boundary(candidate, monkeypatch):
    from goodteam_os.execution import candidate_validation as module
    c = candidate
    original = module._write
    def fail_result(path, value):
        if path.name.startswith("validation-result-"):
            raise OSError("synthetic lost acknowledgement")
        original(path, value)
    monkeypatch.setattr(module, "_write", fail_result)
    with pytest.raises(ValueError, match="memory_storage_unavailable"):
        validate(c)
    monkeypatch.setattr(module, "_write", original)
    monkeypatch.setattr(module, "run_explicit_command", lambda *a, **k: pytest.fail("must not retry"))
    with pytest.raises(TaskInputError, match="validation_attempt_indeterminate"):
        validate(c)


def test_lost_plan_cannot_be_rebuilt_over_retained_attempt(candidate):
    c = candidate
    validate(c)
    next(protected(c).glob("validation-plan-*")).unlink()
    with pytest.raises(TaskInputError, match="validation_plan_missing"):
        validate(c)
    assert c.validation.evidence(c.execution_id)["check_status"] == "unknown"


def test_cached_receipt_is_not_returned_as_current_after_edit(candidate):
    c = candidate
    validate(c)
    (c.project / "feature.py").write_text("later edit")
    with pytest.raises(TaskInputError, match="validation_worktree_dirty"):
        validate(c)


def _competing_check(project, execution, head, connection):
    try:
        result = CandidateValidationService(Path(project)).validate(execution, operation_id="concurrent",
            head_sha=head, criteria=[0], argv=[sys.executable, "-c",
                "from pathlib import Path; import time; p=Path('.goodteam/count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(.1)"])
        connection.send(result["receipt_id"])
    finally:
        connection.close()


def test_two_processes_same_operation_execute_only_once(candidate):
    import multiprocessing
    c = candidate
    mp = multiprocessing.get_context("fork")
    pairs = [mp.Pipe(duplex=False) for _ in range(2)]
    children = [mp.Process(target=_competing_check, args=(str(c.project), c.execution_id, c.head, send))
                for _receive, send in pairs]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert pairs[0][0].recv() == pairs[1][0].recv()
    assert (c.project / ".goodteam/count").read_text() == "x"


def test_source_probe_never_runs_git_clean_filter(candidate, tmp_path):
    c = candidate
    (c.project / ".gitattributes").write_text("feature.py filter=untrusted\n")
    git(c.project, "add", ".gitattributes")
    git(c.project, "commit", "-qm", "Synthetic filter metadata")
    c.head = git(c.project, "rev-parse", "HEAD")
    marker = tmp_path / "filter-called"
    git(c.project, "config", "filter.untrusted.clean", f"touch {marker}; cat")
    (c.project / "feature.py").write_text("changed")
    with pytest.raises(TaskInputError, match="validation_worktree_dirty"):
        validate(c)
    assert not marker.exists()


def test_human_reject_identity_and_reason_are_visible_and_html_escaped(candidate):
    c = candidate
    receipt = validate(c)
    c.validation.attest_review(c.execution_id, receipt_id=receipt["receipt_id"], head_sha=c.head,
        reviewer="reviewer-label", decision="reject", reason="Missing edge case <script>alert(1)</script>")
    review = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    text = render_review_workbench(review)
    html = render_review_workbench_html(review)
    for expected in ("Human decision: reject", "Reviewer label: reviewer-label", "Missing edge case",
                     "not_authenticated", "unverified"):
        assert expected in text and expected in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "Acceptance: unknown" in text
