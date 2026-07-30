from __future__ import annotations

from copy import deepcopy

import pytest

from goodteam_os.onboarding_profile import (
    ProjectProfileError,
    canonicalize_project_profile,
    compute_project_profile_id,
    render_project_profile_projections,
)


_SNAPSHOT_ID = "snap_" + "a" * 64
_FOREIGN_SNAPSHOT_ID = "snap_" + "b" * 64
_JOB_ID = "onboarding_" + "1" * 32


def _snapshot(*, complete: bool = True) -> dict[str, object]:
    return {
        "schema": "repository_snapshot.v1",
        "snapshot_id": _SNAPSHOT_ID,
        "project_key": "DEMO",
        "content_scope": {"complete": complete},
    }


def _observation_envelopes(
    *, snapshot_id: str = _SNAPSHOT_ID,
) -> list[dict[str, object]]:
    observations = []
    for index in range(8):
        observations.append(
            {
                "observation_id": f"tool_example:{index}",
                "kind": "file_excerpt",
                "path": f"src/example_{index}.py",
                "start_line": 1,
                "end_line": 2,
                "content_hash": "sha256:" + f"{index:064x}",
            }
        )
    return [
        {
            "schema": "repository_observation.v1",
            "tool_call_id": "tool_example",
            "snapshot_id": snapshot_id,
            "status": "ok",
            "observations": observations,
        }
    ]


def _coverage(*, status: str = "sufficient") -> dict[str, object]:
    return {
        "status": status,
        "dimensions": {
            "project_purpose": {"status": "covered", "claim_refs": [0]},
            "repository_shape": {"status": "covered", "claim_refs": [1]},
            "main_entrypoint": {"status": "covered", "claim_refs": [3]},
            "core_module_map": {"status": "covered", "claim_refs": [4]},
            "development_commands": {"status": "covered", "claim_refs": [4]},
            "test_commands": {"status": "covered", "claim_refs": [5]},
            "instruction_constraint_review": {
                "status": "covered",
                "claim_refs": [6],
            },
            "high_risk_assessment": {"status": "covered", "claim_refs": [7]},
            "explicit_unknowns": {"status": "documented", "claim_refs": []},
        },
        "limitations": [] if status == "sufficient" else ["Background workers were not explored."],
        "recommended_followups": ["Inspect background worker paths before changes."],
    }


def _draft(*, coverage_status: str = "sufficient") -> dict[str, object]:
    claims = [
        ("purpose", "The repository provides order processing.", 0),
        ("stage", "The service is in active maintenance.", 1),
        ("repository_shape", "Source code is organized under src/.", 1),
        ("entrypoint", "src/example_3.py is a main entrypoint.", 3),
        ("module", "The core module owns order processing.", 2),
        ("development_command", "Use the documented development command.", 4),
        ("test_command", "Use the documented test command.", 5),
        ("constraint", "Review repository instructions before modifying code.", 6),
        ("risk", "Order processing is a high-risk area.", 7),
    ]
    return {
        "schema": "project_profile.v1",
        "profile_id": compute_project_profile_id(_SNAPSHOT_ID, _JOB_ID),
        "project_key": "DEMO",
        "snapshot_id": _SNAPSHOT_ID,
        "previous_profile_id": None,
        "generated_by": {
            "job_id": _JOB_ID,
            "adapter": "scripted",
            "provider": "test",
            "model": "scripted-v1",
            "external_invocation": False,
        },
        "project": {"purpose_claim_id": 0, "current_stage_claim_id": 1},
        "architecture": {
            "overview_claim_ids": [1],
            "modules": [
                {
                    "name": "core",
                    "path": "src/example_2.py",
                    "purpose_claim_id": 4,
                    "ownership_claim_ids": [],
                    "entrypoint_claim_ids": [3],
                    "test_claim_ids": [5],
                }
            ],
            "entrypoints": [3],
            "data_flows": [],
            "external_dependencies": [],
        },
        "operations": {
            "development": [4],
            "test": [5],
            "lint": [],
            "build": [],
            "deploy": [],
        },
        "constraints": {
            "coding_rules": [6],
            "protected_areas": [],
            "prohibited_operations": [],
        },
        "risk": {
            "high_risk_modules": [7],
            "legacy_areas": [],
            "weak_test_coverage": [],
        },
        "knowledge": {"unknowns": ["Background worker behavior is unexplored."]},
        "coverage": _coverage(status=coverage_status),
        "claims": [
            {
                "kind": kind,
                "text": text,
                "certainty": "confirmed",
                "observation_ids": [f"tool_example:{observation_index}"],
            }
            for kind, text, observation_index in claims
        ],
        "created_at": "2026-07-30T01:02:03+00:00",
    }


