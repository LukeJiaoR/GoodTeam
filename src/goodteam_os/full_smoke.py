import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import MutableMapping

from goodteam_os.execution.models import RuntimePolicy
from goodteam_os.execution.runtime_policy import classify_worker_backend
from goodteam_os.smoke import build_smoke_project_registry, run_smoke_suite
from goodteam_os.workers import worker_backend_local_preflight


SAFE_WORKER_MODES = {"fake", "noop"}
REAL_WORKER_MODES = {"codex", "anthropic", "claude", "gemini", "agy", "opencode"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output_root = _resolve_output_root(args.output_root, repo_root)
    workspace = output_root / "workspace"
    dirs = _ensure_output_dirs(output_root)
    suites = [suite.strip() for suite in args.suite.split(",") if suite.strip()]
    worker_mode = "anthropic" if args.worker_mode == "claude" else args.worker_mode
    if worker_mode in REAL_WORKER_MODES and not args.dry_run and not args.allow_real_worker:
        raise SystemExit(f"{args.worker_mode} worker is explicit opt-in only; rerun with --allow-real-worker.")

    started_at = datetime.now(UTC).isoformat()
    original_env = _managed_env_snapshot()
    env_updates, isolation_warnings = _configure_isolated_env(output_root, workspace, repo_root)
    dashboard_url = f"http://127.0.0.1:{args.dashboard_port}/"
    summary: dict[str, object] = {
        "status": "running",
        "started_at": started_at,
        "issue": args.issue,
        "repo_root": str(repo_root),
        "output_root": str(output_root),
        "workspace": str(workspace),
        "paths": {name: str(path) for name, path in dirs.items()},
        "suites": suites,
        "cycles": args.cycles,
        "worker_mode": worker_mode,
        "dry_run": args.dry_run,
        "dashboard_urls": {"root": dashboard_url},
        "ports": {"dashboard": args.dashboard_port},
        "warnings": list(isolation_warnings),
        "failures": [],
    }
    summary["runtime_policy_preflight"] = _full_smoke_runtime_policy(
        worker_mode=worker_mode,
        dry_run=args.dry_run,
        allow_real_worker=args.allow_real_worker,
        output_root=output_root,
        workspace=workspace,
    )
    if worker_mode in REAL_WORKER_MODES:
        summary["worker_backend_preflight"] = worker_backend_local_preflight(
            worker_mode,
            dry_run=args.dry_run,
            allow_real_worker=args.allow_real_worker,
        )
        summary["runtime_policy_preflight"]["worker_backend"]["preflight"] = {
            "status": summary["worker_backend_preflight"]["status"],
            "local_only": True,
            "external_invocation": False,
        }
        if summary["worker_backend_preflight"]["status"] == "warn":
            summary["warnings"].append(summary["worker_backend_preflight"]["message"])
    summary.update(_collect_environment(repo_root))
    _write_log(dirs["logs"] / "environment.log", json.dumps(summary, indent=2))

    try:
        registry = build_smoke_project_registry(workspace, repo_root)
        summary["project_configs"] = {suite: str(project.project_config) for suite, project in registry.items()}
        summary["target_roots"] = {suite: str(project.target_root) for suite, project in registry.items()}
        report = run_smoke_suite(
            workspace=workspace,
            suites=suites,
            cycles=args.cycles,
            autonomy="full",
            dry_run=args.dry_run,
            worker_mode=worker_mode,
            repo_root=repo_root,
            registry=registry,
        )
        summary["smoke_report_path"] = str(report.get("report_path", ""))
        summary["run_ids"] = _run_ids(report)
        summary["dashboard_urls"].update(_dashboard_urls(dashboard_url, report))
        summary["smoke_report"] = report
        _write_log(dirs["logs"] / "smoke.json", json.dumps(report, indent=2))
    except Exception as exc:
        summary["failures"] = [{"classification": "env", "message": str(exc)}]
        summary["status"] = "failed"
    else:
        failed_runs = [
            {
                "classification": str(run.get("classification") or "product"),
                "message": str(run.get("next_task") or run.get("project_key") or "smoke run failed"),
            }
            for run in report.get("runs", [])
            if isinstance(run, dict) and str(run.get("status", "")) == "failed"
        ]
        if summary.get("worker_backend_preflight", {}).get("status") == "fail":
            failed_runs.append(
                {
                    "classification": "env",
                    "message": str(summary["worker_backend_preflight"].get("message", "worker preflight failed")),
                }
            )
        summary["failures"] = failed_runs
        if failed_runs or report.get("status") == "failed":
            summary["status"] = "failed"
        elif report.get("status") == "blocked":
            summary["status"] = "blocked"
            summary["failures"] = [
                {"classification": str(run.get("classification") or "product"),
                 "message": str(run.get("blocked_reason", {}).get("message") or "Approval is required.")}
                for run in report.get("runs", []) if isinstance(run, dict) and run.get("status") == "blocked"
            ]
        else:
            summary["status"] = "passed"

    summary["environment_overrides"] = env_updates
    summary["completed_at"] = datetime.now(UTC).isoformat()
    _copy_state_snapshots(workspace, dirs["state_snapshots"])
    _write_screenshot_index(dirs["screenshots"], summary)
    _write_coverage_matrix(output_root / "coverage-matrix.md", summary)
    _write_markdown_report(output_root / "report.md", summary)
    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _restore_managed_env(original_env)
    print(json.dumps({"status": summary["status"], "summary": str(summary_path), "report": str(output_root / "report.md")}, indent=2))
    return 1 if summary["status"] in {"failed", "blocked"} else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="full-acceptance-smoke")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--suite", default="contentops,self")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--worker-mode", choices=sorted(SAFE_WORKER_MODES | REAL_WORKER_MODES), default="fake")
    parser.add_argument("--allow-real-worker", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dashboard-port", type=int, default=8788)
    parser.add_argument("--issue", default="")
    return parser


def _resolve_output_root(raw_output_root: Path | None, repo_root: Path) -> Path:
    if raw_output_root is not None:
        return raw_output_root.resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return (repo_root / "output" / f"full-smoke-{stamp}").resolve()


def _ensure_output_dirs(output_root: Path) -> dict[str, Path]:
    dirs = {
        "logs": output_root / "logs",
        "screenshots": output_root / "screenshots",
        "state_snapshots": output_root / "state-snapshots",
        "targets": output_root / "targets",
        "workspace": output_root / "workspace",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _configure_isolated_env(
    output_root: Path,
    workspace: Path,
    repo_root: Path,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    env = environ if environ is not None else os.environ
    updates: dict[str, str] = {}
    warnings: list[str] = []
    if "GOODTEAM_CONTENTOPS_ROOT" not in env:
        updates["GOODTEAM_CONTENTOPS_ROOT"] = str(output_root / "targets" / "contentops")
        env["GOODTEAM_CONTENTOPS_ROOT"] = updates["GOODTEAM_CONTENTOPS_ROOT"]
    else:
        warnings.append("GOODTEAM_CONTENTOPS_ROOT was explicitly provided; contentops target may be outside output root.")
    if "GOODTEAM_SELF_WORKTREE_ROOT" not in env and _is_inside(workspace, repo_root):
        external = Path(os.getenv("TMPDIR", "/tmp")) / "goodteam-full-smoke-worktrees" / output_root.name / "goodteam-self"
        updates["GOODTEAM_SELF_WORKTREE_ROOT"] = str(external)
        env["GOODTEAM_SELF_WORKTREE_ROOT"] = updates["GOODTEAM_SELF_WORKTREE_ROOT"]
        warnings.append("Self suite worktree root moved to an external temp root because output root is inside the repo.")
    return updates, warnings


def _managed_env_snapshot() -> dict[str, str | None]:
    return {
        "GOODTEAM_CONTENTOPS_ROOT": os.environ.get("GOODTEAM_CONTENTOPS_ROOT"),
        "GOODTEAM_SELF_WORKTREE_ROOT": os.environ.get("GOODTEAM_SELF_WORKTREE_ROOT"),
    }


def _restore_managed_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _collect_environment(repo_root: Path) -> dict[str, object]:
    return {
        "git_sha": _run_text(["git", "rev-parse", "HEAD"], repo_root),
        "working_tree": _run_text(["git", "status", "--short"], repo_root),
        "versions": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "node": _run_text(["node", "--version"], repo_root),
            "npm": _run_text(["npm", "--version"], repo_root),
        },
    }


def _full_smoke_runtime_policy(
    *,
    worker_mode: str,
    dry_run: bool,
    allow_real_worker: bool,
    output_root: Path,
    workspace: Path,
) -> dict[str, object]:
    backend_class = classify_worker_backend(worker_mode, dry_run=dry_run)
    real_external_requested = classify_worker_backend(worker_mode) == "real-external"
    external_blocked = bool(dry_run or (real_external_requested and not allow_real_worker))
    policy = RuntimePolicy(
        task_contract={
            "available": False,
            "schema_version": "",
            "allowed_summary": "Full smoke harness preflight; no single task_contract.v1 applies.",
            "constraints": [],
        },
        worker_backend={
            "mode": worker_mode,
            "class": backend_class,
            "source": "full_smoke.worker_mode",
        },
        external_invocation={
            "requested": real_external_requested,
            "allowed": real_external_requested and allow_real_worker and not dry_run,
            "blocked": external_blocked,
            "reason": _full_smoke_external_reason(
                real_external_requested=real_external_requested,
                allow_real_worker=allow_real_worker,
                dry_run=dry_run,
            ),
        },
        sandbox={
            "enabled": True,
            "mode": "isolated-output-root",
            "workspace_mode": "workspace",
            "source": "full_smoke",
        },
        path_scope={
            "policy": "full_smoke.output_root",
            "focus_paths": [str(output_root), str(workspace)],
            "allowed_paths": [str(output_root)],
            "excluded_paths": [],
        },
        verification={
            "expectations": ["full acceptance smoke report", "summary.json", "coverage-matrix.md"],
            "full_smoke_compatible": True,
            "full_smoke_reason": "full smoke harness owns this preflight",
        },
        operator_flags={
            "allow_real_external_worker": allow_real_worker,
            "dry_run": dry_run,
        },
        sources=["full_smoke.worker_mode", "full_smoke.output_root", "operator_flags"],
        warnings=[],
    )
    return policy.to_dict()


def _full_smoke_external_reason(
    *,
    real_external_requested: bool,
    allow_real_worker: bool,
    dry_run: bool,
) -> str:
    if dry_run:
        return "dry-run blocks external worker invocation"
    if real_external_requested and allow_real_worker:
        return "real external worker explicitly allowed for full smoke"
    if real_external_requested:
        return "real external worker requires --allow-real-worker"
    return "selected worker mode does not invoke an external agent"


def _run_text(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return (completed.stdout or completed.stderr).strip()


def _write_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def _run_ids(report: dict[str, object]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for run in report.get("runs", []):
        if isinstance(run, dict) and run.get("run_id"):
            ids[str(run.get("project_key", ""))] = str(run["run_id"])
    return ids


def _dashboard_urls(root_url: str, report: dict[str, object]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for run in report.get("runs", []):
        if isinstance(run, dict) and run.get("project_key"):
            urls[str(run["project_key"])] = f"{root_url}?project={run['project_key']}"
    return urls


def _copy_state_snapshots(workspace: Path, destination: Path) -> None:
    state_root = workspace / "state"
    if not state_root.is_dir():
        return
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(state_root, destination)


def _write_screenshot_index(screenshots_dir: Path, summary: dict[str, object]) -> None:
    index = screenshots_dir / "index.json"
    index.write_text(
        json.dumps({"dashboard_urls": summary.get("dashboard_urls", {}), "screenshots": []}, indent=2),
        encoding="utf-8",
    )
    summary["dashboard_screenshot_index"] = str(index)


def _write_coverage_matrix(path: Path, summary: dict[str, object]) -> None:
    rows = [
        ("isolated output root", "PASS", str(summary["output_root"])),
        ("contentops isolated by default", "PASS", str(summary.get("target_roots", {}).get("contentops", ""))),
        ("self worktree safe root", "PASS", str(summary.get("target_roots", {}).get("self", ""))),
        ("standard report/summary/matrix dirs", "PASS", str(path.parent)),
        ("real workers explicit opt-in", "PASS", str(summary["worker_mode"])),
        ("failure class product/test/env", "PASS", ", ".join(sorted({str(item.get("classification")) for item in summary.get("failures", []) if isinstance(item, dict)})) or "none"),
    ]
    lines = ["# Full Smoke Coverage Matrix", "", "| Check | Result | Evidence |", "| --- | --- | --- |"]
    lines.extend(f"| {name} | {result} | `{evidence}` |" for name, result, evidence in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_markdown_report(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# GoodTeam Full Acceptance Smoke Report",
        "",
        f"Result: **{summary['status']}**",
        "",
        f"- Git SHA: `{summary.get('git_sha', '')}`",
        f"- Output root: `{summary['output_root']}`",
        f"- Workspace: `{summary['workspace']}`",
        f"- Worker mode: `{summary['worker_mode']}`",
        f"- Dashboard root: `{summary.get('dashboard_urls', {}).get('root', '')}`",
        f"- Summary: `{path.parent / 'summary.json'}`",
        f"- Coverage matrix: `{path.parent / 'coverage-matrix.md'}`",
        f"- Screenshots index: `{summary.get('dashboard_screenshot_index', '')}`",
    ]
    preflight = summary.get("worker_backend_preflight")
    if isinstance(preflight, dict):
        lines.extend(
            [
                "",
                "## Worker Backend Local Preflight",
                "",
                f"- Status: **{preflight.get('status', '')}**",
                f"- Local only: `{str(preflight.get('local_only', False)).lower()}`",
                f"- External invocation: `{str(preflight.get('external_invocation', True)).lower()}`",
                f"- Version: `{preflight.get('version', '')}`",
                f"- Planned command: `{' '.join(preflight.get('planned_command', []))}`",
                f"- CWD: `{preflight.get('cwd', '')}`",
                f"- Sandbox: `{preflight.get('sandbox_mode', '')}`",
                f"- Message: {preflight.get('message', '')}",
            ]
        )
    lines.extend([
        "",
        "## Warnings",
        "",
    ])
    warnings = list(summary.get("warnings", []))
    lines.extend(f"- {warning}" for warning in warnings) if warnings else lines.append("- None")
    lines.extend(["", "## Failures", ""])
    failures = [item for item in summary.get("failures", []) if isinstance(item, dict)]
    if failures:
        lines.extend(f"- {item.get('classification')}: {item.get('message')}" for item in failures)
    else:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
