import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable

from goodteam_os.state import StateStore
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.web import decide_approval, start_workflow


CommandRunner = Callable[[str, Path], dict[str, object]]


@dataclass
class SmokeProject:
    suite: str
    project_key: str
    name: str
    idea: str
    delivery_mode: str
    project_config: Path
    target_root: Path
    verification_commands: list[dict[str, str]]
    cleanup_policy: str


def build_smoke_project_registry(workspace: Path, repo_root: Path) -> dict[str, SmokeProject]:
    python = repo_root / ".venv" / "bin" / "python"
    python_command = shlex.quote(str(python if python.is_file() else Path(sys.executable)))
    contentops_root = Path(
        str(
            _env_or_default(
                "GOODTEAM_CONTENTOPS_ROOT",
                _default_contentops_root(workspace),
            )
        )
    )
    contentops_commands = _contentops_verification_commands(contentops_root, python_command)
    self_root = _default_self_worktree_root(workspace, repo_root)
    return {
        "contentops": SmokeProject(
            suite="contentops",
            project_key="CONTENTOPS",
            name="AI Content Studio",
            idea="Build and verify the CONTENTOPS AI content studio smoke project.",
            delivery_mode="init_project",
            project_config=workspace / "projects" / "contentops.local.yaml",
            target_root=contentops_root,
            verification_commands=contentops_commands,
            cleanup_policy="keep",
        ),
        "self": SmokeProject(
            suite="self",
            project_key="GOODTEAM-SELF",
            name="GoodTeam Self Smoke",
            idea="Use GoodTeam to improve GoodTeam itself in an isolated worktree.",
            delivery_mode="init_project",
            project_config=workspace / "projects" / "goodteam-self.local.yaml",
            target_root=self_root,
            verification_commands=[
                {"name": "pytest", "command": f"PYTHONPATH=src {python_command} -m pytest tests -q", "cwd": str(self_root)},
                {"name": "cli-dry-run", "command": f"PYTHONPATH=src {python_command} -m goodteam_os.cli start 'Smoke GoodTeam self' --dry-run", "cwd": str(self_root)},
            ],
            cleanup_policy="temporary_worktree",
        ),
    }


