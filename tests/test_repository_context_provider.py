from __future__ import annotations

import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from math import inf, nan
from pathlib import Path
from types import MappingProxyType

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


def _all_records(workspace: Path, project_key: str) -> list[dict[str, object]]:
    path = observation_path(workspace, project_key, "job-1")
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _assert_detached_error(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None


def _assert_no_raw_binding_data(error: BaseException, *forbidden: str) -> None:
    rendered = f"{error!r} {error} {getattr(error, 'details', {})!r}"
    for value in forbidden:
        assert value not in rendered


def _rebuild_provider(fixture: dict[str, object], provider_type, *, budget=None):
    from goodteam_os.repository_context_provider import OnboardingBudget

    return provider_type(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget() if budget is None else budget,
    )


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
    record = _last_record_dict(workspace, project_key)
    assert store.read_all() == [record]
    assert record["budget"]["tool_calls_used"] == 1
    assert record == {
        "budget": record["budget"],
        "created_at": record["created_at"],
        "limits": provider.budget.limits.as_dict(),
        "model_turn": 1,
        "observations": [],
        "request": {"redacted": True},
        "response_metadata": {"error": {"code": "context_tool_not_allowed"}},
        "schema": "repository_observation.v1",
        "snapshot_id": fixture["snapshot"].snapshot_id,
        "status": "denied",
        "tool": "run_command",
        "tool_call_id": record["tool_call_id"],
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
    ):
        with pytest.raises(ContextProviderError) as captured:
            action()
        assert captured.value.code == "onboarding_budget_exceeded"

    budget.record_model_usage(
        turn_number=1,
        actual_input_tokens=1,
        actual_output_tokens=1,
        cost_usd=0.1,
    )
    assert budget.as_dict()["estimated_input_tokens_used"] == 1
    assert budget.as_dict()["estimated_output_tokens_used"] == 1


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


def test_rejected_observation_leaves_all_budget_state_unchanged() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_file_cumulative_chars": 5,
            "max_total_observation_chars": 10,
        },
        monotonic_clock=lambda: 0.0,
    )
    before = budget.as_dict()

    with pytest.raises(ContextProviderError) as captured:
        budget.record_observation(path="new.txt", chars=6, count_toward_file=True)

    assert captured.value.details == {"limit": "max_file_cumulative_chars"}
    assert budget.as_dict() == before


def test_result_reservation_is_atomic_and_released_after_rejection() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_file_cumulative_chars": 5,
            "max_single_result_chars": 10,
            "max_total_observation_chars": 10,
        }
    )
    reservation = budget.reserve_result()

    with pytest.raises(ContextProviderError) as captured:
        budget.commit_result(
            reservation,
            result_chars=6,
            total_chars=6,
            observed_paths=("new.txt",),
            file_chars={"new.txt": 6},
        )

    assert captured.value.details == {"limit": "max_file_cumulative_chars"}
    assert budget.as_dict()["files_observed"] == 0
    assert budget.as_dict()["total_observation_chars_used"] == 0
    assert budget.reserve_result().allowance == 10


def test_result_reservation_prevents_overcommit_until_released() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 5,
            "max_total_observation_chars": 5,
        }
    )
    first = budget.reserve_result()

    with pytest.raises(ContextProviderError) as captured:
        budget.reserve_result()
    assert captured.value.details == {"limit": "max_total_observation_chars"}

    budget.release_result(first)
    assert budget.reserve_result().allowance == 5


def test_result_transaction_rejects_total_above_reserved_allowance() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 5,
            "max_total_observation_chars": 10,
        },
        monotonic_clock=lambda: 0.0,
    )
    reservation = budget.reserve_result()

    with pytest.raises(ContextProviderError) as captured:
        budget.commit_result(
            reservation,
            result_chars=0,
            total_chars=6,
            observed_paths=(),
            file_chars={},
        )

    assert captured.value.details == {"limit": "max_total_observation_chars"}
    assert budget.as_dict()["total_observation_chars_used"] == 0
    assert budget.result_char_allowance() == 5


def test_budget_uses_injected_monotonic_clock() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    now = [10.0]
    budget = OnboardingBudget(monotonic_clock=lambda: now[0])

    budget.begin_model_turn(1)
    now[0] = 820.0
    budget.begin_tool_call()
    assert budget.close_requested is True

    now[0] = 950.1
    with pytest.raises(ContextProviderError) as captured:
        budget.begin_tool_call()

    assert captured.value.code == "onboarding_budget_exceeded"
    assert budget.as_dict()["elapsed_seconds"] == pytest.approx(940.1)


def test_budget_as_dict_refreshes_elapsed_and_close_requested() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    now = [0.0]
    budget = OnboardingBudget(
        requested_limits={"max_elapsed_seconds": 10.0},
        monotonic_clock=lambda: now[0],
    )
    now[0] = 8.0

    state = budget.as_dict()

    assert state["elapsed_seconds"] == 8.0
    assert state["close_requested"] is True


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


def test_budget_replaces_exact_ceiling_estimate_with_partial_authoritative_usage() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_estimated_input_tokens": 10,
            "max_estimated_output_tokens": 10,
        }
    )
    budget.record_model_usage(
        turn_number=1,
        estimated_input_tokens=10,
        estimated_output_tokens=5,
    )

    budget.record_model_usage(turn_number=1, actual_input_tokens=3)
    assert budget.as_dict()["estimated_input_tokens_used"] == 3
    assert budget.as_dict()["estimated_output_tokens_used"] == 5

    budget.record_model_usage(turn_number=1, actual_output_tokens=0)
    assert budget.as_dict()["estimated_input_tokens_used"] == 3
    assert budget.as_dict()["estimated_output_tokens_used"] == 0


