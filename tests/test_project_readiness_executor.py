from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import goodteam_os.project_readiness_executor as readiness_executor_module
from goodteam_os.project_import import import_project, mutate_project_readiness
from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities
from goodteam_os.repository_access import RepositoryAccessError
from goodteam_os.project_readiness_executor import (
    MAX_ENTRIES,
    ProjectReadinessExecutor,
    ReadinessExecutionError,
    scan_existing_project,
)


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
        if not path.is_symlink()
    }


def _ready_preflight(_key: str) -> dict[str, object]:
    return {
        "status": "ready",
        "mode": "fake",
        "local_only": True,
        "external_invocation": False,
        "message": "ready",
    }


def _executor(workspace: Path) -> ProjectReadinessExecutor:
    return ProjectReadinessExecutor(workspace, preflight=_ready_preflight)


def test_existing_project_runs_read_only_scan_then_waits_for_confirmation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "test": "vitest", "lint": "eslint ."}}),
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    before = _snapshot(root)
    imported = import_project(workspace, str(root))

    readiness = _executor(workspace).start(
        imported["project"]["project_key"],
        root,
        background=False,
    )

    repository = next(step for step in readiness["steps"] if step["id"] == "repository_understanding")
    commands = next(step for step in readiness["steps"] if step["id"] == "command_verification")
    assert readiness["schema_version"] == 2
    assert readiness["execution"]["status"] == "needs_input"
    assert repository["status"] == "complete"
    assert repository["attempt"] == 1
    assert repository["artifact_refs"]
    assert commands["status"] == "needs_input"
    discovered = commands["input_requirements"]["commands"]
    assert {item["command"] for item in discovered} == {
        "npm run dev",
        "npm run lint",
        "npm run test",
    }
    artifact = readiness["artifacts"][repository["artifact_refs"][0]]
    assert "AGENTS.md: Rules" in artifact["content"]["constraints"]
    assert before == _snapshot(root)


def test_existing_confirmations_and_worker_preflight_make_project_ready(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest"}}),
        encoding="utf-8",
    )
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    scanned = executor.start(key, root, background=False)
    command_ids = [
        item["id"]
        for item in next(
            step for step in scanned["steps"] if step["id"] == "command_verification"
        )["input_requirements"]["commands"]
    ]

    constraints = executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": command_ids},
        background=False,
    )
    assert constraints["execution"]["current_step_id"] == "constraints_confirmation"
    complete = executor.submit_input(
        key,
        root,
        "constraints_confirmation",
        {"confirmed": True, "note": "Keep the scan read-only"},
        background=False,
    )

    assert complete["execution"]["status"] == "complete"
    assert complete["lifecycle"] == "ready"
    assert build_capabilities(complete)["start_workflow"] is True
    required_steps = [step for step in complete["steps"] if step["required"]]
    assert all(step["execution_id"] for step in required_steps)
    assert all(step["attempt"] >= 1 for step in required_steps)
    assert all(step["started_at"] and step["completed_at"] for step in required_steps)
    for step in required_steps:
        for ref in step["artifact_refs"]:
            artifact = complete["artifacts"][ref]
            assert artifact["execution_id"] == step["execution_id"]
            assert artifact["attempt"] == step["attempt"]
    command_input = complete["inputs"]["command_verification"]
    assert command_input["project_key"] == key
    assert command_input["canonical_root"] == str(root)
    assert command_input["source_artifact_refs"]


