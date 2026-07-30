from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
import shutil
import threading
import time
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


def _git_provider_fixture(tmp_path: Path, *, process_factory=None) -> dict[str, object]:
    import subprocess

    fixture = _provider_fixture(tmp_path)
    root = fixture["root"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "GoodTeam Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
    _refresh_fixture_snapshot(fixture)
    if process_factory is not None:
        from goodteam_os.repository_context_provider import (
            OnboardingBudget,
            RepositoryContextProvider,
        )

        fixture["provider"] = RepositoryContextProvider(
            workspace=fixture["workspace"],
            project_key=fixture["project_key"],
            root=root,
            snapshot=fixture["snapshot"],
            observation_store=fixture["store"],
            budget=OnboardingBudget(),
            process_factory=process_factory,
        )
    return fixture


def _refresh_fixture_snapshot(fixture: dict[str, object]) -> dict[str, object]:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

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
    fixture["provider"] = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=fixture["budget"],
    )
    return fixture


def _make_pipe_process(
    events: list[tuple[str, bytes, float]],
    *,
    final_returncode: int = 0,
):
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    release = threading.Event()
    writer_done = threading.Event()
    open_fds = {"stdout_w": stdout_w, "stderr_w": stderr_w}

    def close_writer(name: str) -> None:
        fd = open_fds.pop(name, None)
        if fd is not None:
            os.close(fd)

    class PipeProcess:
        def __init__(self) -> None:
            self.stdout = os.fdopen(stdout_r, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_r, "rb", buffering=0)
            self.returncode = None
            self.terminated = False
            self.killed = False
            self.waited = False

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            close_writer("stdout_w")
            close_writer("stderr_w")
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            close_writer("stdout_w")
            close_writer("stderr_w")
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waited = True
            if self.returncode is None:
                self.returncode = final_returncode
            self.stdout.close()
            self.stderr.close()
            return self.returncode

    def writer() -> None:
        release.wait(timeout=10.0)
        try:
            for stream_name, payload, delay_seconds in events:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                if stream_name == "stdout":
                    fd_name = "stdout_w"
                else:
                    fd_name = "stderr_w"
                fd = open_fds.get(fd_name)
                if fd is None:
                    return
                os.write(fd, payload)
            close_writer("stdout_w")
            close_writer("stderr_w")
        finally:
            writer_done.set()

    process = PipeProcess()
    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()
    return process, release, writer_thread, writer_done


