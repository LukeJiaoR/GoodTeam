from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goodteam_os._repository_content_policy import redact_observation_value
from goodteam_os.project_import import (
    ProjectImportError,
    bound_project_state_directory,
)
from goodteam_os.repository_inventory import compute_repository_inventory_digest
from goodteam_os.repository_snapshot import (
    RepositorySnapshot,
    compute_repository_snapshot_id,
)
from goodteam_os.state import normalize_project_key

_SAFE_IDENTIFIER = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_SNAPSHOT_FIELDS = frozenset(RepositorySnapshot.__dataclass_fields__)
_OBSERVATION_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_OBSERVATION_LOCKS_GUARD = threading.Lock()
_JOB_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_JOB_LOCKS_GUARD = threading.Lock()
_REDACTION_MARKER = "<redacted>"
_LEASE_MAX_SECONDS = 60 * 60
_MAX_PROMPT_BYTES = 256 * 1024
_MAX_MODEL_EVENT_BYTES = 256 * 1024
_MAX_MUTABLE_RECORD_BYTES = 512 * 1024


def snapshot_path(workspace: Path, project_key: str, snapshot_id: str) -> Path:
    identifier = _validate_identifier(snapshot_id)
    return (
        _canonical_workspace(workspace)
        / "state"
        / "projects"
        / normalize_project_key(project_key)
        / "onboarding"
        / "snapshots"
        / f"{identifier}.json"
    )


def inventory_path(workspace: Path, project_key: str, snapshot_id: str) -> Path:
    identifier = _validate_identifier(snapshot_id)
    return (
        _canonical_workspace(workspace)
        / "state"
        / "projects"
        / normalize_project_key(project_key)
        / "onboarding"
        / "inventories"
        / f"{identifier}.json"
    )


def observation_path(workspace: Path, project_key: str, job_id: str) -> Path:
    identifier = _validate_identifier(job_id)
    return (
        _canonical_workspace(workspace)
        / "state"
        / "projects"
        / normalize_project_key(project_key)
        / "onboarding"
        / "jobs"
        / identifier
        / "observations.jsonl"
    )


def job_path(workspace: Path, project_key: str, job_id: str) -> Path:
    identifier = _validate_identifier(job_id)
    return (
        _canonical_workspace(workspace)
        / "state"
        / "projects"
        / normalize_project_key(project_key)
        / "onboarding"
        / "jobs"
        / identifier
    )


def job_metadata_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return job_path(workspace, project_key, job_id) / "job.json"


def job_status_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return job_path(workspace, project_key, job_id) / "status.json"


def job_prompt_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return job_path(workspace, project_key, job_id) / "prompt.json"


def model_event_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return job_path(workspace, project_key, job_id) / "model-events.jsonl"


def checkpoint_path(
    workspace: Path,
    project_key: str,
    job_id: str,
    checkpoint_id: str = "current",
) -> Path:
    identifier = _validate_identifier(checkpoint_id)
    return (
        job_path(workspace, project_key, job_id) / "checkpoints" / f"{identifier}.json"
    )


def job_lease_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return job_path(workspace, project_key, job_id) / "lease.json"


def profile_path(workspace: Path, project_key: str, profile_id: str) -> Path:
    identifier = _validate_identifier(profile_id)
    return (
        _canonical_workspace(workspace)
        / "state"
        / "projects"
        / normalize_project_key(project_key)
        / "onboarding"
        / "profiles"
        / identifier
        / "project-profile.json"
    )


def profile_manifest_path(workspace: Path, project_key: str, profile_id: str) -> Path:
    return profile_path(workspace, project_key, profile_id).with_name(
        "artifact-manifest.json"
    )


class OnboardingArtifactStore:
    def __init__(self, workspace: Path, project_key: str, root: Path):
        self.workspace = _canonical_workspace(workspace)
        self.project_key = normalize_project_key(project_key)
        self.root = Path(root).expanduser().resolve()

    def publish_snapshot(self, snapshot: RepositorySnapshot) -> Path:
        payload = _validate_snapshot(snapshot, self.project_key, self.root)
        identifier = str(payload["snapshot_id"])
        destination = snapshot_path(self.workspace, self.project_key, identifier)
        encoded = _encode_json(payload, conflict=True)
        with (
            bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                True,
            ) as onboarding_fd,
            _bound_child_directories(
                onboarding_fd,
                ("snapshots",),
                create=True,
            ) as snapshots_fd,
        ):
            _publish_immutable_json(
                snapshots_fd,
                f"{identifier}.json",
                encoded,
                equivalent=lambda current: _snapshot_equivalent(
                    current,
                    payload,
                    project_key=self.project_key,
                    root=self.root,
                ),
            )
        return destination

    def verify_snapshot(self, snapshot_id: str) -> RepositorySnapshot:
        identifier = _validate_identifier(snapshot_id)
        try:
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                payload = _require_snapshot_artifact(
                    onboarding_fd,
                    identifier,
                    project_key=self.project_key,
                    root=self.root,
                )
        except FileNotFoundError as exc:
            raise _snapshot_missing(identifier) from exc
        return RepositorySnapshot(**payload)

    def publish_inventory(self, inventory: dict[str, object]) -> Path:
        payload = _validate_inventory(inventory)
        identifier = str(payload["snapshot_id"])
        destination = inventory_path(self.workspace, self.project_key, identifier)
        encoded = _encode_json(payload, conflict=True)
        try:
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                _require_snapshot_artifact(
                    onboarding_fd,
                    identifier,
                    project_key=self.project_key,
                    root=self.root,
                )
                with _bound_child_directories(
                    onboarding_fd,
                    ("inventories",),
                    create=True,
                ) as inventories_fd:
                    _publish_immutable_json(
                        inventories_fd,
                        f"{identifier}.json",
                        encoded,
                        equivalent=lambda current: current == payload,
                    )
        except FileNotFoundError as exc:
            raise _snapshot_missing(identifier) from exc
        return destination

    def read_inventory(self, snapshot_id: str) -> dict[str, object]:
        """Read a digest-verified inventory only through its snapshot binding."""

        identifier = _validate_identifier(snapshot_id)
        try:
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                _require_snapshot_artifact(
                    onboarding_fd,
                    identifier,
                    project_key=self.project_key,
                    root=self.root,
                )
                with _bound_child_directories(
                    onboarding_fd,
                    ("inventories",),
                    create=False,
                ) as inventories_fd:
                    payload = _read_json_file(
                        inventories_fd,
                        f"{identifier}.json",
                        invalid_message="Published onboarding inventory is invalid.",
                    )
        except FileNotFoundError as exc:
            raise _inventory_missing(identifier) from exc
        try:
            inventory = _validate_inventory(payload)
        except ProjectImportError:
            raise
        except (TypeError, ValueError) as exc:
            raise _artifact_conflict(
                "Published onboarding inventory is invalid."
            ) from exc
        if inventory.get("snapshot_id") != identifier:
            raise _artifact_conflict(
                "Published onboarding inventory identity conflicts."
            )
        return inventory


