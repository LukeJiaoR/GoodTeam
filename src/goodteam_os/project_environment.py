"""Bind existing project dependencies to source worktrees.

Planning and installation are ordinary agent work. This module only supplies
locations and runtime paths; it neither creates an environment nor gates tasks.
"""
from __future__ import annotations

from pathlib import Path
import os
import stat


def project_environment_context(project_root):
    root = Path(project_root).expanduser().resolve()
    return {"project_root": str(root), "environment_root": str(root)}


def environment_for_task(project_root, setup, write_domains=(), *, source_root=None):
    value = project_environment_context(project_root)
    root = Path(value["project_root"])
    # Reuse actual installations whether or not a setup command was configured.
    paths = setup.get("shared_paths") if setup else None
    if paths is None:
        paths = [name for name in (".venv", "node_modules") if (root / name).is_dir()]
    value["shared_paths"] = paths
    if setup:
        value["setup"] = setup
        value["configured_setup_command"] = setup["command"]
    return value


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
    """Reuse installed dependencies without creating or replacing source trees."""
    import subprocess

    workspace = Path(workspace).resolve()
    shared = Path(environment["environment_root"])
    if workspace == shared.resolve():
        return
    paths = []
    for relative in environment.get("shared_paths", []):
        target = shared / relative
        local = workspace / relative
        if not target.exists():
            continue
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Dependency binding requires a project-relative path: " + relative)
        if local.exists() and not local.is_symlink() and Path(relative).name != "node_modules":
            continue  # Use dependencies already installed in this worktree.
        if Path(relative).name == "node_modules":
            if local.is_dir() and not local.is_symlink() and not (local / ".goodteam-environment-binding").exists() and any(local.iterdir()):
                continue
            _bind_node_modules(local, target, workspace, shared)
        elif not local.is_symlink():
            local.parent.mkdir(parents=True, exist_ok=True)
            local.symlink_to(target, target_is_directory=True)
        paths.append(relative)
    if not paths:
        return
    # Dependency links are runtime inputs, not candidate source changes.
    exclude_name = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=workspace,
                                  check=True, capture_output=True, text=True).stdout.strip()
    exclude = Path(exclude_name)
    if not exclude.is_absolute():
        exclude = workspace / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    missing = ["/" + path for path in paths if "/" + path not in existing.splitlines()]
    if missing:
        with exclude.open("a") as stream:
            stream.write("\n" + "\n".join(missing) + "\n")


def worker_environment_instructions(environment, *, setup_tool=None, workspace=None):
    import json

    context = {key: val for key, val in environment.items() if key not in {"setup", "configured_setup_cwd"}}
    setup = environment.get("setup")
    if setup:
        context["configured_setup_command"] = setup["command"]
        if workspace is not None:
            context["configured_setup_cwd"] = str(workspace)
        context["installation_method"] = ("Run source-affecting installation in the assigned worktree so lockfile and generated source changes enter its candidate. Reuse existing project dependency locations; Planner assigns shared environment changes as ordinary tasks.")
    if setup_tool is not None:
        context["configured_setup_tool"] = setup_tool
    return "Project runtime context (configuration, not a readiness verdict):\n" + json.dumps(context, ensure_ascii=False)


def runtime_environment(environment, inherited, *, workspace=None):
    """Keep system configuration and resolve current worktree source first."""
    result = dict(inherited)
    if workspace:
        source = Path(workspace)
        roots = [str(source / "src")] if (source / "src").is_dir() else []
        roots.append(str(source))
        if result.get("PYTHONPATH"):
            roots.append(result["PYTHONPATH"])
        result["PYTHONPATH"] = os.pathsep.join(roots)
    if not environment:
        return result
    root = Path(workspace or environment["environment_root"])
    bins = []
    for relative in environment.get("shared_paths", []):
        path = root / relative
        if path.name == ".venv" and (path / "bin").is_dir():
            bins.append(str(path / "bin"))
            result["VIRTUAL_ENV"] = str(path)
        elif path.name == "node_modules" and (path / ".bin").is_dir():
            bins.append(str(path / ".bin"))
    if bins:
        result["PATH"] = os.pathsep.join([*bins, result.get("PATH", os.defpath)])
    return result
