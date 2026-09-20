import json
from pathlib import Path

import pytest

import goodteam_os.web as web
from goodteam_os.review_policy import (
    evaluate_review_evidence_policy,
    normalize_review_evidence_policy_config,
)
from goodteam_os.state import ApprovalDecision, StateStore, create_initial_state


def _provenance(
    badge_kind: str,
    *,
    invocation_status: str = "completed",
    adapter: str = "codex",
    provider: str = "openai",
    external_invocation: bool = True,
    command_available: bool | None = True,
    reason: str = "reviewer invocation completed",
) -> dict[str, object]:
    return {
        "badge": {
            "kind": badge_kind,
            "label": badge_kind.replace("_", " "),
            "adapter": adapter,
            "provider": provider,
        },
        "invocation": {
            "status": invocation_status,
            "external_invocation": external_invocation,
            "command_available": command_available,
            "reason": reason,
        },
        "advisory_only": True,
        "operator_message": "Human operator decides; this reviewer is advisory only.",
    }


def _provider_status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
        "job_id": "job-codex",
        "run_id": "run-1",
        "role": "code_review",
        "adapter": "codex",
        "status": "failed",
        "summary": "Codex CLI exited with code 1.",
        "updated_at": "2026-07-05T10:00:00+00:00",
        "invocation_evidence": {
            "kind": "provider",
            "adapter": "codex",
            "provider": "openai",
            "external_invocation": True,
            "command_available": True,
            "reason": "Codex CLI invocation attempted",
        },
    }
    status.update(overrides)
    return status