def test_symlinked_project_root_keeps_consistent_canonical_root(tmp_path: Path) -> None:
    """Symlinked roots must not break artifact binding or the start_workflow gate.

    Regression for macOS /tmp -> /private/tmp: readiness stores the resolved
    canonical root, but the executor used to write execution/artifact records
    with the raw caller path, so binding comparisons failed
    (readiness_artifact_mismatch on input submission and start_workflow gated
    off even after readiness completed).
    """
    workspace = tmp_path / "workspace"
    # The root itself must stay a real directory (the executor rejects symlink
    # roots via O_NOFOLLOW), but a symlinked parent reproduces the macOS
    # /tmp -> /private/tmp shape where resolve() differs from the raw path.
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    link_parent = tmp_path / "link-parent"
    link_parent.symlink_to(real_parent, target_is_directory=True)
    root = link_parent / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest"}}),
        encoding="utf-8",
    )
    assert str(root) != str(root.resolve())

    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    scanned = executor.start(key, root, background=False)
    command_ids = [
        item["id"]
        for item in next(
            step for step in scanned["steps"] if step["id"] == "command_verification"
        )["input_requirements"]["commands"]
    ]

    constraints = executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": command_ids},
        background=False,
    )
    assert constraints["execution"]["current_step_id"] == "constraints_confirmation"
    complete = executor.submit_input(
        key,
        root,
        "constraints_confirmation",
        {"confirmed": True},
        background=False,
    )

    assert complete["execution"]["status"] == "complete"
    assert complete["lifecycle"] == "ready"
    assert build_capabilities(complete)["start_workflow"] is True
    expected = str(root.resolve())
    assert complete["canonical_root"] == expected
    assert complete["execution"]["canonical_root"] == expected
    repository = next(
        step for step in complete["steps"] if step["id"] == "repository_understanding"
    )
    for ref in repository["artifact_refs"]:
        artifact = complete["artifacts"][ref]
        assert artifact["canonical_root"] == expected
        bound = executor.artifact(key, root, ref)
        assert bound["canonical_root"] == expected


def test_new_project_inputs_are_persisted_without_writing_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]

    readiness = _executor(workspace).start(
        key,
        root,
        {
            "goal": "Ship a calm project home",
            "brief": "A local-first delivery workspace",
            "tech_preferences": "Python and React",
            "constraints": "No network by default",
            "prohibited_operations": "No dependency install without approval",
        },
        background=False,
    )

    assert readiness["lifecycle"] == "ready"
    assert readiness["inputs"]["goal"] == "Ship a calm project home"
    assert list(root.iterdir()) == []
    init_step = next(step for step in readiness["steps"] if step["id"] == "new_project_init")
    artifact = _executor(workspace).artifact(key, root, init_step["artifact_refs"][0])
    assert artifact["content"]["target_root_writes_authorized"] is False


def test_new_project_without_required_input_is_resumable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)

    waiting = executor.start(key, root, background=False)
    assert waiting["execution"]["status"] == "needs_input"
    resumed = executor.submit_input(
        key,
        root,
        "new_project_init",
        {"goal": "Demo", "brief": "Demo brief"},
        background=False,
    )
    assert resumed["execution"]["status"] == "complete"


def test_duplicate_start_reuses_execution_and_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)

    first = executor.start(key, root, idempotency_key="same", background=False)
    second = executor.start(key, root, idempotency_key="same", background=False)

    assert second["execution"]["id"] == first["execution"]["id"]
    assert second["execution"]["attempt"] == 1
    assert second["artifacts"] == first["artifacts"]


def test_concurrent_duplicate_start_creates_one_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(
                lambda _index: executor.start(
                    key, root, idempotency_key="concurrent-start", background=False
                ),
                range(20),
            )
        )

    assert {result["execution"]["id"] for result in results} == {
        results[0]["execution"]["id"]
    }
    assert {result["execution"]["attempt"] for result in results} == {1}
    final = executor.status(key, root)
    assert len(final["artifacts"]) == 1