def _tracking_selector_factory(base_selector_type):
    created: list[object] = []

    class TrackingSelector(base_selector_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_calls = 0
            created.append(self)

        def close(self):
            self.close_calls += 1
            return super().close()

    return TrackingSelector, created


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
        "request": {"argv": ["cat", "README.md"]},
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


def test_manifest_is_data_and_never_executed(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    marker = tmp_path / "project-command-marker"
    (fixture["root"] / "package.json").write_text(
        json.dumps(
            {
                "name": "orders-app",
                "packageManager": "pnpm@10.0.0",
                "scripts": {
                    "test": "vitest",
                    "postinstall": f"write-marker {marker}",
                },
                "dependencies": {"react": "latest"},
                "private": True,
                "repository": {"url": "raw-secret-url"},
            }
        ),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_manifest", {"path": "package.json"}, model_turn=1
    )

    manifest = result["observations"][0]
    assert manifest["kind"] == "package.json"
    assert manifest["scripts"]["test"] == "vitest"
    assert "write-marker" in manifest["scripts"]["postinstall"]
    assert manifest["dependencies"] == ["react"]
    assert "repository" not in manifest
    assert manifest["path"] == "package.json"
    assert manifest["content_hash"].startswith("sha256:")
    assert not marker.exists()


def test_inspect_pyproject_allowlists_fields(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "pyproject.toml").write_text(
        """
[project]
name = "orders-service"
dependencies = ["fastapi>=0.100", "httpx[http2]"]
[project.optional-dependencies]
test = ["pytest"]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.ruff]
line-length = 88
[tool.private]
password = "never-return-this"
""".strip(),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    manifest = fixture["provider"].call(
        "inspect_manifest", {"path": "pyproject.toml"}, model_turn=1
    )["observations"][0]

    assert manifest["name"] == "orders-service"
    assert manifest["dependencies"] == ["fastapi", "httpx"]
    assert manifest["feature_names"] == ["test"]
    assert manifest["build_backend"] == "hatchling.build"
    assert manifest["build_dependencies"] == ["hatchling"]
    assert manifest["tool_names"] == ["private", "ruff"]
    assert "never-return-this" not in json.dumps(manifest)


def test_inspect_cargo_allowlists_fields(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "Cargo.toml").write_text(
        """
[package]
name = "orders"
publish = false
[dependencies]
serde = "1"
[dev-dependencies]
insta = "1"
[features]
default = []
enterprise = []
[workspace]
members = ["crates/api", "crates/core"]
[[bin]]
name = "orders-cli"
path = "src/private-path.rs"
""".strip(),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    manifest = fixture["provider"].call(
        "inspect_manifest", {"path": "Cargo.toml"}, model_turn=1
    )["observations"][0]

    assert manifest["name"] == "orders"
    assert manifest["dependencies"] == ["serde"]
    assert manifest["dev_dependencies"] == ["insta"]
    assert manifest["feature_names"] == ["default", "enterprise"]
    assert manifest["workspace_names"] == ["crates/api", "crates/core"]
    assert manifest["target_names"] == ["orders-cli"]
    assert "private-path" not in json.dumps(manifest)


def test_inspect_go_mod_conservatively(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "go.mod").write_text(
        """
module example.com/orders
go 1.24
require (
    github.com/acme/api v1.2.3
    github.com/acme/indirect v1.0.0 // indirect
)
replace github.com/acme/api => ../private-checkout
""".strip(),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    manifest = fixture["provider"].call(
        "inspect_manifest", {"path": "go.mod"}, model_turn=1
    )["observations"][0]

    assert manifest["module"] == "example.com/orders"
    assert manifest["go_version"] == "1.24"
    assert manifest["dependencies"] == [
        "github.com/acme/api",
        "github.com/acme/indirect",
    ]
    assert "private-checkout" not in json.dumps(manifest)


def test_invalid_manifest_returns_invalid(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    raw = '{"name": "raw-parser-secret", invalid}'
    (fixture["root"] / "package.json").write_text(raw, encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_manifest", {"path": "package.json"}, model_turn=1
    )

    assert result["status"] == "invalid"
    assert result["observations"] == []
    assert "raw-parser-secret" not in json.dumps(result)


def test_test_inspection_returns_candidates(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    for relative in (
        "tests/test_orders.py",
        "test/unit.js",
        "src/order_test.py",
        "src/order.spec.ts",
        "src/order.test.tsx",
        "src/orders.py",
    ):
        path = fixture["root"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pass\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_tests", {"path": "", "max_files": 20}, model_turn=1
    )

    assert all(
        item["classification"] == "test_candidate"
        and item["basis"] == "path_convention"
        for item in result["observations"]
    )
    paths = {item["path"] for item in result["observations"]}
    assert "tests/test_orders.py" in paths
    assert "src/order_test.py" in paths
    assert "src/order.spec.ts" in paths
    assert "src/orders.py" not in paths


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("inspect_manifest", {"path": "package.json", "extra": True}),
        ("inspect_tests", {"path": "", "max_files": 1, "extra": True}),
        ("inspect_tests", {"path": "", "max_files": True}),
        ("inspect_tests", {"path": "", "max_files": 51}),
    ],
)
def test_structured_tools_reject_invalid_requests(
    tmp_path: Path, tool: str, payload: dict[str, object]
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, payload, model_turn=1)
    assert captured.value.code in {
        "context_path_rejected",
        "context_request_invalid",
        "context_tool_limit_exceeded",
    }


def test_structured_tools_reject_symlinks(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"name":"outside"}', encoding="utf-8")
    (fixture["root"] / "package.json").symlink_to(outside)
    _refresh_fixture_snapshot(fixture)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_manifest", {"path": "package.json"}, model_turn=1
        )
    assert captured.value.code == "context_path_rejected"


def test_all_structured_tools_leave_target_bytes_and_status_unchanged(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "package.json").write_text('{"name":"orders"}\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_orders.py").write_text("pass\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)
    before_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v2", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    before_bytes = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }

    fixture["provider"].call(
        "inspect_manifest", {"path": "package.json"}, model_turn=1
    )
    fixture["provider"].call(
        "inspect_tests", {"path": "", "max_files": 20}, model_turn=1
    )
    fixture["provider"].call(
        "inspect_git_history", {"path": "", "limit": 20}, model_turn=1
    )
    fixture["provider"].call(
        "inspect_recent_changes", {"path": "", "limit": 20}, model_turn=1
    )

    after_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v2", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    after_bytes = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    assert after_bytes == before_bytes
    assert after_status == before_status


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
    assert records[1]["request"] == {"path": "", "max_entries": 10}


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

    assert captured.value.code == "context_tool_limit_exceeded"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    assert record["response_metadata"] == {"error": {"code": "context_tool_limit_exceeded"}}


@pytest.mark.parametrize("payload", [{"path": "", "extra": True}])
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

def test_read_excerpt_returns_lines_and_redacts_secrets(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    src = fixture["root"] / "src"
    src.mkdir()
    (src / "settings.py").write_text(
        "\n".join(
            [
                "DEBUG = False",
                'API_KEY = "sk-live-secret"',
                "SERVICE = 'payments'",
                "ENABLED = True",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "read_file_excerpt",
        {"path": "src/settings.py", "start_line": 2, "line_count": 3},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["truncated"] is False
    assert len(response["observations"]) == 1
    observation = response["observations"][0]
    assert observation["path"] == "src/settings.py"
    assert observation["start_line"] == 2
    assert observation["end_line"] == 4
    assert observation["content_hash"].startswith("sha256:")
    assert "sk-live-secret" not in observation["text"]
    assert "redacted" in observation["text"]

    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "sk-live-secret" not in encoded


def test_list_tree_is_bounded_and_excludes_generated_content(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "docs").mkdir()
    (fixture["root"] / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (fixture["root"] / "src" / "nested").mkdir()
    (fixture["root"] / "src" / "nested" / "more.py").write_text("pass\n", encoding="utf-8")
    (fixture["root"] / "node_modules").mkdir()
    (fixture["root"] / "node_modules" / "left-pad.js").write_text("module.exports = 1;\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "list_tree",
        {"path": "", "depth": 2, "max_entries": 3},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["truncated"] is True
    assert len(response["observations"]) == 3
    assert all(
        not item["path"].startswith("node_modules")
        for item in response["observations"]
    )


def test_list_tree_skips_sensitive_entries_without_leaking_names(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (root / ".env").write_text("topsecret\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "credentials.dev").write_text("topsecret\n", encoding="utf-8")
    (root / "nested" / "secrets.local").write_text("topsecret\n", encoding="utf-8")
    (root / "nested" / "id_ed25519").write_text("topsecret\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "list_tree",
        {"path": "", "depth": 3, "max_entries": 10},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["truncated"] is True
    returned_paths = [item["path"] for item in response["observations"]]
    assert ".env" not in returned_paths
    assert "nested/credentials.dev" not in returned_paths
    assert "nested/secrets.local" not in returned_paths
    assert "nested/id_ed25519" not in returned_paths
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    for forbidden in (".env", "credentials.dev", "secrets.local", "id_ed25519"):
        assert forbidden not in encoded


def test_literal_search_returns_bounded_matches(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    src = fixture["root"] / "src"
    src.mkdir()
    for index in range(7):
        (src / f"service_{index}.py").write_text(
            "\n".join(
                [
                    "def noop():",
                    "    return None",
                    f"def create_order_{index}():",
                    "    return 'ok'",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "search_text",
        {"path": "src", "query": "create_order", "mode": "literal", "max_matches": 5},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert len(response["observations"]) <= 5
    assert all(
        {"path", "line", "text", "content_hash"} <= set(item)
        for item in response["observations"]
    )


def test_literal_search_skips_sensitive_files_during_broad_search(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "docs").mkdir()
    (root / "docs" / "public.txt").write_text("SAFE_MATCH\n", encoding="utf-8")
    (root / ".env").write_text("SAFE_MATCH\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "credentials.json").write_text("SAFE_MATCH\n", encoding="utf-8")
    (root / "nested" / "secrets.prod").write_text("SAFE_MATCH\n", encoding="utf-8")
    (root / "nested" / "id_rsa").write_text("SAFE_MATCH\n", encoding="utf-8")
    (root / "nested" / "tls.pem").write_text("SAFE_MATCH\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "SAFE_MATCH", "mode": "literal", "max_matches": 10},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["truncated"] is True
    assert [item["path"] for item in response["observations"]] == ["docs/public.txt"]
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["budget"]["files_observed"] == 1
    assert record["budget"]["observed_file_chars"] == {"docs/public.txt": 10}
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    for forbidden in (
        ".env",
        "credentials.json",
        "secrets.prod",
        "id_rsa",
        "tls.pem",
    ):
        assert forbidden not in encoded


def test_literal_and_regex_matches_share_exact_line_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    raw_line = "create_order(token)\n"
    (fixture["root"] / "src" / "orders.py").write_text(
        "before\n" + raw_line + "after\n",
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    payload = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": "src/orders.py"},
                "line_number": 2,
                "lines": {"text": raw_line},
            },
        }
    ).encode("utf-8") + b"\n"
    fake, release, writer, done = _make_pipe_process([("stdout", payload, 0.0)])

    monkeypatch.setattr(
        content_tools,
        "_spawn_rg_process",
        lambda **kwargs: fake,
    )

    literal = fixture["provider"].call(
        "search_text",
        {"path": "src", "query": "create_order", "mode": "literal", "max_matches": 5},
        model_turn=1,
    )["observations"][0]
    release.set()
    regex = fixture["provider"].call(
        "search_text",
        {"path": "src", "query": "create_order", "mode": "regex", "max_matches": 5},
        model_turn=2,
    )["observations"][0]
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)

    for observation in (literal, regex):
        assert {"path", "start_line", "end_line", "text", "content_hash", "redaction_applied"} <= set(observation)
        assert observation["path"] == "src/orders.py"
        assert (observation["start_line"], observation["end_line"]) == (2, 2)
    assert literal["content_hash"] == regex["content_hash"]


def test_excerpt_rejects_invalid_line_range(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "settings.py").write_text("one\ntwo\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "read_file_excerpt",
            {"path": "src/settings.py", "start_line": 2, "line_count": 3},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_limit_exceeded"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["response_metadata"] == {
        "error": {"code": "context_tool_limit_exceeded"}
    }


@pytest.mark.parametrize("query", ["", "x" * 257])
def test_search_rejects_empty_or_oversized_query(tmp_path: Path, query: str) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "app.py").write_text("create_order()\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "src", "query": query, "mode": "literal", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_limit_exceeded"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["response_metadata"] == {
        "error": {"code": "context_tool_limit_exceeded"}
    }


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        (
            "read_file_excerpt",
            {"path": "src/app.py", "start_line": 1, "line_count": 1, "extra": True},
        ),
        (
            "search_text",
            {
                "path": "src",
                "query": "alpha",
                "mode": "literal",
                "max_matches": 5,
                "extra": True,
            },
        ),
    ],
)
def test_content_tools_reject_unknown_request_keys(
    tmp_path: Path,
    tool: str,
    payload: dict[str, object],
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, payload, model_turn=1)

    assert captured.value.code == "context_tool_limit_exceeded"


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("list_tree", {"path": "", "depth": True, "max_entries": 5}),
        ("read_file_excerpt", {"path": "src/app.py", "start_line": 1, "line_count": True}),
        ("search_text", {"path": "src", "query": "alpha", "mode": "literal", "max_matches": True}),
    ],
)
def test_content_tool_bounds_reject_bool_values(
    tmp_path: Path,
    tool: str,
    payload: dict[str, object],
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, payload, model_turn=1)

    assert captured.value.code == "context_tool_limit_exceeded"


def test_tools_reject_sensitive_file_names(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / ".env").write_text(
        "OPENAI_API_KEY=sk-live-secret\n",
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "read_file_excerpt",
            {"path": ".env", "start_line": 1, "line_count": 1},
            model_turn=1,
        )

    assert captured.value.code == "context_path_rejected"
    assert captured.value.details == {
        "path": "<redacted>/.env",
        "reason": "sensitive_path",
    }
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert record["response_metadata"] == {
        "error": {"code": "context_path_rejected"}
    }
    assert "sk-live-secret" not in encoded
    assert '".env"' not in encoded


def test_regex_search_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    fake_process, release, writer, done = _make_pipe_process(
        [("stdout", b"", 2.4)],
        final_returncode=0,
    )

    def fake_spawn_rg_process(**kwargs):
        del kwargs
        return fake_process

    monkeypatch.setattr(
        content_tools,
        "_spawn_rg_process",
        fake_spawn_rg_process,
    )

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_timeout"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    assert fake_process.terminated is True
    assert fake_process.waited is True
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["response_metadata"] == {
        "error": {"code": "context_tool_timeout"}
    }


def test_regex_search_returns_unsupported_when_rg_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    def missing_rg(**kwargs):
        del kwargs
        raise FileNotFoundError("rg")

    monkeypatch.setattr(content_tools, "_spawn_rg_process", missing_rg)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_unsupported"
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["response_metadata"] == {
        "error": {"code": "context_tool_unsupported"}
    }


@pytest.mark.parametrize(
    "path_bypass",
    ["relative", "target_absolute", "directory_symlink", "hardlink"],
)
def test_regex_search_never_executes_target_controlled_path_rg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_bypass: str,
) -> None:
    fixture = _provider_fixture(tmp_path)
    root = fixture["root"]
    target_bin = root / "bin"
    target_bin.mkdir()
    marker = tmp_path / f"{path_bypass}-rg-executed"
    target_rg = target_bin / "rg"
    target_rg.write_text(f"#!/bin/sh\n: > {marker}\nexit 0\n", encoding="utf-8")
    target_rg.chmod(0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    if path_bypass == "relative":
        (root / "rg").write_bytes(target_rg.read_bytes())
        (root / "rg").chmod(0o755)
        path_value = "."
    elif path_bypass == "target_absolute":
        path_value = str(target_bin)
    elif path_bypass == "directory_symlink":
        linked = outside / "linked-bin"
        linked.symlink_to(target_bin, target_is_directory=True)
        path_value = str(linked)
    else:
        alias_bin = outside / "alias-bin"
        alias_bin.mkdir()
        os.link(target_rg, alias_bin / "rg")
        path_value = str(alias_bin)
    monkeypatch.setenv("PATH", path_value)
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "alpha", "mode": "regex", "max_matches": 5},
        model_turn=1,
    )

    assert result["status"] == "ok"
    assert not marker.exists()


def test_regex_search_rejects_parent_escape_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("sk-live-secret\n", encoding="utf-8")

    called = False

    def fail_if_spawned(*, root, query: str, target: str):
        nonlocal called
        del root, query, target
        called = True
        raise AssertionError("rg must not spawn for escaped paths")

    monkeypatch.setattr(content_tools, "_spawn_rg_process", fail_if_spawned)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "../outside", "query": "secret", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_path_rejected"
    assert called is False
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert record["response_metadata"] == {"error": {"code": "context_path_rejected"}}
    assert "sk-live-secret" not in encoded
    assert "../outside" not in encoded


def test_regex_search_stops_streaming_at_match_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    fake, release, writer, done = _make_pipe_process(
        [
            (
                "stdout",
                json.dumps(
                    {
                        "type": "match",
                        "data": {
                            "path": {"text": "README.md"},
                            "line_number": 1,
                            "lines": {"text": "alpha\n"},
                        },
                    }
                ).encode("utf-8")
                + b"\n",
                0.0,
            ),
            (
                "stdout",
                json.dumps(
                    {
                        "type": "match",
                        "data": {
                            "path": {"text": "README.md"},
                            "line_number": 2,
                            "lines": {"text": "beta\n"},
                        },
                    }
                ).encode("utf-8")
                + b"\n",
                0.0,
            ),
        ]
    )
    monkeypatch.setattr(
        content_tools,
        "_spawn_rg_process",
        lambda **kwargs: fake,
    )

    release.set()
    response = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "a", "mode": "regex", "max_matches": 1},
        model_turn=1,
    )

    assert response["truncated"] is True
    assert len(response["observations"]) == 1
    assert fake.terminated is True
    assert fake.waited is True
    assert fake.killed is False
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)


def test_regex_stream_timeout_terminates_and_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    now = {"value": 100.0}
    fake, release, writer, done = _make_pipe_process(
        [
            (
                "stdout",
                json.dumps(
                    {
                        "type": "match",
                        "data": {
                            "path": {"text": "README.md"},
                            "line_number": 1,
                            "lines": {"text": "alpha\n"},
                        },
                    }
                ).encode("utf-8")
                + b"\n",
                0.0,
            )
        ]
    )
    original_select = content_tools.selectors.DefaultSelector.select

    def fake_select(self, timeout=None):
        now["value"] = 102.5
        return original_select(self, timeout)

    monkeypatch.setattr(content_tools.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(content_tools.selectors.DefaultSelector, "select", fake_select)
    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_timeout"
    assert fake.terminated is True
    assert fake.waited is True
    assert fake.killed is False
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "alpha" not in encoded


def test_regex_real_pipe_enforces_deadline_while_waiting_for_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    fake, release, writer, done = _make_pipe_process(
        [
            (
                "stdout",
                (
                    json.dumps(
                        {
                            "type": "match",
                            "data": {
                                "path": {"text": "README.md"},
                                "line_number": 1,
                                "lines": {"text": "alpha\n"},
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8"),
                2.4,
            )
        ]
    )
    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    started = time.monotonic()
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )
    elapsed = time.monotonic() - started
    writer.join(timeout=5.0)

    assert captured.value.code == "context_tool_timeout"
    assert elapsed < 2.3
    assert fake.terminated is True
    assert fake.waited is True
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)


def test_regex_worker_never_spawns_external_process_and_omits_sensitive_paths(
    tmp_path: Path,
) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / ".env").write_text("MATCH_ME=secret\n", encoding="utf-8")
    (fixture["root"] / "visible.txt").write_text("MATCH_ME=visible\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "MATCH_ME", "mode": "regex", "max_matches": 5},
        model_turn=1,
    )

    assert [item["path"] for item in result["observations"]] == ["visible.txt"]


def test_regex_root_search_normalizes_dot_slash_paths_with_internal_worker(
    tmp_path: Path,
) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "ascii.txt").write_text("ROOT_MATCH\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "ROOT_MATCH", "mode": "regex", "max_matches": 5},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["truncated"] is False
    assert len(response["observations"]) == 1
    observation = response["observations"][0]
    assert observation["path"] == "ascii.txt"
    assert observation["start_line"] == 1
    assert observation["end_line"] == 1
    assert observation["text"] == "ROOT_MATCH"
    assert observation["content_hash"].startswith("sha256:")
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert '"path":"./ascii.txt"' not in encoded


def test_regex_search_accepts_path_and_line_bytes_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import goodteam_os._repository_content_tools as content_tools
    from goodteam_os._repository_content_policy import hash_bytes

    fixture = _provider_fixture(tmp_path)
    raw_line = b"BYTE_MATCH\n"
    payload = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"bytes": base64.b64encode(b"./ascii.txt").decode("ascii")},
                "lines": {"bytes": base64.b64encode(raw_line).decode("ascii")},
                "line_number": 1,
                "submatches": [
                    {
                        "match": {
                            "bytes": base64.b64encode(b"BYTE_MATCH").decode("ascii")
                        },
                        "start": 0,
                        "end": 10,
                    }
                ],
            },
        }
    ).encode("utf-8") + b"\n"
    fake, release, writer, done = _make_pipe_process([("stdout", payload, 0.0)])
    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    response = fixture["provider"].call(
        "search_text",
        {"path": "", "query": "BYTE_MATCH", "mode": "regex", "max_matches": 5},
        model_turn=1,
    )

    assert response["status"] == "ok"
    assert response["observations"] == [
        {
            "kind": "search_match",
            "path": "ascii.txt",
            "line": 1,
            "start_line": 1,
            "end_line": 1,
            "text": "BYTE_MATCH",
            "content_hash": hash_bytes(raw_line),
            "redaction_applied": False,
            "observation_id": response["observations"][0]["observation_id"],
        }
    ]
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)


def test_regex_search_rejects_invalid_base64_payload_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    payload = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"bytes": "%%%not-base64%%%"},
                "lines": {"bytes": "%%%still-bad%%%"},
                "line_number": 1,
            },
        }
    ).encode("utf-8") + b"\n"
    fake, release, writer, done = _make_pipe_process([("stdout", payload, 0.0)])
    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "BYTE_MATCH", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_invalid_output"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "%%%not-base64%%%" not in encoded
    assert "%%%still-bad%%%" not in encoded


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("success", None),
        ("match_limit", None),
        ("timeout", "context_tool_timeout"),
        ("stdout_overflow", "context_tool_limit_exceeded"),
        ("stderr_overflow", "context_tool_limit_exceeded"),
        ("invalid_base64", "context_tool_invalid_output"),
        ("malformed_json", "context_tool_invalid_output"),
        ("nonzero_exit", "context_tool_failed"),
    ],
)
def test_regex_selector_is_closed_once_on_every_terminal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_code: str | None,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    output_limit = max(content_tools.FILE_READ_MAX_BYTES, 80 * 4)
    if case == "success":
        payload = json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "README.md"},
                    "lines": {"text": "alpha\n"},
                    "line_number": 1,
                },
            }
        ).encode("utf-8") + b"\n"
        events = [("stdout", payload, 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "match_limit":
        first = json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "README.md"},
                    "lines": {"text": "alpha\n"},
                    "line_number": 1,
                },
            }
        ).encode("utf-8") + b"\n"
        second = json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "README.md"},
                    "lines": {"text": "beta\n"},
                    "line_number": 2,
                },
            }
        ).encode("utf-8") + b"\n"
        events = [("stdout", first, 0.0), ("stdout", second, 0.0)]
        query = "a"
        max_matches = 1
        final_returncode = 0
    elif case == "timeout":
        events = [("stdout", b"", 2.4)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "stdout_overflow":
        events = [("stdout", b"x" * (output_limit + 1), 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "stderr_overflow":
        events = [("stderr", b"e" * (output_limit + 1), 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "invalid_base64":
        payload = json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"bytes": "%%%bad%%%"},
                    "lines": {"text": "alpha\n"},
                    "line_number": 1,
                },
            }
        ).encode("utf-8") + b"\n"
        events = [("stdout", payload, 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "malformed_json":
        events = [("stdout", b'{"type":"match"\n', 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 0
    elif case == "nonzero_exit":
        events = [("stderr", b"fatal\n", 0.0)]
        query = "alpha"
        max_matches = 5
        final_returncode = 2
    else:
        raise AssertionError(case)

    fake, release, writer, done = _make_pipe_process(events, final_returncode=final_returncode)
    TrackingSelector, created = _tracking_selector_factory(content_tools.selectors.DefaultSelector)
    monkeypatch.setattr(content_tools.selectors, "DefaultSelector", TrackingSelector)
    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    if expected_code is None:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": query, "mode": "regex", "max_matches": max_matches},
            model_turn=1,
        )
    else:
        with pytest.raises(ContextProviderError) as captured:
            fixture["provider"].call(
                "search_text",
                {"path": "", "query": query, "mode": "regex", "max_matches": max_matches},
                model_turn=1,
            )
        assert captured.value.code == expected_code

    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    assert len(created) == 1
    assert created[0].close_calls == 1
    assert fake.waited is True
    assert fake.stdout.closed is True
    assert fake.stderr.closed is True


def test_regex_search_rejects_malformed_json_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    fake, release, writer, done = _make_pipe_process(
        [
            ("stdout", b'{"type":"match","data":{"path":{"text":"README.md"}}\n', 0.0),
            ("stderr", b"raw stderr secret", 0.0),
        ]
    )

    monkeypatch.setattr(
        content_tools,
        "_spawn_rg_process",
        lambda **kwargs: fake,
    )

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "alpha", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_invalid_output"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "raw stderr secret" not in encoded


def test_regex_search_rejects_emitted_escape_path_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    payload = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": "../outside/secret.txt"},
                "line_number": 1,
                "lines": {"text": "sk-live-secret-value\n"},
            },
        }
    ).encode("utf-8") + b"\n"
    fake, release, writer, done = _make_pipe_process(
        [("stdout", payload, 0.0), ("stderr", b"stderr secret", 0.0)]
    )

    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_invalid_output"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "../outside/secret.txt" not in encoded
    assert "sk-live-secret-value" not in encoded
    assert "stderr secret" not in encoded


def test_regex_search_reports_nonzero_exit_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    fake, release, writer, done = _make_pipe_process(
        [("stderr", b"fatal secret output", 0.0)],
        final_returncode=2,
    )

    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_failed"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "fatal secret output" not in encoded


def test_regex_search_overflow_is_bounded_and_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    oversized = b"x" * (content_tools.FILE_READ_MAX_BYTES + 1)
    fake, release, writer, done = _make_pipe_process(
        [("stdout", oversized, 0.0), ("stderr", b"stderr secret", 0.0)]
    )

    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_limit_exceeded"
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "stderr secret" not in encoded
    assert ("x" * 64) not in encoded


def test_regex_search_stderr_overflow_is_bounded_and_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    oversized = b"e" * (content_tools.FILE_READ_MAX_BYTES + 1)
    fake, release, writer, done = _make_pipe_process([("stderr", oversized, 0.0)])

    monkeypatch.setattr(content_tools, "_spawn_rg_process", lambda **kwargs: fake)

    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "search_text",
            {"path": "", "query": "token", "mode": "regex", "max_matches": 5},
            model_turn=1,
        )

    assert captured.value.code == "context_tool_limit_exceeded"
    assert fake.terminated is True
    assert fake.waited is True
    assert done.wait(timeout=5.0)
    writer.join(timeout=5.0)
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert ("e" * 64) not in encoded


