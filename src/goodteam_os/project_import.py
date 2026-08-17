from __future__ import annotations

import errno
import fcntl
import logging
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Iterator

from goodteam_os.project_readiness import ProjectReadinessStore, build_capabilities
from goodteam_os.projects import build_registered_project_config, load_project_target
from goodteam_os.state import normalize_project_key


IMPORT_LOCK_NAME = ".project-import.lock"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
FileIdentity = tuple[int, int]


class ProjectImportError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class _PublishedConfig:
    descriptor: int

    @property
    def identity(self) -> FileIdentity:
        return _descriptor_identity(self.descriptor)

    def close(self) -> None:
        os.close(self.descriptor)


def import_project(
    workspace: Path,
    root_value: str,
    display_name: str = "",
    requested_key: str = "",
    existing_configs: list[Path] | None = None,
    default_model_profile: str = "balanced",
    default_worker_mode: str = "codex",
) -> dict[str, object]:
    raw_root = str(root_value)
    unresolved_root = Path(raw_root)
    if not unresolved_root.is_absolute():
        raise ProjectImportError(
            "project_root_not_absolute",
            "Project root must be an absolute path.",
            details={"root": raw_root},
        )
    root = unresolved_root.expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            return _import_project_locked(
                workspace,
                root,
                workspace_fd=workspace_fd,
                display_name=display_name,
                requested_key=requested_key,
                existing_configs=existing_configs,
                default_model_profile=default_model_profile,
                default_worker_mode=default_worker_mode,
            )
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


def confirm_project_mode(
    workspace: Path,
    project_key: str,
    root: Path,
    mode: str,
) -> dict[str, object]:
    root = Path(root).expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    key = normalize_project_key(project_key)
    _validate_project_state_destination(workspace, root, key)
    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            readiness_store = ProjectReadinessStore(
                workspace,
                workspace_fd=workspace_fd,
            )
            try:
                readiness = readiness_store.confirm_mode(
                    key,
                    mode,
                    readiness_validator=lambda stored: _validate_project_readiness_binding(
                        stored,
                        project_key=key,
                        root=root,
                    ),
                )
                readiness_store._verify_anchored_write(key)
                readiness_store._commit_anchored_write(key)
                return readiness
            except BaseException:
                try:
                    readiness_store._rollback_anchored_write(key)
                except OSError:
                    pass
                raise
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


