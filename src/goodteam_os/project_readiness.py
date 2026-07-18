from __future__ import annotations

import copy
import errno
import json
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from goodteam_os.state import normalize_project_key


MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"}
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}
MODE_STEPS = {
    "new_project": ["new_project_init", "project_brief", "worker_preflight"],
    "existing_project": [
        "repository_understanding",
        "command_verification",
        "constraints_confirmation",
        "worker_preflight",
    ],
}
FileIdentity = tuple[int, int]
_EXPECTED_DESCRIPTOR_UNSET = object()


@dataclass(frozen=True)
class AnchoredWrite:
    descriptors: tuple[int, ...]
    readiness_descriptor: int
    temporary_name: str
    backup_name: str | None
    backup_descriptor: int | None


def classify_project_mode(root: Path) -> str:
    entries = [entry for entry in root.iterdir() if entry.name not in IGNORED_NAMES]
    visible = [entry for entry in entries if entry.name != ".git"]
    if not visible:
        return "new_project"
    if (root / ".git").exists():
        return "existing_project"
    for entry in visible:
        if entry.name in MANIFEST_NAMES or entry.suffix.lower() in SOURCE_SUFFIXES:
            return "existing_project"
        if entry.is_dir() and entry.name in {"src", "app", "frontend", "backend", "packages"}:
            return "existing_project"
    return "unconfirmed"