def test_literal_search_respects_prework_scan_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    calls: dict[str, object] = {"walk_max_entries": None, "read_count": 0}

    class FakeText:
        def __init__(self, path: str) -> None:
            self.path = path
            self.text = "no match here\n"
            self.truncated = False

    class FakeEntry:
        def __init__(self, path: str) -> None:
            self.kind = "file"
            self.path = path

    class FakeRepository:
        def __init__(self, root, *, expected_identity=None):
            del root, expected_identity

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        def walk(self, path="", *, ignored_directories=None, max_entries=None, max_depth=None):
            del path, ignored_directories, max_depth
            calls["walk_max_entries"] = max_entries
            return [FakeEntry(f"src/file_{index}.py") for index in range(100)]

        def read_text(self, path: str, *, max_bytes: int):
            del max_bytes
            calls["read_count"] = int(calls["read_count"]) + 1
            return FakeText(path)

        def verify_root(self) -> None:
            return None

    monkeypatch.setattr(content_tools, "AnchoredRepository", FakeRepository)
    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=OnboardingBudget(
            requested_limits={
                "max_single_result_chars": 80,
                "max_total_observation_chars": 80,
            }
        ),
    )

    response = provider.call(
        "search_text",
        {"path": "src", "query": "create_order", "mode": "literal", "max_matches": 5},
        model_turn=1,
    )

    assert response["observations"] == []
    assert calls["walk_max_entries"] is not None
    assert int(calls["walk_max_entries"]) < 100
    assert int(calls["read_count"]) < 100


