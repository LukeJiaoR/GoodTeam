from __future__ import annotations

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from goodteam_os import onboarding_store
from goodteam_os.onboarding_store import (
    OnboardingArtifactStore,
    OnboardingObservationStore,
    inventory_path,
    observation_path,
    snapshot_path,
)
from goodteam_os.project_import import ProjectImportError, import_project
from goodteam_os.repository_inventory import build_repository_inventory
from goodteam_os.repository_snapshot import capture_repository_snapshot


def _imported_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("unchanged\n", encoding="utf-8")
    result = import_project(workspace, str(root))
    return workspace, root, str(result["project"]["project_key"])


def _published_snapshot(tmp_path: Path):
    workspace, root, key = _imported_repository(tmp_path)
    snapshot = capture_repository_snapshot(root, project_key=key)
    OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)
    return workspace, root, key, snapshot


def _target_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_store_writes_only_to_goodteam_workspace(tmp_path: Path) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    before = _target_files(root)
    snapshot = capture_repository_snapshot(root, project_key=key)
    OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )

    path = store.append(
        {
            "tool_call_id": "obs-1",
            "snapshot_id": snapshot.snapshot_id,
            "tool": "list_tree",
        }
    )

    expected = (
        workspace
        / "state"
        / "projects"
        / key
        / "onboarding"
        / "jobs"
        / "job-1"
        / "observations.jsonl"
    )
    assert path == expected
    assert store.read_all() == [
        {
            "snapshot_id": snapshot.snapshot_id,
            "tool": "list_tree",
            "tool_call_id": "obs-1",
        }
    ]
    assert _target_files(root) == before


def test_store_rejects_wrong_root(tmp_path: Path) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(ProjectImportError, match="does not match"):
        OnboardingObservationStore(workspace, key, other, "job-1", "snap-1")

    assert _target_files(root) == {Path("README.md"): b"unchanged\n"}


