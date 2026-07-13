import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from goodteam_os.dispatch import dispatch_tracks, parse_dispatch_track_spec, parse_dispatch_verify_spec
from goodteam_os.execution import ExecutionAPI
from goodteam_os.failure_patterns import FailurePatternLibrary
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.projects import (
    DELIVERY_MODES,
    WORKER_MODES,
    check_project_target,
    init_project,
    load_project_target,
)
from goodteam_os.smoke import run_smoke_suite
from goodteam_os.service import DEFAULT_PORT, restart_service, service_status, stop_service
from goodteam_os.tasks import LocalTaskStore, build_task_board, serialize_task_board
from goodteam_os.telegram import poll_telegram_approvals
from goodteam_os.web import serve, start_workflow
from goodteam_os.workers import WorkerJobStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("idea")
    start.add_argument("--project-key", default="GTO")
    start.add_argument("--workspace", type=Path, default=Path("."))
    start.add_argument("--model-config", type=Path, default=Path("models.yaml"))
    start.add_argument("--model-profile", default="")
    start.add_argument("--role-model", action="append", default=[])
    start.add_argument("--delivery-mode", choices=list(DELIVERY_MODES), default="")
    start.add_argument("--source-type", choices=["new_project", "existing_project"], default="new_project")
    start.add_argument("--project-config", type=Path)
    start.add_argument("--dry-run", action="store_true")

    serve_command = subparsers.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    serve_command.add_argument("--workspace", type=Path, default=Path("."))
    serve_command.add_argument("--frontend-dist", type=Path, help=argparse.SUPPRESS)
    serve_command.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    serve_command.add_argument("--ready-token", default="", help=argparse.SUPPRESS)

    restart = subparsers.add_parser("restart", help="Restart the background GoodTeam server")
    restart.add_argument("--host", default="127.0.0.1")
    restart.add_argument("--port", type=int, default=DEFAULT_PORT)
    restart.add_argument("--workspace", type=Path, default=Path("."))

    subparsers.add_parser("stop", help="Stop the background GoodTeam server")
    subparsers.add_parser("status", help="Show the background GoodTeam server status")

    init = subparsers.add_parser("init", help="Scaffold a project config and run doctor")
    init.add_argument("--key", required=True, help="Short project key, e.g. MYAPP")
    init.add_argument("--name", default="", help="Human project name (defaults to --key)")
    init.add_argument("--repo", default=".", help="Path to the target repo")
    init.add_argument("--projects-dir", type=Path, default=Path("projects"))
    init.add_argument("--model-profile", default="balanced")
    init.add_argument("--worker-mode", choices=list(WORKER_MODES), default="fake")
    init.add_argument("--delivery-mode", choices=list(DELIVERY_MODES), default="mvp")
    init.add_argument("--force", action="store_true")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project-config", type=Path, required=True)

    board = subparsers.add_parser("board")
    board.add_argument("--project-root", type=Path, required=True)

    workers = subparsers.add_parser("workers")
    workers.add_argument("--project-root", type=Path, required=True)

    failure_patterns = subparsers.add_parser("failure-patterns")
    failure_patterns.add_argument("--project-root", type=Path, required=True)
    failure_patterns.add_argument("--min-hits", type=int, default=3)
    failure_patterns.add_argument("--min-confirmed", type=int, default=3)
    failure_patterns.add_argument("--max-false-positive-rate", type=float, default=0.0)
    failure_patterns.add_argument("--min-llm-triage-samples", type=int, default=5)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--workspace", type=Path, default=Path("."))
    smoke.add_argument("--suite", default="contentops,self")
    smoke.add_argument("--cycles", type=int, default=2)
    smoke.add_argument("--autonomy", choices=["full"], default="full")
    smoke.add_argument("--worker-mode", choices=["fake", "noop", "codex"], default="fake")
    smoke.add_argument("--dry-run", action="store_true")

    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("--project-root", type=Path, required=True)
    dispatch.add_argument("--track", action="append", default=[])
    dispatch.add_argument("--verify", action="append", default=[])
    dispatch.add_argument("--constraints-file", type=Path)
    dispatch.add_argument("--research-report", type=Path)
    dispatch.add_argument("--allow-unsafe", action="store_true")

    memory_append = subparsers.add_parser("memory-append")
    memory_append.add_argument("--project-root", type=Path, required=True)
    memory_append.add_argument("--role", required=True)
    memory_append.add_argument("--horizon", required=True)
    memory_append.add_argument("--text", required=True)
    memory_append.add_argument("--source", default="")
    memory_append.add_argument("--supersedes", default="")

    tg_poll = subparsers.add_parser("tg-poll")
    tg_poll.add_argument("--workspace", type=Path, default=Path("."))
    tg_poll.add_argument("--project-key", default="")
    tg_poll.add_argument("--once", action="store_true")

    execution = subparsers.add_parser("execution")
    execution_subparsers = execution.add_subparsers(dest="execution_command", required=True)

    intake_github_issue = execution_subparsers.add_parser("intake-github-issue")
    intake_github_issue.add_argument("--repo", required=True)
    intake_github_issue.add_argument("--issue", type=int, required=True)
    intake_github_issue.add_argument("--project-root", type=Path, required=True)
    intake_github_issue.add_argument("--adapter", default="fake")
    intake_github_issue.add_argument("--role", default="implementation")

    for command in ("run", "pr", "ci", "replay", "review"):
        execution_command = execution_subparsers.add_parser(command)
        execution_command.add_argument("--execution-id", required=True)
        execution_command.add_argument("--project-root", type=Path, required=True)
    review_decision = execution_subparsers.add_parser("record-review-decision")
    review_decision.add_argument("--execution-id", required=True)
    review_decision.add_argument("--project-root", type=Path, required=True)
    review_decision.add_argument(
        "--decision",
        required=True,
        choices=["approve", "request_changes", "reject"],
    )
    review_decision.add_argument("--reviewer", required=True)
    review_decision.add_argument("--reason", default="")
    export_review_workbench = execution_subparsers.add_parser("export-review-workbench")
    export_review_workbench.add_argument("--execution-id", required=True)
    export_review_workbench.add_argument("--project-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        payload = start_workflow(
            workspace=args.workspace,
            idea=args.idea,
            project_key=args.project_key,
            model_config=args.model_config,
            model_profile=args.model_profile,
            delivery_mode=args.delivery_mode,
            source_type=args.source_type,
            project_config=args.project_config,
            role_model_overrides=args.role_model,
            dry_run=args.dry_run,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "serve":
        serve(
            workspace=args.workspace,
            host=args.host,
            port=args.port,
            frontend_dist=args.frontend_dist,
            ready_file=args.ready_file,
            ready_token=args.ready_token,
        )
        return 0
    if args.command == "restart":
        try:
            payload = restart_service(workspace=args.workspace, host=args.host, port=args.port)
        except RuntimeError as exc:
            print(f"goodteam restart: {exc}", file=sys.stderr)
            return 1
        _print_service_status(payload)
        return 0
    if args.command == "stop":
        payload = stop_service()
        print(f"GoodTeam stopped (pid {payload['pid']})" if payload.get("stopped") else "GoodTeam is not running")
        return 0
    if args.command == "status":
        payload = service_status()
        if not payload.get("running"):
            print("GoodTeam is not running")
            return 1
        _print_service_status(payload)
        return 0
    if args.command == "init":
        result = init_project(
            projects_dir=args.projects_dir,
            key=args.key,
            name=args.name or args.key,
            repo_path=args.repo,
            model_profile=args.model_profile,
            worker_mode=args.worker_mode,
            delivery_mode=args.delivery_mode,
            force=args.force,
        )
        print(json.dumps(result, indent=2))
        print(
            f"\nWrote {result['config_path']}. Next:\n"
            f"  goodteam-os start \"Your idea\" --project-key {args.key.upper()} "
            f"--project-config {result['config_path']} --workspace ./run\n"
            f"  goodteam-os serve --workspace ./run",
            file=sys.stderr,
        )
        return 0
    if args.command == "doctor":
        target = load_project_target(args.project_config)
        print(json.dumps(check_project_target(target), indent=2))
        return 0
    if args.command == "board":
        payload = serialize_task_board(build_task_board(LocalTaskStore(args.project_root).list_tasks()))
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "workers":
        payload = {
            "project_root": str(args.project_root),
            "jobs": WorkerJobStore(args.project_root).list_jobs(),
        }
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "failure-patterns":
        payload = FailurePatternLibrary(args.project_root).routing_readiness_report(
            min_hits=args.min_hits,
            min_confirmed=args.min_confirmed,
            max_false_positive_rate=args.max_false_positive_rate,
            min_llm_triage_samples=args.min_llm_triage_samples,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "smoke":
        payload = run_smoke_suite(
            workspace=args.workspace,
            suites=[suite.strip() for suite in args.suite.split(",") if suite.strip()],
            cycles=args.cycles,
            autonomy=args.autonomy,
            dry_run=args.dry_run,
            worker_mode=args.worker_mode,
        )
        print(json.dumps(payload, indent=2))
        return 1 if payload.get("status") == "failed" else 0
    if args.command == "dispatch":
        tracks = [
            parse_dispatch_track_spec(track, base_dir=Path.cwd())
            for track in args.track
        ]
        verify_commands = dict(parse_dispatch_verify_spec(item) for item in args.verify)
        payload = dispatch_tracks(
            project_root=args.project_root,
            tracks=tracks,
            verify_commands=verify_commands,
            constraints_file=args.constraints_file,
            research_report_file=args.research_report,
            allow_unsafe=args.allow_unsafe,
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("all_ok") else 1
    if args.command == "memory-append":
        store = RoleMemoryStore(args.project_root)
        event_path = store.event_path(args.role)
        before_count = (
            sum(1 for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip())
            if event_path.is_file()
            else 0
        )
        kwargs = {
            "horizon": args.horizon,
            "text": args.text,
            "source": args.source,
        }
        if args.supersedes:
            kwargs["supersedes"] = args.supersedes
        try:
            event = store.append_memory_event(args.role, **kwargs)
        except ValueError as exc:
            print(f"memory-append: {exc}", file=sys.stderr)
            return 2
        after_count = sum(1 for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip())
        payload = {
            "status": "ok" if after_count > before_count else "deduped",
            "role": event["role_key"],
            "horizon": event["horizon"],
            "source": event.get("source", ""),
        }
        print(json.dumps(payload))
        return 0
    if args.command == "tg-poll":
        payload = poll_telegram_approvals(workspace=args.workspace, project_key=args.project_key, once=args.once)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "execution":
        try:
            payload = _run_execution_command(args)
        except ValueError as exc:
            print(f"execution: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2))
        return 0
    return 1


def _print_service_status(payload: dict[str, object]) -> None:
    started_at = payload.get("started_at")
    if isinstance(started_at, int | float):
        started_label = datetime.fromtimestamp(started_at).astimezone().isoformat(timespec="seconds")
    else:
        started_label = str(started_at or "unknown")
    print("Running: yes")
    print(f"URL: http://{payload['host']}:{payload['port']}")
    print(f"PID: {payload['pid']}")
    print(f"Workspace: {payload['workspace']}")
    print(f"Frontend dist: {payload.get('frontend_dist', 'unknown')}")
    print(f"Log path: {payload['log']}")
    print(f"Started at: {started_label}")


def _run_execution_command(args: argparse.Namespace) -> dict[str, object]:
    api = ExecutionAPI(args.project_root)
    if args.execution_command == "intake-github-issue":
        return api.intake_github_issue(
            repo=args.repo,
            issue_number=args.issue,
            adapter=args.adapter,
            role=args.role,
        )
    if args.execution_command == "run":
        return api.run_execution(args.execution_id)
    if args.execution_command == "pr":
        return api.create_or_reuse_pr(args.execution_id)
    if args.execution_command == "ci":
        return api.observe_ci(args.execution_id)
    if args.execution_command == "replay":
        return api.replay(args.execution_id)
    if args.execution_command == "review":
        return api.get_review_workbench(args.execution_id)
    if args.execution_command == "record-review-decision":
        return api.record_review_decision(
            args.execution_id,
            args.decision,
            args.reviewer,
            args.reason,
        )
    if args.execution_command == "export-review-workbench":
        return api.export_review_workbench_html(args.execution_id)
    raise ValueError(f"Unknown execution command: {args.execution_command}")


if __name__ == "__main__":
    raise SystemExit(main())