def test_rejected_model_usage_does_not_advance_turn_or_change_usage() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={"max_estimated_input_tokens": 10},
        monotonic_clock=lambda: 0.0,
    )
    budget.record_model_usage(turn_number=1, estimated_input_tokens=5)
    before = budget.as_dict()

    with pytest.raises(ContextProviderError) as captured:
        budget.record_model_usage(turn_number=2, actual_input_tokens=6)

    assert captured.value.details == {"limit": "max_estimated_input_tokens"}
    assert budget.as_dict() == before

    budget.record_model_usage(turn_number=1, estimated_input_tokens=4)
    assert budget.as_dict()["estimated_input_tokens_used"] == 4


def test_result_char_failure_names_total_dimension() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 10,
            "max_total_observation_chars": 5,
        }
    )
    budget.record_observation(path=None, chars=4, count_toward_file=False)

    with pytest.raises(ContextProviderError) as captured:
        budget.record_result_chars(2)

    assert captured.value.details == {"limit": "max_total_observation_chars"}


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


def test_budget_limits_and_server_ceilings_cannot_be_widened() -> None:
    import goodteam_os.repository_context_provider as context_provider
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        OnboardingBudgetLimits,
    )

    budget = OnboardingBudget(requested_limits={"max_tool_calls": 1})

    assert isinstance(context_provider._SERVER_CEILINGS, MappingProxyType)
    with pytest.raises(TypeError):
        context_provider._SERVER_CEILINGS["max_tool_calls"] = 80  # type: ignore[index]
    with pytest.raises(AttributeError):
        budget.limits = OnboardingBudgetLimits(max_tool_calls=80)

    budget.begin_tool_call()
    with pytest.raises(ContextProviderError) as captured:
        budget.begin_tool_call()

    assert captured.value.code == "onboarding_budget_exceeded"
    assert captured.value.details == {"limit": "max_tool_calls"}


def test_budget_requests_close_at_eighty_percent() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget(requested_limits={"max_tool_calls": 5})
    assert budget.close_requested is False

    for _ in range(4):
        budget.begin_tool_call()

    assert budget.close_requested is True
    assert budget.as_dict()["tool_calls_used"] == 4


def test_second_call_over_max_tool_calls_persists_one_error_envelope(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)
    workspace = fixture["workspace"]
    root = fixture["root"]
    project_key = fixture["project_key"]
    snapshot = fixture["snapshot"]
    store = fixture["store"]
    provider = RepositoryContextProvider(
        workspace=workspace,
        project_key=project_key,
        root=root,
        snapshot=snapshot,
        observation_store=store,
        budget=OnboardingBudget(requested_limits={"max_tool_calls": 1}),
    )

    first = provider.call("list_tree", {"path": "", "max_entries": 10}, model_turn=1)
    with pytest.raises(Exception) as captured:
        provider.call("list_tree", {"path": "", "max_entries": 10}, model_turn=1)

    assert captured.value.code == "onboarding_budget_exceeded"
    records = _all_records(workspace, project_key)
    assert [record["status"] for record in records] == ["ok", "error"]
    assert records[0] == first
    assert records[1]["response_metadata"] == {
        "error": {"code": "onboarding_budget_exceeded"}
    }
    assert records[1]["observations"] == []
    assert records[1]["request"] == {"redacted": True}


def test_model_turn_hard_limit_or_order_failure_persists_one_error_envelope(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)
    workspace = fixture["workspace"]
    root = fixture["root"]
    project_key = fixture["project_key"]
    snapshot = fixture["snapshot"]
    store = fixture["store"]
    provider = RepositoryContextProvider(
        workspace=workspace,
        project_key=project_key,
        root=root,
        snapshot=snapshot,
        observation_store=store,
        budget=OnboardingBudget(requested_limits={"max_model_turns": 1}),
    )

    provider.call("list_tree", {"path": "", "max_entries": 10}, model_turn=1)
    with pytest.raises(Exception) as captured:
        provider.call("list_tree", {"path": "", "max_entries": 10}, model_turn=2)

    assert captured.value.code == "onboarding_budget_exceeded"
    records = _all_records(workspace, project_key)
    assert len(records) == 2
    assert records[-1]["status"] == "error"
    assert records[-1]["response_metadata"] == {
        "error": {"code": "onboarding_budget_exceeded"}
    }
    assert records[-1]["observations"] == []


