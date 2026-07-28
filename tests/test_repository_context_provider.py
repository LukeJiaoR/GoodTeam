from __future__ import annotations

import json
from math import inf, nan
from pathlib import Path

import pytest

from goodteam_os.onboarding_store import (
    OnboardingArtifactStore,
    OnboardingObservationStore,
    observation_path,
    snapshot_path,
)
from goodteam_os.project_import import ProjectImportError, import_project
from goodteam_os.repository_snapshot import capture_repository_snapshot


def _provider_fixture(tmp_path: Path) -> dict[str, object]:
    from goodteam_os.repository_context_provider import (
        OnboardingBudget,
        RepositoryContextProvider,
    )

    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    imported = import_project(workspace, str(root))
    project_key = str(imported["project"]["project_key"])
    snapshot = capture_repository_snapshot(root, project_key=project_key)
    artifacts = OnboardingArtifactStore(workspace, project_key, root)
    artifacts.publish_snapshot(snapshot)
    store = OnboardingObservationStore(
        workspace,
        project_key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    budget = OnboardingBudget()
    provider = RepositoryContextProvider(
        workspace=workspace,
        project_key=project_key,
        root=root,
        snapshot=snapshot,
        observation_store=store,
        budget=budget,
    )
    return {
        "workspace": workspace,
        "root": root,
        "project_key": project_key,
        "snapshot": snapshot,
        "store": store,
        "budget": budget,
        "provider": provider,
    }


def _last_record_bytes(workspace: Path, project_key: str) -> bytes:
    path = observation_path(workspace, project_key, "job-1")
    return path.read_bytes().splitlines()[-1] + b"\n"


def _last_record_dict(workspace: Path, project_key: str) -> dict[str, object]:
    return json.loads(_last_record_bytes(workspace, project_key))


def test_provider_rejects_unknown_tool(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]
    store = fixture["store"]
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]

    with pytest.raises(ContextProviderError) as captured:
        provider.call("run_command", {"argv": ["cat", "README.md"]}, model_turn=1)

    assert captured.value.code == "context_tool_not_allowed"
    assert store.read_all() == [_last_record_dict(workspace, project_key)]
    assert _last_record_dict(workspace, project_key) == {
        "budget": provider.budget.as_dict(),
        "created_at": _last_record_dict(workspace, project_key)["created_at"],
        "limits": provider.budget.limits.as_dict(),
        "model_turn": 1,
        "observations": [],
        "request": {"redacted": True},
        "response_metadata": {"error": {"code": "context_tool_not_allowed"}},
        "schema": "repository_observation.v1",
        "snapshot_id": fixture["snapshot"].snapshot_id,
        "status": "denied",
        "tool": "run_command",
        "tool_call_id": _last_record_dict(workspace, project_key)["tool_call_id"],
        "truncated": False,
    }
    encoded = _last_record_bytes(workspace, project_key).decode("utf-8")
    assert "run_command" in encoded
    assert "argv" not in encoded
    assert "README.md" not in encoded
    assert '"cat"' not in encoded
    assert "shell" not in encoded


