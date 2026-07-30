from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from goodteam_os import onboarding_runner
from goodteam_os.onboarding_backend import (
    ModelTurnRequest,
    OnboardingAdapterIdentity,
    OnboardingBackendPreflight,
)
from goodteam_os.onboarding_profile import compute_project_profile_id
from goodteam_os.onboarding_runner import OnboardingRunnerError, ProjectOnboardingRunner
from goodteam_os.onboarding_store import profile_path
from goodteam_os.project_import import import_project


def _target_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _imported_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    workspace = tmp_path / "workspace"
    root = tmp_path / "target-repository"
    root.mkdir()
    (root / "src").mkdir()
    (root / "README.md").write_text(
        f"""# Demo service

Entry point: `src/main.py`
Development: `python -m app`
Tests: `pytest`
Constraint: do not write to the target repository during onboarding.
The target-root string is {root} and must never reach the adapter.
""",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        f"Do not expose the target root {root} or workspace path {workspace}.\n",
        encoding="utf-8",
    )
    (root / "src" / "main.py").write_text(
        '"""Demo application entry point."""\n',
        encoding="utf-8",
    )
    imported = import_project(workspace, str(root), requested_key="DEMO")
    return workspace, root, str(imported["project"]["project_key"])


def _profile_draft(snapshot_id: str, observation_id: str) -> dict[str, object]:
    """Return a complete model-owned draft that cites one actual observation."""

    claims = [
        ("purpose", "The reviewed README describes a demo service."),
        ("stage", "The repository is an existing project requiring review."),
        ("repository_shape", "The reviewed repository contains a source entrypoint."),
        ("entrypoint", "src/main.py is documented as the entrypoint."),
        ("module", "The documented application entrypoint is a core module."),
        ("development_command", "The README documents a development command."),
        ("test_command", "The README documents a test command."),
        ("constraint", "The README prohibits target writes during onboarding."),
        ("risk", "The application entrypoint should be reviewed before changes."),
    ]
    return {
        "schema": "project_profile.v1",
        "snapshot_id": snapshot_id,
        "project": {"purpose_claim_id": 0, "current_stage_claim_id": 1},
        "architecture": {
            "overview_claim_ids": [2],
            "modules": [
                {
                    "name": "application",
                    "purpose_claim_id": 4,
                    "ownership_claim_ids": [],
                    "entrypoint_claim_ids": [3],
                    "test_claim_ids": [6],
                }
            ],
            "entrypoints": [3],
            "data_flows": [],
            "external_dependencies": [],
        },
        "operations": {
            "development": [5],
            "test": [6],
            "lint": [],
            "build": [],
            "deploy": [],
        },
        "constraints": {
            "coding_rules": [7],
            "protected_areas": [],
            "prohibited_operations": [],
        },
        "risk": {
            "high_risk_modules": [8],
            "legacy_areas": [],
            "weak_test_coverage": [],
        },
        "knowledge": {"unknowns": ["No deployment procedure was reviewed."]},
        "coverage": {
            "status": "sufficient",
            "dimensions": {
                "project_purpose": {"status": "covered", "claim_refs": [0]},
                "repository_shape": {"status": "covered", "claim_refs": [2]},
                "main_entrypoint": {"status": "covered", "claim_refs": [3]},
                "core_module_map": {"status": "covered", "claim_refs": [4]},
                "development_commands": {"status": "covered", "claim_refs": [5]},
                "test_commands": {"status": "covered", "claim_refs": [6]},
                "instruction_constraint_review": {
                    "status": "covered",
                    "claim_refs": [7],
                },
                "high_risk_assessment": {
                    "status": "covered",
                    "claim_refs": [8],
                },
                "explicit_unknowns": {"status": "documented", "claim_refs": []},
            },
            "limitations": [],
            "recommended_followups": ["Review deployment before release work."],
        },
        "claims": [
            {
                "kind": kind,
                "text": text,
                "certainty": "confirmed",
                "observation_ids": [observation_id],
            }
            for kind, text in claims
        ],
    }