def _workspace_with_project(
    tmp_path: Path,
    *,
    review_evidence_policy: str = "",
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    project_root = tmp_path / "target"
    (workspace / "projects").mkdir(parents=True)
    project_root.mkdir()
    policy_block = review_evidence_policy.strip()
    if policy_block:
        policy_block = f"\n{policy_block}\n"
    (workspace / "projects" / "demo.local.yaml").write_text(
        f"""
name: Demo
key: DEMO
repos:
  app: {project_root}
reviewer_profiles:
  implementation:
    - correctness-security
{policy_block}""",
        encoding="utf-8",
    )
    return workspace, project_root


def _write_worker_status(project_root: Path, status: dict[str, object]) -> None:
    job_root = project_root / ".goodteam" / "workers" / "jobs" / str(status["job_id"])
    job_root.mkdir(parents=True)
    (job_root / "job.json").write_text(
        json.dumps(
            {
                "id": status["job_id"],
                "run_id": status["run_id"],
                "role": status["role"],
                "adapter": status["adapter"],
            }
        ),
        encoding="utf-8",
    )
    (job_root / "status.json").write_text(json.dumps(status), encoding="utf-8")


def _staging_state() -> object:
    state = create_initial_state(run_id="run-1", project_key="DEMO", idea="ship it")
    state.approvals["staging"] = ApprovalDecision.PENDING
    state.next_node = "await_staging_approval"
    return state


def test_provider_backed_completed_evidence_is_ok_and_visible():
    policy = evaluate_review_evidence_policy(
        _provenance("provider"),
        gate="staging",
    )

    assert policy["state"] == "ok"
    assert policy["severity"] == "ok"
    assert policy["gate"] == "staging"
    assert policy["visible_to_operator"] is True
    assert policy["advisory_only"] is True
    assert policy["can_human_override"] is True
    assert "Human operator decides" in policy["rationale"]
    assert policy["evidence"] == {
        "badge_kind": "provider",
        "adapter": "codex",
        "provider": "openai",
        "external_invocation": True,
        "command_available": True,
        "invocation_status": "completed",
    }


@pytest.mark.parametrize(
    ("badge_kind", "expected_state", "adapter", "provider", "external_invocation", "command_available"),
    [
        ("missing", "missing_evidence", "codex", "unknown", False, None),
        ("fallback", "fallback_evidence", "codex", "openai", False, False),
        ("builtin", "non_provider_evidence", "fake", "none", False, None),
        ("manual", "non_provider_evidence", "human", "none", False, None),
    ],
)
def test_default_policy_marks_incomplete_evidence_as_warning(
    badge_kind: str,
    expected_state: str,
    adapter: str,
    provider: str,
    external_invocation: bool,
    command_available: bool | None,
):
    policy = evaluate_review_evidence_policy(
        _provenance(
            badge_kind,
            adapter=adapter,
            provider=provider,
            external_invocation=external_invocation,
            command_available=command_available,
        ),
        gate="staging",
    )

    assert policy["state"] == expected_state
    assert policy["severity"] == "warning"
    assert policy["visible_to_operator"] is True
    assert policy["advisory_only"] is True
    assert policy["can_human_override"] is True
    assert "Human operator decides" in policy["rationale"]
    assert policy["evidence"]["badge_kind"] == badge_kind


def test_failed_provider_invocation_is_visible_warning_by_default():
    policy = evaluate_review_evidence_policy(
        _provenance("failed", invocation_status="failed", reason="Codex CLI exited with code 1."),
        gate="staging",
    )

    assert policy["state"] == "failed_invocation"
    assert policy["severity"] == "warning"
    assert "failed" in policy["rationale"].lower()
    assert "Human operator decides" in policy["rationale"]
    assert policy["advisory_only"] is True
    assert policy["can_human_override"] is True


def test_strict_policy_can_mark_selected_states_as_blockers_with_rationale():
    policy = evaluate_review_evidence_policy(
        _provenance("failed", invocation_status="failed"),
        gate="release",
        config={"mode": "strict", "blockers": ["failed_invocation"]},
    )

    assert policy["state"] == "failed_invocation"
    assert policy["severity"] == "blocker"
    assert "strict review_evidence_policy" in policy["rationale"]
    assert "failed_invocation" in policy["rationale"]
    assert policy["can_human_override"] is True


def test_review_evidence_policy_config_rejects_unknown_mode_and_blockers():
    with pytest.raises(ValueError, match="review_evidence_policy must be a mapping"):
        normalize_review_evidence_policy_config("strict")

    with pytest.raises(ValueError, match="review_evidence_policy.mode"):
        normalize_review_evidence_policy_config({"mode": "enforce"})

    with pytest.raises(ValueError, match="unknown_state"):
        normalize_review_evidence_policy_config({"mode": "strict", "blockers": ["unknown_state"]})


def test_workbench_reviews_include_review_evidence_policy_without_side_effects(tmp_path: Path, monkeypatch):
    workspace, project_root = _workspace_with_project(
        tmp_path,
        review_evidence_policy="""
review_evidence_policy:
  mode: strict
  blockers:
    - failed_invocation
""",
    )
    _write_worker_status(project_root, _provider_status())
    state = _staging_state()

    def fail_append_action(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("review evidence policy projection must not write ledger events")

    def fail_dispatch(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("review evidence policy projection must not dispatch reviewer jobs")

    monkeypatch.setattr(web, "append_action", fail_append_action)
    monkeypatch.setattr(web, "dispatch_tracks", fail_dispatch)

    reviews = web.build_approval_reviews(state, workspace=workspace)
    staging = next(review for review in reviews if review["approval"] == "staging")

    assert staging["reviewer_provenance"]["badge"]["kind"] == "failed"
    assert staging["review_evidence_policy"]["state"] == "failed_invocation"
    assert staging["review_evidence_policy"]["severity"] == "blocker"
    assert "Human operator decides" in staging["review_evidence_policy"]["rationale"]
    assert state.approvals["staging"] == ApprovalDecision.PENDING
    assert state.next_node == "await_staging_approval"


def test_approval_action_still_accepts_human_decision_when_policy_would_block(tmp_path: Path, monkeypatch):
    workspace, project_root = _workspace_with_project(
        tmp_path,
        review_evidence_policy="""
review_evidence_policy:
  mode: strict
  blockers:
    - failed_invocation
""",
    )
    _write_worker_status(project_root, _provider_status())
    state = _staging_state()
    StateStore(workspace).write_project_current(state, "DEMO")

    monkeypatch.setattr(web, "advance_approved_workflow", lambda **kwargs: kwargs["state"])
    monkeypatch.setattr(web, "sync_project_stage_tasks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web, "notify_current_approval_gate", lambda *_args, **_kwargs: None)

    payload = web.decide_approval(
        workspace=workspace,
        project_key="DEMO",
        approval="staging",
        decision="approved",
        note="operator reviewed the failed invocation evidence",
    )

    assert payload["decision"] == "approved"
    assert payload["approvals"]["staging"] == "approved"
