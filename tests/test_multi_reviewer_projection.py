import json
from pathlib import Path

import goodteam_os.web as web
from goodteam_os.multi_reviewer_projection import build_multi_reviewer_projection
from goodteam_os.state import ApprovalDecision, create_initial_state


def _target(*profile_ids: str, policy: dict[str, object] | None = None) -> dict[str, object]:
    target: dict[str, object] = {
        "reviewer_profiles": {
            "implementation": list(profile_ids),
        }
    }
    if policy is not None:
        target["review_evidence_policy"] = policy
    return target


def _provider_status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
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
            "command_available": True,
            "reason": "Codex CLI invocation attempted",
        },
    }
    status.update(overrides)
    return status


def _workspace_with_project(tmp_path: Path, *, profiles: list[str]) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    project_root = tmp_path / "target"
    (workspace / "projects").mkdir(parents=True)
    project_root.mkdir()
    profile_lines = "\n".join(f"    - {profile_id}" for profile_id in profiles)
    (workspace / "projects" / "demo.local.yaml").write_text(
        f"""
name: Demo
key: DEMO
repos:
  app: {project_root}
reviewer_profiles:
  implementation:
{profile_lines}
review_evidence_policy:
  mode: strict
  blockers:
    - failed_invocation
""",
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
                "profile_id": status.get("profile_id", ""),
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


def test_multiple_profiles_project_in_config_order_with_individual_policy_and_aggregate_counts():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=_target(
            "architecture-maintainability",
            "correctness-security",
            policy={"mode": "strict", "blockers": ["failed_invocation"]},
        ),
        reviewer_statuses=[
            _provider_status(profile_id="correctness-security", job_id="job-correctness"),
            _provider_status(
                profile_id="architecture-maintainability",
                job_id="job-architecture",
                adapter="claude",
                status="failed",
                summary="Claude reviewer failed.",
            ),
        ],
        review_evidence_policy_config={"mode": "strict", "blockers": ["failed_invocation"]},
    )

    assert projection["approval"] == "staging"
    assert projection["phase"] == "implementation"
    assert projection["advisory_only"] is True
    assert projection["can_human_override"] is True
    assert [reviewer["profile_id"] for reviewer in projection["reviewers"]] == [
        "architecture-maintainability",
        "correctness-security",
    ]
    assert [reviewer["order"] for reviewer in projection["reviewers"]] == [0, 1]
    assert projection["reviewers"][0]["status"]["job_id"] == "job-architecture"
    assert projection["reviewers"][0]["policy"]["state"] == "failed_invocation"
    assert projection["reviewers"][0]["policy"]["severity"] == "blocker"
    assert projection["reviewers"][1]["policy"]["state"] == "ok"
    assert projection["aggregate_policy"] == {
        "severity": "blocker",
        "state_counts": {"failed_invocation": 1, "ok": 1},
        "advisory_only": True,
        "can_human_override": True,
    }


def test_single_configured_profile_accepts_legacy_status_for_backward_compatibility():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=_target("correctness-security"),
        reviewer_statuses=[_provider_status(job_id="job-legacy")],
    )

    assert len(projection["reviewers"]) == 1
    reviewer = projection["reviewers"][0]
    assert reviewer["profile_id"] == "correctness-security"
    assert reviewer["match_kind"] == "legacy_single_profile"
    assert reviewer["status"]["job_id"] == "job-legacy"
    assert reviewer["policy"]["state"] == "ok"


def test_omitted_reviewer_profiles_produces_compatible_empty_projection_without_statuses():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target={},
        reviewer_statuses=[],
    )

    assert projection["phase"] == "implementation"
    assert projection["reviewers"] == []
    assert projection["aggregate_policy"]["severity"] == "ok"
    assert projection["aggregate_policy"]["state_counts"] == {}


def test_missing_configured_reviewer_evidence_remains_visible_per_reviewer():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=_target("correctness-security", "architecture-maintainability"),
        reviewer_statuses=[_provider_status(profile_id="correctness-security")],
    )

    missing = projection["reviewers"][1]
    assert missing["profile_id"] == "architecture-maintainability"
    assert missing["status"] is None
    assert missing["provenance"]["badge"]["kind"] == "missing"
    assert missing["policy"]["state"] == "missing_evidence"
    assert projection["aggregate_policy"]["state_counts"] == {"ok": 1, "missing_evidence": 1}