class _ReadThenProfileAdapter:
    """A deterministic adapter that derives its citation from the tool result."""

    def __init__(self) -> None:
        self._identity = OnboardingAdapterIdentity(
            adapter="runner_test",
            provider="test",
            model="deterministic",
            external_invocation=False,
            test_only=True,
        )
        self.requests: list[dict[str, object]] = []
        self.calls = 0

    @property
    def identity(self) -> OnboardingAdapterIdentity:
        return self._identity

    def preflight(self) -> OnboardingBackendPreflight:
        return OnboardingBackendPreflight(
            identity=self.identity,
            available=True,
            credentials_configured=False,
            tool_loop_capable=True,
            read_only_runtime=True,
            reason="Deterministic test adapter.",
        )

    def next_turn(self, request: ModelTurnRequest) -> Mapping[str, object]:
        self.calls += 1
        self.requests.append(request.as_dict())
        if request.turn == 1:
            assert request.prior_tool_results == ()
            return {
                "tool_calls": [
                    {
                        "id": "read_readme",
                        "name": "read_file_excerpt",
                        "arguments": {
                            "path": "README.md",
                            "start_line": 1,
                            "line_count": 7,
                        },
                    }
                ],
                "finish_reason": "tool_calls",
            }
        if request.turn == 2:
            assert len(request.prior_tool_results) == 1
            result = request.prior_tool_results[0].result
            observations = result["observations"]
            assert isinstance(observations, list) and len(observations) == 1
            observation_id = observations[0]["observation_id"]
            assert isinstance(observation_id, str)
            return {
                "profile_draft": _profile_draft(request.snapshot_id, observation_id),
                "finish_reason": "stop",
            }
        raise AssertionError(f"Unexpected model turn: {request.turn}")


class _InterruptedModelAdapter(_ReadThenProfileAdapter):
    def next_turn(self, request: ModelTurnRequest) -> Mapping[str, object]:
        self.calls += 1
        self.requests.append(request.as_dict())
        raise KeyboardInterrupt


class _FailingModelAdapter(_ReadThenProfileAdapter):
    def next_turn(self, request: ModelTurnRequest) -> Mapping[str, object]:
        self.calls += 1
        self.requests.append(request.as_dict())
        raise RuntimeError("adapter transport failed after request dispatch")


class _PreflightFailsOnceAdapter(_ReadThenProfileAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.preflight_calls = 0

    def preflight(self) -> OnboardingBackendPreflight:
        self.preflight_calls += 1
        if self.preflight_calls == 1:
            raise RuntimeError("transient preflight transport failure")
        return super().preflight()


class _ChangingPreflightAdapter(_ReadThenProfileAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.preflight_calls = 0

    def preflight(self) -> OnboardingBackendPreflight:
        self.preflight_calls += 1
        return OnboardingBackendPreflight(
            identity=self.identity,
            available=True,
            credentials_configured=False,
            tool_loop_capable=True,
            read_only_runtime=True,
            reason=f"Preflight sample {self.preflight_calls}.",
        )


class _OfflineAfterFirstPreflightAdapter(_ReadThenProfileAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.preflight_calls = 0

    def preflight(self) -> OnboardingBackendPreflight:
        self.preflight_calls += 1
        if self.preflight_calls > 1:
            raise RuntimeError("backend is temporarily offline")
        return super().preflight()


def _runner(
    tmp_path: Path,
    *,
    adapter: _ReadThenProfileAdapter,
) -> tuple[ProjectOnboardingRunner, Path, Path, str]:
    workspace, root, key = _imported_repository(tmp_path)
    return (
        ProjectOnboardingRunner(
            workspace=workspace,
            project_key=key,
            root=root,
            adapter=adapter,
        ),
        workspace,
        root,
        key,
    )


def test_runner_publishes_snapshot_bound_non_promoting_profile_without_target_writes(
    tmp_path: Path,
) -> None:
    adapter = _ReadThenProfileAdapter()
    runner, workspace, root, key = _runner(tmp_path, adapter=adapter)
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    before_readiness = readiness_path.read_bytes()
    before_target = _target_files(root)

    job = runner.create_job(job_id="onboarding_" + "1" * 32)
    result = runner.run(job.job_id)

    assert result.job_id == job.job_id
    assert result.snapshot_id == job.snapshot_id
    assert result.profile_id == compute_project_profile_id(job.snapshot_id, job.job_id)
    assert result.non_promoting is True
    assert result.profile["generated_by"]["external_invocation"] is False  # type: ignore[index]
    assert _target_files(root) == before_target
    assert readiness_path.read_bytes() == before_readiness

    adapter_payload = json.dumps(adapter.requests, sort_keys=True)
    assert str(root.resolve()) not in adapter_payload
    assert str(workspace.resolve()) not in adapter_payload
    assert "canonical_root" not in adapter_payload
    assert "root_identity" not in adapter_payload
    assert len(adapter.requests) == 2
    assert adapter.requests[1]["prior_tool_results"]

    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "succeeded"
    assert status["non_promoting"] is True
    bundle = runner.job_store.read_profile(
        result.profile_id,
        job.job_id,
        job.snapshot_id,
    )
    assert bundle["manifest"]["manifest"]["readiness_promotion"] == "not_available"  # type: ignore[index]
    persisted_profile = profile_path(workspace, key, result.profile_id).read_text(
        encoding="utf-8"
    )
    assert str(root.resolve()) not in persisted_profile


def test_interrupted_model_turn_fails_closed_without_replaying_unknown_call(
    tmp_path: Path,
) -> None:
    adapter = _InterruptedModelAdapter()
    runner, workspace, _root, key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "2" * 32)
    expected_profile_id = compute_project_profile_id(job.snapshot_id, job.job_id)

    with pytest.raises(KeyboardInterrupt):
        runner.run(job.job_id)

    checkpoint = runner.job_store.read_checkpoint(job.job_id, job.snapshot_id)
    assert checkpoint is not None
    assert checkpoint["phase"] == "model_response_pending"
    assert adapter.calls == 1

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_operation_uncertain"
    assert adapter.calls == 1
    assert not profile_path(workspace, key, expected_profile_id).exists()
    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "recovery_required"


def test_model_failure_after_dispatch_is_marked_recovery_required(
    tmp_path: Path,
) -> None:
    adapter = _FailingModelAdapter()
    runner, _workspace, _root, _key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "5" * 32)

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_runner_failed"
    checkpoint = runner.job_store.read_checkpoint(job.job_id, job.snapshot_id)
    assert checkpoint is not None
    assert checkpoint["phase"] == "model_response_pending"
    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "recovery_required"

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_operation_uncertain"
    assert adapter.calls == 1


def test_ready_checkpoint_retries_after_transient_preflight_failure(
    tmp_path: Path,
) -> None:
    adapter = _PreflightFailsOnceAdapter()
    runner, _workspace, _root, _key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "8" * 32)

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_runner_failed"
    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "failed"
    assert status["phase"] == "ready"

    result = runner.run(job.job_id)

    assert result.profile_id == compute_project_profile_id(job.snapshot_id, job.job_id)
    assert adapter.preflight_calls == 2
    assert adapter.calls == 2