def test_budget_fails_closed_at_hard_limits() -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        OnboardingBudgetLimits,
    )

    assert OnboardingBudgetLimits().as_dict() == {
        "max_cost_usd": 5.0,
        "max_elapsed_seconds": 900.0,
        "max_estimated_input_tokens": 180000,
        "max_estimated_output_tokens": 30000,
        "max_file_cumulative_chars": 48000,
        "max_files_observed": 120,
        "max_model_turns": 24,
        "max_single_result_chars": 16000,
        "max_tool_calls": 80,
        "max_total_observation_chars": 500000,
    }

    budget = OnboardingBudget(
        requested_limits=OnboardingBudgetLimits(
            max_model_turns=1,
            max_tool_calls=1,
            max_files_observed=1,
            max_total_observation_chars=10,
            max_single_result_chars=10,
            max_file_cumulative_chars=10,
            max_elapsed_seconds=1.0,
            max_estimated_input_tokens=10,
            max_estimated_output_tokens=10,
            max_cost_usd=1.0,
        ),
        monotonic_clock=lambda: 100.0,
    )
    budget.begin_model_turn(1)
    budget.begin_tool_call()
    budget.record_observation(path="README.md", chars=10, count_toward_file=True)
    budget.record_model_usage(
        turn_number=1,
        estimated_input_tokens=10,
        estimated_output_tokens=10,
        actual_input_tokens=10,
        actual_output_tokens=10,
        cost_usd=1.0,
    )

    assert budget.result_char_allowance() == 0
    assert budget.close_requested is True
    assert budget.as_dict()["tool_calls_used"] == 1

    for action in (
        lambda: budget.begin_tool_call(),
        lambda: budget.begin_model_turn(2),
        lambda: budget.record_observation(
            path="README.md", chars=1, count_toward_file=True
        ),
        lambda: budget.record_model_usage(
            turn_number=1,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
            actual_input_tokens=1,
            actual_output_tokens=1,
            cost_usd=0.1,
        ),
    ):
        with pytest.raises(ContextProviderError) as captured:
            action()
        assert captured.value.code == "onboarding_budget_exceeded"


def test_budget_counts_unique_files_once() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget()

    budget.record_observation(path="README.md", chars=10, count_toward_file=True)
    budget.record_observation(path="README.md", chars=5, count_toward_file=True)
    budget.record_observation(path="docs/guide.md", chars=1, count_toward_file=True)

    snapshot = budget.as_dict()
    assert snapshot["files_observed"] == 2
    assert snapshot["observed_file_chars"]["README.md"] == 15
    assert snapshot["observed_file_chars"]["docs/guide.md"] == 1


def test_budget_enforces_cumulative_per_file_chars() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(monotonic_clock=lambda: 0.0)
    for _ in range(3):
        budget.record_observation(path="README.md", chars=16000, count_toward_file=True)

    with pytest.raises(ContextProviderError) as captured:
        budget.record_observation(path="README.md", chars=1, count_toward_file=True)

    assert captured.value.code == "onboarding_budget_exceeded"


def test_budget_uses_injected_monotonic_clock() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    times = iter((10.0, 10.0, 820.0, 950.1))
    budget = OnboardingBudget(monotonic_clock=lambda: next(times))

    budget.begin_model_turn(1)
    budget.begin_tool_call()
    assert budget.close_requested is True

    with pytest.raises(ContextProviderError) as captured:
        budget.begin_tool_call()

    assert captured.value.code == "onboarding_budget_exceeded"
    assert budget.as_dict()["elapsed_seconds"] == pytest.approx(940.1)


def test_budget_replaces_estimates_with_authoritative_model_usage() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget()

    budget.record_model_usage(
        turn_number=1,
        estimated_input_tokens=100,
        estimated_output_tokens=50,
    )
    assert budget.as_dict()["estimated_input_tokens_used"] == 100
    assert budget.as_dict()["estimated_output_tokens_used"] == 50
    assert budget.close_requested is False

    budget.record_model_usage(
        turn_number=1,
        estimated_input_tokens=100,
        estimated_output_tokens=50,
        actual_input_tokens=10,
        actual_output_tokens=5,
        cost_usd=0.25,
    )
    assert budget.as_dict()["estimated_input_tokens_used"] == 10
    assert budget.as_dict()["estimated_output_tokens_used"] == 5
    assert budget.as_dict()["cost_usd_used"] == 0.25


@pytest.mark.parametrize(
    "requested_limits",
    [
        {"max_tool_calls": 81},
        {"max_tool_calls": True},
        {"max_tool_calls": 0},
        {"max_tool_calls": -1},
        {"max_elapsed_seconds": 0.0},
        {"max_elapsed_seconds": inf},
        {"max_elapsed_seconds": nan},
        {"max_cost_usd": 5.1},
        {"max_model_turns": "24"},
    ],
)
def test_budget_rejects_limits_above_server_ceiling(requested_limits: dict[str, object]) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    with pytest.raises(ContextProviderError) as captured:
        OnboardingBudget(requested_limits=requested_limits)

    assert captured.value.code == "onboarding_budget_invalid"