def test_next_stage_dispatches_while_previous_stage_thread_is_exiting(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    previous_stage_published = threading.Event()
    release_previous_stage = threading.Event()
    original_runner = executor._run_existing

    def blocked_previous_stage(
        runner_key: str,
        runner_root: Path,
        execution_id: str,
        attempt: int,
    ) -> None:
        original_runner(runner_key, runner_root, execution_id, attempt)
        previous_stage_published.set()
        release_previous_stage.wait(timeout=5)

    executor._run_existing = blocked_previous_stage  # type: ignore[method-assign]
    executor.start(key, root, background=True)
    assert previous_stage_published.wait(timeout=5)
    waiting = executor.status(key, root)
    command_ids = [
        command["id"]
        for command in next(
            step for step in waiting["steps"] if step["id"] == "command_verification"
        )["input_requirements"]["commands"]
    ]
    executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": command_ids},
        background=True,
    )
    completed = executor.submit_input(
        key,
        root,
        "constraints_confirmation",
        {"confirmed": True},
        background=True,
    )
    try:
        assert completed["execution"]["status"] in {"running", "complete"}
        for _attempt in range(100):
            completed = executor.status(key, root)
            if completed["execution"]["status"] == "complete":
                break
            time.sleep(0.01)
        assert completed["execution"]["status"] == "complete"
    finally:
        release_previous_stage.set()


def test_command_confirmation_rejects_undiscovered_command_id(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    executor.start(key, root, background=False)

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.submit_input(
            key,
            root,
            "command_verification",
            {"confirmed_command_ids": ["forged-command"]},
            background=False,
        )

    assert captured.value.code == "readiness_input_invalid"
    status = executor.status(key, root)
    command_step = next(
        step for step in status["steps"] if step["id"] == "command_verification"
    )
    assert command_step["status"] == "needs_input"


def test_command_confirmation_replay_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        '{"scripts":{"test":"pytest","lint":"ruff check ."}}', encoding="utf-8"
    )
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    scanned = executor.start(key, root, background=False)
    commands = next(
        step for step in scanned["steps"] if step["id"] == "command_verification"
    )["input_requirements"]["commands"]
    first_id = commands[0]["id"]

    first = executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": [first_id]},
        background=False,
    )
    replay = executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": [first_id]},
        background=False,
    )
    assert replay["state_revision"] == first["state_revision"]

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.submit_input(
            key,
            root,
            "command_verification",
            {"confirmed_command_ids": [commands[-1]["id"]]},
            background=False,
        )
    assert captured.value.code == "readiness_input_conflict"


def test_command_confirmation_rejects_candidate_from_cancelled_scan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    manifest = root / "package.json"
    manifest.write_text('{"scripts":{"old-test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    first = executor.start(key, root, background=False)
    old_id = next(
        step for step in first["steps"] if step["id"] == "command_verification"
    )["input_requirements"]["commands"][0]["id"]
    executor.cancel(key, root)
    manifest.write_text('{"scripts":{"new-test":"pytest"}}', encoding="utf-8")
    second = executor.start(key, root, background=False)
    new_id = next(
        step for step in second["steps"] if step["id"] == "command_verification"
    )["input_requirements"]["commands"][0]["id"]
    assert new_id != old_id

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.submit_input(
            key,
            root,
            "command_verification",
            {"confirmed_command_ids": [old_id]},
            background=False,
        )

    assert captured.value.code == "readiness_input_invalid"


def test_repository_artifact_remains_bound_and_readable_after_worker_retry(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"test":"pytest"}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    ready = False

    def preflight(_key: str) -> dict[str, object]:
        return {
            "status": "ready" if ready else "fail",
            "message": "ready" if ready else "missing worker",
        }

    executor = ProjectReadinessExecutor(workspace, preflight=preflight)
    scanned = executor.start(key, root, background=False)
    repository_step = next(
        step for step in scanned["steps"] if step["id"] == "repository_understanding"
    )
    ref = repository_step["artifact_refs"][0]
    command_ids = [
        command["id"]
        for command in next(
            step for step in scanned["steps"] if step["id"] == "command_verification"
        )["input_requirements"]["commands"]
    ]
    executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": command_ids},
        background=False,
    )
    failed = executor.submit_input(
        key,
        root,
        "constraints_confirmation",
        {"confirmed": True},
        background=False,
    )
    assert failed["execution"]["status"] == "failed"

    ready = True
    retried = executor.retry(key, root, "worker_preflight", background=False)

    assert retried["execution"]["status"] == "complete"
    assert executor.artifact(key, root, ref)["step_id"] == "repository_understanding"


