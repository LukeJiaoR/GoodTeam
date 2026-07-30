from __future__ import annotations

import json
from pathlib import Path

import pytest

from goodteam_os.onboarding_backend import (
    ONBOARDING_TOOL_NAMES,
    ModelProtocolError,
    OnboardingAdapterIdentity,
    ScriptedOnboardingAdapter,
    build_model_turn_request,
    build_sanitized_model_context,
    collect_observation_ids,
    make_tool_result,
    normalize_invocation_evidence,
    normalize_model_usage,
    onboarding_tool_schemas,
    parse_model_turn_response,
    validate_adapter_identity,
    validate_backend_preflight,
    validate_profile_draft,
    validate_tool_call,
    validate_tool_result,
)
from goodteam_os.repository_snapshot import RepositorySnapshot


def _snapshot(root: Path, *, snapshot_id: str = "snap_test") -> RepositorySnapshot:
    return RepositorySnapshot(
        schema="repository_snapshot.v1",
        snapshot_id=snapshot_id,
        project_key="DEMO",
        canonical_root=str(root.resolve()),
        root_identity={"device": 1, "inode": 2},
        source_control={"kind": "none"},
        content_scope={"complete": True, "eligible_files": 1},
        path_manifest={
            "complete": True,
            "entries": [],
            "excluded": [],
            "omitted": [],
            "omitted_paths": [],
        },
        captured_at="2026-07-30T00:00:00Z",
    )


def _inventory(snapshot_id: str, *, unsafe_root: str | None = None) -> dict[str, object]:
    return {
        "schema": "repository_inventory.v1",
        "snapshot_id": snapshot_id,
        "inventory_digest": "sha256:test",
        "technology_stack": ["Python"],
        "directory_structure": ["src"],
        "entry_points": ["src/main.py"],
        "commands": [],
        "key_documents": ["README.md"],
        "constraints": [],
        "risks": [unsafe_root] if unsafe_root else [],
        "scan": {"read_only": True},
        # This is deliberately not allowlisted by model context construction.
        "canonical_root": unsafe_root,
    }


def _context(root: Path) -> dict[str, object]:
    snapshot = _snapshot(root)
    return build_sanitized_model_context(
        snapshot=snapshot,
        inventory=_inventory(snapshot.snapshot_id, unsafe_root=str(root.resolve())),
        budget={"max_model_turns": 24},
    )


def _valid_provider_envelope(
    *,
    snapshot_id: str = "snap_test",
    tool: str = "list_tree",
) -> dict[str, object]:
    return {
        "schema": "repository_observation.v1",
        "tool_call_id": "tool_provider_1",
        "tool": tool,
        "snapshot_id": snapshot_id,
        "model_turn": 1,
        "request": {},
        "status": "ok",
        "truncated": False,
        "observations": [
            {
                "observation_id": "tool_provider_1:0",
                "kind": "tree_entry",
                "path": "src/main.py",
            }
        ],
        "response_metadata": {"observation_count": 1},
    }


def _valid_profile(snapshot_id: str = "snap_test") -> dict[str, object]:
    return {
        "schema": "project_profile.v1",
        "snapshot_id": snapshot_id,
        "claims": [
            {
                "kind": "purpose",
                "text": "The repository contains a Python service.",
                "certainty": "confirmed",
                "observation_ids": ["tool_provider_1:0"],
            }
        ],
        "knowledge": {"unknowns": ["Business purpose needs confirmation."]},
    }


def test_sanitized_turn_request_never_exposes_target_root_to_adapter(tmp_path: Path) -> None:
    root = tmp_path / "target-repository"
    root.mkdir()
    context = _context(root)
    request = build_model_turn_request(turn=1, context=context)
    adapter = ScriptedOnboardingAdapter(
        [{"tool_calls": [{"id": "call_1", "name": "list_tree", "arguments": {}}]}]
    )

    response = adapter.next_turn(request)

    assert response.tool_calls[0].name == "list_tree"
    adapter_input = json.dumps(adapter.received_requests[0], sort_keys=True)
    assert str(root.resolve()) not in adapter_input
    assert "canonical_root" not in adapter_input
    assert "root_identity" not in adapter_input
    assert context["snapshot"].get("snapshot_id") == "snap_test"


