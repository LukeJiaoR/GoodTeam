import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from goodteam_os.graph import run_until_next_gate
from goodteam_os.state import StateStore, create_initial_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goodteam-os")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("idea")
    start.add_argument("--project-key", default="GTO")
    start.add_argument("--workspace", type=Path, default=Path("."))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        state = create_initial_state(
            run_id=str(uuid4()),
            project_key=args.project_key,
            idea=args.idea,
        )
        result = run_until_next_gate(state=state, workspace=args.workspace)
        state_store = StateStore(root=args.workspace)
        state_store.write_current(result)
        state_store.write_checkpoint(result)
        print(json.dumps(asdict(result), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
