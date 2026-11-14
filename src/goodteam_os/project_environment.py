"""One project-owned environment reference, independent of Worker workspaces.

This module describes the environment to planning and execution. It does not
install dependencies, dispatch jobs, approve changes, or implement a scheduler.
Preparation and changes belong to ordinary Planner tasks and their dependencies.
"""
from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import os
import stat


def project_environment_context(project_root: Path) -> dict[str, str]:
    """Use the project as the sole source of environment identity.

    A Worker id, batch id, candidate hash or retry number must never select an
    environment. Source worktrees may differ without creating environments.
    Merely returning this reference makes no claim that preparation succeeded.
    """
    root = Path(project_root).expanduser().resolve()
    return {
        "project_root": str(root),
        "environment_root": str(root / ".goodteam" / "environment"),
        "owner": "planner",
        "preparation": "ordinary_project_task",
    }


PROJECT_ENVIRONMENT_PLANNING = """The project has one shared execution environment.
An environment preparation/change task declares the write domain
.goodteam/environment; affected development tasks use
the existing dependencies field to depend on it. Do not dispatch a preparation
task and its dependent work in the same parallel batch.
Before dispatching work, determine the project's actual toolchain and dependency
requirements from its repository. If preparation or a change is needed, plan it
as an ordinary project task and make affected work depend on that task. Use the
existing task queue and Worker pool, not a separate environment queue. All
Workers use the same project environment; do not plan per-Worker dependency
versions or repeat installation for every source worktree. A Worker reports an
actual environment failure or requested change to Planner. Environment context
is a location and ownership reference, not proof of successful preparation.
"""


def environment_for_task(project_root, setup, write_domains=(), *, source_root=None):
    """Describe one ordinary task's use of the project environment.

    The Planner uses the existing declared write scope to assign preparation;
    there is no environment role, new task type, admission loop or scheduler.
    """
    from goodteam_os.project_iteration import normalize_write_domains

    value = project_environment_context(project_root)
    root = Path(source_root or project_root)
    defaults = (["node_modules"] if (root / "package.json").is_file() else [])
    if any((root / name).is_file() for name in ("pyproject.toml", "requirements.txt")):
        defaults.append(".venv")
    paths = setup.get("shared_paths", defaults) if setup else []
    if paths and normalize_write_domains(paths) != paths:
        raise ValueError("Project environment shared_paths must be relative dependency directories.")
    if any(path == ".git" or path.startswith(".git/") or path == ".goodteam"
           or path.startswith(".goodteam/") for path in paths):
        raise ValueError("Dependency directories cannot replace repository or GoodTeam metadata.")
    value["shared_paths"] = paths
    value["preparation_task"] = ".goodteam/environment" in (write_domains or ())
    value["setup_configured"] = bool(setup)
    if setup:
        value["setup"] = setup
    return value


