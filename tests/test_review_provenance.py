import json
from pathlib import Path

from goodteam_os.review_provenance import (
    build_reviewer_provenance,
    latest_reviewer_status_for_approval,
    reviewer_phase_for_approval,
)
from goodteam_os.reviewer_profiles import get_reviewer_profile
from goodteam_os.state import ApprovalDecision, create_initial_state
from goodteam_os.web import build_approval_reviews


def _provider_status(**overrides):
    status = {
        "job_id": "job-codex",
        "run_id": "run-1",
        "role": "code_review",
        "adapter": "codex",
        "status": "completed",
        "summary": "Codex reviewer completed.",
        "updated_at": "2026-07-05T10:00:00+00:00",
        "result_path": "/tmp/job-codex/result.md",
        "stdout_path": "/tmp/job-codex/stdout.txt",
        "stderr_path": "/tmp/job-codex/stderr.txt",
        "changed_files": ["src/app.py", "tests/test_app.py"],
        "invocation_evidence": {
            "kind": "provider",
            "adapter": "codex",
            "provider": "openai",
            "external_invocation": True,
            "command": "/usr/local/bin/codex",
            "command_available": True,
            "model": "gpt-5.4",
            "reason": "Codex CLI invocation attempted",
        },
    }
    status.update(overrides)
    return status


def test_provider_backed_completed_status_has_provider_badge_and_focus_areas():
    provenance = build_reviewer_provenance(
        _provider_status(),
        profile=get_reviewer_profile("correctness-security"),
    )

    assert provenance["badge"] == {
        "label": "codex",
        "kind": "provider",
        "provider": "openai",
        "adapter": "codex",
    }
    assert provenance["reviewer"]["profile_id"] == "correctness-security"
    assert provenance["reviewer"]["label"] == "Correctness and security"
    assert "Security regressions" in provenance["reviewer"]["focus_areas"]
    assert provenance["invocation"] == {
        "external_invocation": True,
        "command_available": True,
        "reason": "Codex CLI invocation attempted",
        "status": "completed",
    }
    assert provenance["evidence_handles"] == {
        "result_path": "/tmp/job-codex/result.md",
        "stdout_path": "/tmp/job-codex/stdout.txt",
        "stderr_path": "/tmp/job-codex/stderr.txt",
        "changed_files": ["src/app.py", "tests/test_app.py"],
    }
    assert provenance["advisory_only"] is True
    assert provenance["operator_message"] == "Human operator decides; this reviewer is advisory only."


def test_builtin_fake_noop_and_human_reviewers_are_marked_non_provider():
    fake = build_reviewer_provenance(
        {
            "adapter": "fake",
            "status": "completed",
            "summary": "Fake reviewer completed.",
            "invocation_evidence": {
                "kind": "builtin",
                "adapter": "fake",
                "provider": "none",
                "external_invocation": False,
                "command_available": None,
                "reason": "fake adapter completed without external invocation",
            },
        }
    )
    noop = build_reviewer_provenance(
        {
            "adapter": "noop",
            "status": "completed",
            "invocation_evidence": {"kind": "builtin", "adapter": "noop", "provider": "none"},
        }
    )
    human = build_reviewer_provenance(
        {
            "adapter": "human",
            "status": "needs_review",
            "invocation_evidence": {"kind": "manual", "adapter": "human", "provider": "none"},
        }
    )

    assert fake["badge"]["kind"] == "builtin"
    assert fake["badge"]["label"] == "fake"
    assert fake["invocation"]["external_invocation"] is False
    assert noop["badge"]["kind"] == "builtin"
    assert noop["badge"]["label"] == "noop"
    assert human["badge"]["kind"] == "manual"
    assert human["badge"]["label"] == "human"
    assert human["invocation"]["external_invocation"] is False


def test_missing_invocation_evidence_is_explicitly_non_authoritative():
    provenance = build_reviewer_provenance({"adapter": "codex", "status": "completed"})

    assert provenance["badge"] == {
        "label": "missing evidence",
        "kind": "missing",
        "provider": "unknown",
        "adapter": "codex",
    }
    assert provenance["invocation"]["status"] == "missing"
    assert provenance["advisory_only"] is True


def test_unavailable_adapter_is_fallback_unless_status_failed():
    fallback = build_reviewer_provenance(
        _provider_status(
            status="completed",
            invocation_evidence={
                "kind": "unavailable",
                "adapter": "codex",
                "provider": "openai",
                "external_invocation": False,
                "command_available": False,
                "reason": "codex command is not available",
            },
        )
    )
    failed = build_reviewer_provenance(
        _provider_status(
            status="failed",
            summary="Codex CLI exited with code 1.",
            invocation_evidence={
                "kind": "provider",
                "adapter": "codex",
                "provider": "openai",
                "external_invocation": True,
                "command_available": True,
                "reason": "Codex CLI invocation attempted",
            },
        )
    )

    assert fallback["badge"]["kind"] == "fallback"
    assert fallback["badge"]["label"] == "fallback"
    assert fallback["invocation"]["command_available"] is False
    assert failed["badge"]["kind"] == "failed"
    assert failed["badge"]["label"] == "failed invocation"
    assert failed["invocation"]["reason"] == "Codex CLI exited with code 1."
    assert failed["evidence_handles"]["stderr_path"] == "/tmp/job-codex/stderr.txt"


def test_reviewer_phase_maps_existing_approval_gates_without_schema_changes():
    assert reviewer_phase_for_approval("staging") == "implementation"
    assert reviewer_phase_for_approval("release") == "release"
    assert reviewer_phase_for_approval("fix_plan") == "implementation"
    assert reviewer_phase_for_approval("unknown") is None


def test_latest_status_prefers_explicit_gate_then_known_review_role():
    explicit_gate = _provider_status(job_id="job-gate", role="qa", approval="release")
    mapped_role = _provider_status(job_id="job-role", role="code_review", updated_at="2026-07-05T11:00:00+00:00")
    unrelated = _provider_status(job_id="job-impl", role="implementation", updated_at="2026-07-05T12:00:00+00:00")

    assert latest_reviewer_status_for_approval([unrelated, mapped_role, explicit_gate], "release")["job_id"] == "job-gate"
    assert latest_reviewer_status_for_approval([unrelated, mapped_role], "staging")["job_id"] == "job-role"
    assert latest_reviewer_status_for_approval([mapped_role], "release") is None
    assert latest_reviewer_status_for_approval([unrelated], "staging") is None


def test_workbench_reviews_include_reviewer_provenance_from_worker_status(tmp_path: Path):
    workspace = tmp_path / "workspace"
    project_root = tmp_path / "target"
    (workspace / "projects").mkdir(parents=True)
    project_root.mkdir()
    (workspace / "projects" / "demo.local.yaml").write_text(
        f"""
name: Demo
key: DEMO
repos:
  app: {project_root}
reviewer_profiles:
  implementation:
    - correctness-security
""",
        encoding="utf-8",
    )
    job_root = project_root / ".goodteam" / "workers" / "jobs" / "job-codex"
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps({"id": "job-codex", "run_id": "run-1", "role": "code_review", "adapter": "codex"}),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(json.dumps(_provider_status()), encoding="utf-8")
    state = create_initial_state(run_id="run-1", project_key="DEMO", idea="ship it")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"

    reviews = build_approval_reviews(state, workspace=workspace)
    staging = next(review for review in reviews if review["approval"] == "staging")

    assert staging["reviewer_provenance"]["badge"]["kind"] == "provider"
    assert staging["reviewer_provenance"]["reviewer"]["profile_id"] == "correctness-security"
    assert staging["reviewer_provenance"]["advisory_only"] is True