def test_failed_fallback_and_non_provider_states_contribute_to_deterministic_aggregate_severity():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=_target("correctness-security", "architecture-maintainability", "product-operator"),
        reviewer_statuses=[
            _provider_status(profile_id="correctness-security"),
            _provider_status(
                profile_id="architecture-maintainability",
                invocation_evidence={
                    "kind": "unavailable",
                    "adapter": "claude",
                    "provider": "anthropic",
                    "external_invocation": False,
                    "command_available": False,
                    "reason": "claude command missing",
                },
            ),
            _provider_status(
                profile_id="product-operator",
                adapter="fake",
                invocation_evidence={
                    "kind": "builtin",
                    "adapter": "fake",
                    "provider": "none",
                    "external_invocation": False,
                    "command_available": None,
                    "reason": "fake adapter completed without external invocation",
                },
            ),
        ],
    )

    assert [reviewer["policy"]["state"] for reviewer in projection["reviewers"]] == [
        "ok",
        "fallback_evidence",
        "non_provider_evidence",
    ]
    assert projection["aggregate_policy"]["severity"] == "warning"
    assert projection["aggregate_policy"]["state_counts"] == {
        "ok": 1,
        "fallback_evidence": 1,
        "non_provider_evidence": 1,
    }


def test_ambiguous_legacy_status_is_not_silently_assigned_to_multiple_profiles():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target=_target("correctness-security", "architecture-maintainability"),
        reviewer_statuses=[_provider_status(job_id="job-ambiguous")],
    )

    configured_reviewers = projection["reviewers"][:2]
    assert [reviewer["status"] for reviewer in configured_reviewers] == [None, None]
    assert [reviewer["policy"]["state"] for reviewer in configured_reviewers] == [
        "missing_evidence",
        "missing_evidence",
    ]
    assert projection["reviewers"][2]["match_kind"] == "unmatched_legacy"
    assert projection["reviewers"][2]["status"]["job_id"] == "job-ambiguous"


def test_unconfigured_legacy_statuses_use_stable_fallback_order():
    projection = build_multi_reviewer_projection(
        approval="staging",
        reviewer_target={},
        reviewer_statuses=[
            _provider_status(job_id="job-z", role="qa", adapter="gemini"),
            _provider_status(job_id="job-a", role="code_review", adapter="codex"),
        ],
    )

    assert [(reviewer["status"]["role"], reviewer["status"]["adapter"], reviewer["status"]["job_id"]) for reviewer in projection["reviewers"]] == [
        ("code_review", "codex", "job-a"),
        ("qa", "gemini", "job-z"),
    ]


def test_workbench_payload_adds_multi_projection_without_replacing_existing_fields_or_side_effects(tmp_path, monkeypatch):
    workspace, project_root = _workspace_with_project(
        tmp_path,
        profiles=["correctness-security", "architecture-maintainability"],
    )
    _write_worker_status(
        project_root,
        _provider_status(
            profile_id="correctness-security",
            job_id="job-codex",
            updated_at="2026-07-05T11:00:00+00:00",
        ),
    )
    _write_worker_status(
        project_root,
        _provider_status(
            profile_id="architecture-maintainability",
            job_id="job-claude",
            adapter="claude",
            status="failed",
            summary="Claude reviewer failed.",
        ),
    )
    state = _staging_state()

    def fail_append_action(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("multi reviewer projection must not write ledger events")

    def fail_dispatch(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("multi reviewer projection must not dispatch reviewer jobs")

    monkeypatch.setattr(web, "append_action", fail_append_action)
    monkeypatch.setattr(web, "dispatch_tracks", fail_dispatch)

    reviews = web.build_approval_reviews(state, workspace=workspace)
    staging = next(review for review in reviews if review["approval"] == "staging")

    assert staging["reviewer_provenance"]["reviewer"]["profile_id"] == "correctness-security"
    assert staging["review_evidence_policy"]["state"] == "ok"
    assert [reviewer["profile_id"] for reviewer in staging["multi_reviewer_projection"]["reviewers"][:2]] == [
        "correctness-security",
        "architecture-maintainability",
    ]
    assert staging["multi_reviewer_projection"]["aggregate_policy"]["severity"] == "blocker"
    assert state.approvals["staging"] == ApprovalDecision.PENDING
    assert state.next_node == "await_staging_approval"