def test_artifact_store_publishes_immutable_snapshot_and_inventory(
    tmp_path: Path,
) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    snapshot = capture_repository_snapshot(root, project_key=key)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    store = OnboardingArtifactStore(workspace, key, root)

    published_snapshot = store.publish_snapshot(snapshot)
    published_inventory = store.publish_inventory(inventory)
    original_snapshot_bytes = published_snapshot.read_bytes()

    assert published_snapshot == snapshot_path(
        workspace,
        key,
        snapshot.snapshot_id,
    )
    assert published_inventory == inventory_path(
        workspace,
        key,
        snapshot.snapshot_id,
    )
    assert (
        json.loads(published_snapshot.read_text(encoding="utf-8")) == snapshot.as_dict()
    )
    assert json.loads(published_inventory.read_text(encoding="utf-8")) == inventory
    assert published_snapshot.read_bytes() == json.dumps(
        snapshot.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    recaptured = replace(snapshot, captured_at="2099-01-01T00:00:00Z")
    assert store.publish_snapshot(recaptured) == published_snapshot
    assert published_snapshot.read_bytes() == original_snapshot_bytes


def test_artifact_store_rejects_inventory_from_another_snapshot(
    tmp_path: Path,
) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    first = capture_repository_snapshot(root, project_key=key)
    OnboardingArtifactStore(workspace, key, root).publish_snapshot(first)
    (root / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
    second = capture_repository_snapshot(root, project_key=key)
    second_inventory = build_repository_inventory(root, snapshot=second)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_inventory(
            second_inventory
        )

    assert captured.value.code == "onboarding_snapshot_missing"
    assert not inventory_path(workspace, key, second.snapshot_id).exists()


def test_artifact_store_rejects_conflicting_republication(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingArtifactStore(workspace, key, root)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    original = artifact.read_bytes()
    conflicting = replace(
        snapshot,
        source_control={**snapshot.source_control, "dirty": "invalid"},
    )

    with pytest.raises(ProjectImportError) as captured:
        store.publish_snapshot(conflicting)

    assert captured.value.code == "onboarding_artifact_conflict"
    assert artifact.read_bytes() == original


def test_artifact_store_rejects_racing_malformed_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    snapshot = capture_repository_snapshot(root, project_key=key)
    destination = snapshot_path(workspace, key, snapshot.snapshot_id)

    def publish_foreign_then_conflict(
        _source: str,
        name: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        del src_dir_fd, follow_symlinks
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, b"{malformed}")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        raise FileExistsError(errno.EEXIST, "injected publication race")

    monkeypatch.setattr(onboarding_store.os, "link", publish_foreign_then_conflict)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)

    assert captured.value.code == "onboarding_artifact_conflict"
    assert destination.read_bytes() == b"{malformed}"


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (b"{malformed}", "Published onboarding snapshot is invalid."),
        (b"\xff\xfeinvalid-utf8", "Published onboarding snapshot is invalid."),
    ],
)
def test_publish_inventory_rejects_malformed_existing_snapshot_artifact(
    tmp_path: Path,
    payload: bytes,
    expected_message: str,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    before_root = _target_files(root)
    artifact.write_bytes(payload)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_inventory(inventory)

    assert captured.value.code == "onboarding_artifact_conflict"
    assert str(captured.value) == expected_message
    assert artifact.read_bytes() == payload
    assert _target_files(root) == before_root


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (b"[]", "Published onboarding snapshot is invalid."),
        (b"{\"schema\":\"repository_snapshot.v1\"}", "Published onboarding snapshot is invalid."),
    ],
)
def test_observation_store_init_rejects_malformed_existing_snapshot_artifact(
    tmp_path: Path,
    payload: bytes,
    expected_message: str,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    before_root = _target_files(root)
    artifact.write_bytes(payload)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingObservationStore(
            workspace,
            key,
            root,
            "job-1",
            snapshot.snapshot_id,
        )

    assert captured.value.code == "onboarding_artifact_conflict"
    assert str(captured.value) == expected_message
    assert artifact.read_bytes() == payload
    assert _target_files(root) == before_root


def test_observation_store_rejects_another_snapshot(tmp_path: Path) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    first = capture_repository_snapshot(root, project_key=key)
    store = OnboardingArtifactStore(workspace, key, root)
    store.publish_snapshot(first)
    (root / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
    second = capture_repository_snapshot(root, project_key=key)
    store.publish_snapshot(second)
    observations = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        first.snapshot_id,
    )

    with pytest.raises(ProjectImportError) as captured:
        observations.append(
            {"tool_call_id": "obs-1", "snapshot_id": second.snapshot_id}
        )

    assert captured.value.code == "onboarding_snapshot_mismatch"
    assert not observation_path(workspace, key, "job-1").exists()


def test_store_rejects_invalid_job_id(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)

    for job_id in ("", ".", "..", "job/escape", "job\\escape", "job id"):
        with pytest.raises(ProjectImportError) as captured:
            OnboardingObservationStore(
                workspace,
                key,
                root,
                job_id,
                snapshot.snapshot_id,
            )
        assert captured.value.code == "onboarding_identifier_invalid"


def test_store_rejects_onboarding_symlink(tmp_path: Path) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    snapshot = capture_repository_snapshot(root, project_key=key)
    external = tmp_path / "external"
    external.mkdir()
    onboarding = workspace / "state" / "projects" / key / "onboarding"
    onboarding.symlink_to(external, target_is_directory=True)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)

    assert captured.value.code == "project_workspace_not_isolated"
    assert list(external.iterdir()) == []


def test_store_detects_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key = _imported_repository(tmp_path)
    snapshot = capture_repository_snapshot(root, project_key=key)
    snapshots = workspace / "state" / "projects" / key / "onboarding" / "snapshots"
    replaced = snapshots.with_name("snapshots-replaced")
    real_fsync = onboarding_store.os.fsync
    swapped = False

    def replace_directory_before_fsync(descriptor: int) -> None:
        nonlocal swapped
        if not swapped and snapshots.is_dir():
            swapped = True
            snapshots.rename(replaced)
            snapshots.mkdir()
        real_fsync(descriptor)

    monkeypatch.setattr(onboarding_store.os, "fsync", replace_directory_before_fsync)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)

    assert swapped
    assert captured.value.code == "project_workspace_not_isolated"
    assert not snapshot_path(workspace, key, snapshot.snapshot_id).exists()


def test_store_detects_directory_replacement_when_write_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    job_directory = workspace / "state" / "projects" / key / "onboarding" / "jobs"
    replaced = job_directory.with_name("jobs-replaced")

    def replace_directory_then_fail(_descriptor: int, _content: bytes) -> None:
        job_directory.rename(replaced)
        job_directory.mkdir()
        raise OSError(errno.EIO, "injected write failure")

    monkeypatch.setattr(onboarding_store, "_write_all", replace_directory_then_fail)

    with pytest.raises(ProjectImportError) as captured:
        store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id})

    assert captured.value.code == "project_workspace_not_isolated"


