"""One project-owned environment reference, independent of Worker workspaces.

This module describes the environment to planning and execution. It does not
install dependencies, dispatch jobs, approve changes, or implement a scheduler.
Preparation and changes belong to ordinary Planner tasks and their dependencies.
"""
from __future__ import annotations

from pathlib import Path


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
    if not environment.get("setup"):
        return
    shared.mkdir(parents=True, exist_ok=True)
    # The ordinary preparation task owns dependency contents.
    paths = list(environment["shared_paths"])
    for relative in paths:
        local = workspace / relative
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



def runtime_environment(environment, inherited):
    """Use the same project tool executables for CLI and session Workers."""
    import os

    result = dict(inherited)
    if not environment or not environment.get("setup_configured"):
        return result
    root = Path(environment["environment_root"])
    paths = environment["shared_paths"]
    bins = [str(root / path / ("bin" if path.endswith(".venv") else ".bin"))
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
    target = Path(environment["environment_root"])
    target.mkdir(parents=True, exist_ok=True)
    paths = subprocess.run(["git", "ls-files", "-z"], cwd=source, check=True,
                           capture_output=True).stdout.decode().split("\0")
    paths = [p for p in paths if p and not p.startswith((".git/", ".goodteam/"))]
    record = target / ".goodteam-source-files.json"
    previous = json.loads(record.read_text()) if record.exists() else []
    for relative in set(previous) - set(paths):
        old = target / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not old.resolve().is_relative_to(target.resolve()):
            raise ValueError("Invalid retained environment source path")
        if old.is_file() and not old.is_symlink():
            old.unlink()
    for relative in paths:
        if relative.startswith((".git/", ".goodteam/")):
            continue
        original = source / relative
        destination = target / relative
        # Captured source files are regular files; never follow destination links.
        if original.is_symlink() or destination.is_symlink():
            raise ValueError("Environment source input must be a regular file: " + relative)
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError("Environment source destination escapes the project environment")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    record.write_text(json.dumps(paths))



def preparation_planning_scope(project_config, tasks, authorized_ids):
    """Planner ordering over existing tasks, before any Worker is assigned."""
    from goodteam_os.worker_setup import load_worker_setup

    if not load_worker_setup(project_config):
        return None
    authorized = [task for task in tasks if task["id"] in authorized_ids]
    preparation = [task for task in authorized if task.get("write_domains") == [".goodteam/environment"]]
    if any(task["status"] == "done" and task.get("delivery_verified") is True for task in preparation):
        return None
    return {
        "needs_discovery": not preparation,
        "task_ids": [task["id"] for task in authorized if task in preparation
                     or task.get("execution_policy") == "read_only"],
    }