class OnboardingObservationStore:
    def __init__(
        self,
        workspace: Path,
        project_key: str,
        root: Path,
        job_id: str,
        snapshot_id: str,
    ):
        self.workspace = _canonical_workspace(workspace)
        self.project_key = normalize_project_key(project_key)
        self.root = Path(root).expanduser().resolve()
        self.job_id = _validate_identifier(job_id)
        self.snapshot_id = _validate_identifier(snapshot_id)
        self._require_snapshot()

    def append(self, record: dict[str, object]) -> Path:
        if not isinstance(record, dict):
            raise _observation_invalid("Onboarding observation must be a JSON object.")
        if record.get("snapshot_id") != self.snapshot_id:
            raise ProjectImportError(
                "onboarding_snapshot_mismatch",
                "Onboarding observation does not match the store snapshot.",
                details={"snapshot_id": self.snapshot_id},
            )
        encoded = _encode_json(record, conflict=False) + b"\n"
        with _observation_lock(self.workspace, self.project_key, self.job_id):
            try:
                with bound_project_state_directory(
                    self.workspace,
                    self.project_key,
                    self.root,
                    ("onboarding",),
                    False,
                ) as onboarding_fd:
                    _require_snapshot_artifact(
                        onboarding_fd,
                        self.snapshot_id,
                        project_key=self.project_key,
                        root=self.root,
                    )
                    with _bound_child_directories(
                        onboarding_fd,
                        ("jobs", self.job_id),
                        create=True,
                    ) as job_fd:
                        descriptor = _open_observation_for_append(job_fd)
                        pending_error: ProjectImportError | None = None
                        try:
                            _write_all(descriptor, encoded)
                            os.fsync(descriptor)
                            _verify_observation_leaf(
                                job_fd,
                                "observations.jsonl",
                                descriptor,
                            )
                        finally:
                            try:
                                _verify_observation_leaf(
                                    job_fd,
                                    "observations.jsonl",
                                    descriptor,
                                )
                            except ProjectImportError as exc:
                                pending_error = exc
                            finally:
                                os.close(descriptor)
                        if pending_error is not None:
                            raise pending_error
            except FileNotFoundError as exc:
                raise _snapshot_missing(self.snapshot_id) from exc
        return observation_path(self.workspace, self.project_key, self.job_id)

    def read_all(self) -> list[dict[str, object]]:
        with _observation_lock(self.workspace, self.project_key, self.job_id):
            try:
                with bound_project_state_directory(
                    self.workspace,
                    self.project_key,
                    self.root,
                    ("onboarding",),
                    False,
                ) as onboarding_fd:
                    _require_snapshot_artifact(
                        onboarding_fd,
                        self.snapshot_id,
                        project_key=self.project_key,
                        root=self.root,
                    )
                    try:
                        with _bound_child_directories(
                            onboarding_fd,
                            ("jobs", self.job_id),
                            create=False,
                        ) as job_fd:
                            try:
                                descriptor = _open_observation_file(
                                    job_fd,
                                    "observations.jsonl",
                                )
                            except FileNotFoundError:
                                return []
                            pending_error: ProjectImportError | None = None
                            try:
                                content = _read_all(descriptor)
                                _verify_observation_leaf(
                                    job_fd,
                                    "observations.jsonl",
                                    descriptor,
                                )
                            finally:
                                try:
                                    _verify_observation_leaf(
                                        job_fd,
                                        "observations.jsonl",
                                        descriptor,
                                    )
                                except ProjectImportError as exc:
                                    pending_error = exc
                                finally:
                                    os.close(descriptor)
                            if pending_error is not None:
                                raise pending_error
                    except FileNotFoundError:
                        return []
            except FileNotFoundError as exc:
                raise _snapshot_missing(self.snapshot_id) from exc
        return _parse_observations(content, snapshot_id=self.snapshot_id)

    def _require_snapshot(self) -> None:
        try:
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                _require_snapshot_artifact(
                    onboarding_fd,
                    self.snapshot_id,
                    project_key=self.project_key,
                    root=self.root,
                )
        except FileNotFoundError as exc:
            raise _snapshot_missing(self.snapshot_id) from exc