def test_provider_rejects_list_of_pairs_request_and_does_not_execute_handler(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]
    called = False

    class TrackingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            nonlocal called
            called = True
            return {"observations": [], "truncated": False}

    provider = _rebuild_provider(fixture, TrackingProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", [("path", "")], model_turn=1)  # type: ignore[arg-type]

    assert captured.value.code == "context_request_invalid"
    assert called is False
    record = _last_record_dict(workspace, project_key)
    assert record["status"] == "error"
    assert record["response_metadata"] == {"error": {"code": "context_request_invalid"}}
    assert record["observations"] == []
    assert record["request"] == {"redacted": True}


def test_unknown_tool_invalid_request_precedence_is_request_validation(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]

    with pytest.raises(ContextProviderError) as captured:
        provider.call("run_command", [("argv", ["cat", "README.md"])], model_turn=1)  # type: ignore[arg-type]

    assert captured.value.code == "context_request_invalid"
    records = _all_records(workspace, project_key)
    assert len(records) == 1
    assert records[0]["status"] == "error"
    assert records[0]["tool"] == "run_command"
    assert records[0]["response_metadata"] == {"error": {"code": "context_request_invalid"}}


def test_request_validation_precedes_invalid_turn_and_tool_type(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]

    with pytest.raises(ContextProviderError) as request_error:
        provider.call([], [("path", "")], model_turn=0)  # type: ignore[arg-type]
    assert request_error.value.code == "context_request_invalid"

    with pytest.raises(ContextProviderError) as tool_error:
        provider.call([], {}, model_turn=1)  # type: ignore[arg-type]
    assert tool_error.value.code == "context_tool_not_allowed"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["tool"] == "<invalid>"
    assert record["status"] == "denied"


def test_denied_tools_consume_quota_before_dispatch(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    budget = OnboardingBudget(requested_limits={"max_tool_calls": 1})
    provider = _rebuild_provider(fixture, RepositoryContextProvider, budget=budget)

    with pytest.raises(ContextProviderError) as first:
        provider.call("run_command", {}, model_turn=1)
    assert first.value.code == "context_tool_not_allowed"
    assert budget.as_dict()["tool_calls_used"] == 1

    with pytest.raises(ContextProviderError) as second:
        provider.call("another_unknown", {}, model_turn=1)
    assert second.value.code == "onboarding_budget_exceeded"
    assert second.value.details == {"limit": "max_tool_calls"}
    assert [
        record["status"]
        for record in _all_records(fixture["workspace"], fixture["project_key"])
    ] == ["denied", "error"]


def test_constructor_rejects_cross_workspace_observation_store(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    other_workspace = tmp_path / "other-workspace"
    other_import = import_project(other_workspace, str(fixture["root"]))
    assert str(other_import["project"]["project_key"]) == fixture["project_key"]
    OnboardingArtifactStore(
        other_workspace,
        fixture["project_key"],
        fixture["root"],
    ).publish_snapshot(fixture["snapshot"])
    other_store = OnboardingObservationStore(
        other_workspace,
        fixture["project_key"],
        fixture["root"],
        "job-1",
        fixture["snapshot"].snapshot_id,
    )

    with pytest.raises(ContextProviderError) as captured:
        RepositoryContextProvider(
            workspace=fixture["workspace"],
            project_key=fixture["project_key"],
            root=fixture["root"],
            snapshot=fixture["snapshot"],
            observation_store=other_store,
            budget=OnboardingBudget(),
        )

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)


@pytest.mark.parametrize(
    ("mutate", "expected_code", "forbidden"),
    [
        (
            lambda fixture: setattr(
                fixture["store"],
                "snapshot_id",
                "snap_foreign",
            ),
            "onboarding_snapshot_mismatch",
            ("snap_foreign",),
        ),
        (
            lambda fixture: setattr(
                fixture["store"],
                "project_key",
                "FOREIGN",
            ),
            "onboarding_snapshot_mismatch",
            ("FOREIGN",),
        ),
        (
            lambda fixture: setattr(
                fixture["store"],
                "root",
                fixture["root"].parent,
            ),
            "onboarding_snapshot_mismatch",
            tuple(),
        ),
        (
            lambda fixture: (
                snapshot_path(
                    fixture["workspace"],
                    fixture["project_key"],
                    fixture["snapshot"].snapshot_id,
                ).unlink()
            ),
            "onboarding_snapshot_mismatch",
            tuple(),
        ),
        (
            lambda fixture: (fixture["root"] / "README.md").write_text(
                "changed\n",
                encoding="utf-8",
            ),
            "repository_snapshot_changed",
            tuple(),
        ),
    ],
)
def test_constructor_binding_failures_are_detached_and_sanitized(
    tmp_path: Path,
    mutate,
    expected_code: str,
    forbidden: tuple[str, ...],
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    mutate(fixture)

    with pytest.raises(ContextProviderError) as captured:
        _rebuild_provider(fixture, RepositoryContextProvider)

    assert captured.value.code == expected_code
    _assert_detached_error(captured.value)
    _assert_no_raw_binding_data(captured.value, *forbidden)


@pytest.mark.parametrize(
    ("field", "raw_value"),
    [
        ("workspace", "raw-workspace-secret"),
        ("project_key", "RAW_PROJECT_KEY"),
        ("root", "raw-root-secret"),
        ("job_id", "raw-job-secret"),
        ("snapshot_id", "raw-snapshot-secret"),
    ],
)
def test_constructor_store_accessor_failures_are_detached_and_sanitized(
    tmp_path: Path,
    field: str,
    raw_value: str,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class ExplodingStore:
        def __init__(self, backing_store, exploding_field: str) -> None:
            self._backing_store = backing_store
            self._exploding_field = exploding_field

        @property
        def workspace(self):
            if self._exploding_field == "workspace":
                raise RuntimeError(raw_value)
            return self._backing_store.workspace

        @property
        def project_key(self):
            if self._exploding_field == "project_key":
                raise RuntimeError(raw_value)
            return self._backing_store.project_key

        @property
        def root(self):
            if self._exploding_field == "root":
                raise RuntimeError(raw_value)
            return self._backing_store.root

        @property
        def job_id(self):
            if self._exploding_field == "job_id":
                raise RuntimeError(raw_value)
            return self._backing_store.job_id

        @property
        def snapshot_id(self):
            if self._exploding_field == "snapshot_id":
                raise RuntimeError(raw_value)
            return self._backing_store.snapshot_id

    with pytest.raises(ContextProviderError) as captured:
        RepositoryContextProvider(
            workspace=fixture["workspace"],
            project_key=fixture["project_key"],
            root=fixture["root"],
            snapshot=fixture["snapshot"],
            observation_store=ExplodingStore(fixture["store"], field),
            budget=OnboardingBudget(),
        )

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    _assert_no_raw_binding_data(
        captured.value,
        raw_value,
        fixture["snapshot"].snapshot_id,
        str(fixture["root"]),
        str(fixture["workspace"]),
        fixture["project_key"],
    )


def test_record_observation_cannot_steal_reserved_result_capacity() -> None:
    from goodteam_os.repository_context_provider import ContextProviderError, OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 6,
            "max_total_observation_chars": 10,
        },
        monotonic_clock=lambda: 0.0,
    )
    reservation = budget.reserve_result()
    before = budget.as_dict()

    with pytest.raises(ContextProviderError) as captured:
        budget.record_observation(path="README.md", chars=5, count_toward_file=True)

    assert captured.value.code == "onboarding_budget_exceeded"
    assert captured.value.details == {"limit": "max_total_observation_chars"}
    assert budget.as_dict() == before

    budget.release_result(reservation)
    budget.record_observation(path="README.md", chars=5, count_toward_file=True)
    assert budget.as_dict()["total_observation_chars_used"] == 5


@pytest.mark.parametrize(
    ("field", "replacement_factory"),
    [
        ("workspace", lambda fixture: fixture["workspace"].parent / "redirected-workspace"),
        ("job_id", lambda fixture: "job-redirected"),
        ("project_key", lambda fixture: "REDIRECTED"),
        ("root", lambda fixture: fixture["root"].parent),
        ("snapshot_id", lambda fixture: "snap_redirected"),
    ],
)
def test_store_rebinding_after_construction_fails_closed_before_handler_and_writes_nowhere(
    tmp_path: Path,
    field: str,
    replacement_factory,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )
    replacement = replacement_factory(fixture)
    if field == "workspace":
        import_project(replacement, str(fixture["root"]))
        OnboardingArtifactStore(
            replacement,
            fixture["project_key"],
            fixture["root"],
        ).publish_snapshot(fixture["snapshot"])
    setattr(fixture["store"], field, replacement)
    redirected_path = observation_path(
        fixture["store"].workspace,
        fixture["store"].project_key,
        fixture["store"].job_id,
    )

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", {}, model_turn=1)

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    assert not redirected_path.exists()


def test_store_rebinding_during_handler_fails_closed_and_writes_nowhere(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )
    redirected_job = "job-redirected"
    redirected_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        redirected_job,
    )

    class MutatingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            del request
            self.observation_store.job_id = redirected_job
            return {
                "observations": [{"path": "README.md", "text": "x"}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, MutatingProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    assert not redirected_path.exists()


def test_store_rebinding_between_post_verification_and_append_fails_closed(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )
    redirected_job = "job-redirected"
    redirected_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        redirected_job,
    )

    class AppendWindowProvider(RepositoryContextProvider):
        def _build_envelope(self, **kwargs):
            self.observation_store.job_id = redirected_job
            return super()._build_envelope(**kwargs)

    provider = _rebuild_provider(fixture, AppendWindowProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    assert not redirected_path.exists()


@pytest.mark.parametrize(
    ("status_kind", "field", "replacement"),
    [
        ("ok", "workspace", "redirected-workspace"),
        ("ok", "project_key", "REDIRECTED"),
        ("ok", "root", "redirected-root"),
        ("ok", "job_id", "job-redirected"),
        ("ok", "snapshot_id", "snap_redirected"),
        ("denied", "workspace", "redirected-workspace"),
        ("denied", "project_key", "REDIRECTED"),
        ("denied", "root", "redirected-root"),
        ("denied", "job_id", "job-redirected"),
        ("denied", "snapshot_id", "snap_redirected"),
        ("error", "workspace", "redirected-workspace"),
        ("error", "project_key", "REDIRECTED"),
        ("error", "root", "redirected-root"),
        ("error", "job_id", "job-redirected"),
        ("error", "snapshot_id", "snap_redirected"),
    ],
)
def test_append_window_binding_mutations_fail_closed_and_write_nowhere(
    tmp_path: Path,
    status_kind: str,
    field: str,
    replacement: str,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )

    if field == "workspace":
        redirected_workspace = fixture["workspace"].parent / replacement
        import_project(redirected_workspace, str(fixture["root"]))
        OnboardingArtifactStore(
            redirected_workspace,
            fixture["project_key"],
            fixture["root"],
        ).publish_snapshot(fixture["snapshot"])
        replacement_value = redirected_workspace
    elif field == "root":
        replacement_value = fixture["root"].parent / replacement
    else:
        replacement_value = replacement

    class AppendMutationProvider(RepositoryContextProvider):
        def _recording_authority_error(self):
            error = super()._recording_authority_error()
            if error is None:
                setattr(self.observation_store, field, replacement_value)
            return error

        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            if status_kind == "error":
                raise ContextProviderError(
                    "context_handler_failed",
                    "Repository context handler failed.",
                )
            return super()._handle_list_tree(request)

    provider = _rebuild_provider(fixture, AppendMutationProvider)
    redirected_path = observation_path(
        provider.observation_store.workspace,
        provider.observation_store.project_key,
        provider.observation_store.job_id,
    )
    tool = "list_tree" if status_kind != "denied" else "run_command"

    with pytest.raises(ContextProviderError) as captured:
        provider.call(tool, {}, model_turn=1)

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    if redirected_path != original_path:
        assert not redirected_path.exists()


@pytest.mark.parametrize("status_kind", ["ok", "denied", "error"])
def test_store_swap_after_final_authority_fails_closed_and_writes_nowhere(
    tmp_path: Path,
    status_kind: str,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    redirected_job = "job-redirected"
    redirected_store = OnboardingObservationStore(
        fixture["workspace"],
        fixture["project_key"],
        fixture["root"],
        redirected_job,
        fixture["snapshot"].snapshot_id,
    )
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )
    redirected_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        redirected_job,
    )

    class SwappingProvider(RepositoryContextProvider):
        def _recording_authority_error(self):
            error = super()._recording_authority_error()
            if error is None:
                self.observation_store = redirected_store
            return error

        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            if status_kind == "error":
                raise ContextProviderError(
                    "context_handler_failed",
                    "Repository context handler failed.",
                )
            return super()._handle_list_tree(request)

    provider = _rebuild_provider(fixture, SwappingProvider)
    tool = "list_tree" if status_kind != "denied" else "run_command"

    with pytest.raises(ContextProviderError) as captured:
        provider.call(tool, {}, model_turn=1)

    assert captured.value.code == "onboarding_snapshot_mismatch"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    assert not redirected_path.exists()


def test_list_tree_request_rejects_max_entries_above_server_cap(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "list_tree",
            {"path": "", "max_entries": 501},
            model_turn=1,
        )

    assert captured.value.code == "context_request_invalid"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    assert record["response_metadata"] == {"error": {"code": "context_request_invalid"}}


@pytest.mark.parametrize("payload", [{"path": "", "depth": 1}, {"path": "", "extra": True}])
def test_list_tree_request_rejects_unsupported_keys(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", payload, model_turn=1)

    assert captured.value.code == "context_request_invalid"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    assert record["response_metadata"] == {"error": {"code": "context_request_invalid"}}


def test_list_tree_reports_truncation_honestly(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    for index in range(12):
        (fixture["root"] / f"file-{index:02d}.txt").write_text("x\n", encoding="utf-8")
    fixture["snapshot"] = capture_repository_snapshot(
        fixture["root"],
        project_key=fixture["project_key"],
    )
    OnboardingArtifactStore(
        fixture["workspace"],
        fixture["project_key"],
        fixture["root"],
    ).publish_snapshot(fixture["snapshot"])
    fixture["store"] = OnboardingObservationStore(
        fixture["workspace"],
        fixture["project_key"],
        fixture["root"],
        "job-1",
        fixture["snapshot"].snapshot_id,
    )
    from goodteam_os.repository_context_provider import RepositoryContextProvider

    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=fixture["budget"],
    )

    response = provider.call("list_tree", {"path": "", "max_entries": 5}, model_turn=1)

    assert response["status"] == "ok"
    assert response["truncated"] is True
    assert len(response["observations"]) == 5


def test_handler_receives_immutable_call_context_with_reserved_allowance(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)

    class ContextAwareProvider(RepositoryContextProvider):
        def _handle_list_tree(
            self,
            request: dict[str, object],
        ) -> dict[str, object]:
            context = self._current_tool_call_context()
            assert request == {"path": "", "max_entries": 1}
            assert isinstance(context.result_char_allowance, int)
            assert context.result_char_allowance > 0
            with pytest.raises(Exception):
                context.result_char_allowance = 1  # type: ignore[misc]
            return {
                "observations": [{"path": "README.md", "text": "x"}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, ContextAwareProvider)
    response = provider.call(
        "list_tree",
        {"path": "", "max_entries": 1},
        model_turn=1,
    )

    assert response["status"] == "ok"


@pytest.mark.parametrize(
    ("build_provider", "mutate_fixture", "expected_code"),
    [
        (
            lambda fixture: _rebuild_provider(
                fixture,
                type(
                    "HandlerFail",
                    (fixture["provider"].__class__,),
                    {
                        "_handle_list_tree": lambda self, request: (_ for _ in ()).throw(
                            RuntimeError("alpha README.md sk-live-secret")
                        )
                    },
                ),
            ),
            lambda fixture: None,
            "context_handler_failed",
        ),
        (
            lambda fixture: fixture["provider"],
            lambda fixture: (fixture["root"] / "README.md").write_text(
                "changed\n",
                encoding="utf-8",
            ),
            "repository_snapshot_changed",
        ),
    ],
)
def test_top_level_errors_detach_exception_chains_for_selected_failures(
    tmp_path: Path,
    build_provider,
    mutate_fixture,
    expected_code,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    mutate_fixture(fixture)
    provider = build_provider(fixture)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == expected_code
    _assert_detached_error(captured.value)


def test_top_level_persistence_failure_is_detached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goodteam_os.project_import import ProjectImportError
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    class FailingAppendStore:
        def append(self, record: dict[str, object]) -> Path:
            del record
            raise ProjectImportError(
                "onboarding_observation_invalid",
                "alpha raw persistence failure",
            )

    monkeypatch.setattr(
        fixture["provider"],
        "_authoritative_append_store",
        lambda: FailingAppendStore(),
    )

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_observation_persistence_failed"
    _assert_detached_error(captured.value)


def test_top_level_artifact_failure_is_detached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goodteam_os.project_import import ProjectImportError
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    def fail_verify(snapshot_id: str):
        del snapshot_id
        raise ProjectImportError(
            "onboarding_artifact_conflict",
            "alpha raw artifact failure",
        )

    monkeypatch.setattr(fixture["provider"]._artifact_store, "verify_snapshot", fail_verify)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", {}, model_turn=1)

    assert captured.value.code == "onboarding_artifact_conflict"
    _assert_detached_error(captured.value)


def test_top_level_repository_access_failure_is_detached(tmp_path: Path) -> None:
    from goodteam_os.repository_access import RepositoryAccessError
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class AccessFailProvider(RepositoryContextProvider):
        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            del request
            raise RepositoryAccessError(
                "context_path_rejected",
                "alpha raw path failure",
                details={"path": "README.md", "reason": "symlink"},
            )

    provider = _rebuild_provider(fixture, AccessFailProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_path_rejected"
    _assert_detached_error(captured.value)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda fixture: (fixture["workspace"] / "state" / "projects" / fixture["project_key"] / "onboarding").rename(
                fixture["workspace"] / "state" / "projects" / fixture["project_key"] / "onboarding-moved"
            ),
            "onboarding_snapshot_mismatch",
        ),
        (
            lambda fixture: snapshot_path(
                fixture["workspace"],
                fixture["project_key"],
                fixture["snapshot"].snapshot_id,
            ).unlink(),
            "onboarding_snapshot_mismatch",
        ),
        (
            lambda fixture: (
                snapshot_path(
                    fixture["workspace"],
                    fixture["project_key"],
                    fixture["snapshot"].snapshot_id,
                ).rename(
                    snapshot_path(
                        fixture["workspace"],
                        fixture["project_key"],
                        fixture["snapshot"].snapshot_id,
                    ).with_name("snapshot-original.json")
                ),
                snapshot_path(
                    fixture["workspace"],
                    fixture["project_key"],
                    fixture["snapshot"].snapshot_id,
                ).symlink_to(
                    snapshot_path(
                        fixture["workspace"],
                        fixture["project_key"],
                        fixture["snapshot"].snapshot_id,
                    ).with_name("snapshot-original.json")
                ),
            ),
            "onboarding_artifact_conflict",
        ),
        (
            lambda fixture: snapshot_path(
                fixture["workspace"],
                fixture["project_key"],
                fixture["snapshot"].snapshot_id,
            ).write_text("{malformed", encoding="utf-8"),
            "onboarding_artifact_conflict",
        ),
        (
            lambda fixture: (fixture["root"] / "README.md").write_text(
                "changed\n",
                encoding="utf-8",
            ),
            "repository_snapshot_changed",
        ),
        (
            lambda fixture: setattr(fixture["store"], "job_id", "job-redirected"),
            "onboarding_snapshot_mismatch",
        ),
    ],
)
def test_call_time_binding_failures_win_without_any_ledger_write(
    tmp_path: Path,
    mutate,
    expected_code: str,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    original_path = observation_path(
        fixture["workspace"],
        fixture["project_key"],
        "job-1",
    )
    mutate(fixture)
    redirected_path = observation_path(
        fixture["store"].workspace,
        fixture["store"].project_key,
        fixture["store"].job_id,
    )

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", {}, model_turn=1)

    assert captured.value.code == expected_code
    _assert_detached_error(captured.value)
    assert not original_path.exists()
    if redirected_path != original_path:
        assert not redirected_path.exists()

@pytest.mark.parametrize(
    "tool",
    [
        "search_text",
        "read_file_excerpt",
        "inspect_manifest",
        "inspect_git_history",
        "inspect_tests",
        "inspect_recent_changes",
    ],
)
def test_future_context_tools_fail_explicitly_until_implemented(
    tmp_path: Path,
    tool: str,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, {}, model_turn=1)

    assert captured.value.code == "context_tool_not_implemented"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    assert record["response_metadata"] == {
        "error": {"code": "context_tool_not_implemented"}
    }


def test_provider_has_no_mutable_callable_dispatch_map(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    provider = fixture["provider"]

    assert not hasattr(provider, "_tool_handlers")
    assert provider.tool_names() == (
        "list_tree",
        "search_text",
        "read_file_excerpt",
        "inspect_manifest",
        "inspect_git_history",
        "inspect_tests",
        "inspect_recent_changes",
    )


@pytest.mark.parametrize(
    ("apply_usage", "expect_close"),
    [
        (
            lambda budget: budget.begin_model_turn(1),
            False,
        ),
        (
            lambda budget: [budget.begin_model_turn(1), budget.begin_model_turn(2), budget.begin_model_turn(3), budget.begin_model_turn(4)],
            True,
        ),
        (
            lambda budget: [budget.begin_tool_call(), budget.begin_tool_call(), budget.begin_tool_call()],
            False,
        ),
        (
            lambda budget: [budget.begin_tool_call(), budget.begin_tool_call(), budget.begin_tool_call(), budget.begin_tool_call()],
            True,
        ),
        (
            lambda budget: [budget.record_observation(path=f"f{index}.txt", chars=1, count_toward_file=True) for index in range(7)],
            False,
        ),
        (
            lambda budget: [budget.record_observation(path=f"f{index}.txt", chars=1, count_toward_file=True) for index in range(8)],
            True,
        ),
        (
            lambda budget: budget.record_observation(path="README.md", chars=7, count_toward_file=True),
            False,
        ),
        (
            lambda budget: budget.record_observation(path="README.md", chars=8, count_toward_file=True),
            True,
        ),
        (
            lambda budget: budget.record_result_chars(7),
            False,
        ),
        (
            lambda budget: budget.record_result_chars(8),
            True,
        ),
        (
            lambda budget: budget.record_observation(path=None, chars=7, count_toward_file=False),
            False,
        ),
        (
            lambda budget: budget.record_observation(path=None, chars=8, count_toward_file=False),
            True,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                estimated_input_tokens=7,
                estimated_output_tokens=1,
            ),
            False,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                estimated_input_tokens=8,
                estimated_output_tokens=1,
            ),
            True,
        ),
        (
            lambda budget: budget.record_model_usage(turn_number=1, estimated_input_tokens=1, estimated_output_tokens=7),
            False,
        ),
        (
            lambda budget: budget.record_model_usage(turn_number=1, estimated_input_tokens=1, estimated_output_tokens=8),
            True,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                actual_input_tokens=7,
                estimated_input_tokens=7,
                estimated_output_tokens=1,
            ),
            False,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                actual_input_tokens=8,
                estimated_input_tokens=8,
                estimated_output_tokens=1,
            ),
            True,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                actual_output_tokens=7,
                estimated_input_tokens=1,
                estimated_output_tokens=7,
            ),
            False,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                actual_output_tokens=8,
                estimated_input_tokens=1,
                estimated_output_tokens=8,
            ),
            True,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                cost_usd=0.7,
                estimated_input_tokens=1,
                estimated_output_tokens=1,
            ),
            False,
        ),
        (
            lambda budget: budget.record_model_usage(
                turn_number=1,
                cost_usd=0.8,
                estimated_input_tokens=1,
                estimated_output_tokens=1,
            ),
            True,
        ),
    ],
)
def test_budget_close_requested_audits_all_declared_dimensions(
    apply_usage,
    expect_close: bool,
) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    budget = OnboardingBudget(
        requested_limits={
            "max_model_turns": 5,
            "max_tool_calls": 5,
            "max_files_observed": 10,
            "max_total_observation_chars": 10,
            "max_single_result_chars": 10,
            "max_file_cumulative_chars": 10,
            "max_elapsed_seconds": 10.0,
            "max_estimated_input_tokens": 10,
            "max_estimated_output_tokens": 10,
            "max_cost_usd": 1.0,
        },
        monotonic_clock=lambda: 0.0,
    )

    result = apply_usage(budget)
    assert result is None or isinstance(result, list)
    assert budget.close_requested is expect_close