class ProjectReadinessStore:
    def __init__(self, workspace: Path, *, workspace_fd: int | None = None):
        self.workspace = workspace
        self.workspace_fd = workspace_fd
        self._anchored_writes: dict[str, AnchoredWrite] = {}

    def initialize(self, project_key: str, root: Path) -> dict[str, object]:
        key = normalize_project_key(project_key)
        project_mode = classify_project_mode(root)
        mode_confirmed = project_mode != "unconfirmed"
        root_stat = root.stat()
        readiness: dict[str, object] = {
            "schema_version": 1,
            "project_key": key,
            "canonical_root": str(root.resolve()),
            "root_identity": {
                "device": root_stat.st_dev,
                "inode": root_stat.st_ino,
            },
            "project_mode": project_mode,
            "lifecycle": "preparing" if mode_confirmed else "attention_required",
            "steps": [
                {"id": "project_record", "status": "complete", "required": True},
                {"id": "safe_preflight", "status": "complete", "required": True},
                {
                    "id": "project_mode",
                    "status": "complete" if mode_confirmed else "needs_input",
                    "required": True,
                },
            ],
            "updated_at": _now(),
        }
        if mode_confirmed:
            readiness["steps"] = [
                *readiness["steps"],
                *_pending_steps(MODE_STEPS[project_mode]),
            ]
        self._write(key, readiness)
        return readiness

    def read(self, project_key: str) -> dict[str, object] | None:
        if self.workspace_fd is not None:
            data = self._read_anchored(project_key)
            if data is None:
                return None
            if not isinstance(data, dict):
                raise ValueError(f"readiness for {project_key!r} must be a JSON object")
            return data
        path = self._path(project_key)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"readiness for {project_key!r} must be a JSON object")
        return data

    def confirm_mode(
        self,
        project_key: str,
        mode: str,
        *,
        readiness_validator: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        if mode not in MODE_STEPS:
            raise ValueError("mode must be new_project or existing_project")

        key = normalize_project_key(project_key)
        if self.workspace_fd is not None:
            return self._confirm_mode_anchored(
                key,
                mode,
                readiness_validator=readiness_validator,
            )
        readiness = self.read(key)
        if readiness is None:
            raise ValueError(f"No readiness found for project: {key}")
        if readiness_validator is not None:
            readiness_validator(readiness)
        readiness = _confirmed_mode_readiness(readiness, mode)
        self._write(key, readiness)
        return readiness

    def mutate(
        self,
        project_key: str,
        transform: Callable[[dict[str, object]], dict[str, object]],
        *,
        readiness_validator: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        """Atomically transform a persisted readiness document.

        Descriptor-anchored callers keep the exact source inode pinned from read
        through publication.  This is the public mutation primitive used by
        readiness executors so they inherit the import foundation's symlink,
        replacement, and rollback protections instead of implementing a weaker
        parallel write path.
        """

        key = normalize_project_key(project_key)
        if self.workspace_fd is not None:
            return self._mutate_anchored(
                key,
                transform,
                readiness_validator=readiness_validator,
            )
        readiness = self.read(key)
        if readiness is None:
            raise ValueError(f"No readiness found for project: {key}")
        if readiness_validator is not None:
            readiness_validator(readiness)
        updated = transform(copy.deepcopy(readiness))
        if not isinstance(updated, dict):
            raise ValueError("readiness transform must return a JSON object")
        self._write(key, updated)
        return updated

    def _mutate_anchored(
        self,
        key: str,
        transform: Callable[[dict[str, object]], dict[str, object]],
        *,
        readiness_validator: Callable[[dict[str, object]], None] | None,
    ) -> dict[str, object]:
        descriptors = self._open_project_directory_chain(key, create=False)
        if descriptors is None:
            raise ValueError(f"No readiness found for project: {key}")
        readiness_descriptor: int | None = None
        try:
            readiness, readiness_descriptor = self._read_anchored_from_descriptors(
                key,
                descriptors,
            )
            if readiness_validator is not None:
                readiness_validator(readiness)
            updated = transform(copy.deepcopy(readiness))
            if not isinstance(updated, dict):
                raise ValueError("readiness transform must return a JSON object")
            self._write_anchored(
                key,
                updated,
                descriptors=descriptors,
                expected_descriptor=readiness_descriptor,
            )
            return updated
        finally:
            if readiness_descriptor is not None:
                os.close(readiness_descriptor)
            _close_descriptors(descriptors)

    def _confirm_mode_anchored(
        self,
        key: str,
        mode: str,
        *,
        readiness_validator: Callable[[dict[str, object]], None] | None,
    ) -> dict[str, object]:
        descriptors = self._open_project_directory_chain(key, create=False)
        if descriptors is None:
            raise ValueError(f"No readiness found for project: {key}")
        readiness_descriptor: int | None = None
        try:
            readiness, readiness_descriptor = self._read_anchored_from_descriptors(
                key,
                descriptors,
            )
            if readiness_validator is not None:
                readiness_validator(readiness)
            confirmed = _confirmed_mode_readiness(readiness, mode)
            self._write_anchored(
                key,
                confirmed,
                descriptors=descriptors,
                expected_descriptor=readiness_descriptor,
            )
            return confirmed
        finally:
            if readiness_descriptor is not None:
                os.close(readiness_descriptor)
            _close_descriptors(descriptors)

    def _verify_anchored_write(self, project_key: str) -> None:
        if self.workspace_fd is None:
            return
        key = normalize_project_key(project_key)
        expected = self._anchored_writes.get(key)
        if expected is None:
            return
        descriptors = self._open_project_directory_chain(key, create=False)
        if descriptors is None:
            raise OSError(errno.ENOTDIR, f"Readiness directory disappeared: {key}")
        try:
            if tuple(_descriptor_identity(fd) for fd in descriptors) != tuple(
                _descriptor_identity(fd) for fd in expected.descriptors
            ):
                raise OSError(errno.ENOTDIR, f"Readiness directory changed: {key}")
            self._verify_current_chain(key, descriptors)
            try:
                readiness_identity = _entry_identity(descriptors[-1], "readiness.json")
            except FileNotFoundError as exc:
                raise OSError(errno.ENOTDIR, f"Readiness file disappeared: {key}") from exc
            if readiness_identity != _descriptor_identity(
                expected.readiness_descriptor
            ):
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
        finally:
            _close_descriptors(descriptors)

    def _commit_anchored_write(self, project_key: str) -> None:
        key = normalize_project_key(project_key)
        anchored = self._anchored_writes.get(key)
        if anchored is None:
            return
        self._remove_anchored_backup(anchored)
        self._anchored_writes.pop(key, None)
        _close_anchored_descriptors(anchored)

    def _rollback_anchored_write(self, project_key: str) -> None:
        key = normalize_project_key(project_key)
        anchored = self._anchored_writes.pop(key, None)
        if anchored is None:
            return
        self._rollback_anchored_transaction(anchored)

    def close(self) -> None:
        for key in list(self._anchored_writes):
            self._commit_anchored_write(key)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _path(self, project_key: str) -> Path:
        key = normalize_project_key(project_key)
        return self.workspace / "state" / "projects" / key / "readiness.json"

    def _write(self, project_key: str, readiness: dict[str, object]) -> None:
        if self.workspace_fd is not None:
            self._write_anchored(project_key, readiness)
            return
        path = self._path(project_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(readiness, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
            temporary_path.replace(path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_anchored(self, project_key: str) -> object | None:
        key = normalize_project_key(project_key)
        descriptors = self._open_project_directory_chain(key, create=False)
        if descriptors is None:
            return None
        readiness_descriptor: int | None = None
        try:
            try:
                readiness, readiness_descriptor = self._read_anchored_from_descriptors(
                    key,
                    descriptors,
                )
            except FileNotFoundError:
                return None
            return readiness
        finally:
            if readiness_descriptor is not None:
                os.close(readiness_descriptor)
            _close_descriptors(descriptors)

    def _read_anchored_from_descriptors(
        self,
        key: str,
        descriptors: list[int],
    ) -> tuple[dict[str, object], int]:
        project_fd = descriptors[-1]
        self._verify_current_chain(key, descriptors)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open("readiness.json", flags, dir_fd=project_fd)
        except FileNotFoundError:
            self._verify_current_chain(key, descriptors)
            raise
        try:
            with os.fdopen(os.dup(descriptor), encoding="utf-8") as readiness_file:
                readiness = json.load(readiness_file)
            self._verify_current_chain(key, descriptors)
            try:
                current_identity = _entry_identity(project_fd, "readiness.json")
            except FileNotFoundError as exc:
                raise OSError(
                    errno.ENOTDIR,
                    f"Readiness file disappeared: {key}",
                ) from exc
            if current_identity != _descriptor_identity(descriptor):
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
            if not isinstance(readiness, dict):
                raise ValueError(f"readiness for {key!r} must be a JSON object")
            return readiness, descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _write_anchored(
        self,
        project_key: str,
        readiness: dict[str, object],
        *,
        descriptors: list[int] | None = None,
        expected_descriptor: int | object = _EXPECTED_DESCRIPTOR_UNSET,
    ) -> None:
        key = normalize_project_key(project_key)
        self._rollback_anchored_write(key)
        owns_descriptors = descriptors is None
        if descriptors is None:
            descriptors = self._open_project_directory_chain(key, create=True)
            assert descriptors is not None
        project_fd = descriptors[-1]
        temporary_name = f".readiness.json.{secrets.token_hex(8)}.tmp"
        temporary_created = False
        current_descriptor: int | None = None
        published_descriptor: int | None = None
        backup_name: str | None = None
        transaction: AnchoredWrite | None = None
        try:
            self._verify_current_chain(key, descriptors)
            if expected_descriptor is _EXPECTED_DESCRIPTOR_UNSET:
                current_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                try:
                    current_descriptor = os.open(
                        "readiness.json",
                        current_flags,
                        dir_fd=project_fd,
                    )
                except FileNotFoundError:
                    current_descriptor = None
            else:
                current_descriptor = os.dup(expected_descriptor)
            current_identity = (
                _descriptor_identity(current_descriptor)
                if current_descriptor is not None
                else None
            )
            if (
                _entry_identity_or_none(project_fd, "readiness.json")
                != current_identity
            ):
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
            if current_identity is not None:
                backup_name = f".readiness.json.{secrets.token_hex(8)}.backup"
                os.link(
                    "readiness.json",
                    backup_name,
                    src_dir_fd=project_fd,
                    dst_dir_fd=project_fd,
                    follow_symlinks=False,
                )
                if _entry_identity(project_fd, backup_name) != current_identity:
                    raise OSError(errno.ENOTDIR, f"Readiness backup changed: {key}")
                if _entry_identity_or_none(project_fd, "readiness.json") != current_identity:
                    raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            published_descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=project_fd,
            )
            temporary_created = True
            with os.fdopen(
                os.dup(published_descriptor),
                "w",
                encoding="utf-8",
            ) as temporary:
                json.dump(readiness, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            published_identity = _descriptor_identity(published_descriptor)
            self._verify_current_chain(key, descriptors)
            if _entry_identity_or_none(project_fd, "readiness.json") != current_identity:
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
            pinned: list[int] = []
            try:
                for source_descriptor in descriptors:
                    pinned.append(os.dup(source_descriptor))
            except BaseException:
                _close_descriptors(pinned)
                raise
            transaction = AnchoredWrite(
                descriptors=tuple(pinned),
                readiness_descriptor=published_descriptor,
                temporary_name=temporary_name,
                backup_name=backup_name,
                backup_descriptor=current_descriptor,
            )
            published_descriptor = None
            current_descriptor = None
            if _entry_identity_or_none(project_fd, temporary_name) != published_identity:
                raise OSError(errno.ENOTDIR, f"Readiness temporary file changed: {key}")
            os.replace(
                temporary_name,
                "readiness.json",
                src_dir_fd=project_fd,
                dst_dir_fd=project_fd,
            )
            temporary_created = False
            self._verify_current_chain(key, descriptors)
            if _entry_identity(project_fd, "readiness.json") != published_identity:
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
            self._anchored_writes[key] = transaction
            transaction = None
        except BaseException:
            if transaction is not None:
                try:
                    self._rollback_anchored_transaction(transaction)
                except OSError:
                    pass
                transaction = None
            elif backup_name is not None and current_descriptor is not None:
                try:
                    _unlink_if_descriptor(
                        project_fd,
                        backup_name,
                        current_descriptor,
                        restore_foreign=False,
                    )
                except OSError:
                    pass
            raise
        finally:
            if temporary_created and published_descriptor is not None:
                try:
                    _unlink_if_descriptor(
                        project_fd,
                        temporary_name,
                        published_descriptor,
                    )
                except OSError:
                    pass
            if published_descriptor is not None:
                os.close(published_descriptor)
            if current_descriptor is not None:
                os.close(current_descriptor)
            if owns_descriptors:
                _close_descriptors(descriptors)

    def _remove_anchored_backup(self, anchored: AnchoredWrite) -> None:
        if anchored.backup_name is None or anchored.backup_descriptor is None:
            return
        _unlink_if_descriptor(
            anchored.descriptors[-1],
            anchored.backup_name,
            anchored.backup_descriptor,
        )

    def _rollback_anchored_transaction(self, anchored: AnchoredWrite) -> None:
        recovered = False
        try:
            self._recover_previous_readiness(anchored)
            recovered = True
        finally:
            try:
                if recovered:
                    self._remove_anchored_backup(anchored)
            finally:
                try:
                    project_fd = anchored.descriptors[-1]
                    if _entry_identity_or_none(
                        project_fd,
                        anchored.temporary_name,
                    ) == _descriptor_identity(anchored.readiness_descriptor):
                        _unlink_if_descriptor(
                            project_fd,
                            anchored.temporary_name,
                            anchored.readiness_descriptor,
                        )
                finally:
                    _close_anchored_descriptors(anchored)

    def _recover_previous_readiness(self, anchored: AnchoredWrite) -> None:
        project_fd = anchored.descriptors[-1]
        has_backup = (
            anchored.backup_name is not None and anchored.backup_descriptor is not None
        )
        backup_identity = (
            _descriptor_identity(anchored.backup_descriptor)
            if anchored.backup_descriptor is not None
            else None
        )
        if has_backup and (
            _entry_identity_or_none(project_fd, anchored.backup_name)
            != backup_identity
        ):
            raise OSError(errno.ENOTDIR, "Readiness backup changed during rollback")
        readiness_identity = _descriptor_identity(anchored.readiness_descriptor)
        for _attempt in range(256):
            current_identity = _entry_identity_or_none(project_fd, "readiness.json")
            if current_identity is None:
                if not has_backup:
                    return
                try:
                    os.link(
                        anchored.backup_name,
                        "readiness.json",
                        src_dir_fd=project_fd,
                        dst_dir_fd=project_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                if (
                    _entry_identity_or_none(project_fd, "readiness.json")
                    != backup_identity
                ):
                    raise OSError(
                        errno.ENOTDIR,
                        "Restored readiness identity changed during rollback",
                    )
                return
            if has_backup and current_identity == backup_identity:
                return
            if current_identity == readiness_identity:
                _unlink_if_descriptor(
                    project_fd,
                    "readiness.json",
                    anchored.readiness_descriptor,
                )
                continue
            quarantine_name, quarantined_descriptor = self._quarantine_external_readiness(
                anchored,
            )
            logging.getLogger(__name__).warning(
                "Quarantined external readiness inode as %s",
                quarantine_name,
            )
            try:
                _unlink_if_descriptor(
                    project_fd,
                    "readiness.json",
                    quarantined_descriptor,
                )
            finally:
                os.close(quarantined_descriptor)
        raise OSError(errno.EBUSY, "Readiness changed repeatedly during rollback")

    def _quarantine_external_readiness(
        self,
        anchored: AnchoredWrite,
    ) -> tuple[str, int]:
        project_fd = anchored.descriptors[-1]
        candidate = anchored.temporary_name
        for _attempt in range(128):
            try:
                os.link(
                    "readiness.json",
                    candidate,
                    src_dir_fd=project_fd,
                    dst_dir_fd=project_fd,
                    follow_symlinks=False,
                )
                break
            except FileExistsError:
                candidate = f".readiness.json.{secrets.token_hex(8)}.quarantine"
        else:
            raise OSError(errno.EEXIST, "Could not allocate readiness quarantine name")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags, dir_fd=project_fd)
        if _entry_identity(project_fd, candidate) != _descriptor_identity(descriptor):
            os.close(descriptor)
            raise OSError(errno.ENOTDIR, "Readiness quarantine changed")
        return candidate, descriptor

    def _open_project_directory_chain(
        self,
        project_key: str,
        *,
        create: bool,
    ) -> list[int] | None:
        assert self.workspace_fd is not None
        descriptors: list[int] = []
        parent_fd = self.workspace_fd
        try:
            for name in ("state", "projects", normalize_project_key(project_key)):
                descriptor = _open_directory_at(parent_fd, name, create=create)
                if descriptor is None:
                    _close_descriptors(descriptors)
                    return None
                descriptors.append(descriptor)
                parent_fd = descriptor
            return descriptors
        except BaseException:
            _close_descriptors(descriptors)
            raise

    def _verify_current_chain(self, project_key: str, pinned: list[int]) -> None:
        assert self.workspace_fd is not None
        current: list[int] = []
        parent_fd = self.workspace_fd
        try:
            for name, pinned_fd in zip(
                ("state", "projects", normalize_project_key(project_key)),
                pinned,
                strict=True,
            ):
                descriptor = _open_directory_at(parent_fd, name, create=False)
                if descriptor is None:
                    raise OSError(errno.ENOTDIR, f"Readiness directory disappeared: {name}")
                current.append(descriptor)
                if _descriptor_identity(descriptor) != _descriptor_identity(pinned_fd):
                    raise OSError(errno.ENOTDIR, f"Readiness directory changed: {name}")
                parent_fd = descriptor
        finally:
            _close_descriptors(current)


def build_capabilities(readiness: dict[str, object]) -> dict[str, object]:
    mode = readiness.get("project_mode")
    project_key = readiness.get("project_key")
    canonical_root = readiness.get("canonical_root")
    expected_step_ids = (
        {"project_record", "safe_preflight", "project_mode", *MODE_STEPS[mode]}
        if isinstance(mode, str) and mode in MODE_STEPS
        else set()
    )
    steps = readiness.get("steps")
    artifacts = readiness.get("artifacts")
    execution = readiness.get("execution")

    def execution_pair(value: object) -> tuple[str, int] | None:
        if not isinstance(value, dict):
            return None
        execution_id = value.get("execution_id", value.get("id"))
        attempt = value.get("attempt")
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
        ):
            return None
        return execution_id, attempt

    step_contract_valid = False
    if (
        readiness.get("schema_version") == 2
        and isinstance(project_key, str)
        and bool(project_key)
        and isinstance(canonical_root, str)
        and bool(canonical_root)
        and isinstance(steps, list)
        and isinstance(artifacts, dict)
        and all(isinstance(step, dict) for step in steps)
    ):
        typed_steps = [step for step in steps if isinstance(step, dict)]
        all_ids = [step.get("id") for step in typed_steps]
        steps_by_id = {str(step.get("id", "")): step for step in typed_steps}
        artifact_step_ids = {
            "repository_understanding",
            "new_project_init",
            "project_brief",
            "worker_preflight",
        }

        def step_artifacts_valid(step: dict[str, object]) -> bool:
            raw_refs = step.get("artifact_refs", [])
            if not isinstance(raw_refs, list) or any(not isinstance(ref, str) for ref in raw_refs):
                return False
            refs = list(raw_refs)
            step_id = str(step.get("id", ""))
            if (step_id in artifact_step_ids) != bool(refs):
                return False
            if len(refs) != len(set(refs)):
                return False
            pair = execution_pair(step)
            return pair is not None and all(
                isinstance(artifacts.get(ref), dict)
                and artifacts[ref].get("id") == ref
                and ref == f"readiness://{pair[0]}/{pair[1]}/{step_id}"
                and artifacts[ref].get("project_key") == project_key
                and artifacts[ref].get("canonical_root") == canonical_root
                and artifacts[ref].get("step_id") == step_id
                and execution_pair(artifacts[ref]) == pair
                for ref in refs
            )

        all_steps_valid = (
            len(all_ids) == len(set(all_ids))
            and set(all_ids) == expected_step_ids
            and len(typed_steps) == len(expected_step_ids)
            and all(
                step.get("required") is True
                and step.get("status") == "complete"
                and step.get("project_key") == project_key
                and step.get("canonical_root") == canonical_root
                and execution_pair(step) is not None
                and bool(step.get("started_at"))
                and bool(step.get("completed_at"))
                and step_artifacts_valid(step)
                for step in typed_steps
            )
        )
        current_pair = execution_pair(execution)
        foundation_pairs = {
            execution_pair(steps_by_id[step_id])
            for step_id in {"project_record", "safe_preflight", "project_mode"}
            if step_id in steps_by_id
        }
        if mode == "existing_project" and all_steps_valid:
            repository_pair = execution_pair(steps_by_id["repository_understanding"])
            provenance_valid = (
                repository_pair is not None
                and execution_pair(steps_by_id["command_verification"])
                == repository_pair
                and execution_pair(steps_by_id["constraints_confirmation"])
                == repository_pair
                and execution_pair(steps_by_id["worker_preflight"])
                == current_pair
            )
        elif mode == "new_project" and all_steps_valid:
            provenance_valid = (
                execution_pair(steps_by_id["new_project_init"])
                == execution_pair(steps_by_id["project_brief"])
                and execution_pair(steps_by_id["worker_preflight"])
                == current_pair
            )
        else:
            provenance_valid = False
        step_contract_valid = (
            all_steps_valid
            and len(foundation_pairs) == 1
            and next(iter(foundation_pairs), ("", 0))[0].startswith("foundation:")
            and provenance_valid
        )
    execution_contract_valid = (
        isinstance(execution, dict)
        and execution.get("status") == "complete"
        and execution.get("project_key") == project_key
        and execution.get("canonical_root") == canonical_root
        and not execution.get("current_step_id")
        and execution_pair(execution) is not None
        and bool(execution.get("started_at"))
        and bool(execution.get("completed_at"))
    )
    ready = bool(
        expected_step_ids
        and step_contract_valid
        and execution_contract_valid
        and readiness.get("lifecycle") == "ready"
    )
    return {
        "inspect_project_facts": True,
        "edit_project_settings": True,
        "run_readiness_steps": True,
        "start_workflow": ready,
        "start_workflow_reason": (
            ""
            if ready
            else (
                "project_mode_unconfirmed"
                if mode == "unconfirmed"
                else "mode_specific_readiness_pending"
            )
        ),
    }


def _confirmed_mode_readiness(
    readiness: dict[str, object],
    mode: str,
) -> dict[str, object]:
    confirmed = dict(readiness)
    mode_step_ids = {step_id for steps in MODE_STEPS.values() for step_id in steps}
    existing_steps = confirmed.get("steps", [])
    base_steps = [
        dict(step)
        for step in existing_steps
        if isinstance(step, dict) and step.get("id") not in mode_step_ids
    ]
    for step in base_steps:
        if step.get("id") == "project_mode":
            step["status"] = "complete"
    confirmed.update(
        {
            "project_mode": mode,
            "lifecycle": "preparing",
            "steps": [*base_steps, *_pending_steps(MODE_STEPS[mode])],
            "updated_at": _now(),
        }
    )
    return confirmed


def _pending_steps(step_ids: list[str]) -> list[dict[str, object]]:
    return [{"id": step_id, "status": "pending", "required": True} for step_id in step_ids]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int | None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            os.mkdir(name, mode=0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            return None
        raise


def _descriptor_identity(descriptor: int) -> FileIdentity:
    stat_result = os.fstat(descriptor)
    return stat_result.st_dev, stat_result.st_ino


def _entry_identity(directory_fd: int, name: str) -> FileIdentity:
    stat_result = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return stat_result.st_dev, stat_result.st_ino


def _entry_identity_or_none(directory_fd: int, name: str) -> FileIdentity | None:
    try:
        return _entry_identity(directory_fd, name)
    except FileNotFoundError:
        return None


def _unlink_if_descriptor(
    directory_fd: int,
    name: str,
    expected_descriptor: int,
    *,
    restore_foreign: bool = True,
) -> None:
    quarantine_name = f".{name}.{secrets.token_hex(16)}.quarantine"
    try:
        os.rename(
            name,
            quarantine_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return
    try:
        quarantined_identity = _entry_identity(directory_fd, quarantine_name)
    except FileNotFoundError as exc:
        raise OSError(
            errno.ENOTDIR,
            f"Quarantined entry disappeared: {name}",
        ) from exc
    if quarantined_identity == _descriptor_identity(expected_descriptor):
        try:
            os.unlink(quarantine_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        return
    if restore_foreign:
        try:
            os.link(
                quarantine_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass
        except OSError:
            pass
    logging.getLogger(__name__).warning(
        "Preserved foreign replacement as %s",
        quarantine_name,
    )


def _close_anchored_descriptors(anchored: AnchoredWrite) -> None:
    descriptors = [
        *anchored.descriptors,
        anchored.readiness_descriptor,
    ]
    if anchored.backup_descriptor is not None:
        descriptors.append(anchored.backup_descriptor)
    _close_descriptors(descriptors)


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)