def test_literal_search_caps_per_file_read_bytes_from_tight_allowance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os._repository_content_policy import FILE_READ_MAX_BYTES
    from goodteam_os.repository_access import RepositoryText
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)
    calls: dict[str, object] = {"max_bytes": [], "read_count": 0}

    class FakeEntry:
        def __init__(self, path: str) -> None:
            self.kind = "file"
            self.path = path

    class FakeRepository:
        def __init__(self, root, *, expected_identity=None):
            del root, expected_identity

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        def walk(self, path="", *, ignored_directories=None, max_entries=None, max_depth=None):
            del path, ignored_directories, max_depth, max_entries
            return [FakeEntry("src/huge.txt")]

        def read_text(self, path: str, *, max_bytes: int):
            calls["read_count"] = int(calls["read_count"]) + 1
            cast_list = calls["max_bytes"]
            assert isinstance(cast_list, list)
            cast_list.append(max_bytes)
            text = "x" * max_bytes
            return RepositoryText(
                path=path,
                text=text,
                content_hash="sha256:" + ("0" * 64),
                size=FILE_READ_MAX_BYTES * 2,
                start_line=1,
                end_line=1,
                truncated=True,
            )

        def verify_root(self) -> None:
            return None

    monkeypatch.setattr(content_tools, "AnchoredRepository", FakeRepository)
    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=OnboardingBudget(
            requested_limits={
                "max_single_result_chars": 80,
                "max_total_observation_chars": 80,
            }
        ),
    )

    response = provider.call(
        "search_text",
        {"path": "src", "query": "create_order", "mode": "literal", "max_matches": 5},
        model_turn=1,
    )

    assert response["observations"] == []
    assert response["truncated"] is True
    assert calls["read_count"] == 1
    assert calls["max_bytes"] == [320]
    assert calls["max_bytes"][0] < FILE_READ_MAX_BYTES


