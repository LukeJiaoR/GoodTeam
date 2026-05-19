import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from goodteam_os.graph import run_until_next_gate
from goodteam_os.models import apply_role_overrides, load_model_profile
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
    start.add_argument("--dry-run", action="store_true")

    serve_command = subparsers.add_parser("serve")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8788)
    serve_command.add_argument("--workspace", type=Path, default=Path("."))
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
            planned_prd = args.workspace / "artifacts" / "runs" / state.run_id / "prd" / "prd.md"
            state.current_node = "prd_review_gate"
            state.next_node = "await_prd_approval"
            payload = asdict(state)
            payload["dry_run"] = True
            payload["planned_artifacts"] = {"prd": str(planned_prd)}
            print(json.dumps(payload, indent=2))
            return 0
        result = run_until_next_gate(state=state, workspace=args.workspace)
        state_store = StateStore(root=args.workspace)
        state_store.write_current(result)
        state_store.write_checkpoint(result)
        print(json.dumps(asdict(result), indent=2))
        return 0
    if args.command == "serve":
        serve(workspace=args.workspace, host=args.host, port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