def test_unsupported_retry_does_not_mutate_failed_readiness(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("# Existing project\n", encoding="utf-8")
    (root / "package.json").write_text('{"scripts":{}}', encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = ProjectReadinessExecutor(
        workspace,
        preflight=lambda _key: {"status": "fail", "message": "missing worker"},
    )
    executor.start(key, root, background=False)
    executor.submit_input(
        key,
        root,
        "command_verification",
        {"confirmed_command_ids": []},
        background=False,
    )
    failed = executor.submit_input(
        key,
        root,
        "constraints_confirmation",
        {"confirmed": True},
        background=False,
    )
    assert failed["execution"]["status"] == "failed"
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    before = readiness_path.read_bytes()

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.retry(key, root, "constraints_confirmation", background=False)

    assert captured.value.code == "readiness_step_not_retryable"
    assert readiness_path.read_bytes() == before


def test_idempotency_key_rejects_different_inputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    executor.start(key, root, {"goal": "one", "brief": "one"}, idempotency_key="fixed", background=False)

    with pytest.raises(ReadinessExecutionError, match="different inputs") as captured:
        executor.start(key, root, {"goal": "two", "brief": "two"}, idempotency_key="fixed", background=False)
    assert captured.value.code == "readiness_idempotency_conflict"


def test_running_state_without_live_runner_recovers_as_retryable_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]

    def interrupted(readiness: dict[str, object]) -> dict[str, object]:
        readiness["execution"] = {
            "id": "interrupted",
            "status": "running",
            "project_key": key,
            "canonical_root": str(root),
            "attempt": 1,
            "started_at": "2026-07-17T00:00:00Z",
            "completed_at": None,
            "updated_at": "2026-07-17T00:00:00Z",
            "current_step_id": "repository_understanding",
            "owner_pid": 999_999_999,
        }
        return readiness

    mutate_project_readiness(workspace, key, root, interrupted)
    recovered = _executor(workspace).status(key, root)

    assert recovered["execution"]["status"] == "failed"
    step = next(step for step in recovered["steps"] if step["id"] == "repository_understanding")
    assert step["error"]["code"] == "readiness_interrupted"
    assert "retry_readiness" in step["recovery"]


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled", "complete"])
def test_terminal_execution_cannot_be_revived_by_stale_runner(terminal_status: str) -> None:
    state = {
        "execution": {
            "id": "old-execution",
            "attempt": 2,
            "status": terminal_status,
            "cancel_requested": terminal_status == "cancelled",
        }
    }

    assert (
        ProjectReadinessExecutor._current_execution(state, "old-execution", 2)
        is None
    )


def test_imported_root_replacement_is_rejected_before_scan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    root.rename(tmp_path / "original")
    root.mkdir()
    (root / "secret.txt").write_text("must not be scanned", encoding="utf-8")

    with pytest.raises(ReadinessExecutionError) as captured:
        _executor(workspace).start(key, root, background=False)
    assert captured.value.code == "project_root_changed"


def test_imported_root_symlink_back_to_same_inode_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    original = tmp_path / "original"
    root.rename(original)
    root.symlink_to(original, target_is_directory=True)

    with pytest.raises(ReadinessExecutionError) as captured:
        _executor(workspace).status(key, root)

    assert captured.value.code == "project_root_changed"


def test_missing_imported_root_identity_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness.pop("root_identity")
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
    before = _snapshot(root)

    with pytest.raises(ReadinessExecutionError) as captured:
        _executor(workspace).start(key, root, background=False)

    assert captured.value.code == "project_root_identity_missing"
    assert _snapshot(root) == before


def test_schema_v2_step_binding_tamper_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = _executor(workspace)
    executor.start(key, root, background=False)
    readiness_path = workspace / "state" / "projects" / key / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["steps"][0]["canonical_root"] = str(tmp_path / "other")
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.status(key, root)

    assert captured.value.code == "readiness_step_binding_mismatch"


def test_scanner_skips_file_and_directory_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "secret.txt").write_text("TOP_SECRET_CONTENT", encoding="utf-8")
    (root / "secret-link").symlink_to(external / "secret.txt")
    (root / "external-link").symlink_to(external, target_is_directory=True)

    overview = scan_existing_project(root)

    assert overview["scan"]["symlinks_skipped"] == ["external-link", "secret-link"]
    assert "TOP_SECRET_CONTENT" not in json.dumps(overview)