def test_literal_search_reports_truncation_when_walk_cap_is_spent_on_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider
    import goodteam_os._repository_content_tools as content_tools

    fixture = _provider_fixture(tmp_path)

    class FakeEntry:
        def __init__(self, path: str, kind: str) -> None:
            self.path = path
            self.kind = kind

    class FakeRepository:
        def __init__(self, root, *, expected_identity=None):
            del root, expected_identity

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        def walk(self, path="", *, ignored_directories=None, max_entries=None, max_depth=None):
            del path, ignored_directories, max_depth, max_entries
            entries = [FakeEntry(f"dir_{index}", "directory") for index in range(20)]
            entries.append(FakeEntry("dir_19/deep.txt", "file"))
            return entries

        def read_text(self, path: str, *, max_bytes: int):
            raise AssertionError(f"deep file should not be reached: {path} {max_bytes}")

        def verify_root(self) -> None:
            return None

    monkeypatch.setattr(content_tools, "AnchoredRepository", FakeRepository)
    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=OnboardingBudget(
            requested_limits={
                "max_single_result_chars": 80,
                "max_total_observation_chars": 80,
            }
        ),
    )

    response = provider.call(
        "search_text",
        {"path": "", "query": "DEEP_MATCH", "mode": "literal", "max_matches": 5},
        model_turn=1,
    )

    assert response["observations"] == []
    assert response["truncated"] is True


def test_tools_reject_symlink_and_binary_files(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "real.txt").write_text("alpha\n", encoding="utf-8")
    (fixture["root"] / "src" / "linked.txt").symlink_to(fixture["root"] / "src" / "real.txt")
    (fixture["root"] / "src" / "data.bin").write_bytes(b"alpha\x00beta")
    _refresh_fixture_snapshot(fixture)

    with pytest.raises(ContextProviderError) as symlink_error:
        fixture["provider"].call(
            "read_file_excerpt",
            {"path": "src/linked.txt", "start_line": 1, "line_count": 1},
            model_turn=1,
        )
    assert symlink_error.value.code == "context_path_rejected"

    with pytest.raises(ContextProviderError) as binary_error:
        fixture["provider"].call(
            "search_text",
            {"path": "src", "query": "alpha", "mode": "literal", "max_matches": 5},
            model_turn=2,
        )
    assert binary_error.value.code == "repository_binary_file"

def test_result_is_truncated_to_allowance(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "big.txt").write_text(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda\n",
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)
    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=OnboardingBudget(
            requested_limits={
                "max_single_result_chars": 140,
                "max_total_observation_chars": 140,
            }
        ),
    )

    response = provider.call(
        "read_file_excerpt",
        {"path": "src/big.txt", "start_line": 1, "line_count": 1},
        model_turn=1,
    )

    assert response["truncated"] is True
    assert len(response["observations"][0]["text"]) < len(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda\n"
    )


