from pathlib import Path

import pytest

from goodteam_os.project_readiness import (
    ProjectReadinessStore,
    build_capabilities,
    classify_project_mode,
)


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