def _canonical(
    draft: dict[str, object] | None = None,
    *,
    snapshot: dict[str, object] | None = None,
    observations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return canonicalize_project_profile(
        _draft() if draft is None else draft,
        snapshot=_snapshot() if snapshot is None else snapshot,
        observations=_observation_envelopes() if observations is None else observations,
    )


def test_canonicalizes_valid_sufficient_profile_with_system_derived_ids() -> None:
    profile = _canonical()

    assert profile["coverage"]["status"] == "sufficient"
    assert profile["profile_id"] == compute_project_profile_id(_SNAPSHOT_ID, _JOB_ID)
    assert all(str(item["claim_id"]).startswith("claim_") for item in profile["claims"])
    assert all(str(item["evidence_id"]).startswith("evidence_") for item in profile["evidence"])
    assert all(item["snapshot_id"] == _SNAPSHOT_ID for item in profile["evidence"])
    assert "observation_ids" not in profile["claims"][0]
    assert profile["knowledge"]["confirmed_claim_ids"]


def test_canonicalizes_valid_partial_profile_with_named_limitations() -> None:
    profile = _canonical(_draft(coverage_status="partial"))

    assert profile["coverage"] == {
        **profile["coverage"],
        "status": "partial",
        "limitations": ["Background workers were not explored."],
    }


@pytest.mark.parametrize("foreign", [False, True])
def test_rejects_unknown_or_foreign_observation_citations(foreign: bool) -> None:
    draft = _draft()
    observations = _observation_envelopes()
    if foreign:
        observations[0]["snapshot_id"] = _FOREIGN_SNAPSHOT_ID
    else:
        draft["claims"][0]["observation_ids"] = ["tool_example:missing"]

    with pytest.raises(ProjectProfileError) as captured:
        _canonical(draft, observations=observations)

    assert captured.value.code == "project_profile_invalid"


def test_inferred_claim_requires_a_concise_inference_rationale() -> None:
    draft = _draft()
    draft["claims"][0]["certainty"] = "inferred"

    with pytest.raises(ProjectProfileError) as captured:
        _canonical(draft)

    assert captured.value.code == "project_profile_invalid"
    assert captured.value.details["field"] == "claims[0]"


def test_model_draft_cannot_emit_user_confirmed_claims() -> None:
    draft = _draft()
    draft["claims"][0].update(
        {
            "certainty": "user_confirmed",
            "user_confirmation": {
                "actor": "operator@example.test",
                "confirmed_at": "2026-07-30T01:02:03Z",
            },
        }
    )

    with pytest.raises(ProjectProfileError) as captured:
        _canonical(draft)

    assert captured.value.code == "project_profile_invalid"
    assert captured.value.details["reason"] == "model_user_confirmation"


def test_rejects_profile_snapshot_mismatch() -> None:
    draft = _draft()
    draft["snapshot_id"] = _FOREIGN_SNAPSHOT_ID

    with pytest.raises(ProjectProfileError) as captured:
        _canonical(draft)

    assert captured.value.code == "project_profile_invalid"
    assert captured.value.details["reason"] == "snapshot_mismatch"


def test_module_path_must_be_anchored_by_one_of_its_claim_evidence_paths() -> None:
    draft = _draft()
    draft["architecture"]["modules"][0]["path"] = "src/fabricated.py"  # type: ignore[index]

    with pytest.raises(ProjectProfileError) as captured:
        _canonical(draft)

    assert captured.value.code == "project_profile_invalid"
    assert captured.value.details == {
        "field": "architecture.modules[0].path",
        "reason": "path_not_evidenced",
    }


def test_incomplete_snapshot_scope_cannot_publish_sufficient_profile() -> None:
    with pytest.raises(ProjectProfileError) as captured:
        _canonical(snapshot=_snapshot(complete=False))

    assert captured.value.code == "project_profile_insufficient"
    assert captured.value.details["reason"] == "snapshot_scope_incomplete"


def test_projections_are_deterministic_and_render_only_canonical_profile() -> None:
    profile = _canonical()
    first = render_project_profile_projections(profile)
    second = render_project_profile_projections(deepcopy(profile))

    assert first == second
    assert set(first) == {
        "project-overview.md",
        "architecture.md",
        "module-map.md",
        "development-guide.md",
        "risk-register.md",
    }
    assert _SNAPSHOT_ID in first["project-overview.md"]
    assert profile["project"]["purpose_claim_id"] in first["project-overview.md"]