def test_repeated_excerpt_exhausts_per_file_budget(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)
    provider = _rebuild_provider(
        fixture,
        RepositoryContextProvider,
        budget=OnboardingBudget(
            requested_limits={
                "max_file_cumulative_chars": 12,
                "max_single_result_chars": 200,
                "max_total_observation_chars": 200,
            }
        ),
    )

    provider.call(
        "read_file_excerpt",
        {"path": "src/notes.txt", "start_line": 1, "line_count": 1},
        model_turn=1,
    )
    provider.call(
        "read_file_excerpt",
        {"path": "src/notes.txt", "start_line": 2, "line_count": 1},
        model_turn=2,
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call(
            "read_file_excerpt",
            {"path": "src/notes.txt", "start_line": 3, "line_count": 1},
            model_turn=3,
        )

    assert captured.value.code == "onboarding_budget_exceeded"


def test_authorization_and_bare_tokens_are_redacted(tmp_path: Path) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "config.txt").write_text(
        "\n".join(
            [
                "Authorization: Bearer top-secret-value",
                "password=super-secret",
                "ghp_12345678901234567890",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "read_file_excerpt",
        {"path": "src/config.txt", "start_line": 1, "line_count": 3},
        model_turn=1,
    )

    text = response["observations"][0]["text"]
    assert "top-secret-value" not in text
    assert "super-secret" not in text
    assert "ghp_12345678901234567890" not in text
    assert "<redacted>" in text or "<redacted-token>" in text
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "top-secret-value" not in encoded
    assert "super-secret" not in encoded
    assert "ghp_12345678901234567890" not in encoded


def test_redactor_scope_preserves_plain_token_and_hyphenated_sk_live_but_redacts_allowed_shapes(
    tmp_path: Path,
) -> None:
    fixture = _provider_fixture(tmp_path)
    (fixture["root"] / "src").mkdir()
    (fixture["root"] / "src" / "tokens.txt").write_text(
        "\n".join(
            [
                "token=harmless-value",
                "access_token=top-secret",
                "hyphenated=sk-live-abc",
                "openai=sk-abcdefghijkl",
                "stripe=sk_live_abcdefgh",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _refresh_fixture_snapshot(fixture)

    response = fixture["provider"].call(
        "read_file_excerpt",
        {"path": "src/tokens.txt", "start_line": 1, "line_count": 5},
        model_turn=1,
    )

    text = response["observations"][0]["text"]
    assert "token=harmless-value" in text
    assert "access_token=top-secret" not in text
    assert "sk-live-abc" in text
    assert "sk-abcdefghijkl" not in text
    assert "sk_live_abcdefgh" not in text
    assert "<redacted-token>" in text
    assert "access_token=<redacted>" in text


def test_structured_values_are_recursively_redacted(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)

    class StructuredProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            return {
                "observations": [
                    {
                        "kind": "structured",
                        "path": "src/config.py",
                        "details": {
                            "token": "ghp_12345678901234567890",
                            "headers": ["Authorization: Bearer nested-secret"],
                        },
                    }
                ],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, StructuredProvider)
    response = provider.call("list_tree", {}, model_turn=1)

    details = response["observations"][0]["details"]
    assert details["token"] == "<redacted-token>"
    assert details["headers"] == ["Authorization: <redacted>"]
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "nested-secret" not in encoded
    assert "ghp_12345678901234567890" not in encoded


def test_returned_and_persisted_redaction_are_equal(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import RepositoryContextProvider

    fixture = _provider_fixture(tmp_path)

    class StructuredProvider(RepositoryContextProvider):
        def _handle_list_tree(self, _request: dict[str, object]) -> dict[str, object]:
            return {
                "observations": [
                    {
                        "kind": "structured",
                        "path": "src/config.py",
                        "details": {"password": "super-secret"},
                    }
                ],
                "truncated": False,
            }

    provider = _rebuild_provider(fixture, StructuredProvider)
    response = provider.call("list_tree", {"authorization": "Bearer top-secret"}, model_turn=1)

    assert response == _last_record_dict(fixture["workspace"], fixture["project_key"])
    encoded = _last_record_bytes(fixture["workspace"], fixture["project_key"]).decode(
        "utf-8"
    )
    assert "top-secret" not in encoded
    assert "super-secret" not in encoded


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

def test_git_history_uses_read_only_argv(tmp_path: Path) -> None:
    import subprocess

    recorded = []
    real_popen = subprocess.Popen

    def recording_process(command, **kwargs):
        recorded.append((command, kwargs))
        return real_popen(command, **kwargs)

    fixture = _git_provider_fixture(tmp_path, process_factory=recording_process)
    result = fixture["provider"].call(
        "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
    )

    assert len(result["observations"]) <= 5
    forbidden = {"add", "commit", "checkout", "reset", "clean"}
    assert recorded and all(command[0][0] == "git" for command in recorded)
    assert not any(word in forbidden for command, _ in recorded for word in command)
    assert all(kwargs["shell"] is False for _, kwargs in recorded)
    assert all("--" in command for command, _ in recorded)
    assert all("core.fsmonitor=false" in command for command, _ in recorded)


def test_non_git_inspection_is_unsupported(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)
    for tool, request in (
        ("inspect_git_history", {"path": "", "limit": 5}),
        ("inspect_recent_changes", {"path": "", "limit": 5}),
    ):
        with pytest.raises(ContextProviderError) as captured:
            fixture["provider"].call(tool, request, model_turn=1)
        assert captured.value.code == "context_tool_unsupported"


def test_git_history_is_path_scoped(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "src").mkdir()
    (root / "src" / "orders.py").write_text("orders = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "src/orders.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "src change"], check=True)
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "docs/guide.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "docs change"], check=True)
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_git_history", {"path": "src", "limit": 10}, model_turn=1
    )

    assert [item["message"] for item in result["observations"]] == ["src change"]
    assert all(item["commit_sha"] for item in result["observations"])


def test_git_pathspec_magic_is_treated_as_literal_path(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    magic_path = ":(glob)*"
    (root / magic_path).write_text("literal target\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", magic_path], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "literal magic target"], check=True)
    (root / "outside.txt").write_text("outside tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "outside.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "outside tracked"], check=True)
    (root / ".env").write_text("TOKEN=tracked-secret\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".env"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "sensitive tracked"], check=True)
    (root / magic_path).write_text("literal target changed\n", encoding="utf-8")
    (root / "outside.txt").write_text("outside changed\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=changed-secret\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    history = fixture["provider"].call(
        "inspect_git_history", {"path": magic_path, "limit": 10}, model_turn=1
    )
    changes = fixture["provider"].call(
        "inspect_recent_changes", {"path": magic_path, "limit": 10}, model_turn=2
    )

    assert [item["message"] for item in history["observations"]] == [
        "literal magic target"
    ]
    assert [item["path"] for item in changes["observations"]] == [magic_path]
    rendered = json.dumps([history, changes])
    assert "outside.txt" not in rendered
    assert ".env" not in rendered
    assert "tracked-secret" not in rendered
    assert "changed-secret" not in rendered


def test_git_literal_path_variants_continue_to_work(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "dir with spaces").mkdir()
    variants = {
        "dir with spaces/note file.txt": "spaces",
        "unicodé.txt": "unicode",
        ":(glob)*": "colon",
    }
    for path, marker in variants.items():
        (root / path).write_text(f"{marker}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--", path], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", f"add {marker}"], check=True)
    _refresh_fixture_snapshot(fixture)

    root_history = fixture["provider"].call(
        "inspect_git_history", {"path": "", "limit": 10}, model_turn=1
    )
    for turn, (path, marker) in enumerate(variants.items(), start=2):
        history = fixture["provider"].call(
            "inspect_git_history", {"path": path, "limit": 10}, model_turn=turn
        )
        assert [item["message"] for item in history["observations"]] == [f"add {marker}"]
    assert len(root_history["observations"]) == 4


def test_recent_changes_covers_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "staged.py").write_text("staged = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "staged.py"], check=True)
    (root / "README.md").write_text("unstaged = True\n", encoding="utf-8")
    (root / "untracked.py").write_text("untracked = True\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_recent_changes", {"path": "", "limit": 10}, model_turn=1
    )

    changes = {(item["path"], item["change_scope"]) for item in result["observations"]}
    assert ("staged.py", "staged") in changes
    assert ("README.md", "unstaged") in changes
    assert ("untracked.py", "untracked") in changes
    assert all(item["diff_hash"].startswith("sha256:") for item in result["observations"])


@pytest.mark.parametrize(
    ("path", "payload", "expected_kind"),
    [
        ("non-utf8.bin", b"alpha\xffbeta", "non_utf8"),
        ("binary.bin", b"alpha\x00beta", "binary"),
    ],
)
def test_recent_changes_handles_untracked_non_text_bytes_without_leaking(
    tmp_path: Path,
    path: str,
    payload: bytes,
    expected_kind: str,
) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    before_files = {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }
    before_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    (root / path).write_bytes(payload)
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_recent_changes", {"path": path, "limit": 10}, model_turn=1
    )

    assert [item["path"] for item in result["observations"]] == [path]
    observation = result["observations"][0]
    assert observation["change_scope"] == "untracked"
    assert observation["diff_content_kind"] == expected_kind
    assert observation["diff_truncated"] is False
    assert observation["diff_hash"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    rendered = json.dumps(result)
    assert "alpha" not in rendered
    assert "\\u0000" not in rendered
    assert "\\xff" not in rendered
    assert payload.decode("latin1") not in rendered
    after_files = {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }
    assert after_files == {**before_files, path: payload}
    after_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    assert after_status == before_status + f"?? {path}\0".encode("utf-8")


def test_recent_changes_reports_exact_bounded_hash_for_long_untracked_text(
    tmp_path: Path,
) -> None:
    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    path = "long.txt"
    payload = ("é" * 10000).encode("utf-8")
    (root / path).write_bytes(payload)
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_recent_changes", {"path": path, "limit": 10}, model_turn=1
    )

    observation = result["observations"][0]
    expected_prefix = payload[: fixture["budget"].limits.max_single_result_chars]
    assert observation["diff_content_kind"] == "text"
    assert observation["diff_truncated"] is True
    assert observation["diff_hash"] == "sha256:" + hashlib.sha256(expected_prefix).hexdigest()
    assert observation["diff"] == expected_prefix.decode("utf-8")
    assert observation["diff"] != payload.decode("utf-8")


def test_git_output_is_bounded(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    process, release, writer_thread, writer_done = _make_pipe_process(
        [("stdout", b"x" * 1024, 0.0)]
    )
    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(requested_limits={"max_single_result_chars": 128}),
        process_factory=lambda *_args, **_kwargs: (release.set() or process),
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call("inspect_git_history", {"path": "", "limit": 5}, model_turn=1)

    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_limit_exceeded"
    assert process.terminated and process.waited and writer_done.is_set()
    assert process.stdout.closed and process.stderr.closed


@pytest.mark.parametrize("since_sha", [True, 123, "abc123", "g" * 40, "a" * 41])
def test_invalid_revision_is_rejected(tmp_path: Path, since_sha: object) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _git_provider_fixture(tmp_path)
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_recent_changes",
            {"path": "", "limit": 5, "since_sha": since_sha},
            model_turn=1,
        )
    assert captured.value.code == "context_request_invalid"


def test_git_process_is_terminated_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import goodteam_os._repository_git_tools as git_tools
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stdout", b"late", 1.0)]
    )
    monkeypatch.setattr(git_tools, "_GIT_TIMEOUT_SECONDS", 0.01)
    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(),
        process_factory=lambda *_args, **_kwargs: (release.set() or process),
    )

    with pytest.raises(ContextProviderError) as captured:
        provider.call(
            "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
        )

    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_timeout"
    assert process.terminated and process.waited
    assert process.stdout.closed and process.stderr.closed


