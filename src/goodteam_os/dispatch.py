from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from goodteam_os.models import _parse_simple_yaml
from goodteam_os.workers import WorkerJobStore

if TYPE_CHECKING:
    from goodteam_os.skills.codex_runtime import CodexReviewAdmission


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
    logical_task_id: str = "",
    codex_review_admission: CodexReviewAdmission | None = None,
) -> dict[str, object]:
    safety_kwargs: dict[str, object] = {"allow_unsafe": allow_unsafe}
    if projects_dir is not None:
        safety_kwargs["projects_dir"] = projects_dir
    safety = ensure_dispatch_project_root_is_safe(project_root, **safety_kwargs)
    if not safety["safe"]:
        raise ValueError(str(safety["reason"]))
    # Validate trusted composition before recording a task. Neither track JSON
    # nor a Skill may supply admission, and each execution thread retains it.
    store = WorkerJobStore(project_root, **_review_runtime_kwargs(codex_review_admission))
    from goodteam_os.skills.codex_local import LocalCodexReviewAdmission
    from goodteam_os.skills.codex_trusted import TrustedCodexReviewAdmission

    local_review = isinstance(codex_review_admission, (LocalCodexReviewAdmission, TrustedCodexReviewAdmission))
    if local_review and (len(tracks) != 1 or tracks[0].get("role") != "code_review"
                         or tracks[0].get("adapter") != "codex" or not tracks[0].get("review_target")):
        raise ValueError("Local exposure authorization requires one exact-source Codex Reviewer track.")
    if any(track.get("review_target") is not None for track in tracks) and verify_commands:
        raise ValueError("Exact-source review cannot run post-review shell verification commands.")
    if codex_review_admission is not None and any(
        track.get("role") == "code_review" and track.get("adapter") == "codex" and not track.get("model")
        for track in tracks
    ):
        raise ValueError("An admitted Codex Reviewer requires an explicit model before task creation.")
    constraints = _constraints_from_file(constraints_file)
    from goodteam_os.skills.models import content_digest
    from goodteam_os.skills.task_locks import TaskSkillIdentity
    from goodteam_os.skills.task_start import capture_task_start, registered_project_scope

    logical_id = logical_task_id or str(uuid4())
    run_id = "dispatch-" + content_digest(logical_id.encode("utf-8"))[7:]
    scope = registered_project_scope(project_root)
    context = {}
    key = ""
    if scope is not None:
        workspace, key = scope
        context = capture_task_start(
            workspace=workspace, project_key=key, worker_root=project_root,
            task=TaskSkillIdentity("direct_task", key, logical_id), command_key=f"direct-start:{key}:{logical_id}",
            confirmed_input={"tracks": tracks, "constraints": constraints, "verify_commands": verify_commands or {},
                             "constraints_source": str(constraints_file) if constraints_file else "",
                             "research_report_source": str(research_report_file) if research_report_file else ""},
            **({"subjects": ("role:code_review",), "protocol_only_review": True} if local_review else {}),
        )
    if local_review and not context:
        raise ValueError("Local execution requires a registered project and its captured task protocol.")
    jobs_by_role = [
        _create_track_job(
            store,
            run_id=run_id,
            track=track,
            constraints=constraints,
            constraints_source=str(constraints_file) if constraints_file else "",
            research_report_source=str(research_report_file) if research_report_file else "",
            method_context=context, project_key=key,
            command_key=f"direct-job:{key}:{logical_id}:{index}",
        )
        for index, track in enumerate(tracks)
    ]
    results_by_role: dict[str, dict[str, object]] = {}
    max_workers = max(1, len(jobs_by_role))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run_dispatched_job, project_root, job, dict(verify_commands or {}),
                            **_review_runtime_kwargs(codex_review_admission)): str(job["role"])
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
    reviews = [job for job in jobs if job.get("outcome") == "review_received"]
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
        "all_ok": not needs_human and not file_collisions,
        "has_collisions": bool(file_collisions),
        "file_collisions": file_collisions,
        "jobs": jobs,
        "ok": ok,
        "reviews": reviews,
        "needs_human": needs_human,
        "run_id": run_id,
        "project_root": str(project_root),
    }