@pytest.mark.parametrize(
    ("preflight_status", "expected_execution", "expected_step"),
    [
        ("ready", "complete", "complete"),
        ("warn", "needs_input", "needs_input"),
        ("fail", "failed", "failed"),
    ],
)
def test_worker_preflight_lifecycle_distinguishes_ready_warn_and_fail(
    tmp_path: Path,
    preflight_status: str,
    expected_execution: str,
    expected_step: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = ProjectReadinessExecutor(
        workspace,
        preflight=lambda _key: {
            "status": preflight_status,
            "message": f"preflight {preflight_status}",
        },
    )

    readiness = executor.start(
        key,
        root,
        {"goal": "Lifecycle", "brief": "Exercise Worker preflight outcomes"},
        background=False,
    )

    worker = next(step for step in readiness["steps"] if step["id"] == "worker_preflight")
    assert readiness["execution"]["status"] == expected_execution
    assert worker["status"] == expected_step
    if preflight_status == "warn":
        assert worker["error"]["code"] == "worker_preflight_needs_input"
        assert readiness["lifecycle"] == "attention_required"
        assert build_capabilities(readiness)["start_workflow"] is False
    elif preflight_status == "fail":
        assert worker["error"]["code"] == "worker_preflight_failed"
        assert readiness["lifecycle"] == "attention_required"
    else:
        assert readiness["lifecycle"] == "ready"


def test_worker_preflight_warning_requires_explicit_acknowledgement(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = ProjectReadinessExecutor(
        workspace,
        preflight=lambda _key: {"status": "warn", "message": "auth uncertain"},
    )
    executor.start(
        key,
        root,
        {"goal": "Lifecycle", "brief": "Require an explicit acknowledgement"},
        background=False,
    )

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.submit_input(
            key,
            root,
            "worker_preflight",
            {"confirmed": False},
            background=False,
        )
    assert captured.value.code == "readiness_input_invalid"

    complete = executor.submit_input(
        key,
        root,
        "worker_preflight",
        {"confirmed": True},
        background=False,
    )
    assert complete["execution"]["status"] == "complete"
    assert complete["lifecycle"] == "ready"
    assert build_capabilities(complete)["start_workflow"] is True


def test_start_rejects_failed_execution_and_requires_retry(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "empty"
    root.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    executor = ProjectReadinessExecutor(
        workspace,
        preflight=lambda _key: {"status": "fail", "message": "worker unavailable"},
    )
    failed = executor.start(
        key,
        root,
        {"goal": "Lifecycle", "brief": "Keep failed execution identity"},
        idempotency_key="failed-start",
        background=False,
    )

    with pytest.raises(ReadinessExecutionError) as captured:
        executor.start(
            key,
            root,
            {"goal": "Lifecycle", "brief": "Keep failed execution identity"},
            idempotency_key="failed-start",
            background=False,
        )

    assert captured.value.code == "readiness_retry_required"
    persisted = executor.status(key, root, recover=False)
    assert persisted["execution"]["id"] == failed["execution"]["id"]
    assert persisted["execution"]["attempt"] == failed["execution"]["attempt"]


def test_scanner_does_not_follow_replaced_root_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    original = tmp_path / "original"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "package.json").write_text(
        '{"scripts":{"test":"read-external-secret"}}',
        encoding="utf-8",
    )
    identity = root.stat()
    root.rename(original)
    root.symlink_to(external, target_is_directory=True)

    with pytest.raises(ReadinessExecutionError) as captured:
        scan_existing_project(
            root,
            expected_identity={"device": identity.st_dev, "inode": identity.st_ino},
        )

    assert captured.value.code == "project_root_changed"


def test_scanner_translates_repository_entry_change_for_schema_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def fail_capture(*_args, **_kwargs):
        raise RepositoryAccessError(
            "repository_entry_changed",
            "entry changed",
            details={"path": "package.json"},
        )

    monkeypatch.setattr(
        readiness_executor_module,
        "capture_repository_snapshot",
        fail_capture,
    )
    with pytest.raises(ReadinessExecutionError) as captured:
        scan_existing_project(root)

    assert captured.value.code == "project_entry_changed"
    assert captured.value.details == {"path": "package.json"}


def test_root_replacement_during_executor_scan_persists_bound_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    original = tmp_path / "original"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    real_scan = readiness_executor_module.scan_existing_project

    def replace_before_open(
        scan_root: Path,
        *,
        expected_identity: dict[str, object] | None = None,
    ) -> dict[str, object]:
        root.rename(original)
        root.mkdir()
        (root / "replacement-marker.txt").write_text("unchanged", encoding="utf-8")
        return real_scan(scan_root, expected_identity=expected_identity)

    monkeypatch.setattr(
        readiness_executor_module,
        "scan_existing_project",
        replace_before_open,
    )

    with pytest.raises(ReadinessExecutionError) as captured:
        _executor(workspace).start(key, root, background=False)

    assert captured.value.code == "project_root_changed"
    persisted = ProjectReadinessStore(workspace).read(key)
    assert persisted is not None
    assert persisted["project_key"] == key
    assert persisted["canonical_root"] == str(root)
    assert persisted["execution"]["status"] == "failed"
    repository = next(
        step
        for step in persisted["steps"]
        if step["id"] == "repository_understanding"
    )
    assert repository["error"]["code"] == "project_root_changed"
    assert (root / "replacement-marker.txt").read_text(encoding="utf-8") == "unchanged"


def test_scanner_entry_collection_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(MAX_ENTRIES + 20):
        (root / f"link-{index:04d}").symlink_to(tmp_path / "missing")

    overview = scan_existing_project(root)

    assert overview["scan"]["entries_seen"] == MAX_ENTRIES
    assert len(overview["scan"]["symlinks_skipped"]) <= 80
    assert any("bounded entry limit" in risk for risk in overview["risks"])


def test_scanner_discovers_conventional_python_rust_and_go_commands(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "example"
version = "0.1.0"

[project.scripts]
example-cli = "example.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
strict = true
""".strip(),
        encoding="utf-8",
    )
    (root / "Cargo.toml").write_text(
        """
[package]
name = "example-rs"
version = "0.1.0"

[[bin]]
name = "example-server"
path = "src/main.rs"
""".strip(),
        encoding="utf-8",
    )
    (root / "go.mod").write_text(
        "module example.com/goodteam/example\n\ngo 1.23\n",
        encoding="utf-8",
    )
    before = _snapshot(root)

    first = scan_existing_project(root)["commands"]
    second = scan_existing_project(root)["commands"]

    assert {candidate["command"] for candidate in first} == {
        "example-cli",
        "python -m pytest",
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python -m mypy .",
        "python -m build",
        "cargo check",
        "cargo test",
        "cargo build",
        "cargo fmt --check",
        "cargo clippy --all-targets --all-features",
        "cargo run --bin example-server",
        "go test ./...",
        "go vet ./...",
        "go build ./...",
        "gofmt -l .",
    }
    assert all(candidate["requires_confirmation"] is True for candidate in first)
    assert len({candidate["id"] for candidate in first}) == len(first)
    assert [candidate["id"] for candidate in first] == [
        candidate["id"] for candidate in second
    ]
    assert before == _snapshot(root)


def test_scanner_ignores_invalid_or_unsafe_manifest_commands(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project.scripts]\n"unsafe; touch marker" = "example:main"\n',
        encoding="utf-8",
    )
    (root / "Cargo.toml").write_text(
        '[[bin]]\nname = "unsafe; touch marker"\npath = "src/main.rs"\n',
        encoding="utf-8",
    )
    (root / "go.mod").write_text("not a module declaration\n", encoding="utf-8")

    overview = scan_existing_project(root)

    assert overview["commands"] == []
    assert not (root / "marker").exists()