def test_git_operations_ignore_inherited_config_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    recorded = []
    real_popen = subprocess.Popen
    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "home-hidden.txt").write_text("home\n", encoding="utf-8")
    (root / "xdg-hidden.txt").write_text("xdg\n", encoding="utf-8")
    (root / "config-hidden.txt").write_text("config\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    (fake_home / ".gitignore-global").write_text("home-hidden.txt\n", encoding="utf-8")
    (fake_home / ".gitconfig").write_text(
        "[core]\n\texcludesfile = .gitignore-global\n",
        encoding="utf-8",
    )

    fake_xdg = tmp_path / "fake-xdg"
    (fake_xdg / "git").mkdir(parents=True)
    (fake_xdg / "git" / "ignore").write_text("xdg-hidden.txt\n", encoding="utf-8")

    fake_global = tmp_path / "fake-global.gitconfig"
    (tmp_path / "global-excludes").write_text("config-hidden.txt\n", encoding="utf-8")
    fake_global.write_text(
        "[core]\n\texcludesfile = "
        f"{(tmp_path / 'global-excludes').as_posix()}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_global))
    monkeypatch.setenv("GIT_DIR", "/tmp/unsafe")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'alias.log=!touch /tmp/nope'")

    def recording_process(command, **kwargs):
        recorded.append(kwargs["env"])
        return real_popen(command, **kwargs)

    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider

    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(),
        process_factory=recording_process,
    )
    provider.call(
        "inspect_recent_changes", {"path": "", "limit": 10}, model_turn=1
    )

    env = recorded[0]
    observed_paths = {
        item["path"]
        for item in provider.call(
            "inspect_recent_changes", {"path": "", "limit": 10}, model_turn=2
        )["observations"]
    }
    assert {"home-hidden.txt", "xdg-hidden.txt", "config-hidden.txt"} <= observed_paths
    assert "HOME" not in env
    assert "XDG_CONFIG_HOME" not in env
    assert "GIT_DIR" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert "PATH" in env
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_PAGER"] == "cat"
    assert env["LC_ALL"] == "C"
    if os.name == "nt":
        assert set(env) <= {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "SystemRoot",
            "WINDIR",
            "COMSPEC",
            "ComSpec",
            "TMP",
            "TEMP",
            "GIT_OPTIONAL_LOCKS",
            "GIT_TERMINAL_PROMPT",
            "GIT_PAGER",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_LITERAL_PATHSPECS",
            "LC_ALL",
        }
    else:
        assert set(env) == {
            "PATH",
            "TMPDIR",
            "GIT_OPTIONAL_LOCKS",
            "GIT_TERMINAL_PROMPT",
            "GIT_PAGER",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_LITERAL_PATHSPECS",
            "LC_ALL",
        }


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("inspect_git_history", {"path": "", "limit": True}),
        ("inspect_git_history", {"path": "", "limit": 51}),
        ("inspect_git_history", {"path": "", "limit": 1, "argv": ["status"]}),
        ("inspect_recent_changes", {"path": "../escape", "limit": 1}),
        ("inspect_recent_changes", {"path": "", "limit": 1, "process_factory": "x"}),
    ],
)
def test_git_requests_cannot_control_dispatch(
    tmp_path: Path, tool: str, payload: dict[str, object]
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _git_provider_fixture(tmp_path)
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, payload, model_turn=1)
    assert captured.value.code in {
        "context_path_rejected",
        "context_request_invalid",
        "context_tool_limit_exceeded",
    }


def test_git_failure_is_normalized_without_process_output(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stderr", b"raw-secret-from-git", 0.0)], final_returncode=2
    )
    fixture = _git_provider_fixture(
        tmp_path, process_factory=lambda *_args, **_kwargs: process
    )
    release.set()

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
        )

    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_failed"
    assert "raw-secret-from-git" not in f"{captured.value} {captured.value.details}"


def test_git_stderr_output_is_bounded(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stderr", b"e" * 1024, 0.0)]
    )
    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(requested_limits={"max_single_result_chars": 128}),
        process_factory=lambda *_args, **_kwargs: process,
    )
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        provider.call("inspect_git_history", {"path": "", "limit": 5}, model_turn=1)
    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_limit_exceeded"
    assert process.terminated and process.waited


def test_git_combined_stdout_stderr_output_is_bounded_and_reaped(
    tmp_path: Path,
) -> None:
    import subprocess

    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    process, release, writer_thread, writer_done = _make_pipe_process(
        [("stdout", b"x" * 80, 0.0), ("stderr", b"e" * 80, 0.0)]
    )
    original_kill = process.kill
    original_wait = process.wait

    def stubborn_terminate() -> None:
        process.terminated = True

    def stubborn_wait(timeout=None) -> int:
        if process.terminated and not process.killed:
            raise subprocess.TimeoutExpired("git", timeout)
        return original_wait(timeout)

    process.terminate = stubborn_terminate
    process.wait = stubborn_wait
    process.kill = original_kill
    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(requested_limits={"max_single_result_chars": 128}),
        process_factory=lambda *_args, **_kwargs: process,
    )
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        provider.call("inspect_git_history", {"path": "", "limit": 5}, model_turn=1)
    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_limit_exceeded"
    assert process.terminated and process.killed and process.waited
    assert process.stdout.closed and process.stderr.closed
    assert writer_done.is_set()


@pytest.mark.parametrize("payload", [b"not-a-record", b"\xff\x00bad\x1e"])
def test_git_malformed_and_non_utf8_output_is_rejected(
    tmp_path: Path, payload: bytes
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stdout", payload, 0.0)]
    )
    fixture = _git_provider_fixture(
        tmp_path, process_factory=lambda *_args, **_kwargs: process
    )
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
        )
    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_invalid_output"
    assert process.waited and process.stdout.closed and process.stderr.closed


def test_git_malformed_raw_diff_metadata_is_rejected(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stdout", b":100644 100644 not-an-oid still-not-an-oid M\0safe.txt\0", 0.0)]
    )
    fixture = _git_provider_fixture(
        tmp_path, process_factory=lambda *_args, **_kwargs: process
    )
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_recent_changes", {"path": "", "limit": 5}, model_turn=1
        )
    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_invalid_output"


def test_git_timeout_kills_when_terminate_does_not_stop_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    import goodteam_os._repository_git_tools as git_tools
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stdout", b"late", 1.0)]
    )
    original_kill = process.kill
    original_wait = process.wait

    def stubborn_terminate() -> None:
        process.terminated = True

    def stubborn_wait(timeout=None) -> int:
        if not process.killed:
            raise subprocess.TimeoutExpired("git", timeout)
        return original_wait(timeout)

    def tracked_kill() -> None:
        original_kill()

    process.terminate = stubborn_terminate
    process.wait = stubborn_wait
    process.kill = tracked_kill
    monkeypatch.setattr(git_tools, "_GIT_TIMEOUT_SECONDS", 0.01)
    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(),
        process_factory=lambda *_args, **_kwargs: process,
    )
    release.set()
    with pytest.raises(ContextProviderError) as captured:
        provider.call("inspect_git_history", {"path": "", "limit": 5}, model_turn=1)
    writer_thread.join(timeout=2.0)
    assert captured.value.code == "context_tool_timeout"
    assert process.terminated and process.killed and process.waited


def test_recent_change_argv_families_and_separator_order(tmp_path: Path) -> None:
    import subprocess

    recorded = []
    real_popen = subprocess.Popen

    def recording_process(command, **kwargs):
        recorded.append(command)
        return real_popen(command, **kwargs)

    fixture = _git_provider_fixture(tmp_path, process_factory=recording_process)
    fixture["provider"].call(
        "inspect_recent_changes", {"path": "", "limit": 5}, model_turn=1
    )
    operations = [
        next(word for word in command if word in {"diff", "ls-files"})
        for command in recorded
    ]
    assert set(operations) == {"diff", "ls-files"}
    assert all(command.index("--") < len(command) - 1 for command in recorded)
    assert all(command[-1] == "." for command in recorded)
    assert all("core.fsmonitor=false" in command for command in recorded)
    assert all("--no-ext-diff" in command for command in recorded if "diff" in command)
    assert all("--no-textconv" in command for command in recorded if "diff" in command)