def test_sanitized_context_redacts_caller_supplied_workspace_text(tmp_path: Path) -> None:
    root = tmp_path / "target-repository"
    workspace = tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    snapshot = _snapshot(root)
    inventory = _inventory(snapshot.snapshot_id)
    inventory["constraints"] = [f"Never expose {workspace.resolve()} to a model."]

    context = build_sanitized_model_context(
        snapshot=snapshot,
        inventory=inventory,
        forbidden_texts=(str(workspace.resolve()),),
    )

    payload = json.dumps(context, sort_keys=True)
    assert str(root.resolve()) not in payload
    assert str(workspace.resolve()) not in payload
    assert "<target-root-redacted>" in payload


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "call_1", "name": "run_command", "arguments": {"argv": ["pwd"]}},
        {"id": "call_1", "name": "list_tree", "arguments": {"path": "", "shell": "pwd"}},
        {"id": "call_1", "name": "read_file_excerpt", "arguments": {"path": "/etc/passwd", "start_line": 1, "line_count": 1}},
    ],
)
def test_unknown_or_free_form_tools_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ModelProtocolError) as captured:
        validate_tool_call(payload)

    assert captured.value.code == "model_tool_call_invalid"


def test_malformed_provider_tool_results_are_rejected() -> None:
    call = validate_tool_call(
        {"id": "call_1", "name": "list_tree", "arguments": {}}
    )
    malformed = _valid_provider_envelope()
    malformed.pop("observations")

    with pytest.raises(ModelProtocolError) as captured:
        make_tool_result(call, malformed, snapshot_id="snap_test")

    assert captured.value.code == "model_tool_result_invalid"

    invalid_status = _valid_provider_envelope()
    invalid_status["status"] = "denied"
    with pytest.raises(ModelProtocolError) as captured:
        validate_tool_result(
            {
                "schema": "onboarding_tool_result.v1",
                "call_id": "call_1",
                "name": "list_tree",
                "result": invalid_status,
            },
            snapshot_id="snap_test",
        )

    assert captured.value.code == "model_tool_result_invalid"


def test_tool_result_round_trip_is_snapshot_bound_and_model_safe() -> None:
    call = validate_tool_call(
        {"id": "call_1", "name": "list_tree", "arguments": {}}
    )
    result = make_tool_result(
        call,
        _valid_provider_envelope(),
        snapshot_id="snap_test",
    )

    parsed = validate_tool_result(result.as_dict(), snapshot_id="snap_test")

    assert parsed.call_id == "call_1"
    assert parsed.result["snapshot_id"] == "snap_test"
    assert parsed.result["observations"][0]["observation_id"] == "tool_provider_1:0"  # type: ignore[index]
    assert collect_observation_ids([parsed], snapshot_id="snap_test") == {
        "tool_provider_1:0"
    }


def test_tool_result_redacts_target_root_before_reaching_the_model(tmp_path: Path) -> None:
    root = tmp_path / "target-repository"
    root.mkdir()
    call = validate_tool_call(
        {"id": "call_1", "name": "list_tree", "arguments": {}}
    )
    envelope = _valid_provider_envelope()
    envelope["response_metadata"] = {
        "message": f"Repository root is {root.resolve()}.",
    }

    result = make_tool_result(
        call,
        envelope,
        snapshot_id="snap_test",
        forbidden_texts=(str(root.resolve()),),
    )

    payload = json.dumps(result.as_dict(), sort_keys=True)
    assert str(root.resolve()) not in payload
    assert "<target-root-redacted>" in payload


