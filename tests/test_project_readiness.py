import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

from goodteam_os.project_readiness import (
    ProjectReadinessStore,
    build_capabilities,
    classify_project_mode,
)
from goodteam_os.state import normalize_project_key


def test_classify_empty_directory_as_new_project(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    assert classify_project_mode(root) == "new_project"


def test_classify_manifest_repository_as_existing_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text('{"name":"sample"}', encoding="utf-8")
    assert classify_project_mode(root) == "existing_project"


def test_classify_unstructured_nonempty_directory_as_unconfirmed(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")
    assert classify_project_mode(root) == "unconfirmed"


def test_readiness_initialization_is_recoverable_and_mode_confirmation_is_persisted(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")

    store = ProjectReadinessStore(workspace)
    initial = store.initialize("DEMO", root)
    assert initial["lifecycle"] == "attention_required"
    assert initial["project_mode"] == "unconfirmed"
    assert [step["status"] for step in initial["steps"][:2]] == ["complete", "complete"]
    assert initial["steps"][2]["status"] == "needs_input"

    confirmed = store.confirm_mode("DEMO", "existing_project")
    assert confirmed["project_mode"] == "existing_project"
    assert store.read("DEMO") == confirmed
    assert build_capabilities(confirmed)["start_workflow"] is False
    assert (
        build_capabilities(confirmed)["start_workflow_reason"]
        == "mode_specific_readiness_pending"
    )


def test_initialize_known_mode_persists_versioned_shape_and_pending_steps(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    readiness = ProjectReadinessStore(workspace).initialize("DEMO", root)

    assert readiness["schema_version"] == 1
    assert readiness["canonical_root"] == str(root.resolve())
    assert readiness["project_mode"] == "existing_project"
    assert readiness["lifecycle"] == "preparing"
    assert readiness["steps"][2] == {
        "id": "project_mode",
        "status": "complete",
        "required": True,
    }
    assert [step["id"] for step in readiness["steps"][3:]] == [
        "repository_understanding",
        "command_verification",
        "constraints_confirmation",
        "worker_preflight",
    ]
    assert all(step["status"] == "pending" for step in readiness["steps"][3:])
    assert (workspace / "state" / "projects" / "DEMO" / "readiness.json").is_file()


def test_confirm_mode_rejects_invalid_mode_without_changing_persisted_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")
    store = ProjectReadinessStore(workspace)
    initial = store.initialize("DEMO", root)

    with pytest.raises(ValueError, match="new_project or existing_project"):
        store.confirm_mode("DEMO", "unconfirmed")

    assert store.read("DEMO") == initial


def test_reconfirm_mode_replaces_pending_mode_steps_without_duplicates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")
    store = ProjectReadinessStore(workspace)
    store.initialize("DEMO", root)

    store.confirm_mode("DEMO", "new_project")
    confirmed = store.confirm_mode("DEMO", "existing_project")

    step_ids = [step["id"] for step in confirmed["steps"]]
    assert step_ids == [
        "project_record",
        "safe_preflight",
        "project_mode",
        "repository_understanding",
        "command_verification",
        "constraints_confirmation",
        "worker_preflight",
    ]
    assert len(step_ids) == len(set(step_ids))


def test_absolute_project_key_is_normalized_and_cannot_escape_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    unsafe_key = str(tmp_path / "outside")
    normalized_key = normalize_project_key(unsafe_key)
    store = ProjectReadinessStore(workspace)

    readiness = store.initialize(unsafe_key, root)

    assert readiness["project_key"] == normalized_key
    assert store.read(unsafe_key) == readiness
    assert (
        workspace / "state" / "projects" / normalized_key / "readiness.json"
    ).is_file()
    assert not (tmp_path / "outside" / "readiness.json").exists()


def test_traversal_project_key_is_normalized_for_all_store_operations(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "assets"
    root.mkdir()
    (root / "notes.bin").write_bytes(b"asset")
    store = ProjectReadinessStore(workspace)

    store.initialize("../demo", root)
    confirmed = store.confirm_mode("../demo", "existing_project")

    assert confirmed["project_key"] == "DEMO"
    assert store.read("DEMO") == confirmed
    assert not (workspace / "state" / "demo" / "readiness.json").exists()


def test_missing_readiness_returns_none_and_cannot_be_confirmed(tmp_path: Path) -> None:
    store = ProjectReadinessStore(tmp_path / "workspace")

    assert store.read("missing") is None
    with pytest.raises(ValueError, match="No readiness found for project: MISSING"):
        store.confirm_mode("missing", "existing_project")


def test_concurrent_writers_use_unique_temps_and_readers_only_see_valid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first_root = tmp_path / "first"
    first_root.mkdir()
    second_root = tmp_path / "second"
    second_root.mkdir()
    (second_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    store = ProjectReadinessStore(workspace)
    store.initialize("DEMO", first_root)

    readiness_path = workspace / "state" / "projects" / "DEMO" / "readiness.json"
    replace_barrier = Barrier(2)
    sources_lock = Lock()
    temporary_sources: list[Path] = []
    original_replace = Path.replace

    def synchronized_replace(source: Path, target: Path) -> Path:
        if Path(target) == readiness_path:
            with sources_lock:
                temporary_sources.append(source)
            replace_barrier.wait(timeout=5)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)

    reader_started = Event()
    stop_reader = Event()

    def read_until_writers_finish() -> tuple[int, list[Exception]]:
        observations = 0
        errors: list[Exception] = []
        reader_started.set()
        while not stop_reader.is_set():
            try:
                readiness = store.read("DEMO")
                assert readiness is not None
                json.dumps(readiness)
                observations += 1
            except Exception as exc:  # pragma: no cover - asserted through returned errors
                errors.append(exc)
        return observations, errors

    def initialize_safely(root: Path) -> Exception | None:
        try:
            store.initialize("demo", root)
        except Exception as exc:  # pragma: no cover - asserted through return value
            return exc
        return None

    with ThreadPoolExecutor(max_workers=3) as executor:
        reader_future = executor.submit(read_until_writers_finish)
        assert reader_started.wait(timeout=5)
        writer_futures = [
            executor.submit(initialize_safely, first_root),
            executor.submit(initialize_safely, second_root),
        ]
        writer_errors = [future.result(timeout=5) for future in writer_futures]
        stop_reader.set()
        observations, reader_errors = reader_future.result(timeout=5)

    assert writer_errors == [None, None]
    assert reader_errors == []
    assert observations > 0
    assert len(temporary_sources) == 2
    assert temporary_sources[0] != temporary_sources[1]
    assert store.read("DEMO") is not None