def test_nonexistent_hex_revision_is_normalized(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _git_provider_fixture(tmp_path)
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_recent_changes",
            {"path": "", "limit": 5, "since_sha": "deadbee"},
            model_turn=1,
        )
    assert captured.value.code == "context_tool_failed"
    assert "deadbee" not in f"{captured.value} {captured.value.details}"


def test_git_path_symlink_is_rejected_before_spawn(tmp_path: Path) -> None:
    import subprocess

    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _git_provider_fixture(tmp_path)
    (fixture["root"] / ".gitignore").write_text("linked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(fixture["root"]), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(fixture["root"]), "commit", "-qm", "ignore linked"], check=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (fixture["root"] / "linked").symlink_to(outside, target_is_directory=True)
    _refresh_fixture_snapshot(fixture)
    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(
            "inspect_git_history", {"path": "linked", "limit": 5}, model_turn=1
        )
    assert captured.value.code == "context_path_rejected"


def test_structured_and_git_strings_are_redacted_in_return_and_store(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "Authorization: Bearer raw-script-secret"}}),
        encoding="utf-8",
    )
    (root / "secret.py").write_text('API_KEY = "sk-live-secret-value"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "secret.py"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "Authorization: Bearer raw-commit-secret"],
        check=True,
    )
    (root / "secret.py").write_text('API_KEY = "sk-live-updated-secret"\n', encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    manifest = fixture["provider"].call(
        "inspect_manifest", {"path": "package.json"}, model_turn=1
    )
    history = fixture["provider"].call(
        "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
    )
    changes = fixture["provider"].call(
        "inspect_recent_changes", {"path": "", "limit": 5}, model_turn=1
    )
    rendered = json.dumps([manifest, history, changes])
    persisted = observation_path(
        fixture["workspace"], fixture["project_key"], "job-1"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "raw-script-secret",
        "raw-commit-secret",
        "sk-live-secret-value",
        "sk-live-updated-secret",
    ):
        assert forbidden not in rendered
        assert forbidden not in persisted
    assert "redacted" in rendered


def test_recent_changes_omits_sensitive_paths_without_leaking(tmp_path: Path) -> None:
    import subprocess

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    (root / ".env").write_text("API_KEY=initial-secret\n", encoding="utf-8")
    (root / "safe.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".env", "safe.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "add files"], check=True)
    (root / ".env").write_text("API_KEY=raw-sensitive-change\n", encoding="utf-8")
    (root / "safe.txt").write_text("changed\n", encoding="utf-8")
    _refresh_fixture_snapshot(fixture)

    result = fixture["provider"].call(
        "inspect_recent_changes", {"path": "", "limit": 10}, model_turn=1
    )

    assert result["truncated"] is True
    assert [item["path"] for item in result["observations"]] == ["safe.txt"]
    rendered = json.dumps(result)
    persisted = observation_path(
        fixture["workspace"], fixture["project_key"], "job-1"
    ).read_text(encoding="utf-8")
    assert ".env" not in rendered
    assert ".env" not in persisted
    assert "raw-sensitive-change" not in rendered
    assert "raw-sensitive-change" not in persisted


def test_git_drift_discards_buffered_output(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _git_provider_fixture(tmp_path)
    real_popen = __import__("subprocess").Popen

    def mutate_after_spawn(command, **kwargs):
        process = real_popen(command, **kwargs)
        (fixture["root"] / "README.md").write_text("drifted\n", encoding="utf-8")
        return process

    from goodteam_os.repository_context_provider import OnboardingBudget, RepositoryContextProvider

    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=fixture["root"],
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(),
        process_factory=mutate_after_spawn,
    )
    with pytest.raises(ContextProviderError) as captured:
        provider.call("inspect_git_history", {"path": "", "limit": 5}, model_turn=1)
    assert captured.value.code == "repository_snapshot_changed"
    assert "initial" not in f"{captured.value} {captured.value.details}"


def test_git_root_replacement_after_spawn_reaps_process(tmp_path: Path) -> None:
    from goodteam_os.repository_context_provider import (
        ContextProviderError,
        OnboardingBudget,
        RepositoryContextProvider,
    )

    fixture = _git_provider_fixture(tmp_path)
    root = fixture["root"]
    moved = tmp_path / "moved-repo"
    process, release, writer_thread, _writer_done = _make_pipe_process(
        [("stdout", b"late", 1.0)]
    )

    def replacing_process(*_args, **_kwargs):
        root.rename(moved)
        root.mkdir()
        return process

    provider = RepositoryContextProvider(
        workspace=fixture["workspace"],
        project_key=fixture["project_key"],
        root=root,
        snapshot=fixture["snapshot"],
        observation_store=fixture["store"],
        budget=OnboardingBudget(),
        process_factory=replacing_process,
    )
    release.set()
    try:
        with pytest.raises(ContextProviderError) as captured:
            provider.call(
                "inspect_git_history", {"path": "", "limit": 5}, model_turn=1
            )
        process_state = (
            process.terminated,
            process.waited,
            process.stdout.closed,
            process.stderr.closed,
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        writer_thread.join(timeout=2.0)
        shutil.rmtree(root)
        moved.rename(root)

    assert captured.value.code == "repository_root_changed"
    assert process_state == (True, True, True, True)


@pytest.mark.parametrize("tool", ["search_text"])
def test_future_context_tools_fail_explicitly_until_implemented(
    tmp_path: Path,
    tool: str,
) -> None:
    from goodteam_os.repository_context_provider import ContextProviderError

    fixture = _provider_fixture(tmp_path)

    with pytest.raises(ContextProviderError) as captured:
        fixture["provider"].call(tool, {}, model_turn=1)

    expected_code = (
        "context_tool_limit_exceeded"
        if tool == "search_text"
        else "context_tool_not_implemented"
    )
    assert captured.value.code == expected_code
    record = _last_record_dict(fixture["workspace"], fixture["project_key"])
    assert record["status"] == "error"
    assert record["observations"] == []
    assert record["response_metadata"] == {
        "error": {"code": expected_code}
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
            {"access_token": "sk-abcdefghijkl", "path": "README.md"},
            model_turn=1,
        )

    assert captured.value.code == "context_handler_failed"
    assert str(captured.value) == "Repository context handler failed."
    assert captured.value.details == {"limit": "max_single_result_chars"}
    response = _last_record_dict(workspace, project_key)
    assert response["status"] == "error"
    assert response["observations"] == []
    assert response["request"] == {"access_token": "<redacted-token>", "path": "README.md"}
    encoded = _last_record_bytes(workspace, project_key).decode("utf-8")
    assert "sk-abcdefghijkl" not in encoded
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


@pytest.mark.parametrize("status_kind", ["ok", "denied", "error"])
def test_root_inode_replacement_in_append_window_preserves_primary_root_error(
    tmp_path: Path,
    status_kind: str,
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

    class AppendWindowRootReplacingProvider(RepositoryContextProvider):
        def _authoritative_append_store(self) -> OnboardingObservationStore:
            displaced_root = self.root.with_name("repo-displaced")
            self.root.rename(displaced_root)
            self.root.mkdir()
            (self.root / "README.md").write_text("replacement\n", encoding="utf-8")
            return super()._authoritative_append_store()

        def _handle_list_tree(self, request: dict[str, object]) -> dict[str, object]:
            if status_kind == "error":
                raise RuntimeError("raw repository failure")
            return super()._handle_list_tree(request)

    provider = _rebuild_provider(fixture, AppendWindowRootReplacingProvider)
    tool = "run_command" if status_kind == "denied" else "list_tree"

    with pytest.raises(ContextProviderError) as captured:
        provider.call(tool, {}, model_turn=1)

    assert captured.value.code == "repository_root_changed"
    _assert_detached_error(captured.value)
    assert not original_path.exists()
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
