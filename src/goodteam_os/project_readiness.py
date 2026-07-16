from __future__ import annotations

import errno
import json
import os
import secrets
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
AnchoredWrite = tuple[tuple[int, ...], FileIdentity]


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
        readiness: dict[str, object] = {
            "schema_version": 1,
            "project_key": key,
            "canonical_root": str(root.resolve()),
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

    def confirm_mode(self, project_key: str, mode: str) -> dict[str, object]:
        if mode not in MODE_STEPS:
            raise ValueError("mode must be new_project or existing_project")

        key = normalize_project_key(project_key)
        readiness = self.read(key)
        if readiness is None:
            raise ValueError(f"No readiness found for project: {key}")
        mode_step_ids = {step_id for steps in MODE_STEPS.values() for step_id in steps}
        existing_steps = readiness.get("steps", [])
        base_steps = [
            dict(step)
            for step in existing_steps
            if isinstance(step, dict) and step.get("id") not in mode_step_ids
        ]
        for step in base_steps:
            if step.get("id") == "project_mode":
                step["status"] = "complete"

        readiness.update(
            {
                "project_mode": mode,
                "lifecycle": "preparing",
                "steps": [*base_steps, *_pending_steps(MODE_STEPS[mode])],
                "updated_at": _now(),
            }
        )
        self._write(key, readiness)
        return readiness

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
            pinned, expected_readiness = expected
            if tuple(_descriptor_identity(fd) for fd in descriptors) != tuple(
                _descriptor_identity(fd) for fd in pinned
            ):
                raise OSError(errno.ENOTDIR, f"Readiness directory changed: {key}")
            self._verify_current_chain(key, descriptors)
            try:
                readiness_identity = _entry_identity(descriptors[-1], "readiness.json")
            except FileNotFoundError as exc:
                raise OSError(errno.ENOTDIR, f"Readiness file disappeared: {key}") from exc
            if readiness_identity != expected_readiness:
                raise OSError(errno.ENOTDIR, f"Readiness file changed: {key}")
        finally:
            _close_descriptors(descriptors)

    def _commit_anchored_write(self, project_key: str) -> None:
        self._close_anchored_write(normalize_project_key(project_key))

    def _rollback_anchored_write(self, project_key: str) -> None:
        key = normalize_project_key(project_key)
        anchored = self._anchored_writes.pop(key, None)
        if anchored is None:
            return
        descriptors, readiness_identity = anchored
        try:
            _unlink_if_identity(descriptors[-1], "readiness.json", readiness_identity)
        finally:
            _close_descriptors(list(descriptors))

    def _close_anchored_write(self, project_key: str) -> None:
        anchored = self._anchored_writes.pop(project_key, None)
        if anchored is not None:
            _close_descriptors(list(anchored[0]))

    def close(self) -> None:
        for key in list(self._anchored_writes):
            self._close_anchored_write(key)

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
        project_fd = descriptors[-1]
        try:
            self._verify_current_chain(key, descriptors)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open("readiness.json", flags, dir_fd=project_fd)
            except FileNotFoundError:
                self._verify_current_chain(key, descriptors)
                return None
            with os.fdopen(descriptor, encoding="utf-8") as readiness_file:
                readiness = json.load(readiness_file)
            self._verify_current_chain(key, descriptors)
            return readiness
        finally:
            _close_descriptors(descriptors)

    def _write_anchored(
        self,
        project_key: str,
        readiness: dict[str, object],
    ) -> None:
        key = normalize_project_key(project_key)
        self._rollback_anchored_write(key)
        descriptors = self._open_project_directory_chain(key, create=True)
        assert descriptors is not None
        project_fd = descriptors[-1]
        temporary_name = f".readiness.json.{secrets.token_hex(8)}.tmp"
        temporary_created = False
        published_identity: FileIdentity | None = None
        try:
            self._verify_current_chain(key, descriptors)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=project_fd)
            temporary_created = True
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(readiness, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                published_identity = _descriptor_identity(temporary.fileno())
            self._verify_current_chain(key, descriptors)
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
            pinned: list[int] = []
            try:
                for descriptor in descriptors:
                    pinned.append(os.dup(descriptor))
            except BaseException:
                _close_descriptors(pinned)
                raise
            self._anchored_writes[key] = (tuple(pinned), published_identity)
        except BaseException:
            if published_identity is not None:
                try:
                    _unlink_if_identity(project_fd, "readiness.json", published_identity)
                except OSError:
                    pass
            raise
        finally:
            if temporary_created:
                try:
                    os.unlink(temporary_name, dir_fd=project_fd)
                except FileNotFoundError:
                    pass
            _close_descriptors(descriptors)

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
    return {
        "inspect_project_facts": True,
        "edit_project_settings": True,
        "run_readiness_steps": True,
        "start_workflow": False,
        "start_workflow_reason": (
            "project_mode_unconfirmed"
            if readiness["project_mode"] == "unconfirmed"
            else "mode_specific_readiness_pending"
        ),
    }


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


def _unlink_if_identity(
    directory_fd: int,
    name: str,
    expected_identity: FileIdentity,
) -> None:
    try:
        current_identity = _entry_identity(directory_fd, name)
    except FileNotFoundError:
        return
    if current_identity != expected_identity:
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)