def test_append_rejects_leaf_swap_after_first_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    before_root = _target_files(root)
    observations = observation_path(workspace, key, "job-1")
    real_verify = onboarding_store._verify_file_identity
    swapped = False

    def swap_after_first_verify(directory_fd: int, name: str, descriptor: int) -> None:
        nonlocal swapped
        real_verify(directory_fd, name, descriptor)
        if not swapped and name == "observations.jsonl":
            swapped = True
            replaced = observations.with_name("observations-replaced.jsonl")
            if observations.exists():
                observations.rename(replaced)
            observations.write_text('{"foreign":true}\n', encoding="utf-8")

    monkeypatch.setattr(onboarding_store, "_verify_file_identity", swap_after_first_verify)

    with pytest.raises(ProjectImportError) as captured:
        store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id})

    assert swapped
    assert captured.value.code == "onboarding_observation_invalid"
    assert observations.read_text(encoding="utf-8") == '{"foreign":true}\n'
    assert _target_files(root) == before_root


@pytest.mark.parametrize("operation", ["append", "read_all"])
def test_observation_store_rejects_directory_leaf(
    tmp_path: Path,
    operation: str,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    before_root = _target_files(root)
    store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id})
    observations = observation_path(workspace, key, "job-1")
    observations.unlink()
    observations.mkdir()

    with pytest.raises(ProjectImportError) as captured:
        if operation == "append":
            store.append({"tool_call_id": "obs-2", "snapshot_id": snapshot.snapshot_id})
        else:
            store.read_all()

    assert captured.value.code == "onboarding_observation_invalid"
    assert observations.is_dir()
    assert _target_files(root) == before_root


@pytest.mark.parametrize("operation", ["publish_inventory", "init_store"])
def test_snapshot_artifact_directory_leaf_is_artifact_conflict(
    tmp_path: Path,
    operation: str,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    before_root = _target_files(root)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    artifact.unlink()
    artifact.mkdir()

    with pytest.raises(ProjectImportError) as captured:
        if operation == "publish_inventory":
            OnboardingArtifactStore(workspace, key, root).publish_inventory(inventory)
        else:
            OnboardingObservationStore(
                workspace,
                key,
                root,
                "job-1",
                snapshot.snapshot_id,
            )

    assert captured.value.code == "onboarding_artifact_conflict"
    assert artifact.is_dir()
    assert _target_files(root) == before_root


def test_publish_inventory_rejects_snapshot_leaf_swap_after_first_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    before_root = _target_files(root)
    real_verify = onboarding_store._verify_file_identity
    swapped = False

    def swap_after_first_verify(directory_fd: int, name: str, descriptor: int) -> None:
        nonlocal swapped
        real_verify(directory_fd, name, descriptor)
        if not swapped and name == f"{snapshot.snapshot_id}.json":
            swapped = True
            replaced = artifact.with_name(f"{artifact.stem}.replaced.json")
            if artifact.exists():
                artifact.rename(replaced)
            artifact.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(onboarding_store, "_verify_file_identity", swap_after_first_verify)

    with pytest.raises(ProjectImportError) as captured:
        OnboardingArtifactStore(workspace, key, root).publish_inventory(inventory)

    assert swapped
    assert captured.value.code == "onboarding_artifact_conflict"
    assert artifact.read_text(encoding="utf-8") == "{}"
    assert _target_files(root) == before_root


def test_concurrent_appends_are_complete_json_records(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )

    def append(index: int) -> None:
        store.append(
            {
                "tool_call_id": f"obs-{index}",
                "snapshot_id": snapshot.snapshot_id,
                "index": index,
            }
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    records = store.read_all()
    assert len(records) == 40
    assert {record["index"] for record in records} == set(range(40))
    raw_lines = observation_path(workspace, key, "job-1").read_bytes().splitlines()
    assert len(raw_lines) == 40
    assert all(isinstance(json.loads(line), dict) for line in raw_lines)


def test_append_fsyncs_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    real_fsync = onboarding_store.os.fsync
    calls: list[int] = []

    def record_fsync(descriptor: int) -> None:
        calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(onboarding_store.os, "fsync", record_fsync)
    store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id})

    assert calls
    assert observation_path(workspace, key, "job-1").is_file()


def test_read_all_reports_malformed_json_line(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingObservationStore(
        workspace,
        key,
        root,
        "job-1",
        snapshot.snapshot_id,
    )
    store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id})
    with observation_path(workspace, key, "job-1").open("ab") as observations:
        observations.write(b"{not-json}\n")
        observations.flush()
        os.fsync(observations.fileno())

    with pytest.raises(ProjectImportError, match="line 2") as captured:
        store.read_all()

    assert captured.value.code == "onboarding_observation_invalid"