@contextmanager
def environment_directory(environment):
    """Open the project-owned directory without following repository links."""
    root = Path(environment["project_root"])
    expected = root / ".goodteam" / "environment"
    if Path(environment["environment_root"]) != expected:
        raise ValueError("Environment must belong to the project")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        for part in (".goodteam", "environment"):
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _relative_parent(root_fd, relative, *, create=False):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in {".", ".."} for p in parts):
        raise ValueError("Invalid environment source path")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _bind_node_modules(local, shared, workspace, environment_root):
    """Share installed packages, but keep workspace source imports Worker-local.

    Only the resolution view is local. There is still one installation and no
    per-Worker dependency version. npm workspace and local .bin links refer to
    the current source tree, not the source copied for installation.
    """
    if local.is_symlink():
        if local.resolve() != shared.resolve():
            raise ValueError("Conflicting dependency binding: " + str(local))
        local.unlink()  # Migrate our old whole-directory binding.
    if local.exists() and not local.is_dir():
        raise ValueError("Conflicting dependency binding: " + str(local))
    import json

    marker = local / ".goodteam-environment-binding"
    if local.exists() and not marker.is_file() and any(local.iterdir()):
        raise ValueError("Environment binding must not replace source: " + str(local))
    local.mkdir(parents=True, exist_ok=True)
    try:
        marker_fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        previous = []
    else:
        with os.fdopen(marker_fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Invalid dependency binding record")
            previous = json.load(stream)
    entries = list(shared.iterdir()) if shared.is_dir() else []
    for name in set(previous) - {entry.name for entry in entries}:
        if Path(name).name != name:
            raise ValueError("Invalid dependency binding entry")
        stale = local / name
        if stale.is_symlink():
            stale.unlink()
        elif stale.is_dir() and (stale / ".goodteam-environment-binding").is_file():
            _bind_node_modules(stale, shared / name, workspace, environment_root)
            (stale / ".goodteam-environment-binding").unlink()
            stale.rmdir()
    for entry in entries:
        destination = local / entry.name
        if entry.name.startswith("@") or entry.name == ".bin":
            _bind_node_modules(destination, entry, workspace, environment_root)
            continue
        resolved = entry.resolve()
        target = entry
        if entry.is_symlink() and resolved.is_relative_to(environment_root):
            relative = resolved.relative_to(environment_root)
            if "node_modules" not in relative.parts:
                target = workspace / relative
        if destination.is_symlink():
            if destination.resolve() == target.resolve():
                continue
            destination.unlink()
        elif destination.exists():
            raise ValueError("Environment binding must not replace source: " + str(destination))
        destination.symlink_to(target, target_is_directory=entry.is_dir())
    temporary = local / (".goodteam-binding-" + __import__("uuid").uuid4().hex)
    marker_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(marker_fd, "w") as stream:
            json.dump([entry.name for entry in entries], stream)
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def bind_environment(workspace, environment):
    """Expose the same dependencies in each isolated source worktree.

    Do not install, copy, version, or validate dependencies here. Missing or
    unusable dependencies surface as ordinary execution errors to Planner.
    Existing source content is never replaced by an environment binding.
    """
    import subprocess

    workspace = Path(workspace).resolve()
    shared = Path(environment["environment_root"])
    # Creating an empty location is not installation or successful preparation.
    if not environment.get("setup_configured"):
        return
    with environment_directory(environment):
        pass
    # The ordinary preparation task owns dependency contents.
    paths = list(environment["shared_paths"])
    for relative in paths:
        local = workspace / relative
        if Path(relative).name == "node_modules":
            _bind_node_modules(local, shared / relative, workspace, shared)
            continue
        target = shared if relative == ".goodteam/environment" else shared / relative
        if local.is_symlink():
            if local.resolve() == target.resolve():
                continue
            raise ValueError(f"Project environment binding conflicts with {relative}")
        if local.exists():
            raise ValueError(f"Project environment must not replace source content: {relative}")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.symlink_to(target, target_is_directory=True)
    # Dependencies are runtime inputs, not source changes in a candidate.
    git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=workspace,
                             check=True, capture_output=True, text=True).stdout.strip()
    exclude = Path(git_dir)
    if not exclude.is_absolute():
        exclude = workspace / exclude
    exclude = exclude / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    entries = ["/" + path for path in paths]
    missing = [entry for entry in entries if entry not in existing.splitlines()]
    if missing:
        with exclude.open("a") as stream:
            stream.write("\n" + "\n".join(missing) + "\n")


def worker_environment_instructions(environment):
    import json

    common = ("Use this project's shared environment, not a per-Worker installation. "
              "Environment failures or needed changes go back to Planner as task results. "
              "Source workspaces remain independent.\n" + json.dumps({key: val for key, val in environment.items() if key != "setup"}, ensure_ascii=False))
    if environment["preparation_task"]:
        return common + ("\nThe configured installation is run by this task's restricted setup "
            "executor before the agent starts, in the shared environment. Do not rerun installation "
            "through agent shell tools. Report the actual setup receipt and any remaining problems "
            "to Planner. Do not alter the shared environment outside that executor.")
    return common + ("\nDo not install or change shared dependencies in this development task. "
                    "Use the prepared environment; report actual failures to Planner.")



