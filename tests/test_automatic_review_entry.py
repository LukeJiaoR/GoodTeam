"""CLI → real admission/selector → offline child → reopened Workbench.

Only the selected executable is model-free. Trusted task/root/model, revocation,
context budgets, source capture and result validation are production paths.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from goodteam_os import cli
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_validation import CandidateValidationService, _read
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from goodteam_os.team_control_plane import ensure_team_state
from test_candidate_validation import git, protected
from test_codex_trusted import FixtureTrustedAdmission, native, policy
from test_skill_review_results import channel, report
from test_task_input_snapshot import ctx, start  # noqa: F401


@pytest.fixture
def entry(request):
    c = request.getfixturevalue("ctx")
    c.execution_id = start(c, adapter="codex").execution_id
    ensure_team_state(c.workspace, "DEMO", c.workspace / "projects" / "DEMO.yaml")
    git(c.project, "init", "-q")
    (c.project / ".gitignore").write_text(".goodteam/\n")
    (c.project / "feature.py").write_text("VALUE = 'base'\n")
    git(c.project, "add", ".")
    git(c.project, "commit", "-qm", "Base")
    c.base = git(c.project, "rev-parse", "HEAD")
    (c.project / "feature.py").write_text("VALUE = 'fixed'\n")
    git(c.project, "commit", "-qam", "Candidate")
    c.head = git(c.project, "rev-parse", "HEAD")
    c.argv = [sys.executable, "-c", "exec(open('feature.py').read()); assert VALUE == 'fixed'"]
    c.calls = c.workspace / "reviewer-invocations"
    c.scope = {"task": {"kind": "direct_task", "project_key": "DEMO",
                       "logical_id": "candidate-review:" + c.store.load(c.execution_id).payload_digest}}
    return c


def admitted(c, *, findings=False, **policy_changes):
    digest = c.store.load(c.execution_id).payload_digest
    value = report(evidence=[{"id": "E1", "path": "head/feature.py", "line_start": 1, "line_end": 1,
                             "quote": "VALUE = 'fixed'", "observation": "Exact candidate source inspected"}],
                   checks=[{"name": f"criterion:{digest}:{i}", "status": "checked", "evidence_ids": ["E1"],
                            "detail": "Assessed the original requirement"} for i in range(2)])
    if findings:
        value["findings"] = [{"id": "F1", "severity": "medium", "title": "Original condition unresolved",
            "detail": "Requires correction", "evidence_ids": ["E1"], "recommendation": "Preserve OLD_CONDITION"}]
    bridge = SimpleNamespace(root=c.workspace, repo=c.project)
    body = "assert b'OLD_CONDITION' in prompt\n"
    if getattr(c, "approved_plan", None):
        body += "assert b'No network or unrelated changes' in prompt and b'locally_registered_derivation' in prompt\n"
    body += "pathlib.Path(" + repr(str(c.calls)) + ").open('a').write('review\\n')\n"
    body += "sys.stdout.buffer.write(" + repr(channel(value)) + ");sys.exit(0)"
    host = native(bridge, body=body)
    c.policy_path = policy(bridge, host, c.scope, max_contexts=3, **policy_changes)
    return FixtureTrustedAdmission(c.policy_path)


def validate_args(c, *, base=True):
    return ["execution", "validate-candidate", "--project-root", str(c.project), "--execution-id", c.execution_id,
            "--head", c.head, "--operation-id", "cli-automatic", "--criterion", "0", "--criterion", "1",
            *(["--review-base", c.base] if base else []), "--", *c.argv]


def action_args(c, action):
    return ["execution", action, "--project-root", str(c.project), "--execution-id", c.execution_id]


def test_cli_exact_receipt_to_admitted_review_and_reopened_workbench(entry, capsys):
    c = entry
    admission = admitted(c)
    original = c.store.load(c.execution_id).data
    assert cli.main(validate_args(c), codex_review_admission=admission) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["automatic_review"]["status"] == "reviewed", result
    review = result["automatic_review"]["review"]
    assert review["validation_receipt_id"] == result["receipt_id"]
    assert review["candidate"]["head_sha"] == c.head and review["base_sha"] == c.base
    assert review["provenance"]["task_skill_context"]["task"] == c.scope["task"]
    assert review["provenance"]["runtime_verification"] == "contract_fixture"
    assert len(admission.leases) == 1 and c.calls.read_text() == "review\n"
    assert not list(protected(c).glob("validation-review-*"))
    assert c.store.load(c.execution_id).data == original
    frozen = _read(protected(c) / "automatic-review-start.json")
    assert frozen["initial_receipt"] == result["receipt_id"]
    assert frozen["reviewer_model"] == "explicit-test-model"
    assert frozen["validation_contract"]["argv"] == c.argv
    # Exact initial replay needs no receipt copying and cannot dispatch twice.
    assert cli.main(validate_args(c), codex_review_admission=admission) == 0
    assert json.loads(capsys.readouterr().out)["automatic_review"]["status"] == "reviewed"
    c.policy_path.unlink()
    # Terminal evidence remains reusable after restart without any admission.
    assert cli.main(action_args(c, "continue-automatic-review")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "reviewed"
    assert c.calls.read_text() == "review\n"
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    assert workbench["evidence"]["correction_loop"]["automatic"] is True
    text, html = render_review_workbench(workbench), render_review_workbench_html(workbench)
    for rendered in (text, html):
        assert "Automatic candidate review" in rendered and "accepted_by_review_policy" in rendered
        assert c.base in rendered and c.head in rendered and "contract_fixture" in rendered
        assert "Supervised correction loop" not in rendered
    assert "Human attestation is optional" in html and 'id="prepare-correction-worker"' not in html
    exported = ExecutionAPI(c.project).export_review_workbench_html(c.execution_id)
    assert Path(exported["html_path"]).read_text() == html


def test_automatic_correction_blocker_keeps_mode_and_frozen_resume(entry, capsys):
    c = entry
    admission = admitted(c, findings=True)
    assert cli.main(validate_args(c), codex_review_admission=admission) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["automatic_review"]["reason"] == "correction_verified_runtime_unavailable", result
    before = AutomaticCandidateReviewService(c.project).configuration(c.execution_id)
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    loop = workbench["evidence"]["correction_loop"]
    assert loop["automatic"] is True and loop["loop_id"]
    assert "blocked by runtime admission" in loop["next_action"]
    html = render_review_workbench_html(workbench, decision_actions_enabled=True)
    assert "Automatic candidate review" in html
    assert 'id="prepare-correction-worker"' not in html and 'id="correction-action"' not in html
    assert "Start supervised" not in html and "Fix with your existing tools" not in html
    ExecutionAPI(c.project).export_review_workbench_html(c.execution_id)
    assert cli.main(action_args(c, "continue-automatic-review"), codex_review_admission=admission) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "correction_verified_runtime_unavailable"
    assert AutomaticCandidateReviewService(c.project).configuration(c.execution_id) == before
    assert c.calls.read_text() == "review\n"
    assert cli.main(action_args(c, "stop-automatic-review")) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "correction_stopped_by_operator"
    assert cli.main(action_args(c, "continue-automatic-review"), codex_review_admission=admission) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "correction_stopped_by_operator"
    assert c.calls.read_text() == "review\n"


@pytest.mark.parametrize("wrong", ["task", "root", "model"])
def test_existing_admission_rejects_wrong_scope_before_child(entry, capsys, wrong):
    c = entry
    changes = {}
    if wrong == "task":
        changes["task"] = {**c.scope["task"], "logical_id": "workflow-review-cannot-be-borrowed"}
    if wrong == "root":
        changes["worker_root"] = str(c.workspace / "another-project")
    admission = admitted(c, **changes)
    if wrong == "model":
        # A previously frozen selection cannot be silently replaced on resume.
        checked = CandidateValidationService(c.project).validate(c.execution_id, operation_id="initial",
            head_sha=c.head, argv=c.argv, criteria=[0, 1])
        ExecutionAPI(c.project).start_automatic_review(c.execution_id, base_sha=c.base,
            receipt_id=checked["receipt_id"], head_sha=c.head, reviewer_adapter="codex", reviewer_model="other-model")
        args = action_args(c, "continue-automatic-review")
    else:
        args = validate_args(c)
    assert cli.main(args, codex_review_admission=admission) == 0
    result = json.loads(capsys.readouterr().out)
    outcome = result.get("automatic_review", result)
    assert outcome["status"] == "blocked" and outcome["reason"] == "trusted_codex_task_mismatch", outcome
    assert admission.leases == {} and not c.calls.exists()


def test_first_start_requires_explicit_base_before_check(entry, capsys):
    c = entry
    admission = admitted(c)
    assert cli.main(validate_args(c, base=False), codex_review_admission=admission) == 2
    assert "--review-base" in capsys.readouterr().err
    assert not list(protected(c).glob("validation-started-*")) and not c.calls.exists()


def test_changed_base_or_check_does_not_replace_frozen_input(entry, capsys):
    c = entry
    admission = admitted(c)
    assert cli.main(validate_args(c), codex_review_admission=admission) == 0
    capsys.readouterr()
    before = AutomaticCandidateReviewService(c.project).configuration(c.execution_id)
    changed_base = validate_args(c)
    changed_base[changed_base.index("--review-base") + 1] = c.head
    assert cli.main(changed_base, codex_review_admission=admission) == 2
    assert "automatic_review_already_started" in capsys.readouterr().err
    changed_check = validate_args(c)
    changed_check[-1] = "raise AssertionError('must not execute changed check')"
    assert cli.main(changed_check, codex_review_admission=admission) == 2
    assert "correction_check_changed" in capsys.readouterr().err
    assert AutomaticCandidateReviewService(c.project).configuration(c.execution_id) == before
    assert c.calls.read_text() == "review\n"
