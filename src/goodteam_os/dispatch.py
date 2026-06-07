import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from uuid import uuid4

from goodteam_os.models import _parse_simple_yaml
from goodteam_os.workers import WorkerJobStore


def parse_dispatch_track_spec(spec: str, *, base_dir: Path | None = None) -> dict[str, object]:
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise ValueError("Track spec must use role:adapter:task-file-or-inline-task.")
    role, adapter, task_ref = (part.strip() for part in parts)
    if not role or not adapter or not task_ref:
        raise ValueError("Track spec requires non-empty role, adapter, and task.")

    task_path = Path(task_ref)
    if not task_path.is_absolute() and base_dir is not None:
        task_path = base_dir / task_path
    if task_path.is_file():
        return {
            "role": role,
            "adapter": adapter,
            "task": task_path.read_text(encoding="utf-8"),
            "task_source": str(task_path),
        }
    return {
        "role": role,
        "adapter": adapter,
        "task": task_ref,
        "task_source": "inline",
    }


def parse_dispatch_verify_spec(spec: str) -> tuple[str, str]:
    parts = spec.split(":", 1)
    if len(parts) != 2:
        raise ValueError("Verify spec must use role:shell-command.")
    role, command = (part.strip() for part in parts)
    if not role or not command:
        raise ValueError("Verify spec requires non-empty role and shell command.")
    return role, command


def ensure_dispatch_project_root_is_safe(
    project_root: Path,
    *,
    configured_repo_paths: list[Path] | None = None,
    projects_dir: Path = Path("projects"),
    allow_unsafe: bool = False,
) -> dict[str, object]:
    if allow_unsafe:
        return {"safe": True, "reason": ""}
    resolved_root = project_root.resolve()
    for repo_path in configured_repo_paths if configured_repo_paths is not None else configured_project_repo_paths(projects_dir):
        resolved_repo = repo_path.resolve()
        if resolved_root == resolved_repo or resolved_repo in resolved_root.parents:
            return {
                "safe": False,
                "reason": f"project_root {resolved_root} is inside configured project repo {resolved_repo}",
            }
    return {"safe": True, "reason": ""}


def configured_project_repo_paths(projects_dir: Path = Path("projects")) -> list[Path]:
    paths: list[Path] = []
    if not projects_dir.is_dir():
        return paths
    for config_path in sorted(projects_dir.glob("*.local.yaml")):
        try:
            target = _parse_simple_yaml(config_path)
        except Exception:
            continue
        repos = target.get("repos", {})
        if not isinstance(repos, dict):
            continue
        for value in repos.values():
            raw = str(value).strip()
            if raw:
                paths.append(Path(raw).expanduser())
    return paths


def run_track_verify(command: str, *, cwd: Path) -> dict[str, object]:
    try:
        shell = os.getenv("SHELL") or "/bin/sh"
        completed = subprocess.run(
            [shell, "-lc", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        exit_code = int(completed.returncode)
    except Exception as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = 1
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "passed": exit_code == 0,
        "output_tail": _output_tail(f"{stdout}\n{stderr}"),
    }


def dispatch_tracks(
    *,
    project_root: Path,
    tracks: list[dict[str, object]],
    verify_commands: dict[str, str] | None = None,
    constraints_file: Path | None = None,
    research_report_file: Path | None = None,
    projects_dir: Path | None = None,
    allow_unsafe: bool = False,
) -> dict[str, object]:
    safety_kwargs: dict[str, object] = {"allow_unsafe": allow_unsafe}
    if projects_dir is not None:
        safety_kwargs["projects_dir"] = projects_dir
    safety = ensure_dispatch_project_root_is_safe(project_root, **safety_kwargs)
    if not safety["safe"]:
        raise ValueError(str(safety["reason"]))
    constraints = _constraints_from_file(constraints_file)
    run_id = f"dispatch-{uuid4()}"
    store = WorkerJobStore(project_root)
    jobs_by_role = [
        _create_track_job(
            store,
            run_id=run_id,
            track=track,
            constraints=constraints,
            constraints_source=str(constraints_file) if constraints_file else "",
            research_report_source=str(research_report_file) if research_report_file else "",
        )
        for track in tracks
    ]
    results_by_role: dict[str, dict[str, object]] = {}
    max_workers = max(1, len(jobs_by_role))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run_dispatched_job, project_root, job, dict(verify_commands or {})): str(job["role"])
            for job in jobs_by_role
        }
        for future in as_completed(futures):
            role = futures[future]
            try:
                results_by_role[role] = future.result()
            except Exception as exc:
                results_by_role[role] = _exception_report(role=role, error=exc)

    jobs = [results_by_role.get(str(job["role"]), _exception_report(role=str(job["role"]), error=RuntimeError("missing result"))) for job in jobs_by_role]
    ok = [job for job in jobs if job.get("outcome") == "ok"]
    needs_human = [job for job in jobs if job.get("outcome") == "needs_human"]
    file_to_roles: dict[str, set[str]] = defaultdict(set)
    for job in jobs:
        role = str(job.get("role", ""))
        for path in job.get("changed_files", []):
            if role and isinstance(path, str):
                file_to_roles[path].add(role)
    file_collisions = [
        {"path": path, "roles": sorted(roles)}
        for path, roles in sorted(file_to_roles.items())
        if len(roles) > 1
    ]
    return {
        "all_ok": not needs_human,
        "has_collisions": bool(file_collisions),
        "file_collisions": file_collisions,
        "jobs": jobs,
        "ok": ok,
        "needs_human": needs_human,
        "run_id": run_id,
        "project_root": str(project_root),
    }