def runtime_environment(environment, inherited, *, workspace=None):
    """Use the same project tool executables for CLI and session Workers."""
    import os

    result = dict(inherited)
    if not environment or not environment.get("setup_configured"):
        return result
    root = Path(environment["environment_root"])
    paths = environment["shared_paths"]
    bins = [str((Path(workspace) if workspace and path.endswith("node_modules") else root) / path / ("bin" if path.endswith(".venv") else ".bin"))
            for path in paths if path.endswith((".venv", "node_modules"))]
    if bins:
        result["PATH"] = os.pathsep.join([*bins, result.get("PATH", os.defpath)])
    if ".venv" in paths:
        result["VIRTUAL_ENV"] = str(root / ".venv")
    return result


def prepare_environment_source(workspace, environment):
    """Supply the ordinary preparation task the captured repository inputs.

    Installed dependencies stay in the project environment; copying tracked
    source inputs is not itself installation and does not declare readiness.
    """
    import json
    import shutil
    import subprocess

    source = Path(workspace)
    paths = subprocess.run(["git", "ls-files", "-z"], cwd=source, check=True,
                           capture_output=True).stdout.decode().split("\0")
    paths = [p for p in paths if p and not p.startswith((".git/", ".goodteam/"))]
    with environment_directory(environment) as root_fd:
        record_name = ".goodteam-source-files.json"
        try:
            record_fd = os.open(record_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        except FileNotFoundError:
            previous = []
        else:
            with os.fdopen(record_fd) as stream:
                previous = json.load(stream)
        for relative in set(previous) - set(paths):
            try:
                with _relative_parent(root_fd, relative) as (parent_fd, name):
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        # Remove only obsolete empty source directories, deepest first. Installed
        # dependency contents are not removed by a source file/directory change.
        obsolete_dirs = {str(parent) for relative in set(previous) - set(paths)
                         for parent in Path(relative).parents if str(parent) != "."}
        for relative in sorted(obsolete_dirs, key=lambda value: len(Path(value).parts), reverse=True):
            try:
                with _relative_parent(root_fd, relative) as (parent_fd, name):
                    os.rmdir(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                import errno
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR}:
                    raise
        for relative in paths:
            original = source / relative
            input_fd = os.open(original, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(input_fd, "rb") as input_stream:
                mode = os.fstat(input_stream.fileno()).st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError("Environment input must be a regular file: " + relative)
                with _relative_parent(root_fd, relative, create=True) as (parent_fd, name):
                    # Replace the directory entry, never truncate a symlink or
                    # hardlinked target supplied by repository/setup content.
                    temporary = ".goodteam-copy-" + __import__("uuid").uuid4().hex
                    output_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                        stat.S_IMODE(mode), dir_fd=parent_fd)
                    try:
                        with os.fdopen(output_fd, "wb") as output_stream:
                            shutil.copyfileobj(input_stream, output_stream)
                        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    finally:
                        try:
                            os.unlink(temporary, dir_fd=parent_fd)
                        except FileNotFoundError:
                            pass
        temporary = ".goodteam-record-" + __import__("uuid").uuid4().hex
        record_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        try:
            with os.fdopen(record_fd, "w") as stream:
                json.dump(paths, stream)
            os.replace(temporary, record_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass



def preparation_planning_scope(project_config, tasks, authorized_ids):
    """Planner ordering over existing tasks, before any Worker is assigned."""
    from goodteam_os.worker_setup import load_worker_setup

    if not load_worker_setup(project_config):
        return None
    authorized = [task for task in tasks if task["id"] in authorized_ids]
    if not any(task.get("execution_policy", "workspace_write") != "read_only"
               for task in authorized):
        return None
    dependency_ids = {dependency for task in authorized for dependency in task.get("dependencies", [])}
    preparation_evidence = [task for task in tasks
                            if task["id"] in set(authorized_ids) | dependency_ids
                            and task.get("write_domains") == [".goodteam/environment"]]
    preparation = [task for task in preparation_evidence if task["id"] in authorized_ids]
    if any(task["status"] == "done" and task.get("delivery_verified") is True for task in preparation_evidence):
        return None
    return {
        "needs_discovery": not preparation,
        "task_ids": [task["id"] for task in authorized if task in preparation
                     or task.get("execution_policy") == "read_only"],
    }