class OnboardingJobStore:
    """Descriptor-anchored, workspace-only storage for onboarding runner state.

    A job's immutable metadata is the root binding for all of its mutable records.
    Mutable records require a current lease token, so an interrupted runner cannot
    publish status, checkpoints, model events, or a profile after another runner
    has acquired the job.
    """

    def __init__(self, workspace: Path, project_key: str, root: Path):
        self.workspace = _canonical_workspace(workspace)
        self.project_key = normalize_project_key(project_key)
        self.root = Path(root).expanduser().resolve()

    def create_job(
        self,
        job_id: str,
        snapshot_id: str,
        metadata: dict[str, object],
    ) -> Path:
        """Publish immutable job metadata after verifying the snapshot binding."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        payload = _build_job_payload(
            metadata,
            project_key=self.project_key,
            root=self.root,
            job_id=identifier,
            snapshot_id=snapshot,
        )
        encoded = _encode_job_json(payload, invalid=_job_invalid)
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                _require_snapshot_artifact(
                    onboarding_fd,
                    snapshot,
                    project_key=self.project_key,
                    root=self.root,
                )
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=True,
                ) as job_fd:
                    _publish_immutable_json(
                        job_fd,
                        "job.json",
                        encoded,
                        equivalent=lambda current: _job_equivalent(
                            current,
                            payload,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=identifier,
                            snapshot_id=snapshot,
                        ),
                    )
        return job_metadata_path(self.workspace, self.project_key, identifier)

    def read_job(
        self,
        job_id: str,
        snapshot_id: str | None = None,
    ) -> dict[str, object]:
        """Read immutable metadata and verify its job/snapshot/root binding."""

        identifier = _validate_identifier(job_id)
        expected_snapshot = (
            _validate_identifier(snapshot_id) if snapshot_id is not None else None
        )
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    return _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=expected_snapshot,
                    )

    def read_status(
        self,
        job_id: str,
        snapshot_id: str | None = None,
    ) -> dict[str, object] | None:
        """Read the latest status, or ``None`` before a runner has written one."""

        return self._read_mutable_record(
            job_id,
            snapshot_id,
            name="status.json",
            schema="onboarding_job_status.v1",
            invalid=_status_invalid,
        )

    def write_status(
        self,
        job_id: str,
        snapshot_id: str,
        status: dict[str, object],
        *,
        owner: str,
        lease_token: str,
    ) -> Path:
        """Atomically replace status after proving active lease ownership."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        payload = _build_redacted_bound_payload(
            status,
            schema="onboarding_job_status.v1",
            project_key=self.project_key,
            root=self.root,
            workspace=self.workspace,
            job_id=identifier,
            snapshot_id=snapshot,
            invalid=_status_invalid,
            timestamp_field="updated_at",
        )
        encoded = _encode_bounded_job_json(
            payload,
            invalid=_status_invalid,
            limit=_MAX_MUTABLE_RECORD_BYTES,
        )
        self._write_mutable_record(
            identifier,
            snapshot,
            name="status.json",
            encoded=encoded,
            owner=owner,
            lease_token=lease_token,
            invalid=_status_invalid,
        )
        return job_status_path(self.workspace, self.project_key, identifier)

    def read_checkpoint(
        self,
        job_id: str,
        snapshot_id: str | None = None,
        *,
        checkpoint_id: str = "current",
    ) -> dict[str, object] | None:
        """Read one named mutable checkpoint, if it has been persisted."""

        checkpoint = _validate_identifier(checkpoint_id)
        return self._read_mutable_record(
            job_id,
            snapshot_id,
            name=f"checkpoints/{checkpoint}.json",
            schema="onboarding_job_checkpoint.v1",
            invalid=_checkpoint_invalid,
            checkpoint_id=checkpoint,
        )

    def write_checkpoint(
        self,
        job_id: str,
        snapshot_id: str,
        checkpoint: dict[str, object],
        *,
        checkpoint_id: str = "current",
        owner: str,
        lease_token: str,
    ) -> Path:
        """Atomically replace a named checkpoint under the active job lease."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        checkpoint_name = _validate_identifier(checkpoint_id)
        payload = _build_redacted_bound_payload(
            checkpoint,
            schema="onboarding_job_checkpoint.v1",
            project_key=self.project_key,
            root=self.root,
            workspace=self.workspace,
            job_id=identifier,
            snapshot_id=snapshot,
            invalid=_checkpoint_invalid,
            timestamp_field="saved_at",
            extra={"checkpoint_id": checkpoint_name},
        )
        encoded = _encode_bounded_job_json(
            payload,
            invalid=_checkpoint_invalid,
            limit=_MAX_MUTABLE_RECORD_BYTES,
        )
        self._write_mutable_record(
            identifier,
            snapshot,
            name=f"checkpoints/{checkpoint_name}.json",
            encoded=encoded,
            owner=owner,
            lease_token=lease_token,
            invalid=_checkpoint_invalid,
        )
        return checkpoint_path(
            self.workspace,
            self.project_key,
            identifier,
            checkpoint_name,
        )

    def publish_prompt(
        self,
        job_id: str,
        snapshot_id: str,
        prompt: dict[str, object],
    ) -> Path:
        """Publish an immutable, bounded, redacted prompt record for a job."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        payload = _build_redacted_bound_payload(
            prompt,
            schema="onboarding_job_prompt.v1",
            project_key=self.project_key,
            root=self.root,
            workspace=self.workspace,
            job_id=identifier,
            snapshot_id=snapshot,
            invalid=_prompt_invalid,
            timestamp_field="created_at",
        )
        encoded = _encode_bounded_job_json(
            payload,
            invalid=_prompt_invalid,
            limit=_MAX_PROMPT_BYTES,
        )
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                    )
                    _publish_immutable_json(
                        job_fd,
                        "prompt.json",
                        encoded,
                        equivalent=lambda current: _redacted_record_equivalent(
                            current,
                            payload,
                            schema="onboarding_job_prompt.v1",
                            project_key=self.project_key,
                            root=self.root,
                            job_id=identifier,
                            snapshot_id=snapshot,
                            invalid=_prompt_invalid,
                            ignored_fields={"created_at"},
                        ),
                    )
        return job_prompt_path(self.workspace, self.project_key, identifier)

    def read_prompt(
        self,
        job_id: str,
        snapshot_id: str | None = None,
    ) -> dict[str, object] | None:
        """Read a job's redacted prompt record, if it has been published."""

        return self._read_mutable_record(
            job_id,
            snapshot_id,
            name="prompt.json",
            schema="onboarding_job_prompt.v1",
            invalid=_prompt_invalid,
        )

    def append_model_event(
        self,
        job_id: str,
        snapshot_id: str,
        event: dict[str, object],
        *,
        owner: str,
        lease_token: str,
    ) -> Path:
        """Append one bounded, redacted model event under the active job lease."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        payload = _build_redacted_bound_payload(
            event,
            schema="onboarding_model_event.v1",
            project_key=self.project_key,
            root=self.root,
            workspace=self.workspace,
            job_id=identifier,
            snapshot_id=snapshot,
            invalid=_model_event_invalid,
            timestamp_field="recorded_at",
        )
        encoded = (
            _encode_bounded_job_json(
                payload,
                invalid=_model_event_invalid,
                limit=_MAX_MODEL_EVENT_BYTES,
            )
            + b"\n"
        )
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                    )
                    _require_active_lease_in_directory(
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                        owner=owner,
                        lease_token=lease_token,
                    )
                    descriptor = _open_model_event_for_append(job_fd)
                    pending_error: ProjectImportError | None = None
                    try:
                        _write_all(descriptor, encoded)
                        os.fsync(descriptor)
                        os.fsync(job_fd)
                        _verify_model_event_leaf(job_fd, descriptor)
                    finally:
                        try:
                            _verify_model_event_leaf(job_fd, descriptor)
                        except ProjectImportError as exc:
                            pending_error = exc
                        finally:
                            os.close(descriptor)
                    if pending_error is not None:
                        raise pending_error
        return model_event_path(self.workspace, self.project_key, identifier)

    def read_model_events(
        self,
        job_id: str,
        snapshot_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Read and validate the append-only redacted event ledger."""

        identifier = _validate_identifier(job_id)
        expected_snapshot = (
            _validate_identifier(snapshot_id) if snapshot_id is not None else None
        )
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    job = _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=expected_snapshot,
                    )
                    snapshot = str(job["snapshot_id"])
                    try:
                        descriptor = _open_model_event_file(job_fd)
                    except FileNotFoundError:
                        return []
                    pending_error: ProjectImportError | None = None
                    try:
                        content = _read_all(descriptor)
                        _verify_model_event_leaf(job_fd, descriptor)
                    finally:
                        try:
                            _verify_model_event_leaf(job_fd, descriptor)
                        except ProjectImportError as exc:
                            pending_error = exc
                        finally:
                            os.close(descriptor)
                    if pending_error is not None:
                        raise pending_error
        return _parse_model_events(
            content,
            project_key=self.project_key,
            root=self.root,
            job_id=identifier,
            snapshot_id=snapshot,
        )

    def read_lease(
        self,
        job_id: str,
        snapshot_id: str | None = None,
    ) -> dict[str, object] | None:
        """Read the current or released job lease without granting authority."""

        return self._read_mutable_record(
            job_id,
            snapshot_id,
            name="lease.json",
            schema="onboarding_job_lease.v1",
            invalid=_lease_invalid,
            lease_record=True,
        )

    def acquire_lease(
        self,
        job_id: str,
        snapshot_id: str,
        owner: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, object]:
        """Acquire a new lease only when no active lease remains for the job."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        owner_identifier = _validate_lease_identifier(owner)
        duration = _validate_lease_seconds(lease_seconds)
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                    )
                    existing = _read_optional_bound_json(
                        job_fd,
                        "lease.json",
                        invalid=_lease_invalid,
                    )
                    if existing is not None:
                        active = _validate_lease_payload(
                            existing,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=identifier,
                            snapshot_id=snapshot,
                        )
                        if _lease_is_active(active):
                            raise _lease_conflict(identifier)
                    payload = _new_lease_payload(
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                        owner=owner_identifier,
                        lease_seconds=duration,
                    )
                    _replace_mutable_json(
                        job_fd,
                        "lease.json",
                        _encode_job_json(payload, invalid=_lease_invalid),
                        invalid=_lease_invalid,
                    )
                    return payload

    def renew_lease(
        self,
        job_id: str,
        snapshot_id: str,
        owner: str,
        lease_token: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, object]:
        """Extend an active lease only for its current owner and opaque token."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        owner_identifier = _validate_lease_identifier(owner)
        token = _validate_lease_identifier(lease_token)
        duration = _validate_lease_seconds(lease_seconds)
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                    )
                    current = _require_active_lease_in_directory(
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                        owner=owner_identifier,
                        lease_token=token,
                    )
                    updated = dict(current)
                    now = _utc_now()
                    updated["updated_at"] = _format_timestamp(now)
                    updated["expires_at"] = _format_timestamp(
                        now + timedelta(seconds=duration)
                    )
                    _replace_mutable_json(
                        job_fd,
                        "lease.json",
                        _encode_job_json(updated, invalid=_lease_invalid),
                        invalid=_lease_invalid,
                    )
                    return updated

    def release_lease(
        self,
        job_id: str,
        snapshot_id: str,
        owner: str,
        lease_token: str,
    ) -> dict[str, object]:
        """Record an immutable-in-time release transition for the active lease."""

        identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        owner_identifier = _validate_lease_identifier(owner)
        token = _validate_lease_identifier(lease_token)
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                    )
                    current = _require_active_lease_in_directory(
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                        owner=owner_identifier,
                        lease_token=token,
                    )
                    released = dict(current)
                    released["state"] = "released"
                    released["updated_at"] = _format_timestamp(_utc_now())
                    released["released_at"] = released["updated_at"]
                    _replace_mutable_json(
                        job_fd,
                        "lease.json",
                        _encode_job_json(released, invalid=_lease_invalid),
                        invalid=_lease_invalid,
                    )
                    return released

    def publish_profile(
        self,
        profile_id: str,
        job_id: str,
        snapshot_id: str,
        profile: dict[str, object],
        manifest: dict[str, object],
        *,
        owner: str,
        lease_token: str,
    ) -> Path:
        """Publish a canonical profile and final manifest marker exactly once.

        The manifest is written last. Readers reject a profile directory that lacks
        it, so an interrupted publication never becomes a readable profile.
        """

        profile_identifier = _validate_identifier(profile_id)
        job_identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        canonical_profile = _validate_profile_payload(
            profile,
            profile_id=profile_identifier,
            project_key=self.project_key,
            root=self.root,
            job_id=job_identifier,
            snapshot_id=snapshot,
        )
        profile_encoded = _encode_job_json(canonical_profile, invalid=_profile_invalid)
        manifest_payload = _build_profile_manifest_payload(
            manifest,
            profile_id=profile_identifier,
            project_key=self.project_key,
            root=self.root,
            workspace=self.workspace,
            job_id=job_identifier,
            snapshot_id=snapshot,
            profile_encoded=profile_encoded,
        )
        manifest_encoded = _encode_job_json(manifest_payload, invalid=_profile_invalid)
        with _job_lock(self.workspace, self.project_key, job_identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", job_identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_identifier,
                        snapshot_id=snapshot,
                    )
                    _require_active_lease_in_directory(
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_identifier,
                        snapshot_id=snapshot,
                        owner=owner,
                        lease_token=lease_token,
                    )
                with _bound_child_directories(
                    onboarding_fd,
                    ("profiles",),
                    create=True,
                ) as profiles_fd:
                    if _directory_exists(
                        profiles_fd, profile_identifier
                    ) or not _create_directory_exclusively(
                        profiles_fd, profile_identifier
                    ):
                        with _bound_child_directories(
                            profiles_fd,
                            (profile_identifier,),
                            create=False,
                        ) as profile_fd:
                            current_profile, current_manifest = _recover_or_read_profile_bundle(
                                profile_fd,
                                expected_profile=canonical_profile,
                                expected_manifest=manifest_payload,
                                manifest_encoded=manifest_encoded,
                                profile_id=profile_identifier,
                                project_key=self.project_key,
                                root=self.root,
                                job_id=job_identifier,
                                snapshot_id=snapshot,
                            )
                        if _profile_bundle_equivalent(
                            current_profile,
                            current_manifest,
                            canonical_profile,
                            manifest_payload,
                            profile_id=profile_identifier,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=job_identifier,
                            snapshot_id=snapshot,
                        ):
                            return profile_path(
                                self.workspace,
                                self.project_key,
                                profile_identifier,
                            )
                        raise _artifact_conflict(
                            "Published onboarding profile conflicts."
                        )
                    os.fsync(profiles_fd)
                    with _bound_child_directories(
                        profiles_fd,
                        (profile_identifier,),
                        create=False,
                    ) as profile_fd:
                        _publish_immutable_json(
                            profile_fd,
                            "project-profile.json",
                            profile_encoded,
                            equivalent=lambda current: (
                                _validate_profile_payload(
                                    current,
                                    profile_id=profile_identifier,
                                    project_key=self.project_key,
                                    root=self.root,
                                    job_id=job_identifier,
                                    snapshot_id=snapshot,
                                )
                                == canonical_profile
                            ),
                        )
                        # This is the completion marker.  Do not reorder it before
                        # project-profile.json or readers could observe a profile
                        # that looks complete without its canonical content.
                        _publish_immutable_json(
                            profile_fd,
                            "artifact-manifest.json",
                            manifest_encoded,
                            equivalent=lambda current: _profile_manifest_equivalent(
                                current,
                                manifest_payload,
                                profile_id=profile_identifier,
                                project_key=self.project_key,
                                root=self.root,
                                job_id=job_identifier,
                                snapshot_id=snapshot,
                            ),
                        )
                        os.fsync(profile_fd)
                        stored_profile, stored_manifest = _read_profile_bundle(
                            profile_fd,
                            profile_id=profile_identifier,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=job_identifier,
                            snapshot_id=snapshot,
                        )
                        if not _profile_bundle_equivalent(
                            stored_profile,
                            stored_manifest,
                            canonical_profile,
                            manifest_payload,
                            profile_id=profile_identifier,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=job_identifier,
                            snapshot_id=snapshot,
                        ):
                            raise _artifact_conflict(
                                "Published onboarding profile conflicts."
                            )
        return profile_path(self.workspace, self.project_key, profile_identifier)

    def read_profile(
        self,
        profile_id: str,
        job_id: str,
        snapshot_id: str,
    ) -> dict[str, dict[str, object]]:
        """Read a complete immutable profile bundle bound to one onboarding job."""

        profile_identifier = _validate_identifier(profile_id)
        job_identifier = _validate_identifier(job_id)
        snapshot = _validate_identifier(snapshot_id)
        with _job_lock(self.workspace, self.project_key, job_identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", job_identifier),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_identifier,
                        snapshot_id=snapshot,
                    )
                with _bound_child_directories(
                    onboarding_fd,
                    ("profiles", profile_identifier),
                    create=False,
                ) as profile_fd:
                    profile, artifact_manifest = _read_profile_bundle(
                        profile_fd,
                        profile_id=profile_identifier,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_identifier,
                        snapshot_id=snapshot,
                    )
        return {"profile": profile, "manifest": artifact_manifest}

    def _read_mutable_record(
        self,
        job_id: str,
        snapshot_id: str | None,
        *,
        name: str,
        schema: str,
        invalid,
        checkpoint_id: str | None = None,
        lease_record: bool = False,
    ) -> dict[str, object] | None:
        identifier = _validate_identifier(job_id)
        expected_snapshot = (
            _validate_identifier(snapshot_id) if snapshot_id is not None else None
        )
        parts = tuple(name.split("/"))
        if len(parts) == 1:
            directory_parts: tuple[str, ...] = ()
            leaf = parts[0]
        else:
            directory_parts = parts[:-1]
            leaf = parts[-1]
        with _job_lock(self.workspace, self.project_key, identifier):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", identifier),
                    create=False,
                ) as job_fd:
                    job = _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=expected_snapshot,
                    )
                    snapshot = str(job["snapshot_id"])
                    try:
                        if directory_parts:
                            with _bound_child_directories(
                                job_fd,
                                directory_parts,
                                create=False,
                            ) as record_fd:
                                stored = _read_optional_bound_json(
                                    record_fd,
                                    leaf,
                                    invalid=invalid,
                                )
                        else:
                            stored = _read_optional_bound_json(
                                job_fd,
                                leaf,
                                invalid=invalid,
                            )
                    except FileNotFoundError:
                        return None
                    if stored is None:
                        return None
                    if lease_record:
                        return _validate_lease_payload(
                            stored,
                            project_key=self.project_key,
                            root=self.root,
                            job_id=identifier,
                            snapshot_id=snapshot,
                        )
                    return _validate_bound_payload(
                        stored,
                        schema=schema,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=identifier,
                        snapshot_id=snapshot,
                        invalid=invalid,
                        checkpoint_id=checkpoint_id,
                    )

    def _write_mutable_record(
        self,
        job_id: str,
        snapshot_id: str,
        *,
        name: str,
        encoded: bytes,
        owner: str,
        lease_token: str,
        invalid,
    ) -> None:
        owner_identifier = _validate_lease_identifier(owner)
        token = _validate_lease_identifier(lease_token)
        parts = tuple(name.split("/"))
        if len(parts) == 1:
            directory_parts: tuple[str, ...] = ()
            leaf = parts[0]
        else:
            directory_parts = parts[:-1]
            leaf = parts[-1]
        with _job_lock(self.workspace, self.project_key, job_id):
            with bound_project_state_directory(
                self.workspace,
                self.project_key,
                self.root,
                ("onboarding",),
                False,
            ) as onboarding_fd:
                with _bound_child_directories(
                    onboarding_fd,
                    ("jobs", job_id),
                    create=False,
                ) as job_fd:
                    _require_job_in_directory(
                        onboarding_fd,
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_id,
                        snapshot_id=snapshot_id,
                    )
                    _require_active_lease_in_directory(
                        job_fd,
                        project_key=self.project_key,
                        root=self.root,
                        job_id=job_id,
                        snapshot_id=snapshot_id,
                        owner=owner_identifier,
                        lease_token=token,
                    )
                    if directory_parts:
                        with _bound_child_directories(
                            job_fd,
                            directory_parts,
                            create=True,
                        ) as record_fd:
                            _replace_mutable_json(
                                record_fd,
                                leaf,
                                encoded,
                                invalid=invalid,
                            )
                    else:
                        _replace_mutable_json(
                            job_fd,
                            leaf,
                            encoded,
                            invalid=invalid,
                        )