def test_interrupted_tool_call_fails_closed_without_replaying_unknown_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _ReadThenProfileAdapter()
    runner, workspace, _root, key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "3" * 32)
    expected_profile_id = compute_project_profile_id(job.snapshot_id, job.job_id)
    tool_calls = 0

    def interrupt_tool_call(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal tool_calls
        tool_calls += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(
        onboarding_runner.RepositoryContextProvider,
        "call",
        interrupt_tool_call,
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run(job.job_id)

    checkpoint = runner.job_store.read_checkpoint(job.job_id, job.snapshot_id)
    assert checkpoint is not None
    assert checkpoint["phase"] == "tool_call_pending"
    assert adapter.calls == 1
    assert tool_calls == 1

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_operation_uncertain"
    assert adapter.calls == 1
    assert tool_calls == 1
    assert not profile_path(workspace, key, expected_profile_id).exists()


def test_tool_calls_checkpoint_retries_without_replaying_the_model_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _ReadThenProfileAdapter()
    runner, _workspace, _root, _key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "9" * 32)
    original_write_status = runner.job_store.write_status
    failures_remaining = 1

    def fail_once_after_tool_calls_checkpoint(
        job_id: str,
        snapshot_id: str,
        status: dict[str, object],
        **kwargs: object,
    ) -> Path:
        nonlocal failures_remaining
        if (
            status.get("state") == "running"
            and status.get("phase") == "tool_calls"
            and failures_remaining
        ):
            failures_remaining -= 1
            raise OSError("interrupted after durable model response")
        return original_write_status(job_id, snapshot_id, status, **kwargs)

    monkeypatch.setattr(
        runner.job_store,
        "write_status",
        fail_once_after_tool_calls_checkpoint,
    )

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_runner_failed"
    checkpoint = runner.job_store.read_checkpoint(job.job_id, job.snapshot_id)
    assert checkpoint is not None
    assert checkpoint["phase"] == "tool_calls"
    assert adapter.calls == 1

    result = runner.run(job.job_id)

    assert result.profile_id == compute_project_profile_id(job.snapshot_id, job.job_id)
    assert adapter.calls == 2


def test_profile_publication_retry_reuses_the_checkpointed_created_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _ChangingPreflightAdapter()
    runner, workspace, _root, key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "6" * 32)
    expected_profile_id = compute_project_profile_id(job.snapshot_id, job.job_id)
    original_write_checkpoint = runner.job_store.write_checkpoint
    failures_remaining = 1

    def fail_once_after_profile_publication(
        job_id: str,
        snapshot_id: str,
        checkpoint: dict[str, object],
        **kwargs: object,
    ) -> Path:
        nonlocal failures_remaining
        if checkpoint.get("phase") == "succeeded" and failures_remaining:
            failures_remaining -= 1
            raise OSError("interrupted after immutable profile publication")
        return original_write_checkpoint(job_id, snapshot_id, checkpoint, **kwargs)

    monkeypatch.setattr(
        runner.job_store,
        "write_checkpoint",
        fail_once_after_profile_publication,
    )

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_runner_failed"
    first_profile = runner.job_store.read_profile(
        expected_profile_id,
        job.job_id,
        job.snapshot_id,
    )["profile"]
    assert isinstance(first_profile, dict)

    result = runner.run(job.job_id)

    assert result.profile_id == expected_profile_id
    assert result.profile == first_profile
    assert adapter.calls == 2
    assert adapter.preflight_calls == 1
    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "succeeded"
    assert profile_path(workspace, key, expected_profile_id).exists()