def _create_track_job(
    store: WorkerJobStore,
    *,
    run_id: str,
    track: dict[str, object],
    constraints: list[str],
    constraints_source: str = "",
    research_report_source: str = "",
) -> dict[str, Any]:
    return store.create_job(
        run_id=run_id,
        role=str(track["role"]),
        adapter=str(track["adapter"]),
        model=str(track.get("model", "")),
        task=str(track["task"]),
        context=str(track.get("context", "")),
        task_source=str(track.get("task_source", "")),
        constraints_source=constraints_source,
        research_report_source=research_report_source,
        constraints=[*constraints, *[str(item) for item in track.get("constraints", [])]],
        expected_artifacts=[str(item) for item in track.get("expected_artifacts", [])],
    )


def _run_dispatched_job(project_root: Path, job: dict[str, Any], verify_commands: dict[str, str]) -> dict[str, object]:
    store = WorkerJobStore(project_root)
    try:
        status = store.run_job(str(job["id"]))
    except Exception as exc:
        return _job_report(
            project_root=project_root,
            job=job,
            status={
                "status": "failed",
                "summary": f"{job['role']} worker failed with exception: {exc}",
                "error": exc.__class__.__name__,
            },
            verify={"passed": False, "skipped": True, "reason": "worker_failed"},
        )

    verify: dict[str, object] = {"passed": True, "skipped": True, "reason": "not_configured"}
    if str(status.get("status")) == "completed":
        command = verify_commands.get(str(job["role"]), "").strip()
        if command:
            verify = run_track_verify(command, cwd=project_root)
    else:
        verify = {"passed": False, "skipped": True, "reason": "worker_failed"}
    return _job_report(project_root=project_root, job=job, status=status, verify=verify)


def _job_report(
    *,
    project_root: Path,
    job: dict[str, Any],
    status: dict[str, Any],
    verify: dict[str, object],
) -> dict[str, object]:
    worker_ok = str(status.get("status", "")) == "completed"
    verify_ok = bool(verify.get("passed", False))
    outcome = "ok" if worker_ok and verify_ok else "needs_human"
    job_root = project_root / ".goodteam" / "workers" / "jobs" / str(job["id"])
    return {
        "job_id": str(job["id"]),
        "role": str(job["role"]),
        "adapter": str(job["adapter"]),
        "status": str(status.get("status", "")),
        "summary": str(status.get("summary", "")),
        "failure_class": str(status.get("failure_class", "")),
        "attempts": int(status.get("attempts", 0) or 0),
        "disposition": str(status.get("disposition", "")),
        "result_path": str(job_root / "result.md"),
        "status_path": str(job_root / "status.json"),
        "changed_files": changed_files(project_root),
        "memory_writeback": status.get("memory_writeback", {}),
        "verify": verify,
        "outcome": outcome,
    }


def _exception_report(*, role: str, error: Exception) -> dict[str, object]:
    return {
        "job_id": "",
        "role": role,
        "adapter": "",
        "status": "failed",
        "summary": str(error),
        "result_path": "",
        "status_path": "",
        "changed_files": [],
        "memory_writeback": {},
        "verify": {"passed": False, "skipped": True, "reason": "exception"},
        "outcome": "needs_human",
    }


def changed_files(project_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "diff", "--name-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _constraints_from_file(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ")):
            stripped = stripped[2:].strip()
        if stripped:
            items.append(stripped)
    return items


def _output_tail(text: str, *, max_lines: int = 20) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])
