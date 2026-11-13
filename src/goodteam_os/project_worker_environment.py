"""Locally captured dependency templates for isolated project Workers.

Only an explicit local operator captures a template. The project configuration
selects its immutable digest; Worker/model/HTTP inputs cannot choose a path or
command. Every dispatch receives a separate byte copy, never shared hard links.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import posixpath
from contextlib import nullcontext
import fcntl
from pathlib import Path
import re
import shutil
import stat
import tempfile
from uuid import uuid4

from goodteam_os.dispatch_workspace import _capture, _file_manifest, _git
from goodteam_os.memory_storage import read_bytes, role_lock
from goodteam_os.repository_access import AnchoredRepository, canonical_relative_path
from goodteam_os.repository_snapshot import SnapshotLimits, _canonical_json, _expected_root_identity
from goodteam_os.service import service_state_dir
from goodteam_os.skills.filesystem import directory, write_new


_SCHEMA = "project_worker_environment.v1"
_REF = re.compile(r"sha256:[0-9a-f]{64}")


def _root(path):
    root = Path(path).expanduser().absolute()
    if ".." in root.parts:
        raise ValueError("Environment roots cannot contain parent traversal.")
    with AnchoredRepository(root):
        return root


def _relative(value):
    value = canonical_relative_path(value)
    if any(part.casefold() in {".git", ".goodteam"} for part in value.split("/")):
        raise ValueError("Environment paths cannot contain project metadata.")
    return value


def _environment_root(project_root):
    key = hashlib.sha256(os.fsencode(str(project_root))).hexdigest()
    return service_state_dir().expanduser().resolve() / "project-worker-environments" / key


def _host():
    return {"system": platform.system(), "machine": platform.machine(),
            "release": platform.release(), "libc": list(platform.libc_ver())}


def _file_digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("An environment tool must be a regular file.")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        if _file_identity(before) != _file_identity(after):
            raise ValueError("An environment tool changed while being inspected.")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _tools(paths):
    result = []
    for requested in paths:
        if not Path(requested).is_absolute():
            raise ValueError("Environment tools must be specified by absolute path.")
        name = Path(requested).name
        found = shutil.which(name)
        if not found or Path(found).resolve(strict=True) != Path(requested).resolve(strict=True):
            raise ValueError("The selected environment tool is not the tool on the Worker PATH.")
        actual = Path(found).resolve(strict=True)
        result.append({"command": name, "path": str(actual), "sha256": _file_digest(actual)})
    if not result or len({item["command"] for item in result}) != len(result):
        raise ValueError("Select distinct existing tools for this environment.")
    return sorted(result, key=lambda item: item["command"])


def _inputs(root, patterns):
    capture = _capture(root, SnapshotLimits(), expected_identity=_expected_root_identity(root, None), original=True)
    files = _file_manifest(capture)
    selected = set()
    for pattern in patterns:
        matches = {path for path in files if fnmatch.fnmatchcase(path, pattern)}
        if not matches:
            raise ValueError("An environment input pattern has no captured project files.")
        selected.update(matches)
    return {path: files[path] for path in sorted(selected)}


def _check_artifacts(root, artifacts):
    for path in artifacts:
        if _git(root, ["ls-files", "-z", "--", path]).stdout:
            raise ValueError("Environment artifacts cannot cover tracked project files.")
        ignored = _git(root, ["check-ignore", "-q", "--no-index", "--", path + "/"], check=False)
        if ignored.returncode != 0:
            raise ValueError("Each environment artifact must already be ignored by this project.")


def _open_path(root_fd, path):
    fd = os.dup(root_fd)
    try:
        for part in path.split("/"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _make_path(root_fd, path):
    fd = os.dup(root_fd)
    try:
        for part in path.split("/"):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _link_target(root, relative, target):
    if not target or os.path.isabs(target):
        raise ValueError("Environment links must be relative to their project workspace.")
    logical = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
    if logical in {"", ".", ".."} or logical.startswith("../"):
        raise ValueError("An environment link leaves its project workspace.")
    _relative(logical)
    resolved = (root / relative).resolve(strict=True)
    try:
        inside = resolved.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise ValueError("An environment link resolves outside its project workspace.") from exc
    _relative(inside)
    return inside


def _copy_tree(src_fd, dst_fd, root, prefix, entries, expected=None, *, verify_links=False):
    for name in sorted(os.listdir(src_fd)):
        if name in {".", ".."} or "/" in name:
            raise ValueError("Invalid environment entry.")
        relative = _relative(prefix + "/" + name)
        before = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise ValueError("Environment entries cannot have special permission bits.")
        mode = stat.S_IMODE(before.st_mode)
        if stat.S_ISDIR(before.st_mode):
            source = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=src_fd)
            try:
                if _file_identity(before) != _file_identity(os.fstat(source)):
                    raise ValueError("An environment directory changed during capture.")
                if dst_fd is not None:
                    os.mkdir(name, 0o700, dir_fd=dst_fd)
                destination = (os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dst_fd)
                               if dst_fd is not None else None)
                try:
                    entries[relative] = {"kind": "directory", "mode": mode}
                    _copy_tree(source, destination, root, relative, entries, expected, verify_links=verify_links)
                    if destination is not None:
                        os.fchmod(destination, mode)
                finally:
                    if destination is not None:
                        os.close(destination)
                if _file_identity(before) != _file_identity(os.fstat(source)):
                    raise ValueError("An environment directory changed during capture.")
            finally:
                os.close(source)
        elif stat.S_ISREG(before.st_mode):
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=src_fd)
            try:
                if _file_identity(before) != _file_identity(os.fstat(source)):
                    raise ValueError("An environment file changed during capture.")
                destination = (os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst_fd)
                               if dst_fd is not None else None)
                digest = hashlib.sha256()
                prior = b""
                source_root = os.fsencode(str(root.resolve())) if expected is None else b""
                try:
                    while chunk := os.read(source, 1024 * 1024):
                        if source_root and source_root in prior + chunk:
                            raise ValueError("An environment file embeds the original workspace path; it cannot be safely relocated.")
                        if source_root:
                            prior = (prior + chunk)[-len(source_root):]
                        digest.update(chunk)
                        if destination is not None:
                            view = memoryview(chunk)
                            while view:
                                view = view[os.write(destination, view):]
                    if destination is not None:
                        os.fchmod(destination, mode)
                finally:
                    if destination is not None:
                        os.close(destination)
                if _file_identity(before) != _file_identity(os.fstat(source)):
                    raise ValueError("An environment file changed during capture.")
                entries[relative] = {"kind": "file", "mode": mode, "size": before.st_size,
                                     "sha256": digest.hexdigest()}
            finally:
                os.close(source)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=src_fd)
            resolved = (_link_target(root, relative, target) if expected is None or verify_links
                        else (expected.get(relative) or {}).get("resolved"))
            if not isinstance(resolved, str):
                raise ValueError("An environment link is not in the captured template.")
            _relative(resolved)
            logical = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
            if os.path.isabs(target) or logical in {"", ".", ".."} or logical.startswith("../"):
                raise ValueError("An environment link leaves its project workspace.")
            _relative(logical)
            if dst_fd is not None:
                os.symlink(target, name, dir_fd=dst_fd)
            if (_file_identity(before) != _file_identity(os.stat(name, dir_fd=src_fd, follow_symlinks=False))
                    or os.readlink(name, dir_fd=src_fd) != target):
                raise ValueError("An environment link changed during capture.")
            entries[relative] = {"kind": "symlink", "target": target, "resolved": resolved}
        else:
            raise ValueError("Environment artifacts support only regular files, directories and safe links.")
        if expected is not None and entries[relative] != expected.get(relative):
            raise ValueError("The captured environment changed after registration.")


def _copy_artifacts(source, destination, artifacts, *, expected=None, verify_links=False):
    entries = {}
    with directory(source) as source_fd, (directory(destination) if destination is not None else nullcontext(None)) as destination_fd:
        for path in artifacts:
            source_dir = _open_path(source_fd, path)
            try:
                # Existing project paths are never replaced or merged.
                parent, _, name = path.rpartition("/")
                dest_parent = ((_make_path(destination_fd, parent) if parent else os.dup(destination_fd))
                               if destination_fd is not None else None)
                try:
                    if dest_parent is not None:
                        os.mkdir(name, 0o700, dir_fd=dest_parent)
                    dest_dir = (os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dest_parent)
                                if dest_parent is not None else None)
                    try:
                        info = os.fstat(source_dir)
                        if info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                            raise ValueError("Environment artifacts cannot have special permission bits.")
                        entries[path] = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
                        _copy_tree(source_dir, dest_dir, source, path, entries, expected, verify_links=verify_links)
                        if _file_identity(info) != _file_identity(os.fstat(source_dir)):
                            raise ValueError("An environment artifact changed during capture.")
                        if dest_dir is not None:
                            os.fchmod(dest_dir, stat.S_IMODE(info.st_mode))
                    finally:
                        if dest_dir is not None:
                            os.close(dest_dir)
                finally:
                    if dest_parent is not None:
                        os.close(dest_parent)
            finally:
                os.close(source_dir)
    if expected is not None and entries != expected:
        raise ValueError("The captured environment no longer matches its registered contents.")
    return entries


def capture_environment(*, project_root, prepared_root, inputs, artifacts, tools):
    """Trusted local operation: copy explicit prepared artifacts, no command execution."""
    project_root, prepared_root = _root(project_root), _root(prepared_root)
    patterns = sorted(set(_relative(item) for item in inputs))
    paths = sorted(set(_relative(item) for item in artifacts))
    if not patterns or not paths or len(patterns) != len(inputs) or len(paths) != len(artifacts):
        raise ValueError("Select distinct input patterns and artifact directories.")
    if any(other.startswith(path + "/") for path in paths for other in paths if path != other):
        raise ValueError("Environment artifact directories cannot overlap.")
    _check_artifacts(project_root, paths)
    _check_artifacts(prepared_root, paths)
    inputs_before = _inputs(prepared_root, patterns)
    if inputs_before != _inputs(project_root, patterns):
        raise ValueError("Prepared environment inputs differ from the target project.")
    runtime = _tools(tools)
    storage = _environment_root(project_root)
    with role_lock(storage):
        temporary = Path(tempfile.mkdtemp(prefix="capture-", dir=storage))
        data = temporary / "data"
        data.mkdir(mode=0o700)
        entries = _copy_artifacts(prepared_root, data, paths)
        if inputs_before != _inputs(prepared_root, patterns) or inputs_before != _inputs(project_root, patterns):
            raise ValueError("Environment inputs changed during capture; the incomplete copy was retained.")
        if runtime != _tools(tools):
            raise ValueError("Environment tools changed during capture; the incomplete copy was retained.")
        record = {"schema_version": _SCHEMA, "project_root": str(project_root),
                  "project_root_identity": _expected_root_identity(project_root, None),
                  "input_patterns": patterns, "inputs": inputs_before, "artifacts": paths,
                  "tools": runtime, "host": _host(), "entries": entries,
                  "provenance": {"kind": "local_prepared_artifacts", "source_root": str(prepared_root),
                                 "preparation_command_verified": False}}
        digest = hashlib.sha256(_canonical_json(record)).hexdigest()
        (temporary / "environment.json").write_bytes(_canonical_json(record))
        target = storage / digest
        if target.exists():
            if read_bytes(target / "environment.json") != _canonical_json(record):
                raise ValueError("The existing environment identity is inconsistent.")
            # Preserve the new capture rather than deleting any local artifact.
        else:
            os.rename(temporary, target)
        return {"environment_ref": "sha256:" + digest, "project_root": str(project_root),
                "artifact_paths": paths, "input_patterns": patterns,
                "tool_commands": [item["command"] for item in runtime],
                "preparation_command_verified": False}


def load_environment(*, project_root, ref, source_root):
    """Verify the exact project, current inputs and local tools before admission."""
    project_root, source_root = _root(project_root), _root(source_root)
    if not isinstance(ref, str) or _REF.fullmatch(ref) is None:
        raise ValueError("The configured Worker environment identity is invalid.")
    path = _environment_root(project_root) / ref.split(":", 1)[1]
    raw = read_bytes(path / "environment.json")
    record = json.loads(raw) if raw is not None else None
    if (not isinstance(record, dict) or record.get("schema_version") != _SCHEMA
            or hashlib.sha256(_canonical_json(record)).hexdigest() != ref.split(":", 1)[1]
            or record.get("project_root") != str(project_root)
            or record.get("project_root_identity") != _expected_root_identity(project_root, None)
            or record.get("host") != _host()):
        raise ValueError("The original project Worker environment is unavailable or changed.")
    tools = record.get("tools") or []
    current = []
    for tool in tools:
        found = shutil.which(tool["command"])
        if not found:
            raise ValueError("A registered environment tool is unavailable.")
        actual = Path(found).resolve(strict=True)
        current.append({"command": tool["command"], "path": str(actual), "sha256": _file_digest(actual)})
    if current != tools or _inputs(source_root, record["input_patterns"]) != record["inputs"]:
        raise ValueError("The project inputs or tools differ from the registered Worker environment.")
    _check_artifacts(source_root, record["artifacts"])
    return record


def materialize_environment(*, project_root, ref, source_root, job_root):
    record = load_environment(project_root=project_root, ref=ref, source_root=source_root)
    job_root = _root(job_root)
    template = _environment_root(_root(project_root)) / ref.split(":", 1)[1] / "data"
    entries = _copy_artifacts(template, job_root, record["artifacts"], expected=record["entries"])
    for relative, entry in entries.items():
        if entry["kind"] == "symlink" and _link_target(job_root, relative, entry["target"]) != entry["resolved"]:
            raise ValueError("An environment link does not resolve inside its Worker workspace.")
    return {"environment_ref": ref, "artifact_paths": record["artifacts"]}


def inspect_environment(*, project_root, ref, source_root):
    """Inspect actual retained template bytes, not only its configuration."""
    record = load_environment(project_root=project_root, ref=ref, source_root=source_root)
    template = _environment_root(_root(project_root)) / ref.split(":", 1)[1] / "data"
    _copy_artifacts(template, None, record["artifacts"], expected=record["entries"])
    return record


def verify_environment_instance(*, project_root, ref, source_root, job_root):
    """Recheck the original inputs and private copy immediately before launch."""
    record = load_environment(project_root=project_root, ref=ref, source_root=source_root)
    job_root = _root(job_root)
    _copy_artifacts(job_root, None, record["artifacts"], expected=record["entries"], verify_links=True)
    return {"environment_ref": ref, "artifact_paths": record["artifacts"]}


def activate_environment(*, workspace, project_key, ref, replace_from=None):
    """Select an environment for an idle project, using an explicit replacement identity."""
    from goodteam_os.assistant_queue import active_project_run, require_no_active_project_goal
    from goodteam_os.goal_continuation import goal_guard
    from goodteam_os.models import _parse_yaml_source
    from goodteam_os.project_iteration_execution import active_project_batches
    from goodteam_os.graph import resolve_project_runtime_root
    from goodteam_os.task_assistant_session import TaskAssistantSessionStore
    from goodteam_os.web import _project_config_for_key, _project_target_and_root
    from goodteam_os.workflow_mutations import workflow_guard

    workspace = Path(workspace).resolve()
    key, _target, root = _project_target_and_root(workspace, project_key)
    config = _project_config_for_key(workspace, key)
    inspect_environment(project_root=root, ref=ref, source_root=root)
    with goal_guard(workspace, key, blocking=False), workflow_guard(workspace, key):
        session = TaskAssistantSessionStore(workspace, key).read()
        if ((session.get("project_continuation") or {}).get("status") == "active"
                or active_project_run(workspace, key) or active_project_batches(workspace, key)):
            raise ValueError("Finish or stop active project execution before changing its environment configuration.")
        require_no_active_project_goal(workspace, key)
        with directory(config.parent) as parent:
            descriptor = os.open(config.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024:
                    raise ValueError("The project configuration is unavailable or unsafe to update.")
                os.lseek(descriptor, 0, os.SEEK_SET)
                raw = b""
                while chunk := os.read(descriptor, 1024 * 1024):
                    raw += chunk
                text = raw.decode("utf-8")
                parsed = _parse_yaml_source(text)
                repos = parsed.get("repos")
                if not isinstance(repos, dict) or resolve_project_runtime_root(dict(repos)) != root:
                    raise ValueError("The project root changed during activation; reload the project selection.")
                current = parsed.get("worker_environment_ref")
                if current == ref:
                    return {"project_key": key, "environment_ref": ref, "changed": False}
                if current != replace_from or (replace_from is not None and
                                               (not isinstance(replace_from, str) or _REF.fullmatch(replace_from) is None)):
                    raise ValueError("The existing environment differs from --replace-from; reload the project selection.")
                lines = text.splitlines(keepends=True)
                matches = [index for index, line in enumerate(lines) if re.match(r"^worker_environment_ref\s*:", line)]
                if len(matches) != (1 if current is not None else 0):
                    raise ValueError("The project config contains an ambiguous environment selection.")
                line = "worker_environment_ref: " + json.dumps(ref) + "\n"
                if matches:
                    lines[matches[0]] = line
                    updated = "".join(lines)
                else:
                    updated = text + ("" if not text or text.endswith("\n") else "\n") + line
                if _parse_yaml_source(updated) != {**parsed, "worker_environment_ref": ref}:
                    raise ValueError("The environment selection cannot be safely updated in this project config.")
                current_info = os.stat(config.name, dir_fd=parent, follow_symlinks=False)
                if _file_identity(info) != _file_identity(os.fstat(descriptor)) or _file_identity(info) != _file_identity(current_info):
                    raise ValueError("The project config changed during activation; reload it before continuing.")
                temporary = ".worker-environment-" + uuid4().hex + ".tmp"
                try:
                    write_new(parent, temporary, updated.encode("utf-8"), mode=stat.S_IMODE(info.st_mode))
                    if (_file_identity(info) != _file_identity(os.fstat(descriptor))
                            or _file_identity(info) != _file_identity(os.stat(config.name, dir_fd=parent, follow_symlinks=False))):
                        raise ValueError("The project config changed during activation; reload it before continuing.")
                    os.replace(temporary, config.name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                return {"project_key": key, "environment_ref": ref, "changed": True}
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