def mutate_project_readiness(
    workspace: Path,
    project_key: str,
    root: Path,
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    """Mutate readiness under the import foundation's binding and inode guards."""

    root = Path(root).expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    key = normalize_project_key(project_key)
    _validate_project_state_destination(workspace, root, key)
    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            readiness_store = ProjectReadinessStore(workspace, workspace_fd=workspace_fd)
            try:
                readiness = readiness_store.mutate(
                    key,
                    transform,
                    readiness_validator=lambda stored: _validate_project_readiness_binding(
                        stored,
                        project_key=key,
                        root=root,
                    ),
                )
                readiness_store._verify_anchored_write(key)
                readiness_store._commit_anchored_write(key)
                return readiness
            except BaseException:
                try:
                    readiness_store._rollback_anchored_write(key)
                except OSError:
                    pass
                raise
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


def mutate_project_readiness_root_failure(
    workspace: Path,
    project_key: str,
    canonical_root: Path,
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    """Persist a root-identity failure without touching or trusting the target root.

    This deliberately narrower mutation path is only for recording a failure after
    the imported path can no longer be opened as the imported directory.  It keeps
    the readiness file descriptor-anchored inside GoodTeam's workspace and requires
    an exact stored project-key/canonical-root binding, but never resolves or writes
    through the potentially replaced target path.
    """

    root = Path(canonical_root).expanduser()
    if not root.is_absolute():
        raise ProjectImportError(
            "project_root_not_absolute",
            "Stored project root must be an absolute path.",
            details={"root": str(root)},
        )
    workspace = Path(workspace).expanduser().resolve(strict=False)
    key = normalize_project_key(project_key)
    _validate_project_state_destination(workspace, root, key)

    def validate_failure_binding(stored: dict[str, object]) -> None:
        if stored.get("project_key") != key or stored.get("canonical_root") != str(root):
            raise ProjectImportError(
                "project_readiness_mismatch",
                "Project readiness does not match the requested project.",
                details={"project_key": key},
            )

    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            readiness_store = ProjectReadinessStore(workspace, workspace_fd=workspace_fd)
            try:
                readiness = readiness_store.mutate(
                    key,
                    transform,
                    readiness_validator=validate_failure_binding,
                )
                readiness_store._verify_anchored_write(key)
                readiness_store._commit_anchored_write(key)
                return readiness
            except BaseException:
                try:
                    readiness_store._rollback_anchored_write(key)
                except OSError:
                    pass
                raise
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


def read_bound_project_readiness(
    workspace: Path,
    project_key: str,
    root: Path,
) -> dict[str, object]:
    """Read readiness only after proving its project-key/root binding."""

    root = Path(root).expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    key = normalize_project_key(project_key)
    _validate_project_state_destination(workspace, root, key)
    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            readiness = ProjectReadinessStore(workspace, workspace_fd=workspace_fd).read(key)
            if readiness is None:
                raise ValueError(f"No readiness found for project: {key}")
            _validate_project_readiness_binding(
                readiness,
                project_key=key,
                root=root,
            )
            return readiness
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


@contextmanager
def bound_project_state_directory(
    workspace: Path,
    project_key: str,
    root: Path,
    relative_parts: tuple[str, ...],
    create: bool,
) -> Iterator[int]:
    """Open a descriptor-bound directory below GoodTeam's project state."""

    root = Path(root).expanduser().resolve()
    _validate_root(root)
    workspace = _validate_workspace_isolation(workspace, root)
    key = normalize_project_key(project_key)
    _validate_project_state_destination(workspace, root, key)
    parts = tuple(relative_parts)
    for part in parts:
        _validate_project_state_part(part)

    try:
        with _workspace_import_lock(workspace) as workspace_fd:
            _verify_workspace_identity(workspace, workspace_fd)
            readiness = ProjectReadinessStore(
                workspace, workspace_fd=workspace_fd
            ).read(key)
            if readiness is None:
                raise ProjectImportError(
                    "project_readiness_mismatch",
                    f"Project readiness does not match the requested project: {key}",
                    details={"project_key": key},
                )
            _validate_project_readiness_binding(
                readiness,
                project_key=key,
                root=root,
            )
            _validate_project_root_identity(readiness, root, project_key=key)

            descriptors: list[int] = []
            names = ("state", "projects", key, *parts)
            parent_fd = workspace_fd
            try:
                for name in names:
                    descriptor = _open_directory_at(parent_fd, name, create=create)
                    if descriptor is None:
                        raise FileNotFoundError(
                            errno.ENOENT,
                            f"Project state directory does not exist: {name}",
                            name,
                        )
                    descriptors.append(descriptor)
                    parent_fd = descriptor
                yield descriptors[-1]
            finally:
                try:
                    _verify_workspace_identity(workspace, workspace_fd)
                    parent_fd = workspace_fd
                    for name, descriptor in zip(
                        names,
                        descriptors,
                        strict=False,
                    ):
                        _verify_directory_identity_at(parent_fd, name, descriptor)
                        parent_fd = descriptor
                finally:
                    for descriptor in reversed(descriptors):
                        os.close(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.ELOOP, errno.ENOTDIR}:
            raise
        raise _workspace_not_isolated_error(workspace, root) from exc


def _validate_project_state_part(part: object) -> None:
    if (
        not isinstance(part, str)
        or not part
        or not part.strip(".")
        or "/" in part
        or "\\" in part
        or re.fullmatch(r"[A-Za-z0-9._-]+", part, flags=re.ASCII) is None
    ):
        raise ProjectImportError(
            "project_state_path_invalid",
            "Project state path contains an invalid directory name.",
            details={"part": str(part)},
        )


def _verify_workspace_identity(workspace: Path, workspace_fd: int) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(workspace, flags)
    try:
        if _descriptor_identity(current_fd) != _descriptor_identity(workspace_fd):
            raise OSError(errno.ENOTDIR, "GoodTeam workspace directory changed")
    finally:
        os.close(current_fd)


def _validate_project_root_identity(
    readiness: dict[str, object],
    root: Path,
    *,
    project_key: str,
) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        current = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stored = readiness.get("root_identity")
    if (
        not isinstance(stored, dict)
        or stored.get("device") != current.st_dev
        or stored.get("inode") != current.st_ino
    ):
        raise ProjectImportError(
            "project_readiness_mismatch",
            "Project readiness root identity does not match the requested project.",
            details={"project_key": project_key},
        )


def _validate_project_readiness_binding(
    readiness: dict[str, object],
    *,
    project_key: str,
    root: Path,
) -> None:
    stored_key = readiness.get("project_key")
    stored_root = readiness.get("canonical_root")
    root_matches = False
    if isinstance(stored_root, str) and stored_root:
        try:
            root_matches = Path(stored_root).expanduser().resolve() == root.resolve()
        except (OSError, ValueError):
            root_matches = False
    if not isinstance(stored_key, str) or stored_key != project_key or not root_matches:
        raise ProjectImportError(
            "project_readiness_mismatch",
            "Project readiness does not match the requested project.",
            details={"project_key": project_key},
        )


def _import_project_locked(
    workspace: Path,
    root: Path,
    *,
    workspace_fd: int,
    display_name: str,
    requested_key: str,
    existing_configs: list[Path] | None,
    default_model_profile: str,
    default_worker_mode: str,
) -> dict[str, object]:
    projects_dir = workspace / "projects"
    projects_fd = _open_directory_at(workspace_fd, "projects", create=False)
    try:
        registered = _load_registered_projects(
            list(existing_configs or []),
            workspace_projects_dir=projects_dir,
            projects_fd=projects_fd,
        )
        readiness_store = ProjectReadinessStore(workspace, workspace_fd=workspace_fd)
        existing = _find_root_match(registered, root)
        if existing is not None:
            _validate_project_state_destination(workspace, root, str(existing["key"]))
            return _verified_existing_result(
                readiness_store,
                existing,
                root,
                workspace_fd=workspace_fd,
                projects_fd=projects_fd,
            )

        name = display_name.strip() or root.name
        base_key = normalize_project_key(requested_key or name)
        occupied_keys = {project["key"] for project in registered}
        suffix = 1
        while True:
            key = base_key if suffix == 1 else f"{base_key}-{suffix}"
            if key in occupied_keys:
                suffix += 1
                continue
            config_path = projects_dir / f"{key.lower()}.local.yaml"
            _validate_write_destination(workspace, root, config_path)
            _validate_project_state_destination(workspace, root, key)
            if projects_fd is None:
                projects_fd = _open_directory_at(workspace_fd, "projects", create=True)
                assert projects_fd is not None
            _verify_directory_identity_at(workspace_fd, "projects", projects_fd)
            try:
                published_config = _publish_config_exclusively(
                    config_path,
                    build_registered_project_config(
                        key,
                        name,
                        root,
                        model_profile=default_model_profile,
                        worker_mode=default_worker_mode,
                    ),
                    directory_fd=projects_fd,
                )
            except FileExistsError:
                registered = _load_registered_projects(
                    list(existing_configs or []),
                    workspace_projects_dir=projects_dir,
                    projects_fd=projects_fd,
                )
                existing = _find_root_match(registered, root)
                if existing is not None:
                    _validate_project_state_destination(
                        workspace,
                        root,
                        str(existing["key"]),
                    )
                    return _verified_existing_result(
                        readiness_store,
                        existing,
                        root,
                        workspace_fd=workspace_fd,
                        projects_fd=projects_fd,
                    )
                occupied_keys.update(project["key"] for project in registered)
                suffix += 1
                continue

            try:
                _verify_published_config(
                    workspace_fd,
                    projects_fd,
                    config_path.name,
                    published_config,
                )
                readiness = readiness_store.initialize(key, root)
                readiness_store._verify_anchored_write(key)
                _verify_published_config(
                    workspace_fd,
                    projects_fd,
                    config_path.name,
                    published_config,
                )
                result = _result(
                    created=True,
                    key=key,
                    name=name,
                    root=root,
                    config_path=config_path,
                    readiness=readiness,
                )
                readiness_store._commit_anchored_write(key)
                try:
                    from goodteam_os.project_registry import ProjectRegistry

                    ProjectRegistry().register_project(
                        config_path,
                        root=root,
                        key=key,
                        state_path=workspace / "state" / "projects" / key,
                    )
                except Exception:
                    pass
                return result
            except BaseException:
                try:
                    readiness_store._rollback_anchored_write(key)
                except OSError:
                    pass
                try:
                    _unlink_if_descriptor(
                        projects_fd,
                        config_path.name,
                        published_config.descriptor,
                    )
                except OSError:
                    pass
                raise
            finally:
                published_config.close()
    finally:
        if projects_fd is not None:
            os.close(projects_fd)


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise ProjectImportError(
            "project_root_not_found",
            f"Project root does not exist: {root}",
            details={"root": str(root)},
        )
    if not root.is_dir():
        raise ProjectImportError(
            "project_root_not_directory",
            f"Project root is not a directory: {root}",
            details={"root": str(root)},
        )
    try:
        list(root.iterdir())
    except PermissionError as exc:
        raise ProjectImportError(
            "project_root_not_readable",
            f"Project root is not readable: {root}",
            details={"root": str(root)},
        ) from exc


def _validate_workspace_isolation(workspace: Path, root: Path) -> Path:
    canonical_workspace = Path(workspace).expanduser().resolve(strict=False)
    destinations = [
        canonical_workspace,
        canonical_workspace / "projects",
        canonical_workspace / "state",
        canonical_workspace / "state" / "projects",
        canonical_workspace / IMPORT_LOCK_NAME,
    ]
    for destination in destinations:
        _validate_write_destination(canonical_workspace, root, destination)
    return canonical_workspace


def _validate_project_state_destination(workspace: Path, root: Path, key: str) -> None:
    project_state_dir = workspace / "state" / "projects" / key
    _validate_write_destination(workspace, root, project_state_dir)
    _validate_write_destination(workspace, root, project_state_dir / "readiness.json")


def _validate_write_destination(workspace: Path, root: Path, destination: Path) -> None:
    canonical_destination = destination.resolve(strict=False)
    if not _path_is_within(canonical_destination, root):
        return
    raise ProjectImportError(
        "project_workspace_not_isolated",
        f"GoodTeam workspace write path overlaps the project root: {canonical_destination}",
        details={
            "root": str(root),
            "workspace": str(workspace),
            "destination": str(canonical_destination),
        },
    )


def _workspace_not_isolated_error(workspace: Path, root: Path) -> ProjectImportError:
    return ProjectImportError(
        "project_workspace_not_isolated",
        f"GoodTeam workspace changed during import: {workspace}",
        details={
            "root": str(root),
            "workspace": str(workspace),
            "destination": str(workspace),
        },
    )


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _thread_lock_for(workspace: Path) -> threading.Lock:
    lock_key = str(workspace)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(lock_key, threading.Lock())


@contextmanager
def _workspace_import_lock(workspace: Path) -> Iterator[int]:
    with _thread_lock_for(workspace):
        workspace_descriptor = _open_absolute_directory(workspace)
        try:
            lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            try:
                lock_descriptor = os.open(
                    IMPORT_LOCK_NAME,
                    lock_flags,
                    0o600,
                    dir_fd=workspace_descriptor,
                )
            except FileNotFoundError:
                lock_descriptor = os.open(
                    IMPORT_LOCK_NAME,
                    lock_flags,
                    0o600,
                    dir_fd=workspace_descriptor,
                )
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                try:
                    yield workspace_descriptor
                finally:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)
        finally:
            os.close(workspace_descriptor)


def _open_absolute_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for name in path.parts[1:]:
            child_descriptor = _open_directory_at(descriptor, name, create=True)
            assert child_descriptor is not None
            os.close(descriptor)
            descriptor = child_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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


def _publish_config_exclusively(
    destination: Path,
    content: str,
    *,
    directory_fd: int | None = None,
) -> _PublishedConfig:
    owned_directory_fd = directory_fd is None
    if directory_fd is None:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open(destination.parent, flags)
    temporary_name = f".{destination.name}.{secrets.token_hex(8)}.tmp"
    temporary_created = False
    owned_descriptor: int | None = None
    destination_linked = False
    published = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        owned_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=directory_fd,
        )
        temporary_created = True
        with os.fdopen(os.dup(owned_descriptor), "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            owned_identity = _descriptor_identity(owned_descriptor)
            os.link(
                temporary_name,
                destination.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            destination_linked = True
            try:
                final_identity = _entry_identity(directory_fd, destination.name)
            except FileNotFoundError as exc:
                raise OSError(
                    errno.ENOTDIR,
                    f"Published config disappeared: {destination.name}",
                ) from exc
            if final_identity != owned_identity:
                raise OSError(
                    errno.ENOTDIR,
                    f"Published config changed during publication: {destination.name}",
                )
        published = True
        return _PublishedConfig(descriptor=owned_descriptor)
    except BaseException:
        if owned_descriptor is not None and destination_linked:
            try:
                _unlink_if_descriptor(
                    directory_fd,
                    destination.name,
                    owned_descriptor,
                )
            except OSError:
                pass
        raise
    finally:
        if temporary_created:
            try:
                if owned_descriptor is None:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                else:
                    _unlink_if_descriptor(
                        directory_fd,
                        temporary_name,
                        owned_descriptor,
                    )
            except OSError:
                pass
        if owned_descriptor is not None and not published:
            os.close(owned_descriptor)
        if owned_directory_fd:
            os.close(directory_fd)


def _verify_published_config(
    workspace_fd: int,
    projects_fd: int,
    name: str,
    published_config: _PublishedConfig,
) -> None:
    _verify_directory_identity_at(workspace_fd, "projects", projects_fd)
    try:
        current_identity = _entry_identity(projects_fd, name)
    except FileNotFoundError as exc:
        raise OSError(errno.ENOTDIR, f"Published config disappeared: {name}") from exc
    if current_identity != published_config.identity:
        raise OSError(errno.ENOTDIR, f"Published config changed during import: {name}")


def _verify_directory_identity_at(parent_fd: int, name: str, pinned_fd: int) -> None:
    current_fd = _open_directory_at(parent_fd, name, create=False)
    if current_fd is None:
        raise OSError(errno.ENOTDIR, f"Workspace directory disappeared: {name}")
    try:
        if _descriptor_identity(current_fd) != _descriptor_identity(pinned_fd):
            raise OSError(errno.ENOTDIR, f"Workspace directory changed: {name}")
    finally:
        os.close(current_fd)


def _descriptor_identity(descriptor: int) -> FileIdentity:
    stat_result = os.fstat(descriptor)
    return stat_result.st_dev, stat_result.st_ino


def _entry_identity(directory_fd: int, name: str) -> FileIdentity:
    stat_result = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    return stat_result.st_dev, stat_result.st_ino


def _unlink_if_descriptor(
    directory_fd: int,
    name: str,
    expected_descriptor: int,
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


def _load_registered_projects(
    config_paths: list[Path],
    *,
    workspace_projects_dir: Path,
    projects_fd: int | None,
) -> list[dict[str, object]]:
    registered: list[dict[str, object]] = []
    workspace_paths: set[Path] = set()
    if projects_fd is not None:
        for name in sorted(os.listdir(projects_fd)):
            if not name.endswith(".local.yaml"):
                continue
            config_path = workspace_projects_dir / name
            target = _load_project_target_at(projects_fd, name)
            registered.append(_registered_project(config_path, target))
            workspace_paths.add(config_path.absolute())
    for config_path in config_paths:
        if config_path.absolute() in workspace_paths:
            continue
        try:
            target = load_project_target(config_path)
            registered.append(_registered_project(config_path, target))
            workspace_paths.add(config_path.absolute())
        except Exception:
            continue
    return registered


def _load_project_target_at(directory_fd: int, name: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        return load_project_target(Path(f"/dev/fd/{descriptor}"))
    finally:
        os.close(descriptor)


def _registered_project(
    config_path: Path,
    target: dict[str, object],
) -> dict[str, object]:
    return {
        "config_path": config_path,
        "key": normalize_project_key(str(target.get("key", config_path.stem))),
        "name": str(target.get("name", config_path.stem)),
        "root": _configured_root(config_path, target),
    }


def _find_root_match(
    registered: list[dict[str, object]],
    root: Path,
) -> dict[str, object] | None:
    return next((project for project in registered if project["root"] == root), None)


def _existing_result(
    readiness_store: ProjectReadinessStore,
    project: dict[str, object],
    root: Path,
) -> dict[str, object]:
    key = str(project["key"])
    readiness = readiness_store.read(key) or readiness_store.initialize(key, root)
    return _result(
        created=False,
        key=key,
        name=str(project["name"]),
        root=root,
        config_path=Path(project["config_path"]),
        readiness=readiness,
    )


def _verified_existing_result(
    readiness_store: ProjectReadinessStore,
    project: dict[str, object],
    root: Path,
    *,
    workspace_fd: int,
    projects_fd: int | None,
) -> dict[str, object]:
    key = str(project["key"])
    try:
        result = _existing_result(readiness_store, project, root)
        if projects_fd is not None:
            _verify_directory_identity_at(workspace_fd, "projects", projects_fd)
        readiness_store._verify_anchored_write(key)
        readiness_store._commit_anchored_write(key)
        return result
    except BaseException:
        try:
            readiness_store._rollback_anchored_write(key)
        except OSError:
            pass
        raise


def _configured_root(config_path: Path, target: dict[str, object]) -> Path | None:
    repos = target.get("repos")
    if not isinstance(repos, dict):
        return None
    root_value = repos.get("root") or repos.get("app")
    if not root_value:
        return None
    root = Path(str(root_value)).expanduser()
    if not root.is_absolute():
        root = config_path.parent / root
    return root.resolve()


def _result(
    *,
    created: bool,
    key: str,
    name: str,
    root: Path,
    config_path: Path,
    readiness: dict[str, object],
) -> dict[str, object]:
    project = {
        "project_key": key,
        "name": name,
        "root": str(root),
        "config_path": str(config_path),
    }
    return {
        "created": created,
        "project": project,
        "readiness": readiness,
        "capabilities": build_capabilities(readiness),
        "home_url": f"/projects/{key}/home",
    }