def run_smoke_suite(
    *,
    workspace: Path,
    suites: list[str],
    cycles: int,
    autonomy: str,
    dry_run: bool = False,
    worker_mode: str = "fake",
    repo_root: Path | None = None,
    registry: dict[str, SmokeProject] | None = None,
    command_runner: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    repo = (repo_root or Path.cwd()).resolve()
    projects = registry or build_smoke_project_registry(workspace, repo)
    selected = [_project_or_fail(projects, suite) for suite in suites]
    report: dict[str, object] = {
        "status": "planned" if dry_run else "passed",
        "suite": suites,
        "cycles": cycles,
        "autonomy": autonomy,
        "worker_mode": worker_mode,
        "runs": [],
    }
    runner = command_runner or run_shell_command

    for cycle in range(1, cycles + 1):
        for project in selected:
            if dry_run:
                report["runs"].append(_planned_run(project, cycle))
                continue
            run = _execute_project_cycle(
                workspace=workspace,
                project=project,
                cycle=cycle,
                worker_mode=worker_mode,
                repo_root=repo,
                command_runner=runner,
            )
            report["runs"].append(run)
            if run["status"] != "passed":
                report["status"] = "failed"
                report_path = prepare_smoke_report_for_write(workspace, report)
                write_smoke_report(workspace, report)
                _attach_report_path_to_smoke_tasks(report["runs"], report_path, projects)
                return report

    if not dry_run:
        report_path = prepare_smoke_report_for_write(workspace, report)
        write_smoke_report(workspace, report)
        _attach_report_path_to_smoke_tasks(report["runs"], report_path, projects)
    return report


def _execute_project_cycle(
    *,
    workspace: Path,
    project: SmokeProject,
    cycle: int,
    worker_mode: str,
    repo_root: Path,
    command_runner: Callable[..., dict[str, object]],
) -> dict[str, object]:
    _prepare_project(project, worker_mode=worker_mode, repo_root=repo_root)
    run = {
        "suite": project.suite,
        "project_key": project.project_key,
        "cycle": cycle,
        "status": "passed",
        "classification": "",
        "commands": [],
        "artifacts": {},
        "evidence": {"smoke_report": "", "worker_result_paths": []},
        "worker_job_ids": [],
        "next_task": "",
    }
    smoke_task_id = _ensure_smoke_task(project, cycle)
    run["task_id"] = smoke_task_id
    try:
        start_workflow(
            workspace=workspace,
            idea=project.idea,
            project_key=project.project_key,
            model_config=repo_root / "models.yaml",
            model_profile="",
            delivery_mode=project.delivery_mode,
            source_type="existing_project",
            project_config=project.project_config,
        )
        _approve_until_complete(workspace, project.project_key)
        state = StateStore(workspace).read_project_current(project.project_key)
        if state is None:
            raise RuntimeError("GoodTeam workflow did not write project state")
        if state.blockers or state.next_node:
            raise RuntimeError("; ".join(state.blockers) or f"workflow stopped at {state.next_node}")
        run["run_id"] = state.run_id
        run["artifacts"] = dict(state.artifacts)
        run["worker_job_ids"] = _worker_job_ids(project.target_root, run_id=state.run_id)
        run["evidence"]["worker_result_paths"] = _worker_result_paths(project.target_root, list(run["worker_job_ids"]))
        run["commands"] = [_run_verification(command, command_runner) for command in project.verification_commands]
        failing_command = next((command for command in run["commands"] if int(command.get("exit_code", 0)) != 0), None)
        if failing_command:
            raise RuntimeError(str(failing_command.get("stderr") or failing_command.get("stdout") or failing_command.get("command")))
        _mark_smoke_task_done(project, smoke_task_id, run)
        return run
    except Exception as exc:
        message = str(exc)
        run["status"] = "failed"
        run["classification"] = classify_smoke_failure(message)
        run["next_task"] = _record_failure_task(project, cycle, message, run["classification"], smoke_task_id=smoke_task_id)
        return run


def _approve_until_complete(workspace: Path, project_key: str) -> None:
    for _ in range(8):
        state = StateStore(workspace).read_project_current(project_key)
        if state is None or not state.next_node:
            return
        if state.blockers:
            raise RuntimeError("; ".join(state.blockers))
        if not state.next_node.startswith("await_") or not state.next_node.endswith("_approval"):
            raise RuntimeError(f"GoodTeam workflow is not at an actionable approval gate: {state.next_node}")
        gate = state.next_node.removeprefix("await_").removesuffix("_approval")
        decide_approval(workspace=workspace, project_key=project_key, approval=gate, decision="approved")
    raise RuntimeError("GoodTeam workflow exceeded smoke auto-approval limit")


def classify_smoke_failure(message: str) -> str:
    normalized = message.lower()
    if "outside the current repo" in normalized or "worktree" in normalized:
        return "env"
    if "invalid_api_key" in normalized or "invalid api key" in normalized or "401 unauthorized" in normalized:
        return "env"
    if "timeout" in normalized or "timed out" in normalized:
        return "env"
    if "npm" in normalized or "build failed" in normalized or "scaffold" in normalized or "node_module_version" in normalized:
        return "test"
    if "qa" in normalized or "browser" in normalized or "ui" in normalized:
        return "test"
    if "next_node" in normalized or "blocked" in normalized or "approval gate" in normalized or "workflow" in normalized:
        return "product"
    return "product"


def write_smoke_report(workspace: Path, report: dict[str, object]) -> Path:
    root = workspace / "state" / "smoke-runs"
    root.mkdir(parents=True, exist_ok=True)
    latest = root / "latest.json"
    body = {**report, "updated_at": datetime.now(UTC).isoformat()}
    latest.write_text(json.dumps(body, indent=2), encoding="utf-8")
    snapshot = root / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    snapshot.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return latest


def prepare_smoke_report_for_write(workspace: Path, report: dict[str, object]) -> Path:
    report_path = workspace / "state" / "smoke-runs" / "latest.json"
    report["report_path"] = str(report_path)
    for raw in list(report.get("runs", [])):
        if not isinstance(raw, dict):
            continue
        evidence = dict(raw.get("evidence", {}) or {})
        evidence["smoke_report"] = str(report_path)
        raw["evidence"] = evidence
    return report_path


def ensure_self_worktree_target_is_safe(repo_root: Path, target: Path) -> None:
    repo = repo_root.resolve()
    resolved = target.resolve()
    if resolved == repo or repo in resolved.parents:
        raise ValueError("GoodTeam self smoke worktree must be outside the current repo.")


def ensure_self_worktree(repo_root: Path, target: Path) -> None:
    ensure_self_worktree_target_is_safe(repo_root, target)
    if target.is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(target), "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )


def run_shell_command(command: str, cwd: Path) -> dict[str, object]:
    shell = os.getenv("SHELL") or "/bin/sh"
    completed = subprocess.run([shell, "-lc", command], cwd=cwd, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _run_verification(command: dict[str, str], runner: Callable[..., dict[str, object]]) -> dict[str, object]:
    return runner(command["command"], cwd=Path(command.get("cwd", ".")))


def _prepare_project(project: SmokeProject, *, worker_mode: str, repo_root: Path) -> None:
    if project.suite == "self":
        ensure_self_worktree(repo_root, project.target_root)
    for repo in ["docs", "backend", "frontend"]:
        (project.target_root / repo).mkdir(parents=True, exist_ok=True)
    readme = project.target_root / "README.md"
    if not readme.is_file():
        readme.write_text(f"# {project.name}\n\nPrepared for GoodTeam smoke runs.\n", encoding="utf-8")
    docs_memory = project.target_root / "docs" / "codex.md"
    if not docs_memory.is_file():
        docs_memory.write_text(f"# {project.name} Memory\n\n- Smoke project for {project.project_key}.\n", encoding="utf-8")
    project.project_config.parent.mkdir(parents=True, exist_ok=True)
    project.project_config.write_text(
        "\n".join(
            [
                f"name: {project.name}",
                f"key: {project.project_key}",
                "model_profile: balanced",
                f"delivery_mode: {project.delivery_mode}",
                "repos:",
                f"  docs: {project.target_root / 'docs'}",
                f"  backend: {project.target_root / 'backend'}",
                f"  frontend: {project.target_root / 'frontend'}",
                "worker_backend:",
                f"  mode: {worker_mode}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _ensure_smoke_task(project: SmokeProject, cycle: int) -> str:
    store = LocalTaskStore(project.target_root)
    title = f"Run {project.project_key} smoke cycle"
    existing = next(
        (
            task
            for task in store.list_tasks()
            if task.get("title") == title
            and f"cycle:{cycle}" in list(task.get("external_refs", []))
            and f"suite:{project.suite}" in list(task.get("external_refs", []))
        ),
        None,
    )
    if existing:
        updated = store.update_task(
            str(existing["id"]),
            status="in_progress",
            acceptance_criteria=_smoke_task_acceptance_criteria(),
        )
        return str(updated["id"])
    task = store.create_task(
        title=title,
        description=f"Run smoke suite {project.suite} cycle {cycle} and keep machine-readable evidence.",
        role="qa",
        priority="high",
        acceptance_criteria=_smoke_task_acceptance_criteria(),
    )
    return str(store.update_task(task["id"], status="in_progress", external_refs=[f"suite:{project.suite}", f"cycle:{cycle}"])["id"])


def _smoke_task_acceptance_criteria() -> list[str]:
    return [
        "Smoke report latest.json exists.",
        "Verification commands and exit codes are recorded.",
        "Worker job ids or protocol evidence are recorded.",
    ]


def _attach_report_path_to_smoke_tasks(runs: list[object], report_path: Path, projects: dict[str, SmokeProject]) -> None:
    for raw in runs:
        run = dict(raw)
        evidence = dict(run.get("evidence", {}) or {})
        evidence["smoke_report"] = str(report_path)
        run["evidence"] = evidence
        if isinstance(raw, dict):
            raw["evidence"] = evidence
        task_id = str(run.get("task_id", ""))
        suite = str(run.get("suite", ""))
        if not task_id or suite not in projects:
            continue
        project = projects[suite]
        store = LocalTaskStore(project.target_root)
        try:
            task = store.get_task(task_id)
        except ValueError:
            continue
        artifact_refs = list(task.get("artifact_refs", []))
        for artifact_ref in _smoke_task_artifact_refs(run):
            if artifact_ref not in artifact_refs:
                artifact_refs.append(artifact_ref)
        if str(report_path) not in artifact_refs:
            artifact_refs.append(str(report_path))
        if artifact_refs != list(task.get("artifact_refs", [])):
            store.update_task(task_id, artifact_refs=artifact_refs)


def _mark_smoke_task_done(project: SmokeProject, task_id: str, run: dict[str, object]) -> None:
    artifact_refs = _smoke_task_artifact_refs(run)
    LocalTaskStore(project.target_root).update_task(task_id, status="done", artifact_refs=artifact_refs)


def _smoke_task_artifact_refs(run: dict[str, object]) -> list[str]:
    refs: list[str] = []
    for path in dict(run.get("artifacts", {})).values():
        if str(path):
            refs.append(str(path))
    evidence = dict(run.get("evidence", {}) or {})
    if evidence.get("smoke_report"):
        refs.append(str(evidence["smoke_report"]))
    for path in list(evidence.get("worker_result_paths", [])):
        if str(path):
            refs.append(str(path))
    if run.get("report_path"):
        refs.append(str(run["report_path"]))
    return list(dict.fromkeys(refs))


def _record_failure_task(project: SmokeProject, cycle: int, message: str, classification: str, *, smoke_task_id: str) -> str:
    store = LocalTaskStore(project.target_root)
    store.update_task(
        smoke_task_id,
        status="blocked",
        priority="high",
        acceptance_criteria=[
            "Smoke suite reruns without this failure.",
            "Failure is covered by a regression test.",
        ],
    )
    title = f"Smoke failure: {project.project_key} cycle {cycle}"
    description = f"Classification: {classification}\n\nFailure: {message}"
    external_refs = [f"suite:{project.suite}", f"cycle:{cycle}", "type:smoke-failure"]
    acceptance_criteria = ["Smoke suite reruns without this failure.", "Failure is covered by a regression test."]
    existing = next(
        (
            task
            for task in store.list_tasks()
            if task.get("title") == title
            and all(ref in list(task.get("external_refs", [])) for ref in external_refs)
        ),
        None,
    )
    if existing:
        updated = store.update_task(
            str(existing["id"]),
            description=description,
            status="todo",
            priority="high",
            acceptance_criteria=acceptance_criteria,
        )
        return str(updated["id"])
    task = store.create_task(
        title=title,
        description=description,
        role="qa",
        priority="high",
        acceptance_criteria=acceptance_criteria,
    )
    return str(store.update_task(str(task["id"]), external_refs=external_refs)["id"])


def _worker_job_ids(target_root: Path, *, run_id: str = "") -> list[str]:
    jobs_root = target_root / ".goodteam" / "workers" / "jobs"
    if not jobs_root.is_dir():
        return []
    job_ids = []
    for path in jobs_root.iterdir():
        if not path.is_dir():
            continue
        if run_id and _worker_job_run_id(path) != run_id:
            continue
        job_ids.append(path.name)
    return sorted(job_ids)


def _worker_job_run_id(job_root: Path) -> str:
    path = job_root / "job.json"
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("run_id", ""))
    except json.JSONDecodeError:
        return ""


def _worker_result_paths(target_root: Path, job_ids: list[str]) -> list[str]:
    jobs_root = target_root / ".goodteam" / "workers" / "jobs"
    paths = []
    for job_id in job_ids:
        result = jobs_root / job_id / "result.md"
        if result.is_file():
            paths.append(str(result))
    return paths


def _planned_run(project: SmokeProject, cycle: int) -> dict[str, object]:
    return {
        "suite": project.suite,
        "project_key": project.project_key,
        "cycle": cycle,
        "status": "planned",
        "classification": "",
        "commands": project.verification_commands,
        "artifacts": {},
        "evidence": {"smoke_report": "", "worker_result_paths": []},
        "worker_job_ids": [],
        "next_task": "",
    }


def _project_or_fail(registry: dict[str, SmokeProject], suite: str) -> SmokeProject:
    key = suite.strip()
    if key not in registry:
        raise ValueError(f"Unknown smoke suite: {key}")
    return registry[key]


def _env_or_default(name: str, default: Path) -> str:
    return os.getenv(name, str(default))


def _default_contentops_root(workspace: Path) -> Path:
    return workspace / "smoke-targets" / "contentops"


def _default_self_worktree_root(workspace: Path, repo_root: Path) -> Path:
    explicit = os.getenv("GOODTEAM_SELF_WORKTREE_ROOT")
    if explicit:
        return Path(explicit)
    candidate = workspace / "smoke-worktrees" / "goodteam-self"
    if _is_inside(candidate, repo_root):
        digest = sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / "goodteam-smoke-worktrees" / digest / "goodteam-self"
    return candidate


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _contentops_verification_commands(contentops_root: Path, python_command: str) -> list[dict[str, str]]:
    if (contentops_root / "package.json").is_file():
        return [
            {"name": "contentops-test", "command": "npm run test", "cwd": str(contentops_root)},
            {"name": "contentops-build", "command": "npm run build", "cwd": str(contentops_root)},
        ]
    return [
        {
            "name": "contentops-readme",
            "command": f"{python_command} -c \"from pathlib import Path; assert Path('README.md').is_file()\"",
            "cwd": str(contentops_root),
        }
    ]
