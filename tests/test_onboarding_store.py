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
    OnboardingJobStore,
    OnboardingObservationStore,
    checkpoint_path,
    inventory_path,
    job_metadata_path,
    job_status_path,
    model_event_path,
    observation_path,
    profile_manifest_path,
    profile_path,
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


def _created_onboarding_job(tmp_path: Path):
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingJobStore(workspace, key, root)
    store.create_job(
        "job-1",
        snapshot.snapshot_id,
        {
            "adapter": "scripted",
            "provider": "test",
            "model": "deterministic",
            "external_invocation": False,
            "preflight": {"available": True},
        },
    )
    return workspace, root, key, snapshot, store


def _profile_payload(
    *, profile_id: str, key: str, snapshot_id: str
) -> dict[str, object]:
    return {
        "schema": "project_profile.v1",
        "profile_id": profile_id,
        "project_key": key,
        "snapshot_id": snapshot_id,
        "generated_by": {"job_id": "job-1", "adapter": "scripted"},
        "claims": [],
        "evidence": [],
    }


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
        (
            b'{"schema":"repository_snapshot.v1"}',
            "Published onboarding snapshot is invalid.",
        ),
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


def test_artifact_store_verify_snapshot_is_descriptor_safe(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingArtifactStore(workspace, key, root)
    artifact = snapshot_path(workspace, key, snapshot.snapshot_id)
    original = artifact.with_name("original-snapshot.json")
    artifact.rename(original)
    artifact.symlink_to(original)

    with pytest.raises(ProjectImportError) as captured:
        store.verify_snapshot(snapshot.snapshot_id)

    assert captured.value.code == "onboarding_artifact_conflict"
    assert artifact.is_symlink()
    assert original.read_bytes() == json.dumps(
        snapshot.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def test_artifact_store_verify_snapshot_reports_missing_onboarding_directory(
    tmp_path: Path,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    store = OnboardingArtifactStore(workspace, key, root)
    onboarding = workspace / "state" / "projects" / key / "onboarding"
    moved = onboarding.with_name("onboarding-moved")
    onboarding.rename(moved)

    with pytest.raises(ProjectImportError) as captured:
        store.verify_snapshot(snapshot.snapshot_id)

    assert captured.value.code == "onboarding_snapshot_missing"
    assert moved.is_dir()


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

    monkeypatch.setattr(
        onboarding_store, "_verify_file_identity", swap_after_first_verify
    )

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

    monkeypatch.setattr(
        onboarding_store, "_verify_file_identity", swap_after_first_verify
    )

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


def test_artifact_store_reads_only_digest_and_snapshot_bound_inventory(
    tmp_path: Path,
) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    before_root = _target_files(root)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    artifacts = OnboardingArtifactStore(workspace, key, root)
    artifacts.publish_inventory(inventory)

    assert artifacts.read_inventory(snapshot.snapshot_id) == inventory
    assert _target_files(root) == before_root


def test_artifact_store_read_inventory_rejects_replaced_leaf(tmp_path: Path) -> None:
    workspace, root, key, snapshot = _published_snapshot(tmp_path)
    inventory = build_repository_inventory(root, snapshot=snapshot)
    artifacts = OnboardingArtifactStore(workspace, key, root)
    artifacts.publish_inventory(inventory)
    path = inventory_path(workspace, key, snapshot.snapshot_id)
    original = path.with_name("inventory-original.json")
    path.rename(original)
    path.symlink_to(original)

    with pytest.raises(ProjectImportError) as captured:
        artifacts.read_inventory(snapshot.snapshot_id)

    assert captured.value.code == "onboarding_artifact_conflict"
    assert path.is_symlink()


def test_job_store_persists_redacted_bound_workspace_only_records(
    tmp_path: Path,
) -> None:
    workspace, root, key, snapshot, store = _created_onboarding_job(tmp_path)
    before_root = _target_files(root)
    lease = store.acquire_lease("job-1", snapshot.snapshot_id, "runner-1")
    token = str(lease["lease_token"])

    store.write_status(
        "job-1",
        snapshot.snapshot_id,
        {
            "status": "running",
            "adapter": "scripted",
            "error": f"target was {root}; api_key=sk-abcdefghijklmnopqrstuvwxyz",
            "usage": {"input_tokens": 12},
        },
        owner="runner-1",
        lease_token=token,
    )
    store.write_checkpoint(
        "job-1",
        snapshot.snapshot_id,
        {"turn": 1, "provider": "test", "resume": True},
        owner="runner-1",
        lease_token=token,
    )
    store.publish_prompt(
        "job-1",
        snapshot.snapshot_id,
        {"messages": [{"role": "system", "content": f"never open {root}"}]},
    )
    store.append_model_event(
        "job-1",
        snapshot.snapshot_id,
        {
            "event_type": "adapter_response",
            "text": f"received / path {root}/README.md",
            "authorization": "Bearer secret-value",
        },
        owner="runner-1",
        lease_token=token,
    )
    profile = _profile_payload(
        profile_id="profile-1",
        key=key,
        snapshot_id=snapshot.snapshot_id,
    )
    published = store.publish_profile(
        "profile-1",
        "job-1",
        snapshot.snapshot_id,
        profile,
        {"projections": ["project-overview.md"], "source": f"{root}/README.md"},
        owner="runner-1",
        lease_token=token,
    )

    job = store.read_job("job-1", snapshot.snapshot_id)
    status = store.read_status("job-1", snapshot.snapshot_id)
    checkpoint = store.read_checkpoint("job-1", snapshot.snapshot_id)
    prompt = store.read_prompt("job-1", snapshot.snapshot_id)
    events = store.read_model_events("job-1", snapshot.snapshot_id)
    bundle = store.read_profile("profile-1", "job-1", snapshot.snapshot_id)

    assert job["canonical_root"] == str(root)
    assert status is not None and status["status"] == "running"
    assert checkpoint is not None and checkpoint["turn"] == 1
    assert prompt is not None
    assert events[0]["event_type"] == "adapter_response"
    assert published == profile_path(workspace, key, "profile-1")
    assert bundle["profile"] == profile
    assert bundle["manifest"]["manifest"]["projections"] == ["project-overview.md"]
    assert job_metadata_path(workspace, key, "job-1").is_file()
    assert job_status_path(workspace, key, "job-1").is_file()
    assert checkpoint_path(workspace, key, "job-1").is_file()
    assert model_event_path(workspace, key, "job-1").is_file()
    assert profile_manifest_path(workspace, key, "profile-1").is_file()
    assert str(root) not in json.dumps(status)
    assert str(root) not in json.dumps(prompt)
    assert str(root) not in json.dumps(events)
    assert "secret-value" not in json.dumps(events)
    assert str(root) not in json.dumps(bundle["manifest"])
    assert _target_files(root) == before_root

    store.release_lease("job-1", snapshot.snapshot_id, "runner-1", token)
    with pytest.raises(ProjectImportError) as captured:
        store.write_status(
            "job-1",
            snapshot.snapshot_id,
            {"status": "completed"},
            owner="runner-1",
            lease_token=token,
        )
    assert captured.value.code == "onboarding_lease_stale"


def test_job_store_rejects_cross_snapshot_state_and_lease_owner(
    tmp_path: Path,
) -> None:
    workspace, root, key, first, store = _created_onboarding_job(tmp_path)
    lease = store.acquire_lease("job-1", first.snapshot_id, "runner-1")
    token = str(lease["lease_token"])
    (root / "changed.py").write_text("VALUE = 1\n", encoding="utf-8")
    second = capture_repository_snapshot(root, project_key=key)
    OnboardingArtifactStore(workspace, key, root).publish_snapshot(second)

    with pytest.raises(ProjectImportError) as captured:
        store.write_status(
            "job-1",
            second.snapshot_id,
            {"status": "running"},
            owner="runner-1",
            lease_token=token,
        )
    assert captured.value.code == "onboarding_job_mismatch"

    with pytest.raises(ProjectImportError) as captured:
        store.write_checkpoint(
            "job-1",
            first.snapshot_id,
            {"turn": 2},
            owner="runner-2",
            lease_token=token,
        )
    assert captured.value.code == "onboarding_lease_stale"
    assert not job_status_path(workspace, key, "job-1").exists()


def test_job_store_lease_acquisition_is_single_owner_under_race(
    tmp_path: Path,
) -> None:
    _workspace, _root, _key, snapshot, store = _created_onboarding_job(tmp_path)

    def acquire(index: int) -> tuple[str, str]:
        try:
            lease = store.acquire_lease(
                "job-1",
                snapshot.snapshot_id,
                f"runner-{index}",
            )
        except ProjectImportError as exc:
            return "error", exc.code
        return "lease", str(lease["owner"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(acquire, range(8)))

    assert sum(kind == "lease" for kind, _value in outcomes) == 1
    assert {value for kind, value in outcomes if kind == "error"} == {
        "onboarding_lease_conflict"
    }


def test_job_store_rejects_status_leaf_replacement_after_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot, store = _created_onboarding_job(tmp_path)
    before_root = _target_files(root)
    lease = store.acquire_lease("job-1", snapshot.snapshot_id, "runner-1")
    token = str(lease["lease_token"])
    store.write_status(
        "job-1",
        snapshot.snapshot_id,
        {"status": "running"},
        owner="runner-1",
        lease_token=token,
    )
    status_path = job_status_path(workspace, key, "job-1")
    replaced = status_path.with_name("status-replaced.json")
    real_verify = onboarding_store._verify_file_identity
    swapped = False

    def swap_status_after_verify(directory_fd: int, name: str, descriptor: int) -> None:
        nonlocal swapped
        real_verify(directory_fd, name, descriptor)
        if not swapped and name == "status.json":
            swapped = True
            status_path.rename(replaced)
            status_path.write_text('{"foreign":true}', encoding="utf-8")

    monkeypatch.setattr(
        onboarding_store,
        "_verify_file_identity",
        swap_status_after_verify,
    )

    with pytest.raises(ProjectImportError) as captured:
        store.write_status(
            "job-1",
            snapshot.snapshot_id,
            {"status": "completed"},
            owner="runner-1",
            lease_token=token,
        )

    assert swapped
    assert captured.value.code == "onboarding_status_invalid"
    assert status_path.read_text(encoding="utf-8") == '{"foreign":true}'
    assert _target_files(root) == before_root


def test_job_store_rejects_profile_missing_final_manifest_marker(
    tmp_path: Path,
) -> None:
    workspace, root, key, snapshot, store = _created_onboarding_job(tmp_path)
    before_root = _target_files(root)
    profile = _profile_payload(
        profile_id="profile-1",
        key=key,
        snapshot_id=snapshot.snapshot_id,
    )
    incomplete = profile_path(workspace, key, "profile-1")
    incomplete.parent.mkdir(parents=True)
    incomplete.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(ProjectImportError) as captured:
        store.read_profile("profile-1", "job-1", snapshot.snapshot_id)

    assert captured.value.code == "onboarding_profile_invalid"
    assert not profile_manifest_path(workspace, key, "profile-1").exists()
    assert _target_files(root) == before_root


def test_job_store_recovers_matching_profile_body_missing_final_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, root, key, snapshot, store = _created_onboarding_job(tmp_path)
    before_root = _target_files(root)
    lease = store.acquire_lease("job-1", snapshot.snapshot_id, "runner-1")
    token = str(lease["lease_token"])
    profile = _profile_payload(
        profile_id="profile-1",
        key=key,
        snapshot_id=snapshot.snapshot_id,
    )
    original_publish = onboarding_store._publish_immutable_json
    failures_remaining = 1

    def fail_manifest_once(
        directory_fd: int,
        name: str,
        encoded: bytes,
        *,
        equivalent,
    ) -> None:
        nonlocal failures_remaining
        if name == "artifact-manifest.json" and failures_remaining:
            failures_remaining -= 1
            raise OSError("interrupted between profile body and manifest")
        original_publish(directory_fd, name, encoded, equivalent=equivalent)

    monkeypatch.setattr(
        onboarding_store,
        "_publish_immutable_json",
        fail_manifest_once,
    )

    with pytest.raises(OSError):
        store.publish_profile(
            "profile-1",
            "job-1",
            snapshot.snapshot_id,
            profile,
            {"track": "test"},
            owner="runner-1",
            lease_token=token,
        )

    assert profile_path(workspace, key, "profile-1").is_file()
    assert not profile_manifest_path(workspace, key, "profile-1").exists()

    published = store.publish_profile(
        "profile-1",
        "job-1",
        snapshot.snapshot_id,
        profile,
        {"track": "test"},
        owner="runner-1",
        lease_token=token,
    )

    assert published == profile_path(workspace, key, "profile-1")
    assert profile_manifest_path(workspace, key, "profile-1").is_file()
    assert store.read_profile("profile-1", "job-1", snapshot.snapshot_id)["profile"] == profile
    assert _target_files(root) == before_root


def test_job_store_rejects_conflicting_partial_profile_body(tmp_path: Path) -> None:
    workspace, root, key, snapshot, store = _created_onboarding_job(tmp_path)
    before_root = _target_files(root)
    lease = store.acquire_lease("job-1", snapshot.snapshot_id, "runner-1")
    token = str(lease["lease_token"])
    profile = _profile_payload(
        profile_id="profile-1",
        key=key,
        snapshot_id=snapshot.snapshot_id,
    )
    partial_path = profile_path(workspace, key, "profile-1")
    partial_path.parent.mkdir(parents=True)
    partial_path.write_text(json.dumps(profile), encoding="utf-8")
    conflicting = {**profile, "claims": [{"text": "conflicting"}]}

    with pytest.raises(ProjectImportError) as captured:
        store.publish_profile(
            "profile-1",
            "job-1",
            snapshot.snapshot_id,
            conflicting,
            {"track": "test"},
            owner="runner-1",
            lease_token=token,
        )

    assert captured.value.code == "onboarding_artifact_conflict"
    assert partial_path.read_text(encoding="utf-8") == json.dumps(profile)
    assert not profile_manifest_path(workspace, key, "profile-1").exists()
    assert _target_files(root) == before_root