def test_succeeded_checkpoint_repairs_status_without_republishing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _OfflineAfterFirstPreflightAdapter()
    runner, _workspace, _root, _key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "7" * 32)
    original_write_status = runner.job_store.write_status
    failures_remaining = 1

    def fail_once_on_terminal_status(
        job_id: str,
        snapshot_id: str,
        status: dict[str, object],
        **kwargs: object,
    ) -> Path:
        nonlocal failures_remaining
        if status.get("state") == "succeeded" and failures_remaining:
            failures_remaining -= 1
            raise OSError("interrupted after succeeded checkpoint")
        return original_write_status(job_id, snapshot_id, status, **kwargs)

    monkeypatch.setattr(
        runner.job_store,
        "write_status",
        fail_once_on_terminal_status,
    )

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "onboarding_runner_failed"
    checkpoint = runner.job_store.read_checkpoint(job.job_id, job.snapshot_id)
    assert checkpoint is not None
    assert checkpoint["phase"] == "succeeded"
    assert adapter.calls == 2

    result = runner.run(job.job_id)

    assert result.profile_id == compute_project_profile_id(job.snapshot_id, job.job_id)
    assert adapter.calls == 2
    assert adapter.preflight_calls == 1
    status = runner.job_store.read_status(job.job_id, job.snapshot_id)
    assert status is not None
    assert status["state"] == "succeeded"


def test_target_drift_after_a_tool_result_blocks_the_next_model_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _ReadThenProfileAdapter()
    runner, workspace, root, key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "a" * 32)
    expected_profile_id = compute_project_profile_id(job.snapshot_id, job.job_id)
    original_call = onboarding_runner.RepositoryContextProvider.call

    def mutate_after_tool_result(
        provider: object,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        result = original_call(provider, *args, **kwargs)
        (root / "src" / "main.py").write_text(
            '"""Mutated after the first tool result."""\n',
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(
        onboarding_runner.RepositoryContextProvider,
        "call",
        mutate_after_tool_result,
    )

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "repository_snapshot_changed"
    assert adapter.calls == 1
    assert not profile_path(workspace, key, expected_profile_id).exists()


def test_target_mutation_before_run_prevents_profile_publication(tmp_path: Path) -> None:
    adapter = _ReadThenProfileAdapter()
    runner, workspace, root, key = _runner(tmp_path, adapter=adapter)
    job = runner.create_job(job_id="onboarding_" + "4" * 32)
    expected_profile_id = compute_project_profile_id(job.snapshot_id, job.job_id)

    (root / "src" / "main.py").write_text(
        '"""Mutated after the onboarding snapshot."""\n',
        encoding="utf-8",
    )

    with pytest.raises(OnboardingRunnerError) as captured:
        runner.run(job.job_id)

    assert captured.value.code == "repository_snapshot_changed"
    assert adapter.calls == 0
    assert not profile_path(workspace, key, expected_profile_id).exists()
