from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
        with bound_project_state_directory(
            self.workspace,
            self.project_key,
            self.root,
            ("onboarding",),
            True,
        ) as onboarding_fd:
            with _bound_child_directories(
                onboarding_fd,
                ("snapshots",),
                create=True,
            ) as snapshots_fd:
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
    descriptor = _open_artifact_file(directory_fd, name, invalid_message=invalid_message)
    pending_error: ProjectImportError | None = None
    try:
        content = _read_all(descriptor)
        _verify_artifact_leaf(directory_fd, name, descriptor, message=invalid_message)
    finally:
        try:
            _verify_artifact_leaf(directory_fd, name, descriptor, message=invalid_message)
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


def _artifact_conflict(message: str) -> ProjectImportError:
    return ProjectImportError("onboarding_artifact_conflict", message)


def _snapshot_missing(snapshot_id: str) -> ProjectImportError:
    return ProjectImportError(
        "onboarding_snapshot_missing",
        f"Onboarding snapshot artifact is missing: {snapshot_id}",
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
