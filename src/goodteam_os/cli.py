import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from goodteam_os.graph import run_until_next_gate
from goodteam_os.models import apply_role_overrides, load_model_profile
from goodteam_os.projects import check_project_target, load_project_target
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam-os")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("idea")
    start.add_argument("--project-key", default="GTO")
    start.add_argument("--workspace", type=Path, default=Path("."))
    start.add_argument("--model-config", type=Path, default=Path("models.yaml"))
    start.add_argument("--model-profile", default="balanced")
    start.add_argument("--role-model", action="append", default=[])
    start.add_argument("--source-type", choices=["new_project", "existing_project"], default="new_project")
    start.add_argument("--project-config", type=Path, default=Path("projects/eastern-birth-chart.local.yaml"))
    start.add_argument("--dry-run", action="store_true")

    serve_command = subparsers.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    serve_command.add_argument("--workspace", type=Path, default=Path("."))

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--project-config", type=Path, default=Path("projects/eastern-birth-chart.local.yaml"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        state = create_initial_state(
            run_id=str(uuid4()),
            project_key=args.project_key,
            idea=args.idea,
        )
        state.model_profile = args.model_profile
        state.role_models = apply_role_overrides(
            load_model_profile(args.model_config, args.model_profile),
            args.role_model,
        )
        if args.dry_run:
            state.source_type = args.source_type
            state.source_ref = str(args.project_config) if args.source_type == "existing_project" else "manual"
            if args.source_type == "existing_project":
                planned_artifacts = {
                    "takeover": str(args.workspace / "artifacts" / "runs" / state.run_id / "takeover" / "takeover-context.md")
                }
                state.current_node = "takeover_review_gate"
                state.next_node = "await_takeover_approval"
                state.approvals["takeover"] = "pending"
            else:
                planned_artifacts = {"prd": str(args.workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md")}
                state.current_node = "prd_review_gate"
                state.next_node = "await_prd_approval"
            payload = asdict(state)
            payload["dry_run"] = True
            payload["planned_artifacts"] = planned_artifacts
            print(json.dumps(payload, indent=2))
            return 0
        state.source_type = args.source_type
        state.source_ref = str(args.project_config) if args.source_type == "existing_project" else "manual"
        if args.source_type == "existing_project":
            from goodteam_os.graph import run_adoption_until_next_gate

            result = run_adoption_until_next_gate(
                state=state,
                workspace=args.workspace,
                project_config=args.project_config,
            )
        else:
            result = run_until_next_gate(
                state=state,
                workspace=args.workspace,
                project_config=args.project_config,
            )
        state_store = StateStore(root=args.workspace)
        state_store.write_current(result)
        state_store.write_checkpoint(result)
        print(json.dumps(asdict(result), indent=2))
        return 0
    if args.command == "serve":
        serve(workspace=args.workspace, host=args.host, port=args.port)
        return 0
    if args.command == "doctor":
        target = load_project_target(args.project_config)
        print(json.dumps(check_project_target(target), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