def _build_job_payload(
    metadata: dict[str, object],
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> dict[str, object]:
    if not isinstance(metadata, dict):
        raise _job_invalid("Onboarding job metadata must be a JSON object.")
    payload = dict(metadata)
    expected = {
        "schema": "onboarding_job.v1",
        "project_key": project_key,
        "canonical_root": str(root),
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
    }
    _assert_claimed_bindings(payload, expected, invalid=_job_invalid)
    payload.update(expected)
    if not isinstance(payload.get("created_at"), str) or not payload["created_at"]:
        payload["created_at"] = _format_timestamp(_utc_now())
    return _validate_job_payload(
        payload,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )


def _validate_job_payload(
    payload: object,
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str | None,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise _job_invalid("Published onboarding job metadata is invalid.")
    result = dict(payload)
    expected = {
        "schema": "onboarding_job.v1",
        "project_key": project_key,
        "canonical_root": str(root),
        "root_identity": _root_identity(root),
        "job_id": job_id,
    }
    _require_bindings(result, expected, invalid=_job_invalid)
    stored_snapshot = result.get("snapshot_id")
    try:
        stored_snapshot = _validate_identifier(stored_snapshot)
    except ProjectImportError as exc:
        raise _job_invalid("Published onboarding job snapshot is invalid.") from exc
    if snapshot_id is not None and stored_snapshot != snapshot_id:
        raise _job_mismatch(job_id, snapshot_id)
    if not isinstance(result.get("created_at"), str) or not result["created_at"]:
        raise _job_invalid("Published onboarding job metadata is invalid.")
    return result


def _job_equivalent(
    current: object,
    expected: dict[str, object],
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> bool:
    stored = _validate_job_payload(
        current,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    current_identity = dict(stored)
    expected_identity = dict(expected)
    current_identity.pop("created_at", None)
    expected_identity.pop("created_at", None)
    return current_identity == expected_identity


def _require_job_in_directory(
    onboarding_fd: int,
    job_fd: int,
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str | None,
) -> dict[str, object]:
    try:
        payload = _read_bound_json(job_fd, "job.json", invalid=_job_invalid)
    except FileNotFoundError as exc:
        raise _job_missing(job_id) from exc
    job = _validate_job_payload(
        payload,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    _require_snapshot_artifact(
        onboarding_fd,
        str(job["snapshot_id"]),
        project_key=project_key,
        root=root,
    )
    return job


def _build_redacted_bound_payload(
    payload: dict[str, object],
    *,
    schema: str,
    project_key: str,
    root: Path,
    workspace: Path,
    job_id: str,
    snapshot_id: str,
    invalid,
    timestamp_field: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise invalid("Onboarding job record must be a JSON object.")
    original = dict(payload)
    redacted = _redact_runner_payload(original, root=root, workspace=workspace)
    if not isinstance(redacted, dict):  # defensive: the redactor preserves dicts.
        raise invalid("Onboarding job record must be a JSON object.")
    expected: dict[str, object] = {
        "schema": schema,
        "project_key": project_key,
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
    }
    if extra:
        expected.update(extra)
    _assert_claimed_bindings(redacted, expected, invalid=invalid)
    redacted.update(expected)
    redacted[timestamp_field] = _format_timestamp(_utc_now())
    # This flag means the mandatory store-side scrubber was applied, even when
    # no value needed a replacement on this individual record.
    redacted["redaction_applied"] = True
    return _validate_bound_payload(
        redacted,
        schema=schema,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
        invalid=invalid,
        checkpoint_id=str(extra["checkpoint_id"])
        if extra and "checkpoint_id" in extra
        else None,
    )


def _validate_bound_payload(
    payload: object,
    *,
    schema: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
    invalid,
    checkpoint_id: str | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise invalid("Published onboarding job record is invalid.")
    result = dict(payload)
    expected: dict[str, object] = {
        "schema": schema,
        "project_key": project_key,
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
    }
    if checkpoint_id is not None:
        expected["checkpoint_id"] = checkpoint_id
    _require_bindings(result, expected, invalid=invalid)
    timestamp_field = {
        "onboarding_job_status.v1": "updated_at",
        "onboarding_job_checkpoint.v1": "saved_at",
        "onboarding_job_prompt.v1": "created_at",
        "onboarding_model_event.v1": "recorded_at",
    }.get(schema)
    if (
        timestamp_field is None
        or not isinstance(result.get(timestamp_field), str)
        or not result[timestamp_field]
        or result.get("redaction_applied") is not True
    ):
        raise invalid("Published onboarding job record is invalid.")
    if _contains_local_path(result, root=root, workspace=None):
        raise invalid("Onboarding job records must not persist target-root paths.")
    return result


def _redacted_record_equivalent(
    current: object,
    expected: dict[str, object],
    *,
    schema: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
    invalid,
    ignored_fields: set[str],
) -> bool:
    stored = _validate_bound_payload(
        current,
        schema=schema,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
        invalid=invalid,
    )
    left = dict(stored)
    right = dict(expected)
    for field in ignored_fields:
        left.pop(field, None)
        right.pop(field, None)
    return left == right


def _redact_runner_payload(
    value: object,
    *,
    root: Path,
    workspace: Path,
) -> object:
    return _redact_local_paths(
        redact_observation_value(value),
        root=root,
        workspace=workspace,
    )


def _redact_local_paths(value: object, *, root: Path, workspace: Path) -> object:
    if isinstance(value, str):
        redacted = value
        for sensitive_path in sorted(
            {str(root), str(workspace)}, key=len, reverse=True
        ):
            if sensitive_path:
                redacted = redacted.replace(sensitive_path, _REDACTION_MARKER)
        return redacted
    if isinstance(value, list):
        return [
            _redact_local_paths(item, root=root, workspace=workspace) for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _redact_local_paths(item, root=root, workspace=workspace)
            for key, item in value.items()
        }
    return value


def _contains_local_path(
    value: object,
    *,
    root: Path,
    workspace: Path | None,
) -> bool:
    paths = [str(root)]
    if workspace is not None:
        paths.append(str(workspace))
    if isinstance(value, str):
        return any(path and path in value for path in paths)
    if isinstance(value, list):
        return any(
            _contains_local_path(item, root=root, workspace=workspace) for item in value
        )
    if isinstance(value, dict):
        return any(
            _contains_local_path(item, root=root, workspace=workspace)
            for item in value.values()
        )
    return False


def _assert_claimed_bindings(
    payload: dict[str, object],
    expected: dict[str, object],
    *,
    invalid,
) -> None:
    for key, value in expected.items():
        if key in payload and payload[key] != value:
            raise invalid("Onboarding job record does not match its requested binding.")


def _require_bindings(
    payload: dict[str, object],
    expected: dict[str, object],
    *,
    invalid,
) -> None:
    if any(payload.get(key) != value for key, value in expected.items()):
        raise invalid("Published onboarding job record does not match its binding.")


def _encode_job_json(value: object, *, invalid) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise invalid("Onboarding job record is not valid JSON.") from exc


def _encode_bounded_job_json(value: object, *, invalid, limit: int) -> bytes:
    encoded = _encode_job_json(value, invalid=invalid)
    if len(encoded) > limit:
        raise invalid("Onboarding job record exceeds its persisted size limit.")
    return encoded


def _read_bound_json(directory_fd: int, name: str, *, invalid) -> object:
    try:
        descriptor = _open_regular_file(directory_fd, name)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise invalid("Published onboarding job record is invalid.") from exc
    pending_error: ProjectImportError | None = None
    try:
        content = _read_all(descriptor)
        _verify_bound_leaf(directory_fd, name, descriptor, invalid=invalid)
    finally:
        try:
            _verify_bound_leaf(directory_fd, name, descriptor, invalid=invalid)
        except ProjectImportError as exc:
            pending_error = exc
        finally:
            os.close(descriptor)
    if pending_error is not None:
        raise pending_error
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise invalid("Published onboarding job record is invalid JSON.") from exc


def _read_optional_bound_json(
    directory_fd: int, name: str, *, invalid
) -> object | None:
    try:
        return _read_bound_json(directory_fd, name, invalid=invalid)
    except FileNotFoundError:
        return None


def _verify_bound_leaf(
    directory_fd: int, name: str, descriptor: int, *, invalid
) -> None:
    try:
        _verify_file_identity(directory_fd, name, descriptor)
    except OSError as exc:
        raise invalid("Onboarding job record changed during access.") from exc


def _replace_mutable_json(
    directory_fd: int,
    name: str,
    encoded: bytes,
    *,
    invalid,
) -> None:
    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise invalid("Could not create onboarding job record.") from exc
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        except OSError as exc:
            raise invalid("Could not write onboarding job record.") from exc
        try:
            os.replace(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except OSError as exc:
            raise invalid("Could not publish onboarding job record.") from exc
        try:
            destination_fd = _open_regular_file(directory_fd, name)
        except OSError as exc:
            raise invalid("Published onboarding job record is invalid.") from exc
        pending_error: ProjectImportError | None = None
        try:
            if _descriptor_identity(destination_fd) != _descriptor_identity(descriptor):
                raise OSError(errno.ENOTDIR, f"Onboarding job record changed: {name}")
            os.fsync(destination_fd)
            os.fsync(directory_fd)
            _verify_bound_leaf(directory_fd, name, destination_fd, invalid=invalid)
        except OSError as exc:
            raise invalid("Published onboarding job record is invalid.") from exc
        finally:
            try:
                _verify_bound_leaf(directory_fd, name, destination_fd, invalid=invalid)
            except ProjectImportError as exc:
                pending_error = exc
            finally:
                os.close(destination_fd)
        if pending_error is not None:
            raise pending_error
    finally:
        if descriptor is not None:
            try:
                if _entry_identity(
                    directory_fd, temporary_name
                ) == _descriptor_identity(descriptor):
                    os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(descriptor)


def _open_model_event_for_append(job_fd: int) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("model-events.jsonl", flags, 0o600, dir_fd=job_fd)
    except OSError as exc:
        raise _model_event_invalid("Onboarding model events are invalid.") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _model_event_invalid("Onboarding model events are invalid.")
    return descriptor


def _open_model_event_file(job_fd: int) -> int:
    try:
        return _open_regular_file(job_fd, "model-events.jsonl")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _model_event_invalid("Onboarding model events are invalid.") from exc


def _verify_model_event_leaf(job_fd: int, descriptor: int) -> None:
    _verify_bound_leaf(
        job_fd,
        "model-events.jsonl",
        descriptor,
        invalid=_model_event_invalid,
    )


def _parse_model_events(
    content: bytes,
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _model_event_invalid(
                f"Onboarding model event line {line_number} is malformed.",
                line_number=line_number,
            ) from exc
        try:
            events.append(
                _validate_bound_payload(
                    payload,
                    schema="onboarding_model_event.v1",
                    project_key=project_key,
                    root=root,
                    job_id=job_id,
                    snapshot_id=snapshot_id,
                    invalid=_model_event_invalid,
                )
            )
        except ProjectImportError as exc:
            details = dict(exc.details)
            details["line"] = line_number
            raise ProjectImportError(exc.code, str(exc), details=details) from exc
    return events


def _validate_lease_identifier(value: object) -> str:
    try:
        return _validate_identifier(value)
    except ProjectImportError as exc:
        raise _lease_invalid("Onboarding lease identifier is invalid.") from exc


def _validate_lease_seconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _lease_invalid("Onboarding lease duration is invalid.")
    if value <= 0 or value > _LEASE_MAX_SECONDS:
        raise _lease_invalid("Onboarding lease duration is invalid.")
    return value


def _new_lease_payload(
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
    owner: str,
    lease_seconds: int,
) -> dict[str, object]:
    now = _utc_now()
    return {
        "schema": "onboarding_job_lease.v1",
        "project_key": project_key,
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
        "owner": owner,
        "lease_token": secrets.token_hex(24),
        "state": "active",
        "acquired_at": _format_timestamp(now),
        "updated_at": _format_timestamp(now),
        "expires_at": _format_timestamp(now + timedelta(seconds=lease_seconds)),
    }


def _validate_lease_payload(
    payload: object,
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise _lease_invalid("Published onboarding lease is invalid.")
    result = dict(payload)
    _require_bindings(
        result,
        {
            "schema": "onboarding_job_lease.v1",
            "project_key": project_key,
            "root_identity": _root_identity(root),
            "job_id": job_id,
            "snapshot_id": snapshot_id,
        },
        invalid=_lease_invalid,
    )
    try:
        _validate_lease_identifier(result.get("owner"))
        _validate_lease_identifier(result.get("lease_token"))
    except ProjectImportError as exc:
        raise _lease_invalid("Published onboarding lease is invalid.") from exc
    state = result.get("state")
    if state not in {"active", "released"}:
        raise _lease_invalid("Published onboarding lease is invalid.")
    acquired_at = _parse_timestamp(result.get("acquired_at"), invalid=_lease_invalid)
    _parse_timestamp(result.get("updated_at"), invalid=_lease_invalid)
    expires_at = _parse_timestamp(result.get("expires_at"), invalid=_lease_invalid)
    if expires_at <= acquired_at:
        raise _lease_invalid("Published onboarding lease is invalid.")
    if state == "released":
        _parse_timestamp(result.get("released_at"), invalid=_lease_invalid)
    if _contains_local_path(result, root=root, workspace=None):
        raise _lease_invalid("Onboarding leases must not persist target-root paths.")
    return result


def _lease_is_active(payload: dict[str, object]) -> bool:
    return (
        payload.get("state") == "active"
        and _parse_timestamp(payload.get("expires_at"), invalid=_lease_invalid)
        > _utc_now()
    )


def _require_active_lease_in_directory(
    job_fd: int,
    *,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
    owner: str,
    lease_token: str,
) -> dict[str, object]:
    owner_identifier = _validate_lease_identifier(owner)
    token = _validate_lease_identifier(lease_token)
    payload = _read_optional_bound_json(job_fd, "lease.json", invalid=_lease_invalid)
    if payload is None:
        raise _lease_stale(job_id)
    lease = _validate_lease_payload(
        payload,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    if (
        not _lease_is_active(lease)
        or not secrets.compare_digest(str(lease.get("owner", "")), owner_identifier)
        or not secrets.compare_digest(str(lease.get("lease_token", "")), token)
    ):
        raise _lease_stale(job_id)
    return lease


def _validate_profile_payload(
    payload: object,
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise _profile_invalid("Onboarding profile must be a JSON object.")
    result = dict(payload)
    _require_bindings(
        result,
        {
            "schema": "project_profile.v1",
            "profile_id": profile_id,
            "project_key": project_key,
            "snapshot_id": snapshot_id,
        },
        invalid=_profile_invalid,
    )
    generated_by = result.get("generated_by")
    if not isinstance(generated_by, dict) or generated_by.get("job_id") != job_id:
        raise _profile_invalid("Onboarding profile does not match its generating job.")
    if _contains_local_path(result, root=root, workspace=None):
        raise _profile_invalid(
            "Onboarding profile must not contain a target root path."
        )
    return result


def _build_profile_manifest_payload(
    manifest: dict[str, object],
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    workspace: Path,
    job_id: str,
    snapshot_id: str,
    profile_encoded: bytes,
) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise _profile_invalid("Onboarding profile manifest must be a JSON object.")
    redacted_manifest = _redact_runner_payload(
        dict(manifest),
        root=root,
        workspace=workspace,
    )
    if not isinstance(redacted_manifest, dict):  # pragma: no cover - defensive
        raise _profile_invalid("Onboarding profile manifest must be a JSON object.")
    payload = {
        "schema": "onboarding_profile_artifact_manifest.v1",
        "profile_id": profile_id,
        "project_key": project_key,
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
        "profile_sha256": f"sha256:{hashlib.sha256(profile_encoded).hexdigest()}",
        "manifest": redacted_manifest,
        "published_at": _format_timestamp(_utc_now()),
        "redaction_applied": True,
    }
    return _validate_profile_manifest_payload(
        payload,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
        profile_encoded=profile_encoded,
    )


def _validate_profile_manifest_payload(
    payload: object,
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
    profile_encoded: bytes,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise _profile_invalid("Published onboarding profile manifest is invalid.")
    result = dict(payload)
    _require_bindings(
        result,
        {
            "schema": "onboarding_profile_artifact_manifest.v1",
            "profile_id": profile_id,
            "project_key": project_key,
            "root_identity": _root_identity(root),
            "job_id": job_id,
            "snapshot_id": snapshot_id,
            "profile_sha256": f"sha256:{hashlib.sha256(profile_encoded).hexdigest()}",
        },
        invalid=_profile_invalid,
    )
    if (
        not isinstance(result.get("manifest"), dict)
        or not isinstance(result.get("published_at"), str)
        or not result["published_at"]
        or result.get("redaction_applied") is not True
        or _contains_local_path(result, root=root, workspace=None)
    ):
        raise _profile_invalid("Published onboarding profile manifest is invalid.")
    return result


def _recover_or_read_profile_bundle(
    profile_fd: int,
    *,
    expected_profile: dict[str, object],
    expected_manifest: dict[str, object],
    manifest_encoded: bytes,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Read a complete bundle, or finish only its missing final manifest.

    A process can stop after the immutable canonical profile body is durable but
    before its final manifest marker is linked.  That directory is not readable
    as a profile, but a retry may complete it only after proving that its body is
    exactly the caller's expected canonical profile.  Missing, malformed, or
    conflicting bodies remain fail-closed and are never overwritten.
    """

    try:
        raw_profile = _read_bound_json(
            profile_fd,
            "project-profile.json",
            invalid=_profile_invalid,
        )
    except FileNotFoundError as exc:
        raise _profile_invalid(
            "Published onboarding profile is missing canonical content."
        ) from exc
    current_profile = _validate_profile_payload(
        raw_profile,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    if current_profile != expected_profile:
        raise _artifact_conflict("Published onboarding profile conflicts.")

    try:
        _read_bound_json(
            profile_fd,
            "artifact-manifest.json",
            invalid=_profile_invalid,
        )
    except FileNotFoundError:
        _publish_immutable_json(
            profile_fd,
            "artifact-manifest.json",
            manifest_encoded,
            equivalent=lambda current: _profile_manifest_equivalent(
                current,
                expected_manifest,
                profile_id=profile_id,
                project_key=project_key,
                root=root,
                job_id=job_id,
                snapshot_id=snapshot_id,
            ),
        )
        os.fsync(profile_fd)

    return _read_profile_bundle(
        profile_fd,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )


def _read_profile_bundle(
    profile_fd: int,
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        raw_profile = _read_bound_json(
            profile_fd,
            "project-profile.json",
            invalid=_profile_invalid,
        )
    except FileNotFoundError as exc:
        raise _profile_invalid(
            "Published onboarding profile is missing canonical content."
        ) from exc
    profile = _validate_profile_payload(
        raw_profile,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    profile_encoded = _encode_job_json(profile, invalid=_profile_invalid)
    try:
        raw_manifest = _read_bound_json(
            profile_fd,
            "artifact-manifest.json",
            invalid=_profile_invalid,
        )
    except FileNotFoundError as exc:
        raise _profile_invalid(
            "Published onboarding profile is missing its final manifest."
        ) from exc
    manifest = _validate_profile_manifest_payload(
        raw_manifest,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
        profile_encoded=profile_encoded,
    )
    return profile, manifest


def _require_profile_bundle(
    profile_fd: int,
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> None:
    _read_profile_bundle(
        profile_fd,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )


def _profile_manifest_equivalent(
    current: object,
    expected: dict[str, object],
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> bool:
    # The expected profile digest is already encoded in the expected manifest.
    digest = expected.get("profile_sha256")
    if not isinstance(digest, str):  # pragma: no cover - internal invariant
        raise _profile_invalid("Onboarding profile manifest is invalid.")
    stored = dict(current) if isinstance(current, dict) else current
    if not isinstance(stored, dict):
        raise _profile_invalid("Published onboarding profile manifest is invalid.")
    expected_bindings = {
        "schema": "onboarding_profile_artifact_manifest.v1",
        "profile_id": profile_id,
        "project_key": project_key,
        "root_identity": _root_identity(root),
        "job_id": job_id,
        "snapshot_id": snapshot_id,
        "profile_sha256": digest,
    }
    _require_bindings(stored, expected_bindings, invalid=_profile_invalid)
    if (
        not isinstance(stored.get("manifest"), dict)
        or not isinstance(stored.get("published_at"), str)
        or not stored["published_at"]
        or stored.get("redaction_applied") is not True
        or _contains_local_path(stored, root=root, workspace=None)
    ):
        raise _profile_invalid("Published onboarding profile manifest is invalid.")
    left = dict(stored)
    right = dict(expected)
    left.pop("published_at", None)
    right.pop("published_at", None)
    return left == right


def _profile_bundle_equivalent(
    current_profile: dict[str, object],
    current_manifest: dict[str, object],
    expected_profile: dict[str, object],
    expected_manifest: dict[str, object],
    *,
    profile_id: str,
    project_key: str,
    root: Path,
    job_id: str,
    snapshot_id: str,
) -> bool:
    current = _validate_profile_payload(
        current_profile,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )
    if current != expected_profile:
        return False
    return _profile_manifest_equivalent(
        current_manifest,
        expected_manifest,
        profile_id=profile_id,
        project_key=project_key,
        root=root,
        job_id=job_id,
        snapshot_id=snapshot_id,
    )


def _directory_exists(parent_fd: int, name: str) -> bool:
    try:
        descriptor = _open_directory(parent_fd, name, create=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _profile_invalid("Onboarding profile directory is invalid.") from exc
    try:
        _verify_directory_identity(parent_fd, name, descriptor)
        return True
    except OSError as exc:
        raise _profile_invalid("Onboarding profile directory is invalid.") from exc
    finally:
        os.close(descriptor)


def _create_directory_exclusively(parent_fd: int, name: str) -> bool:
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        return False
    except OSError as exc:
        raise _profile_invalid(
            "Could not create onboarding profile directory."
        ) from exc
    return True


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _parse_timestamp(value: object, *, invalid) -> datetime:
    if not isinstance(value, str) or not value:
        raise invalid("Onboarding timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise invalid("Onboarding timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise invalid("Onboarding timestamp is invalid.")
    return parsed.astimezone(UTC)


def _canonical_workspace(workspace: Path) -> Path:
    return Path(workspace).expanduser().resolve(strict=False)


def _validate_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.strip(".")
        or any(character not in _SAFE_IDENTIFIER for character in value)
    ):
        raise ProjectImportError(
            "onboarding_identifier_invalid",
            "Onboarding identifier contains invalid characters.",
            details={"identifier": str(value)},
        )
    return value


def _validate_snapshot(
    snapshot: RepositorySnapshot,
    project_key: str,
    root: Path,
) -> dict[str, object]:
    if not isinstance(snapshot, RepositorySnapshot):
        raise _artifact_conflict("Onboarding snapshot has an invalid schema.")
    payload = snapshot.as_dict()
    if (
        set(payload) != _SNAPSHOT_FIELDS
        or payload.get("schema") != "repository_snapshot.v1"
        or payload.get("project_key") != project_key
        or payload.get("canonical_root") != str(root)
        or not isinstance(payload.get("captured_at"), str)
        or not isinstance(payload.get("source_control"), dict)
        or not isinstance(payload.get("content_scope"), dict)
        or not isinstance(payload.get("path_manifest"), dict)
    ):
        raise _artifact_conflict("Onboarding snapshot does not match this project.")
    identifier = _validate_artifact_identifier(payload.get("snapshot_id"))
    if payload.get("root_identity") != _root_identity(root):
        raise _artifact_conflict("Onboarding snapshot root identity does not match.")
    try:
        recomputed = compute_repository_snapshot_id(payload)
    except (TypeError, ValueError) as exc:
        raise _artifact_conflict("Onboarding snapshot digest is invalid.") from exc
    if identifier != recomputed:
        raise _artifact_conflict("Onboarding snapshot digest is invalid.")
    return payload


def _validate_snapshot_payload(
    payload: object,
    *,
    project_key: str,
    root: Path,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _SNAPSHOT_FIELDS:
        raise _artifact_conflict("Published onboarding snapshot is invalid.")
    try:
        snapshot = RepositorySnapshot(**payload)
    except (TypeError, ValueError) as exc:
        raise _artifact_conflict("Published onboarding snapshot is invalid.") from exc
    return _validate_snapshot(snapshot, project_key, root)


def _validate_inventory(inventory: dict[str, object]) -> dict[str, object]:
    if not isinstance(inventory, dict):
        raise _artifact_conflict("Onboarding inventory has an invalid schema.")
    payload = dict(inventory)
    if payload.get("schema") != "repository_inventory.v1":
        raise _artifact_conflict("Onboarding inventory has an invalid schema.")
    _validate_artifact_identifier(payload.get("snapshot_id"))
    digest = payload.get("inventory_digest")
    if not isinstance(digest, str):
        raise _artifact_conflict("Onboarding inventory digest is invalid.")
    try:
        recomputed = compute_repository_inventory_digest(payload)
    except (TypeError, ValueError) as exc:
        raise _artifact_conflict("Onboarding inventory digest is invalid.") from exc
    if digest != recomputed:
        raise _artifact_conflict("Onboarding inventory digest is invalid.")
    return payload


def _validate_artifact_identifier(value: object) -> str:
    try:
        return _validate_identifier(value)
    except ProjectImportError as exc:
        raise _artifact_conflict("Onboarding artifact identifier is invalid.") from exc


def _snapshot_equivalent(
    current: object,
    expected: dict[str, object],
    *,
    project_key: str,
    root: Path,
) -> bool:
    validated = _validate_snapshot_payload(current, project_key=project_key, root=root)
    current_identity = dict(validated)
    expected_identity = dict(expected)
    for payload in (current_identity, expected_identity):
        payload.pop("snapshot_id", None)
        payload.pop("captured_at", None)
    return current_identity == expected_identity


def _root_identity(root: Path) -> dict[str, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise
        raise _artifact_conflict(
            "Onboarding snapshot root identity does not match."
        ) from exc
    try:
        value = os.fstat(descriptor)
        return {"device": value.st_dev, "inode": value.st_ino}
    finally:
        os.close(descriptor)


@contextmanager
def _bound_child_directories(
    parent_fd: int,
    parts: tuple[str, ...],
    *,
    create: bool,
) -> Iterator[int]:
    descriptors: list[int] = []
    current_parent = parent_fd
    try:
        for part in parts:
            descriptor = _open_directory(current_parent, part, create=create)
            descriptors.append(descriptor)
            current_parent = descriptor
        yield descriptors[-1]
    finally:
        try:
            current_parent = parent_fd
            for part, descriptor in zip(parts, descriptors, strict=False):
                _verify_directory_identity(current_parent, part, descriptor)
                current_parent = descriptor
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _open_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_fd)


def _verify_directory_identity(parent_fd: int, name: str, expected_fd: int) -> None:
    current_fd = _open_directory(parent_fd, name, create=False)
    try:
        if _descriptor_identity(current_fd) != _descriptor_identity(expected_fd):
            raise OSError(errno.ENOTDIR, f"Onboarding directory changed: {name}")
    finally:
        os.close(current_fd)


def _require_snapshot_artifact(
    onboarding_fd: int,
    snapshot_id: str,
    *,
    project_key: str,
    root: Path,
) -> dict[str, object]:
    try:
        with _bound_child_directories(
            onboarding_fd,
            ("snapshots",),
            create=False,
        ) as snapshots_fd:
            payload = _read_json_file(
                snapshots_fd,
                f"{snapshot_id}.json",
                invalid_message="Published onboarding snapshot is invalid.",
            )
        validated = _validate_snapshot_payload(
            payload,
            project_key=project_key,
            root=root,
        )
    except FileNotFoundError as exc:
        raise _snapshot_missing(snapshot_id) from exc
    except ProjectImportError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _artifact_conflict("Published onboarding snapshot is invalid.") from exc
    if validated.get("snapshot_id") != snapshot_id:
        raise _artifact_conflict("Published onboarding snapshot identity conflicts.")
    return validated


def _publish_immutable_json(
    directory_fd: int,
    name: str,
    encoded: bytes,
    *,
    equivalent,
) -> None:
    try:
        current = _read_json_file(
            directory_fd,
            name,
            invalid_message="Published onboarding artifact is invalid.",
        )
    except FileNotFoundError:
        current = None
    except ProjectImportError:
        raise
    else:
        try:
            if equivalent(current):
                return
        except ProjectImportError:
            raise
        except (TypeError, ValueError) as exc:
            raise _artifact_conflict(
                "Published onboarding artifact is invalid."
            ) from exc
        raise _artifact_conflict("Published onboarding artifact conflicts.")

    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    linked = False
    try:
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            linked = True
        except FileExistsError:
            try:
                current = _read_json_file(
                    directory_fd,
                    name,
                    invalid_message="Published onboarding artifact is invalid.",
                )
                matches = equivalent(current)
            except ProjectImportError:
                raise
            except (
                FileNotFoundError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                TypeError,
                ValueError,
            ) as exc:
                raise _artifact_conflict(
                    "Published onboarding artifact conflicts."
                ) from exc
            if matches:
                return
            raise _artifact_conflict("Published onboarding artifact conflicts.")
        destination_fd = _open_artifact_file(directory_fd, name)
        pending_error: ProjectImportError | None = None
        try:
            if _descriptor_identity(destination_fd) != _descriptor_identity(descriptor):
                raise OSError(errno.ENOTDIR, f"Onboarding artifact changed: {name}")
            os.fsync(destination_fd)
            os.fsync(directory_fd)
            _verify_artifact_leaf(directory_fd, name, destination_fd)
        finally:
            try:
                _verify_artifact_leaf(directory_fd, name, destination_fd)
            except ProjectImportError as exc:
                pending_error = exc
            finally:
                os.close(destination_fd)
        if pending_error is not None:
            raise pending_error
    finally:
        try:
            if _entry_identity(directory_fd, temporary_name) == _descriptor_identity(
                descriptor
            ):
                os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(descriptor)
    if not linked:
        raise _artifact_conflict("Onboarding artifact publication failed.")


def _read_json_file(directory_fd: int, name: str, *, invalid_message: str) -> object:
    descriptor = _open_artifact_file(
        directory_fd, name, invalid_message=invalid_message
    )
    pending_error: ProjectImportError | None = None
    try:
        content = _read_all(descriptor)
        _verify_artifact_leaf(directory_fd, name, descriptor, message=invalid_message)
    finally:
        try:
            _verify_artifact_leaf(
                directory_fd, name, descriptor, message=invalid_message
            )
        except ProjectImportError as exc:
            pending_error = exc
        finally:
            os.close(descriptor)
    if pending_error is not None:
        raise pending_error
    return json.loads(content)


def _open_regular_file(directory_fd: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(errno.ENOTDIR, f"Onboarding artifact is not regular: {name}")
    return descriptor


def _open_observation_for_append(job_fd: int) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("observations.jsonl", flags, 0o600, dir_fd=job_fd)
    except OSError as exc:
        raise _observation_invalid("Onboarding observations are invalid.") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise _observation_invalid("Onboarding observations are invalid.")
    return descriptor


def _open_artifact_file(
    directory_fd: int,
    name: str,
    *,
    invalid_message: str = "Published onboarding artifact is invalid.",
) -> int:
    try:
        return _open_regular_file(directory_fd, name)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _artifact_conflict(invalid_message) from exc


def _open_observation_file(directory_fd: int, name: str) -> int:
    try:
        return _open_regular_file(directory_fd, name)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _observation_invalid("Onboarding observations are invalid.") from exc


def _verify_artifact_leaf(
    directory_fd: int,
    name: str,
    descriptor: int,
    *,
    message: str = "Published onboarding artifact is invalid.",
) -> None:
    try:
        _verify_file_identity(directory_fd, name, descriptor)
    except OSError as exc:
        raise _artifact_conflict(message) from exc


def _verify_observation_leaf(
    directory_fd: int,
    name: str,
    descriptor: int,
) -> None:
    try:
        _verify_file_identity(directory_fd, name, descriptor)
    except OSError as exc:
        raise _observation_invalid("Onboarding observations are invalid.") from exc


def _verify_file_identity(directory_fd: int, name: str, descriptor: int) -> None:
    if _entry_identity(directory_fd, name) != _descriptor_identity(descriptor):
        raise OSError(errno.ENOTDIR, f"Onboarding artifact changed: {name}")


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


def _entry_identity(directory_fd: int, name: str) -> tuple[int, int]:
    value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return value.st_dev, value.st_ino


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "Could not complete onboarding artifact write")
        offset += written


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _encode_json(value: object, *, conflict: bool) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        if conflict:
            raise _artifact_conflict("Onboarding artifact is not valid JSON.") from exc
        raise _observation_invalid("Onboarding observation is not valid JSON.") from exc


def _parse_observations(
    content: bytes,
    *,
    snapshot_id: str,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _observation_invalid(
                f"Onboarding observation line {line_number} is malformed.",
                line_number=line_number,
            ) from exc
        if not isinstance(record, dict) or record.get("snapshot_id") != snapshot_id:
            raise _observation_invalid(
                f"Onboarding observation line {line_number} is invalid.",
                line_number=line_number,
            )
        records.append(record)
    return records


def _observation_lock(
    workspace: Path,
    project_key: str,
    job_id: str,
) -> threading.Lock:
    key = (str(workspace), project_key, job_id)
    with _OBSERVATION_LOCKS_GUARD:
        return _OBSERVATION_LOCKS.setdefault(key, threading.Lock())


def _job_lock(
    workspace: Path,
    project_key: str,
    job_id: str,
) -> threading.Lock:
    key = (str(workspace), project_key, job_id)
    with _JOB_LOCKS_GUARD:
        return _JOB_LOCKS.setdefault(key, threading.Lock())


def _artifact_conflict(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_artifact_conflict", message)


def _snapshot_missing(snapshot_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_snapshot_missing",
        f"Onboarding snapshot artifact is missing: {snapshot_id}",
        details={"snapshot_id": snapshot_id},
    )


def _inventory_missing(snapshot_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_inventory_missing",
        f"Onboarding inventory artifact is missing: {snapshot_id}",
        details={"snapshot_id": snapshot_id},
    )


def _observation_invalid(
    message: str,
    *,
    line_number: int | None = None,
) -> ProjectImportError:
    details: dict[str, object] = {}
    if line_number is not None:
        details["line"] = line_number
    return ProjectImportError(
        "onboarding_observation_invalid",
        message,
        details=details,
    )


def _job_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_job_invalid", message)


def _job_missing(job_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_job_missing",
        f"Onboarding job metadata is missing: {job_id}",
        details={"job_id": job_id},
    )


def _job_mismatch(job_id: str, snapshot_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_job_mismatch",
        "Onboarding job does not match the requested snapshot.",
        details={"job_id": job_id, "snapshot_id": snapshot_id},
    )


def _mutable_record_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_job_record_invalid", message)


def _status_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_status_invalid", message)


def _checkpoint_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_checkpoint_invalid", message)


def _prompt_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_prompt_invalid", message)


def _model_event_invalid(
    message: str,
    *,
    line_number: int | None = None,
) -> ProjectImportError:
    details: dict[str, object] = {}
    if line_number is not None:
        details["line"] = line_number
    return ProjectImportError(
        "onboarding_model_event_invalid", message, details=details
    )


def _lease_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_lease_invalid", message)


def _lease_conflict(job_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_lease_conflict",
        "Onboarding job already has an active lease.",
        details={"job_id": job_id},
    )


def _lease_stale(job_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_lease_stale",
        "Onboarding lease is stale, expired, or no longer owned by this runner.",
        details={"job_id": job_id},
    )


def _profile_invalid(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_profile_invalid", message)
