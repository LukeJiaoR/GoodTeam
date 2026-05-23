import argparse
import json
from pathlib import Path

from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.smoke import run_smoke_suite
from goodteam_os.telegram import poll_telegram_approvals
from goodteam_os.web import serve, start_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam-os")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("idea")
    start.add_argument("--project-key", default="GTO")
    start.add_argument("--workspace", type=Path, default=Path("."))
    start.add_argument("--model-config", type=Path, default=Path("models.yaml"))
    start.add_argument("--model-profile", default="")
    start.add_argument("--role-model", action="append", default=[])
    start.add_argument("--delivery-mode", choices=["mvp", "init_project", "multi_service"], default="")
    start.add_argument("--source-type", choices=["new_project", "existing_project"], default="new_project")
    start.add_argument("--project-config", type=Path)
    start.add_argument("--dry-run", action="store_true")

    serve_command = subparsers.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    serve_command.add_argument("--workspace", type=Path, default=Path("."))

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project-config", type=Path, required=True)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--workspace", type=Path, default=Path("."))
    smoke.add_argument("--suite", default="contentops,self")
    smoke.add_argument("--cycles", type=int, default=2)
    smoke.add_argument("--autonomy", choices=["full"], default="full")
    smoke.add_argument("--worker-mode", choices=["fake", "noop", "codex"], default="fake")
    smoke.add_argument("--dry-run", action="store_true")

    tg_poll = subparsers.add_parser("tg-poll")
    tg_poll.add_argument("--workspace", type=Path, default=Path("."))
    tg_poll.add_argument("--project-key", default="")
    tg_poll.add_argument("--once", action="store_true")
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
        serve(workspace=args.workspace, host=args.host, port=args.port)
        return 0
    if args.command == "doctor":
        target = load_project_target(args.project_config)
        print(json.dumps(check_project_target(target), indent=2))
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
    if args.command == "tg-poll":
        payload = poll_telegram_approvals(workspace=args.workspace, project_key=args.project_key, once=args.once)
        print(json.dumps(payload, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