def test_scripted_adapter_cannot_claim_real_model_invocation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    request = build_model_turn_request(turn=1, context=_context(root))
    adapter = ScriptedOnboardingAdapter(
        [
            {
                "tool_calls": [
                    {"id": "call_1", "name": "list_tree", "arguments": {}}
                ],
                "invocation": {"external_invocation": True},
            }
        ]
    )

    assert adapter.preflight().as_dict()["external_invocation"] is False
    assert adapter.preflight().production_ready is False
    with pytest.raises(ModelProtocolError) as captured:
        adapter.next_turn(request)

    assert captured.value.code == "model_invocation_unverified"
    with pytest.raises(ModelProtocolError) as captured:
        OnboardingAdapterIdentity(
            adapter="fake",
            provider="test",
            model="deterministic",
            external_invocation=True,
        )
    assert captured.value.code == "model_invocation_unverified"


def test_preflight_is_identity_bound_and_cannot_report_fake_as_production_ready() -> None:
    adapter = ScriptedOnboardingAdapter([])
    preflight = validate_backend_preflight(
        adapter.preflight().as_dict(),
        identity=adapter.identity,
    )

    assert preflight.identity == adapter.identity
    assert preflight.production_ready is False
    forged = preflight.as_dict()
    forged["production_ready"] = True
    with pytest.raises(ModelProtocolError) as captured:
        validate_backend_preflight(forged, identity=adapter.identity)
    assert captured.value.code == "onboarding_backend_preflight_invalid"

    with pytest.raises(ModelProtocolError) as captured:
        validate_adapter_identity(
            {
                "adapter": "scripted",
                "provider": "test",
                "model": "deterministic",
                "external_invocation": True,
                "test_only": True,
            }
        )
    assert captured.value.code == "model_invocation_unverified"


def test_invocation_evidence_round_trips_with_an_empty_reason() -> None:
    adapter = ScriptedOnboardingAdapter([])
    response = parse_model_turn_response(
        {
            "tool_calls": [
                {"id": "call_1", "name": "list_tree", "arguments": {}}
            ]
        },
        identity=adapter.identity,
        snapshot_id="snap_test",
    )

    payload = response.invocation.as_dict()
    payload.pop("schema")
    assert normalize_invocation_evidence(payload, identity=adapter.identity) == response.invocation


def test_response_profile_draft_can_cite_only_known_observation_ids() -> None:
    identity = OnboardingAdapterIdentity(
        adapter="hosted",
        provider="example",
        model="model-1",
        external_invocation=True,
    )
    response = parse_model_turn_response(
        {"profile_draft": _valid_profile(), "usage": {"prompt_tokens": 12, "completion_tokens": 3, "authoritative": True}},
        identity=identity,
        snapshot_id="snap_test",
        allowed_observation_ids={"tool_provider_1:0"},
    )

    assert response.profile_draft is not None
    assert response.usage.as_dict() == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
        "cost_usd": None,
        "authoritative": True,
    }
    assert response.invocation.as_dict()["external_invocation"] is True

    forged = _valid_profile()
    forged["claims"][0]["evidence_ids"] = ["evidence_forged"]  # type: ignore[index]
    with pytest.raises(ModelProtocolError) as captured:
        validate_profile_draft(
            forged,
            snapshot_id="snap_test",
            allowed_observation_ids={"tool_provider_1:0"},
        )
    assert captured.value.code == "project_profile_invalid"


def test_usage_normalization_rejects_inconsistent_or_nonfinite_values() -> None:
    assert normalize_model_usage({"input_tokens": 2, "output_tokens": 3}).total_tokens == 5

    with pytest.raises(ModelProtocolError) as captured:
        normalize_model_usage({"input_tokens": 2, "prompt_tokens": 3})
    assert captured.value.code == "model_usage_invalid"

    with pytest.raises(ModelProtocolError) as captured:
        normalize_model_usage({"cost_usd": float("nan")})
    assert captured.value.code == "model_usage_invalid"


def test_tool_schemas_are_exactly_track_ones_fixed_surface() -> None:
    schemas = onboarding_tool_schemas()

    assert tuple(item.name for item in schemas) == ONBOARDING_TOOL_NAMES
    assert len(schemas) == 7
    assert all(item.input_schema["additionalProperties"] is False for item in schemas)