def _review_runtime_kwargs(admission):
    # Keep default/legacy callers unchanged; this is only server-owned wiring,
    # not a provider factory or permission inferred from CLI/track configuration.
    return {} if admission is None else {"codex_review_admission": admission}


def _create_track_job(
    store: WorkerJobStore,
    *,
    run_id: str,
    track: dict[str, object],
    constraints: list[str],
    constraints_source: str = "",
    research_report_source: str = "",
    method_context: dict[str, Any] | None = None,
    project_key: str = "",
    command_key: str = "",
) -> dict[str, Any]:
    from goodteam_os.skills.worker_requests import WORKER_METADATA_LOADER, subject_for_role

    fields = {}
    if method_context:
        fields = {"task_skill_context": method_context, "skill_subject": subject_for_role(str(track["role"])),
                  "skill_loader_version": WORKER_METADATA_LOADER, "project_key": project_key,
                  "idempotency_key": command_key}
    from goodteam_os.skills.review_protocol import review_execution_fields

    fields.update(review_execution_fields(store, method_context, role=str(track["role"])))
    if "review_target" in track:
        fields["review_target"] = track["review_target"]
    job, _created = store.create_or_reuse_job(
        **fields,
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
    return job


def _run_dispatched_job(
    project_root: Path, job: dict[str, Any], verify_commands: dict[str, str], *,
    codex_review_admission: CodexReviewAdmission | None = None,
) -> dict[str, object]:
    store = WorkerJobStore(project_root, **_review_runtime_kwargs(codex_review_admission))
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

    if status.get("status") == "review_received":
        from goodteam_os.skills.models import SkillError
        from goodteam_os.skills.review_results import ReviewResultStore

        try:
            if job.get("role") != "code_review" or not job.get("task_skill_context"):
                raise SkillError("review_identity_invalid", "A received review needs its original task identity.")
            detail = ReviewResultStore(store, str(job["id"])).detail(
                expected_task_context=job["task_skill_context"], include_report=True)
            if (detail["snapshot_ref"] != job.get("skill_execution_snapshot_ref")
                    or detail["status"] != "received" or not detail["report_available"]):
                raise SkillError("review_result_unavailable", "No matching received report exists.")
        except (SkillError, OSError) as exc:
            code = exc.code if isinstance(exc, SkillError) else "review_result_unavailable"
            return _job_report(project_root=project_root, job=job,
                status={**status, "status": "failed", "skill_error_code": code,
                        "summary": "The saved review could not be verified. No replacement report was used."},
                verify={"passed": False, "skipped": True, "reason": code})
        # Received is a successful *delivery*, not approval or a passed check.
        # Return the validated immutable report, not a nonexistent result.md or
        # unrelated dirty-worktree files. Never run a shell after this review.
        return _job_report(project_root=project_root, job=job, status=status,
            verify={"passed": None, "skipped": True, "reason": "advisory_review"}, review=detail)

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
    review: dict[str, Any] | None = None,
) -> dict[str, object]:
    locked_review = job.get("role") == "code_review" and bool(job.get("skill_execution_snapshot_ref"))
    worker_ok = str(status.get("status", "")) == "completed"
    verify_ok = bool(verify.get("passed", False))
    outcome = "review_received" if review is not None else "ok" if worker_ok and verify_ok else "needs_human"
    job_root = project_root / ".goodteam" / "workers" / "jobs" / str(job["id"])
    return {
        **{key: job[key] for key in ("task_skill_context", "skill_execution_snapshot_ref") if key in job},
        **{key: status[key] for key in ("skill_error_code", "skill_preflight_status", "skill_runtime_status", "invocation_evidence") if key in status},
        "job_id": str(job["id"]),
        "role": str(job["role"]),
        "adapter": str(job["adapter"]),
        "status": str(status.get("status", "")),
        "summary": str(status.get("summary", "")),
        "failure_class": str(status.get("failure_class", "")),
        "attempts": int(status.get("attempts", 0) or 0),
        "disposition": str(status.get("disposition", "")),
        "result_path": "" if locked_review else str(job_root / "result.md"),
        "status_path": str(job_root / "status.json"),
        "changed_files": [] if locked_review else changed_files(project_root),
        **({"review": review, "approval_changed": False} if review is not None else {}),
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