def test_budget_close_requested_tracks_elapsed_dimension() -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget

    times = iter((0.0, 0.0, 8.0))
    budget = OnboardingBudget(
        requested_limits={"max_elapsed_seconds": 10.0},
        monotonic_clock=lambda: next(times),
    )

    budget.begin_model_turn(1)
    assert budget.close_requested is False
    budget.begin_tool_call()
    assert budget.close_requested is True


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
    assert artifact_error.value.code in {
        "onboarding_artifact_conflict",
        "onboarding_snapshot_mismatch",
    }
    assert other_snapshot.snapshot_id == snapshot.snapshot_id


def test_denied_error_envelope_exact_persistence_and_no_raw_request_content_leakage(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    workspace = fixture["workspace"]
    project_key = fixture["project_key"]

    class FailingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise ContextProviderError(
                "repository_text_was_alpha",
                "handler failed with alpha",
                details={
                    "path": "README.md",
                    "limit": "max_single_result_chars",
                },
            )

    provider = _rebuild_provider(fixture, FailingProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call(
            "list_tree",
            {"token": "sk-live-secret", "path": "README.md"},
            model_turn=1,
        )

    assert captured.value.code == "context_handler_failed"
    assert str(captured.value) == "Repository context handler failed."
    assert captured.value.details == {"limit": "max_single_result_chars"}
    response = _last_record_dict(workspace, project_key)
    assert response["status"] == "error"
    assert response["observations"] == []
    assert response["request"] == {"redacted": True}
    encoded = _last_record_bytes(workspace, project_key).decode("utf-8")
    assert "sk-live-secret" not in encoded
    assert '"path":"README.md"' not in encoded
    assert "alpha" not in encoded


def test_generic_handler_failure_is_safe_and_persisted(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class FailingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("alpha sk-live-secret README.md")

    provider = _rebuild_provider(fixture, FailingProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {"secret": "sk-live-secret"}, model_turn=1)

    assert captured.value.code == "context_handler_failed"
    assert str(captured.value) == "Repository context handler failed."
    assert captured.value.details == {}
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode()
    assert "alpha" not in encoded
    assert "sk-live-secret" not in encoded
    assert "README.md" not in encoded


def test_allowlisted_handler_error_code_still_normalizes_raw_material(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class UnsupportedProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise ContextProviderError(
                "context_tool_unsupported",
                "unsupported because alpha sk-live-secret",
                details={"path": "README.md", "text": "alpha"},
            )

    provider = _rebuild_provider(fixture, UnsupportedProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_tool_unsupported"
    assert str(captured.value) == "Repository context tool is unsupported."
    assert captured.value.details == {}
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode()
    assert "alpha" not in encoded
    assert "sk-live-secret" not in encoded
    assert "README.md" not in encoded


def test_handler_output_must_be_json_safe(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class InvalidOutputProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            return {
                "observations": [{"path": "README.md", "text": {"alpha"}}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, InvalidOutputProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_handler_failed"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []


def test_cyclic_handler_output_is_a_safe_handler_failure(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class CyclicOutputProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            observation: dict[str, object] = {"path": "README.md"}
            observation["cycle"] = observation
            return {"observations": [observation], "truncated": False}

    provider = _rebuild_provider(fixture, CyclicOutputProvider)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_handler_failed"
    assert str(captured.value) == "Repository context handler returned an invalid payload."
    assert _last_record_dict(fixture["workspace"], fixture["project_key"])[
        "status"
    ] == "error"


def test_derived_observation_id_wins_and_chars_are_counted_once(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)

    class ObservationProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            return {
                "observations": [
                    {
                        "observation_id": "attacker-controlled",
                        "kind": "excerpt",
                        "path": "README.md",
                        "text": "alpha",
                        "matches": [{"text": "beta"}],
                    }
                ],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, ObservationProvider)

    response = provider.call("list_tree", {}, model_turn=1)

    assert response["observations"][0]["observation_id"] == (
        f'{response["tool_call_id"]}:0'
    )
    assert response["response_metadata"]["returned_chars"] == 25
    assert response["budget"]["total_observation_chars_used"] == 25
    assert response["budget"]["observed_file_chars"] == {"README.md": 9}


def test_handler_time_snapshot_drift_rolls_back_result_budget(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 50,
            "max_total_observation_chars": 50,
        }
    )

    class DriftingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            (self.root / "README.md").write_text("changed\n", encoding="utf-8")
            return {
                "observations": [{"path": "README.md", "text": "alpha"}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, DriftingProvider, budget=budget)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "repository_snapshot_changed"
    state = budget.as_dict()
    assert state["total_observation_chars_used"] == 0
    assert state["files_observed"] == 0
    assert budget.result_char_allowance() == 50
    assert _all_records(fixture["workspace"], fixture["project_key"]) == []


def test_root_inode_replacement_preserves_repository_root_changed_without_ledger_write(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 50,
            "max_total_observation_chars": 50,
        }
    )

    class RootReplacingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            displaced_root = self.root.with_name("repo-displaced")
            self.root.rename(displaced_root)
            self.root.mkdir()
            (self.root / "README.md").write_text("replacement\n", encoding="utf-8")
            return {
                "observations": [{"path": "README.md", "text": "alpha"}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, RootReplacingProvider, budget=budget)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "repository_root_changed"
    _assert_detached_error(captured.value)
    state = budget.as_dict()
    assert state["total_observation_chars_used"] == 0
    assert state["files_observed"] == 0
    assert budget.result_char_allowance() == 50
    assert _all_records(fixture["workspace"], fixture["project_key"]) == []


def test_shared_budget_reservation_prevents_concurrent_handler_overcommit(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 5,
            "max_total_observation_chars": 5,
        }
    )
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class BlockingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            name = str(request["name"])
            calls.append(name)
            if name == "first":
                entered.set()
                assert release.wait(timeout=10)
            return {
                "observations": [{"path": "a", "text": "x"}],
                "truncated": False,
            }

    first_provider = _rebuild_provider(fixture, BlockingProvider, budget=budget)
    second_provider = _rebuild_provider(fixture, BlockingProvider, budget=budget)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            first_provider.call,
            "list_tree",
            {"name": "first"},
            model_turn=1,
        )
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(ContextProviderError) as captured:
                second_provider.call(
                    "list_tree",
                    {"name": "second"},
                    model_turn=1,
                )
            assert captured.value.details == {
                "limit": "max_total_observation_chars"
            }
        finally:
            release.set()
        assert future.result()["status"] == "ok"

    assert calls == ["first"]
    assert budget.as_dict()["total_observation_chars_used"] == 2
    assert budget.result_char_allowance() == 3


def test_failed_handler_releases_shared_result_reservation(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    budget = OnboardingBudget(
        requested_limits={
            "max_single_result_chars": 5,
            "max_total_observation_chars": 5,
        }
    )

    class ToggleProvider(RepositoryContextProvider):
        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            if request.get("fail"):
                raise RuntimeError("raw repository failure")
            return {
                "observations": [{"path": "a", "text": "x"}],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, ToggleProvider, budget=budget)

    with pytest.raises(ContextProviderError):
        provider.call("list_tree", {"fail": True}, model_turn=1)

    response = provider.call("list_tree", {"fail": False}, model_turn=1)
    assert response["status"] == "ok"
    assert budget.as_dict()["total_observation_chars_used"] == 2


def test_error_recording_does_not_read_the_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class FailingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("alpha raw repository failure")

    provider = _rebuild_provider(fixture, FailingProvider)

    def fail_read() -> list[dict[str, object]]:
        raise ProjectImportError(
            "onboarding_observation_invalid",
            "raw ledger read failure alpha",
        )

    monkeypatch.setattr(fixture["store"], "read_all", fail_read)

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_handler_failed"
    assert captured.value.operational_signals == ()
    assert _last_record_dict(fixture["workspace"], fixture["project_key"])[
        "status"
    ] == "error"


def test_error_append_failure_surfaces_safe_persistence_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class FailingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("alpha raw repository failure")

    provider = _rebuild_provider(fixture, FailingProvider)

    def fail_append(_record: dict[str, object]) -> Path:
        raise ProjectImportError(
            "onboarding_observation_invalid",
            "raw ledger append failure sk-live-secret",
        )

    class FailingAppendStore:
        def append(self, record: dict[str, object]) -> Path:
            return fail_append(record)

    monkeypatch.setattr(
        provider,
        "_authoritative_append_store",
        lambda: FailingAppendStore(),
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_observation_persistence_failed"
    assert str(captured.value) == "Repository context observation could not be persisted."
    assert captured.value.details == {}
    assert captured.value.operational_signals == (
        "context_observation_persistence_failed",
    )
    _assert_detached_error(captured.value)
    assert "raw ledger" not in str(captured.value)
    assert "sk-live-secret" not in str(captured.value)


def test_error_envelope_build_failure_surfaces_safe_persistence_error(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class BuildFailingBudget(OnboardingBudget):
        def as_dict(self) -> dict[str, object]:
            raise RuntimeError("raw budget audit failure alpha")

    class FailingProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("raw handler failure beta")

    provider = _rebuild_provider(
        fixture,
        FailingProvider,
        budget=BuildFailingBudget(),
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call("list_tree", {}, model_turn=1)

    assert captured.value.code == "context_observation_persistence_failed"
    assert str(captured.value) == "Repository context observation could not be persisted."
    assert captured.value.operational_signals == (
        "context_observation_persistence_failed",
    )
    _assert_detached_error(captured.value)


def test_denied_envelope_build_failure_surfaces_safe_persistence_error(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)

    class BuildFailingBudget(OnboardingBudget):
        def as_dict(self) -> dict[str, object]:
            raise RuntimeError("raw denied audit failure alpha")

    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=BuildFailingBudget(),
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call("run_command", {}, model_turn=1)

    assert captured.value.code == "context_observation_persistence_failed"
    assert str(captured.value) == "Repository context observation could not be persisted."
    assert captured.value.operational_signals == (
        "context_observation_persistence_failed",
    )
    _assert_detached_error(captured.value)


def test_provider_rejects_symlinked_snapshot_artifact_on_call(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    artifact = snapshot_path(
        fixture["workspace"],
        fixture["project_key"],
        fixture["snapshot"].snapshot_id,
    )
    original = artifact.with_name("original-snapshot.json")
    artifact.rename(original)
    artifact.symlink_to(original)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call("list_tree", {}, model_turn=1)

    assert captured.value.code == "onboarding_artifact_conflict"
    _assert_detached_error(captured.value)
    assert _all_records(fixture["workspace"], fixture["project_key"]) == []


def test_provider_requires_exact_validated_snapshot_artifact_binding(
    tmp_path: Path,
) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    artifact = snapshot_path(
        fixture["workspace"],
        fixture["project_key"],
        fixture["snapshot"].snapshot_id,
    )
    artifact.write_text(
        json.dumps(
            {
                **fixture["snapshot"].as_dict(),
                "captured_at": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContextProviderError) as captured:
        _rebuild_provider(fixture, RepositoryContextProvider)

    assert captured.value.code == "onboarding_snapshot_mismatch"


def test_exact_allowlist_no_reflection_assertions(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

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
    current_context_source = inspect.getsource(
        RepositoryContextProvider._current_tool_call_context
    )
    assert "getattr(" not in current_context_source