def test_budget_requests_close_at_eighty_percent() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget(requested_limits={"max_tool_calls": 5})
    assert budget.close_requested is False

    for _ in range(4):
        budget.begin_tool_call()

    assert budget.close_requested is True
    assert budget.as_dict()["tool_calls_used"] == 4


def test_success_envelope_is_exactly_persisted(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]

    response = provider.call("list_tree", {"path": "", "max_entries": 10}, model_turn=1)

    assert response == _last_record_dict(workspace, project_key)
    assert _last_record_bytes(workspace, project_key) == (
        json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        + b"\n"
    )
    assert response["schema"] == "repository_observation.v1"
    assert response["tool"] == "list_tree"
    assert response["status"] == "ok"
    assert response["response_metadata"]["observation_count"] == len(response["observations"])
    assert response["response_metadata"]["returned_chars"] > 0
    assert response["observations"][0]["observation_id"] == f'{response["tool_call_id"]}:0'


def test_constructor_snapshot_store_artifact_mismatch_cases(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    workspace = fixture["workspace"]
    root = fixture["root"]
    project_key = fixture["project_key"]
    snapshot = fixture["snapshot"]
    store = fixture["store"]

    other_snapshot = capture_repository_snapshot(root, project_key=project_key)
    mutated_snapshot = type(snapshot)(**{**snapshot.as_dict(), "snapshot_id": "snap_other"})

    with pytest.raises(ContextProviderError) as captured:
        RepositoryContextProvider(
            workspace=workspace,
            project_key=project_key,
            root=root,
            snapshot=mutated_snapshot,
            observation_store=store,
            budget=OnboardingBudget(),
        )
    assert captured.value.code == "onboarding_snapshot_mismatch"

    other_store = OnboardingObservationStore(
        workspace,
        project_key,
        root,
        "job-2",
        snapshot.snapshot_id,
    )
    artifact = snapshot_path(workspace, project_key, snapshot.snapshot_id)
    artifact.write_text(
        json.dumps({**snapshot.as_dict(), "canonical_root": str(tmp_path / "other")}),
        encoding="utf-8",
    )
    with pytest.raises((ContextProviderError, ProjectImportError)) as artifact_error:
        RepositoryContextProvider(
            workspace=workspace,
            project_key=project_key,
            root=root,
            snapshot=snapshot,
            observation_store=other_store,
            budget=OnboardingBudget(),
        )
    code = getattr(artifact_error.value, "code", "")
    assert code in {"onboarding_artifact_conflict", "onboarding_snapshot_mismatch"}
    assert other_snapshot.snapshot_id == snapshot.snapshot_id


def test_denied_error_envelope_exact_persistence_and_no_raw_request_content_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]

    def boom(_request: dict[str, object]) -> dict[str, object]:
        raise ContextProviderError(
            "context_handler_failed",
            "handler failed",
            details={"path": "README.md", "limit": "max_single_result_chars"},
        )

    monkeypatch.setitem(provider._tool_handlers, "inspect_recent_changes", boom)

    with pytest.raises(ContextProviderError) as captured:
        provider.call(
            "inspect_recent_changes",
            {"token": "sk-live-secret", "path": "README.md"},
            model_turn=1,
        )

    assert captured.value.code == "context_handler_failed"
    response = _last_record_dict(workspace, project_key)
    assert response["status"] == "error"
    assert response["observations"] == []
    assert response["request"] == {"redacted": True}
    encoded = _last_record_bytes(workspace, project_key).decode("utf-8")
    assert "sk-live-secret" not in encoded
    assert '"path":"README.md"' not in encoded
    assert "alpha" not in encoded


def test_exact_allowlist_no_reflection_assertions(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]

    assert set(provider.tool_names()) == {
        "inspect_git_history",
        "inspect_manifest",
        "inspect_recent_changes",
        "inspect_tests",
        "list_tree",
        "read_file_excerpt",
        "search_text",
    }
    assert provider.raw_argv_dispatch is False
    assert provider.uses_reflection_dispatch is False
